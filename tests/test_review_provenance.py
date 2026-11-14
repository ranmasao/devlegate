# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Persisted curator-review binding compatibility."""

import hashlib
import json
import subprocess
from types import SimpleNamespace

import pytest

from devlegate import runtime
from devlegate.execution_result import ExecutionReport, build_execution_report
from devlegate.git import run_git
from devlegate.review_provenance import (
    ReviewProvenanceError,
    _transition,
    resolve_rework_review,
    restore_review_binding,
)
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def _binding():
    ticket_id = "TASK-088"
    execution_id = "a" * 32
    checkpoint = "b" * 40
    control = "c" * 40
    content = (
        "<!-- devlegate-review-v1\n"
        f"ticket: {ticket_id}\n"
        f"execution: {execution_id}\n"
        f"checkpoint: {checkpoint}\n"
        f"control: {control}\n"
        "disposition: rework\n"
        "-->\n"
        "Preserve the worker changes.\n"
    )
    return {
        "content": content,
        "path": f"reviews/{ticket_id}/{execution_id}.md",
        "publication": "d" * 40,
        "transition": ["e" * 40],
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }, ticket_id, execution_id, checkpoint, control


def test_restore_review_binding_accepts_known_inert_legacy_markers():
    binding, ticket_id, execution_id, checkpoint, control = _binding()
    binding.update(legacy=False, legacy_boundary=None)

    restored = restore_review_binding(
        binding,
        ticket_id=ticket_id,
        execution_id=execution_id,
        checkpoint=checkpoint,
        control_head=control,
    )

    assert restored.content == binding["content"]


def test_acceptance_binding_is_opt_in_and_cannot_authorize_ordinary_rework():
    binding, ticket_id, execution_id, checkpoint, control = _binding()
    binding["content"] = binding["content"].replace("disposition: rework",
                                                "disposition: accept")
    binding["sha256"] = hashlib.sha256(binding["content"].encode()).hexdigest()
    arguments = dict(ticket_id=ticket_id, execution_id=execution_id,
                     checkpoint=checkpoint, control_head=control)
    with pytest.raises(ReviewProvenanceError, match="attribution"):
        restore_review_binding(binding, **arguments)
    assert restore_review_binding(binding, **arguments, disposition="accept").content


@pytest.mark.parametrize("key, value", [("legacy", True), ("legacy_boundary", "x")])
def test_restore_review_binding_rejects_non_inert_legacy_markers(key, value):
    binding, ticket_id, execution_id, checkpoint, control = _binding()
    binding[key] = value

    with pytest.raises(ReviewProvenanceError, match="compatibility fields"):
        restore_review_binding(
            binding,
            ticket_id=ticket_id,
            execution_id=execution_id,
            checkpoint=checkpoint,
            control_head=control,
        )


@pytest.mark.parametrize(
    "route", [("todo",), ("backlog", "todo"), ("todo", "backlog", "todo")]
)
def test_real_git_rework_transitions(tmp_path, route):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for name in ("review", "todo", "backlog", "reviews/TASK-1"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "review/TASK-1.md").write_text("ticket\n")
    git("add", ".")
    git("commit", "-qm", "lifecycle")
    lifecycle = git("rev-parse", "HEAD")
    source = "review"
    commits = []
    for index, target in enumerate(route):
        git("mv", f"{source}/TASK-1.md", f"{target}/TASK-1.md")
        if index == 0:
            (tmp_path / "reviews/TASK-1/review.md").write_text("feedback\n")
            git("add", ".")
        git("commit", "-qm", f"move to {target}")
        commits.append(git("rev-parse", "HEAD"))
        if index == 0 and len(route) == 3:
            (tmp_path / "failed-report.json").write_text("failed\n")
            git("add", ".")
            git("commit", "-qm", "failed attempt")
        source = target
    transition, publication = _transition(
        tmp_path, git("rev-parse", "HEAD"), lifecycle, "TASK-1",
        todo_path="todo", review_path="review", backlog_path="backlog",
        artifact_path="reviews/TASK-1/review.md", runner=run_git,
    )
    assert transition == tuple(commits)
    assert publication == commits[0]


def test_transport_failure_is_not_selected_as_reviewed(monkeypatch, tmp_path):
    report = SimpleNamespace(
        ticket_id="TASK-1",
        execution_id="a" * 32,
        result=SimpleNamespace(egress_ok=True, transport_error="oversized event"),
    )
    path = f"executions/{report.ticket_id}/{report.execution_id}.json"
    monkeypatch.setattr(
        runtime, "history_changes", lambda *a, **kw: (("b" * 40, (f"A\t{path}",)),)
    )
    engine = SimpleNamespace(control_worktree=tmp_path, _git_runtime=run_git, _state={})
    assert (
        runtime.ServiceEngine._latest_reviewed_rework_report(engine, (report,)) is None
    )


@pytest.mark.parametrize(
    "transport_error, egress_ok", [("oversized event", True), (None, False)]
)
def test_review_resolver_rejects_failed_generation(
    tmp_path, transport_error, egress_ok
):
    report = SimpleNamespace(
        result=SimpleNamespace(transport_error=transport_error, egress_ok=egress_ok)
    )
    with pytest.raises(ReviewProvenanceError, match="failed worker execution"):
        resolve_rework_review(
            tmp_path, "head", "lifecycle", report,
            todo_path="todo", review_path="review", backlog_path="backlog",
            runner=run_git,
        )


def test_real_git_failed_attempt_preserves_prior_review_on_restart(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for directory in (
        "review", "todo", "backlog", "reviews/TASK-1", "executions/TASK-1"
    ):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "review/TASK-1.md").write_text("canonical ticket\n")
    git("add", ".")
    git("commit", "-qm", "initial ticket")
    control = git("rev-parse", "HEAD")
    git("update-ref", "refs/heads/checkpoint-reviewed", control)
    claim = WorkerClaim("completed", "done", (), ())
    reviewed = build_execution_report(
        ticket_id="TASK-1", execution_id="a" * 32, code_base_head=control,
        workspace_head=control, control_head=control, execution_branch="work/TASK-1",
        execution_path=str(tmp_path / "work"),
        run=WorkerRunResult(0, None, claim, None),
    )
    report_path = tmp_path / f"executions/TASK-1/{reviewed.execution_id}.json"
    report_path.write_text(reviewed.to_json())
    git("add", ".")
    git("commit", "-qm", "publish reviewed execution")
    lifecycle = git("rev-parse", "HEAD")
    feedback = (
        "<!-- devlegate-review-v1\n"
        f"ticket: {reviewed.ticket_id}\n"
        f"execution: {reviewed.execution_id}\n"
        f"checkpoint: {reviewed.workspace_head}\n"
        f"control: {reviewed.control_head}\n"
        "disposition: rework\n-->\nExact historical feedback.\n"
    )
    artifact = tmp_path / f"reviews/TASK-1/{reviewed.execution_id}.md"
    artifact.write_text(feedback)
    git("mv", "review/TASK-1.md", "todo/TASK-1.md")
    git("add", ".")
    git("commit", "-qm", "publish review and rework")
    publication = git("rev-parse", "HEAD")
    failed = build_execution_report(
        ticket_id="TASK-1", execution_id="b" * 32, code_base_head=control,
        workspace_head=publication, control_head=publication,
        execution_branch="work/TASK-1", execution_path=str(tmp_path / "work"),
        run=WorkerRunResult(0, "oversized event", claim, None),
    )
    git("update-ref", "refs/heads/checkpoint-failed", failed.workspace_head)
    failed_path = tmp_path / f"executions/TASK-1/{failed.execution_id}.json"
    failed_path.write_text(failed.to_json())
    git("add", ".")
    git("commit", "-qm", "publish failed execution")
    git("mv", "todo/TASK-1.md", "backlog/TASK-1.md")
    git("commit", "-qm", "defer")
    deferred = git("rev-parse", "HEAD")
    git("mv", "backlog/TASK-1.md", "todo/TASK-1.md")
    git("commit", "-qm", "requeue")
    head = git("rev-parse", "HEAD")
    refs = git("show-ref")
    evidence = {
        path: path.read_bytes() for path in (report_path, failed_path, artifact)
    }

    # Recreate the engine on the second evaluation, as after a restart.
    for _ in range(2):
        reviewed = ExecutionReport.from_dict(json.loads(report_path.read_text()))
        failed = ExecutionReport.from_dict(json.loads(failed_path.read_text()))
        assert failed.result.egress_ok and failed.result.transport_error is not None
        binding = resolve_rework_review(
            tmp_path, head, lifecycle, reviewed,
            todo_path="todo", review_path="review", backlog_path="backlog",
            runner=run_git,
        )
        assert binding.content == feedback
        assert binding.path == str(artifact.relative_to(tmp_path))
        assert binding.publication == publication
        assert binding.transition == (publication, deferred, head)
        with pytest.raises(ReviewProvenanceError, match="failed worker execution"):
            resolve_rework_review(
                tmp_path, head, lifecycle, failed,
                todo_path="todo", review_path="review", backlog_path="backlog",
                runner=run_git,
            )
        engine = SimpleNamespace(
            control_worktree=tmp_path, _git_runtime=run_git, _state={},
        )
        with pytest.raises(
            runtime.WorkflowBlockedError, match="continuation ownership"
        ):
            runtime.ServiceEngine._latest_reviewed_rework_report(
                engine, (reviewed, failed)
            )
        assert git("show-ref") == refs
        assert git("status", "--porcelain") == ""
        assert {path: path.read_bytes() for path in evidence} == evidence
        assert (tmp_path / "todo/TASK-1.md").read_text() == "canonical ticket\n"


def test_failed_execution_does_not_fall_back_to_older_review(monkeypatch, tmp_path):
    failed = SimpleNamespace(
        ticket_id="TASK-1", execution_id="a" * 32,
        result=SimpleNamespace(egress_ok=True, transport_error="oversized event"),
    )
    older = SimpleNamespace(
        ticket_id="TASK-1", execution_id="b" * 32,
        result=SimpleNamespace(egress_ok=True, transport_error=None),
    )
    monkeypatch.setattr(
        runtime, "history_changes",
        lambda *a, **kw: tuple(
            (commit * 40, (f"A\texecutions/TASK-1/{report.execution_id}.json",))
            for commit, report in (("c", failed), ("d", older))
        ),
    )
    engine = SimpleNamespace(control_worktree=tmp_path, _git_runtime=run_git, _state={})
    with pytest.raises(runtime.WorkflowBlockedError, match="continuation ownership"):
        runtime.ServiceEngine._latest_reviewed_rework_report(engine, (failed, older))


@pytest.mark.parametrize("damage", ["deleted", "modified", "ticket", "out_of_order"])
def test_real_git_rework_rejects_damaged_evidence(tmp_path, damage):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True
        ).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for name in ("review", "todo", "backlog", "reviews/TASK-1"):
        (tmp_path / name).mkdir(parents=True, exist_ok=True)
    (tmp_path / "review/TASK-1.md").write_text("original ticket\n")
    git("add", ".")
    git("commit", "-qm", "lifecycle")
    lifecycle = git("rev-parse", "HEAD")
    report = SimpleNamespace(
        ticket_id="TASK-1", execution_id="a" * 32,
        workspace_head="b" * 40, control_head="c" * 40,
        result=SimpleNamespace(egress_ok=True, transport_error=None),
    )
    artifact = tmp_path / f"reviews/TASK-1/{report.execution_id}.md"
    artifact.write_text(
        "<!-- devlegate-review-v1\n"
        f"ticket: {report.ticket_id}\n"
        f"execution: {report.execution_id}\n"
        f"checkpoint: {report.workspace_head}\n"
        f"control: {report.control_head}\n"
        "disposition: rework\n-->\nOriginal feedback.\n"
    )
    git("mv", "review/TASK-1.md", "todo/TASK-1.md")
    git("add", ".")
    git("commit", "-qm", "publish review")
    if damage == "deleted":
        git("rm", str(artifact.relative_to(tmp_path)))
    elif damage == "modified":
        artifact.write_text(artifact.read_text() + "Changed feedback.\n")
        git("add", ".")
    elif damage == "ticket":
        git("mv", "todo/TASK-1.md", "backlog/TASK-1.md")
        (tmp_path / "backlog/TASK-1.md").write_text("changed ticket\n")
        git("add", ".")
    else:
        git("mv", "todo/TASK-1.md", "backlog/TASK-1.md")
        git("commit", "-qm", "defer")
        git("mv", "backlog/TASK-1.md", "review/TASK-1.md")
    git("commit", "-qm", "damage evidence")
    with pytest.raises(ReviewProvenanceError):
        resolve_rework_review(
            tmp_path, git("rev-parse", "HEAD"), lifecycle, report,
            todo_path="todo", review_path="review", backlog_path="backlog",
            runner=run_git,
        )
