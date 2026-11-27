# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Reviewed origin and interrupted worker generations are distinct lineages."""

import copy
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.execution_result import ExecutionReportStore
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.resource_binding import ResourceBinding
from devlegate.rework_transfer import ReworkTransfer
from devlegate.runtime import (
    DevlegateError,
    WorkflowBlockedError,
    WorkspacePreparationBlockedError,
)
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from devlegate.worker_supervisor import _capture_worker_identity


class ProcessLoss(BaseException):
    pass


def completed():
    return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)


def publish_rework(owner, report):
    control = owner.control_worktree
    artifact = control / "reviews" / "T-1" / f"{report.execution_id}.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        "<!-- devlegate-review-v1\n"
        "ticket: T-1\n"
        f"execution: {report.execution_id}\n"
        f"checkpoint: {report.workspace_head}\n"
        f"control: {report.control_head}\n"
        "disposition: rework\n-->\nKeep both implementation commits.\n"
    )
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "add", str(artifact.relative_to(control)))
    git(control, "commit", "-m", "request rework")
    git(control, "push", "origin", "devlegate/control")


@pytest.fixture
def multi_commit_rework(tmp_path, monkeypatch):
    repo, config, _ = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    for name in ("first.txt", "second.txt"):
        def work(workspace, _prompt, *, filename=name, **_kwargs):
            (workspace.path / filename).write_text(filename + "\n")
            return completed()

        monkeypatch.setattr(owner._workers, "run", work)
        assert run_test_iteration(owner) == 0
        report = ExecutionReportStore(owner.control_worktree).read(
            "T-1", owner._state["execution_id"],
        )
        publish_rework(owner, report)
    return owner, config, report


def advance_product(repo):
    (repo / "advanced.txt").write_text("new product\n")
    git(repo, "add", "advanced.txt")
    git(repo, "commit", "-m", "advance product")
    git(repo, "push", "origin", "HEAD:main")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


@pytest.mark.parametrize("source_kind", ["no-op", "retired-no-op", "incorporated"])
def test_rework_tree_already_present(tmp_path, monkeypatch, source_kind):
    repo, config, _ = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    def first(workspace, _prompt, **_kwargs):
        if source_kind == "incorporated":
            (workspace.path / "implementation.txt").write_text("already included\n")
        return completed()

    monkeypatch.setattr(owner._workers, "run", first)
    assert run_test_iteration(owner) == 0
    control = owner.control_worktree
    source = ExecutionReportStore(control).list("T-1")[0]
    if source_kind == "retired-no-op":
        with owner._lock():
            owner._retire_zero_delta_execution(source)
        assert not Path(source.execution_path).exists()
    if source_kind == "incorporated":
        git(repo, "merge", "--ff-only", source.workspace_head)
    target = advance_product(repo)
    publish_rework(owner, source)
    launches = []

    def second(workspace, prompt, **_kwargs):
        launches.append(workspace.head)
        assert workspace.base_head == workspace.head == target
        assert "REWORK" in prompt and "Keep both implementation commits" in prompt
        return completed()

    monkeypatch.setattr(owner._workers, "run", second)
    assert run_test_iteration(owner) == 0
    assert launches == [target]
    reports = ExecutionReportStore(control).list("T-1")
    assert len(reports) == 2 and source in reports
    new = next(item for item in reports if item != source)
    assert new.resource_generation != source.resource_generation
    assert new.workspace_head == new.code_base_head == target


@pytest.mark.parametrize("change", [
    "product", "control", "remote", "unretired", "foreign", "edited",
])
def test_waiting_rework_refuses_stale_or_unowned_handoff(
    multi_commit_rework, monkeypatch, change,
):
    from devlegate.implementation_transfer import source_resolution_digest

    owner, config, source = multi_commit_rework
    repo = owner.repo
    (repo / "first.txt").write_text("product version\n")
    git(repo, "add", "first.txt")
    git(repo, "commit", "-m", "conflict with first implementation commit")
    git(repo, "push", "origin", "HEAD:main")

    def no_launch(*_args, **_kwargs):
        pytest.fail("a waiting or stale candidate must never launch a worker")

    monkeypatch.setattr(owner._workers, "run", no_launch)
    with pytest.raises(WorkspacePreparationBlockedError, match="waiting for source"):
        run_test_iteration(owner)
    pending = owner._state["rework_provenance"]
    reports = ExecutionReportStore(owner.control_worktree).list("T-1")
    path = Path(pending["conflict"]["path"])
    original = Path(source.execution_path)
    (path / "first.txt").write_text("resolved\n")
    digest = source_resolution_digest(path)
    ref = f"refs/heads/{source.execution_branch}"
    if change == "product":
        advance_product(repo)
        expected = "stale REWORK target"
    elif change == "control":
        control = owner.control_worktree
        ticket = control / "kanban/todo/T-1.md"
        ticket.write_text(ticket.read_text() + "New assignment.\n")
        git(control, "commit", "-am", "change assignment")
        git(control, "push", "origin", "devlegate/control")
        expected = "stale REWORK (control|admission)"
    elif change == "remote":
        git(repo, "push", f"--force-with-lease={ref}:{source.workspace_head}",
            "origin", f"HEAD:{ref}")
        expected = "remote changed"
    elif change == "unretired":
        # Missing independent group retirement must refuse even though the
        # successor's pending state has no worker identity of its own.
        prior = ResourceBinding.for_execution(
            owner._execution_generation_binding().manager, source.execution_id,
        )
        next(prior.directory.glob("worker-retired.*.json")).unlink()
        expected = "retirement is unproven"
    elif change == "foreign":
        git(repo, "worktree", "remove", str(original))
        git(repo, "worktree", "add", str(original), source.execution_branch)
        expected = "conflict changed"
    else:
        (path / "added.txt").write_text("changed after observation\n")
        expected = "candidate changed since confirmation"

    restarted = Devlegate(config)
    with restarted._lock():
        transaction = ReworkTransfer(
            restarted, restarted._execution_generation_binding(), pending,
        )
        with pytest.raises(ExecutionWorkspaceError, match=expected):
            transaction.resolve(str(path), digest)
    assert (path / "first.txt").read_text() == "resolved\n"
    assert git(original, "rev-parse", "HEAD").stdout.strip() == source.workspace_head
    assert restarted._state["execution_id"] == owner._state["execution_id"]
    assert ExecutionReportStore(owner.control_worktree).list("T-1") == reports


def test_reset_discards_waiting_rework_without_using_candidate(
    multi_commit_rework, monkeypatch,
):
    from devlegate.ticket_reset import admit

    owner, config, source = multi_commit_rework
    (owner.repo / "first.txt").write_text("canonical product\n")
    git(owner.repo, "add", "first.txt")
    git(owner.repo, "commit", "-m", "produce a conflict")
    git(owner.repo, "push", "origin", "HEAD:main")
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: pytest.fail("launch"))
    with pytest.raises(WorkspacePreparationBlockedError, match="waiting for source"):
        run_test_iteration(owner)
    candidate = Path(owner._state["rework_provenance"]["conflict"]["path"])
    (candidate / "first.txt").write_text("unapproved edit\n")
    with owner._lock():
        assert admit(owner, "T-1")["accepted"] is True

    restarted = Devlegate(config)
    launches = []

    def fresh(workspace, prompt, **_kwargs):
        launches.append(restarted._state["execution_id"])
        assert "FRESH" in prompt and "Keep both implementation commits" not in prompt
        assert not (workspace.path / "second.txt").exists()
        assert (workspace.path / "first.txt").read_text() == "canonical product\n"
        return completed()

    monkeypatch.setattr(restarted._workers, "run", fresh)
    for _ in range(3):
        assert run_test_iteration(restarted) == 0
        if launches:
            break
    assert len(launches) == 1
    assert (candidate / "first.txt").read_text() == "unapproved edit\n"
    assert source in ExecutionReportStore(owner.control_worktree).list("T-1")


@pytest.mark.parametrize(("point", "timing"), [
    ("pin", "before"), ("pin", "after"),
    ("evidence", "before"), ("evidence", "after"), ("evidence", "lost-ack"),
    ("retire", "before"), ("retire", "after"),
    ("cas", "before"), ("cas", "after"),
    ("register", "before"), ("register", "after"),
    ("creation", "before"), ("creation", "after"),
    ("predecessor", "before"), ("predecessor", "after"), ("predecessor", "lost-ack"),
    ("publication", "before"), ("publication", "after"),
])
def test_rework_effect_replay(multi_commit_rework, monkeypatch, point, timing):
    import devlegate.resource_binding as resources
    import devlegate.runtime as runtime

    owner, config, source = multi_commit_rework
    target = advance_product(owner.repo)
    pin = f"refs/devlegate/recovery/workspace/T-1/{source.execution_id}"
    ref = f"refs/heads/{source.execution_branch}"
    calls = []
    original_git = runtime._git
    original_publish = resources._publish_once
    fired = False
    state_at_fault = None

    def boundary(name, effect):
        nonlocal fired, state_at_fault
        calls.append(name)
        if point != name or fired:
            return effect()
        fired = True
        state_at_fault = copy.deepcopy(owner._state)
        if timing == "before":
            raise ProcessLoss()
        result = effect()
        if timing == "after":
            raise ProcessLoss()
        assert result.returncode == 0
        return subprocess.CompletedProcess(result.args, 1, "", "lost acknowledgement")

    def interrupt_git(repo, *args, **kwargs):
        name = None
        if args[0] == "update-ref":
            if pin in args:
                name = "pin"
            elif ref in args:
                name = "cas"
        elif args[:2] == ("worktree", "remove"):
            name = "retire"
        elif args[2:4] == ("worktree", "add"):
            name = "register"
        elif args[0] == "push":
            if any(arg.endswith(f":{pin}") for arg in args):
                name = "evidence"
            elif f"--force-with-lease={ref}:{source.workspace_head}" in args:
                name = "predecessor"
        def effect():
            return original_git(repo, *args, **kwargs)

        return boundary(name, effect) if name else effect()

    def interrupt_record(path, value):
        name = {
            "receipt.json": "creation",
            "predecessor-publication-receipt.json": "publication",
        }.get(path.name)
        if name:
            return boundary(name, lambda: original_publish(path, value))
        return original_publish(path, value)

    launches = []

    def worker(workspace, prompt, *, execution_id, **_kwargs):
        launches.append(execution_id)
        assert "Keep both implementation commits" in prompt and "REWORK" in prompt
        assert workspace.base_head == target
        assert git(workspace.path, "rev-list", "--count", f"{target}..HEAD").stdout == (
            "2\n"
        )
        for name in ("first.txt", "second.txt", "advanced.txt"):
            assert (workspace.path / name).is_file()
        return completed()

    monkeypatch.setattr(runtime, "_git", interrupt_git)
    monkeypatch.setattr(resources, "_publish_once", interrupt_record)
    monkeypatch.setattr(owner._workers, "run", worker)
    if timing == "lost-ack":
        assert run_test_iteration(owner) == 0
    else:
        with pytest.raises(ProcessLoss):
            run_test_iteration(owner)
        assert not launches
        restarted = Devlegate(config)
        monkeypatch.setattr(restarted._workers, "run", worker)
        assert run_test_iteration(restarted) == 0
    assert state_at_fault is not None and launches == [state_at_fault["execution_id"]]
    assert calls.count(point) == (2 if timing == "before" else 1)
    for effect in ("retire", "cas", "register", "predecessor"):
        expected = 2 if timing == "before" and effect == point else 1
        assert calls.count(effect) == expected
    reports = ExecutionReportStore(owner.control_worktree).list("T-1")
    assert len(reports) == 3 and source in reports
    new = next(item for item in reports if item.execution_id == launches[0])
    assert new.resource_generation == (
        state_at_fault["execution_resource_binding"]["generation"]
    )
    assert new.resource_generation != source.resource_generation
    for reference, head in ((ref, new.workspace_head), (pin, source.workspace_head)):
        remote = git(owner.repo, "ls-remote", "origin", reference).stdout.split()[0]
        assert remote == head


@pytest.mark.parametrize("change", ["edit", "unrelated", "delete", "move"])
def test_bound_ticket_continuity_across_control_history(tmp_path, monkeypatch, change):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    control = next((state / "worktrees").glob("*/control"))
    admitted = git(control, "rev-parse", "HEAD").stdout.strip()
    ticket = control / "kanban/todo/T-1.md"
    original = ticket.read_text()
    body = original.split("---", 2)[2].lstrip("\r\n")
    if change in {"delete", "move"}:
        if change == "delete":
            ticket.unlink()
        else:
            ticket.rename(control / "kanban/backlog/T-1.md")
        git(control, "add", "-A")
        git(control, "commit", "-m", "withdraw active ticket")
        if change == "move":
            (control / "kanban/backlog/T-1.md").unlink()
        ticket.write_text(original + "replacement\n")
    elif change == "edit":
        ticket.write_text(original + "amendment\n")
    else:
        (control / "unrelated.txt").write_text("unrelated\n")
    git(control, "add", "-A")
    git(control, "commit", "-m", "update control")
    candidate = git(control, "rev-parse", "HEAD").stdout.strip()

    def prove():
        owner._prove_bound_ticket_descendant(
            candidate, admitted, "T-1", body, allow_amendment=True,
        )

    if change in {"delete", "move"}:
        with pytest.raises(WorkflowBlockedError, match="withdrawn or moved"):
            prove()
    else:
        prove()


@pytest.mark.parametrize("initial_state", ["backlog", "todo"])
def test_unbound_amendment_rechecks_dependencies_before_fresh_admission(
    tmp_path, monkeypatch, initial_state,
):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    control = next((state / "worktrees").glob("*/control"))
    if initial_state == "backlog":
        git(control, "mv", "kanban/todo/T-1.md", "kanban/backlog/T-1.md")
        git(control, "commit", "-m", "defer unbound ticket")
    (control / "kanban/backlog/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\nwork\n'
    )
    ticket = control / f"kanban/{initial_state}/T-1.md"
    ticket.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Amended"\n'
        '"depends_on":\n  - "D-1"\n---\nCurrent requirements.\n'
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "amend dependency and requirements")
    if initial_state == "backlog":
        git(control, "mv", "kanban/backlog/T-1.md", "kanban/todo/T-1.md")
        git(control, "commit", "-m", "schedule amended ticket")
    git(control, "push", "origin", "devlegate/control")
    owner = Devlegate(config)
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: pytest.fail("launch"))
    assert run_test_iteration(owner) == 0
    assert not ExecutionReportStore(control).list("T-1")

    ticket = control / "kanban/todo/T-1.md"
    ticket.write_text(ticket.read_text().replace('"depends_on":\n  - "D-1"\n', ""))
    git(control, "add", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "remove dependency")
    git(control, "push", "origin", "devlegate/control")
    admitted_head = git(control, "rev-parse", "HEAD").stdout.strip()

    def worker(_workspace, prompt, **_kwargs):
        assert "Current requirements." in prompt
        assert "## Historical Review Feedback" not in prompt
        assert owner._state["execution_control_head"] == admitted_head
        return completed()

    monkeypatch.setattr(owner._workers, "run", worker)
    assert run_test_iteration(owner) == 0


@pytest.mark.parametrize("decision", ["accept", "rework", "stale", "stale-rework"])
def test_running_amendment_publishes_and_reviews_current_spec(
    tmp_path, monkeypatch, decision,
):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    architect = tmp_path / "architect"
    git(tmp_path, "clone", tmp_path / "remote.git", architect)
    git(architect, "config", "user.email", "test@example.com")
    git(architect, "config", "user.name", "Test User")
    git(architect, "switch", "-c", "curator", "origin/devlegate/control")
    admitted = {}

    def first(workspace, prompt, **_kwargs):
        admitted["control"] = owner._state["execution_control_head"]
        admitted["execution"] = owner._state["execution_id"]
        assert "Amended requirements" not in prompt
        ticket = architect / "kanban/todo/T-1.md"
        ticket.write_text(ticket.read_text() + "\nAmended requirements.\n")
        git(architect, "add", "-A")
        git(architect, "commit", "-m", "amend running ticket")
        git(architect, "push", "origin", "HEAD:refs/heads/devlegate/control")
        (workspace.path / "implementation.txt").write_text("checkpoint\n")
        return completed()

    monkeypatch.setattr(owner._workers, "run", first)
    assert run_test_iteration(owner) == 0
    control = next((state / "worktrees").glob("*/control"))
    report = ExecutionReportStore(control).list("T-1")[0]
    assert report.control_head == admitted["control"]
    assert report.execution_id == admitted["execution"]
    assert "Amended requirements." in (control / "kanban/review/T-1.md").read_text()

    artifact = control / "reviews" / "T-1" / f"{report.execution_id}.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        f"# {'REWORK' if decision in {'rework', 'stale-rework'} else 'ACCEPT'}\n"
        "Address amended requirements.\n"
    )
    if decision in {"stale", "stale-rework"}:
        ticket = control / "kanban/review/T-1.md"
        ticket.write_text(ticket.read_text() + "\nUnreviewed B2.\n")
        git(control, "add", "kanban/review/T-1.md")
        git(control, "commit", "-m", "change review specification")
    destination = "todo" if decision in {"rework", "stale-rework"} else "accepted"
    git(control, "mv", "kanban/review/T-1.md", f"kanban/{destination}/T-1.md")
    git(control, "add", str(artifact.relative_to(control)))
    git(control, "commit", "-m", "review amended ticket")
    git(control, "push", "origin", "devlegate/control")

    if decision in {"accept", "stale", "stale-rework"}:
        monkeypatch.setattr(
            owner._workers, "run", lambda *_a, **_k: pytest.fail("duplicate worker")
        )
        if decision in {"stale", "stale-rework"}:
            with pytest.raises(
                DevlegateError,
                match="review content changed|review disposition history",
            ):
                run_test_iteration(owner)
            assert not (control / "kanban/done/T-1.md").exists()
            return
        assert run_test_iteration(owner) == 0
        assert (control / "kanban/done/T-1.md").is_file()
        assert len(ExecutionReportStore(control).list("T-1")) == 1
        return

    def rework(workspace, prompt, **_kwargs):
        assert owner._state["execution_id"] != report.execution_id
        assert "Amended requirements." in prompt
        assert "Address amended requirements." in prompt
        assert (workspace.path / "implementation.txt").read_text() == "checkpoint\n"
        return completed()

    monkeypatch.setattr(owner._workers, "run", rework)
    assert run_test_iteration(owner) == 0
    assert len(ExecutionReportStore(control).list("T-1")) == 2


@pytest.mark.parametrize(
    "scenario", ["valid", "missing", "invalid", "race", "dependency"],
)
def test_owner_admits_amended_continuation_with_historical_review(
    tmp_path, monkeypatch, scenario,
):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    def first(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("checkpoint\n")
        return completed()

    monkeypatch.setattr(owner._workers, "run", first)
    assert run_test_iteration(owner) == 0
    control = next((state / "worktrees").glob("*/control"))
    report = ExecutionReportStore(control).list("T-1")[0]
    artifact = control / "reviews" / "T-1" / f"{report.execution_id}.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "<!-- devlegate-review-v1\n"
        "ticket: T-1\n"
        f"execution: {report.execution_id}\n"
        f"checkpoint: {report.workspace_head}\n"
        f"control: {report.control_head}\n"
        "disposition: rework\n-->\nReview of original specification.\n"
    )
    if scenario == "invalid":
        artifact.write_text(artifact.read_text().replace(report.execution_id, "f" * 32))
    git(control, "mv", "kanban/review/T-1.md", "kanban/backlog/T-1.md")
    if scenario == "missing":
        artifact.unlink()
    else:
        git(control, "add", str(artifact.relative_to(control)))
    git(control, "commit", "-m", "defer reviewed ticket")
    ticket = control / "kanban/backlog/T-1.md"
    ticket.write_text(ticket.read_text() + "\nAmended requirements.\n")
    if scenario == "dependency":
        ticket.write_text(ticket.read_text().replace(
            "\n---\n", '\n"depends_on":\n  - "D-1"\n---\n', 1,
        ))
        (control / "kanban/backlog/D-1.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\nwork\n'
        )
    git(control, "add", "kanban/backlog")
    git(control, "commit", "-m", "amend ticket")
    for ticket_id in ("T-2", "T-3", "T-4"):
        (control / f"kanban/backlog/{ticket_id}.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Other work"\n'
            '"depends_on":\n  - "T-1"\n---\nwork\n'
        )
    git(control, "add", "kanban/backlog")
    git(control, "commit", "-m", "add independently scheduled tickets")
    for source, target, other in (
        ("backlog", "todo", "T-2"), ("todo", "backlog", "T-3"),
        ("backlog", "todo", "T-4"),
    ):
        git(control, "mv", f"kanban/{source}/T-1.md", f"kanban/{target}/T-1.md")
        git(control, "mv", f"kanban/backlog/{other}.md", "kanban/todo/")
        git(control, "commit", "-m", f"move amended ticket to {target}")
    git(control, "push", "origin", "devlegate/control")

    if scenario == "dependency":
        monkeypatch.setattr(
            owner._workers, "run", lambda *_a, **_k: pytest.fail("unmet dependency"),
        )
        assert run_test_iteration(owner) == 0
        assert len(ExecutionReportStore(control).list("T-1")) == 1
        return

    if scenario in {"missing", "invalid"}:
        monkeypatch.setattr(
            owner._workers, "run", lambda *_a, **_k: pytest.fail("launch")
        )
        with pytest.raises(DevlegateError, match="curator review"):
            run_test_iteration(owner)
        assert len(ExecutionReportStore(control).list("T-1")) == 1
        return

    if scenario == "race":
        with monkeypatch.context() as patch:
            patch.setattr(
                owner, "_stop_before_admission",
                lambda: owner._state.get("phase") == "agent_pending",
            )
            assert run_test_iteration(owner) == 0
        ticket = control / "kanban/todo/T-1.md"
        ticket.write_text(ticket.read_text() + "\nUnadmitted B2.\n")
        git(control, "add", "kanban/todo/T-1.md")
        git(control, "commit", "-m", "race pending admission")
        git(control, "push", "origin", "devlegate/control")
        pending = Devlegate(config)
        monkeypatch.setattr(
            pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
        )
        with pytest.raises(DevlegateError):
            run_test_iteration(pending)
        assert len(ExecutionReportStore(control).list("T-1")) == 1
        return

    def amended_worker(workspace, prompt, **_kwargs):
        assert owner._state["execution_id"] != report.execution_id
        assert owner._state["rework_provenance"]["amended_continuation"] is True
        assert "Amended requirements." in prompt
        assert "## Historical Review Feedback" in prompt
        assert "not a review of the current assigned work" in prompt
        assert "Review of original specification." in prompt
        assert (workspace.path / "implementation.txt").read_text() == "checkpoint\n"
        assert owner._state["rework_provenance"]["previous_report_checkpoint"] == (
            report.workspace_head
        )
        return completed()

    monkeypatch.setattr(owner._workers, "run", amended_worker)
    assert run_test_iteration(owner) == 0
    reports = ExecutionReportStore(control).list("T-1")
    assert len(reports) == 2
    new_report = next(
        item for item in reports if item.execution_id != report.execution_id
    )
    assert new_report.control_head != report.control_head
    assert (control / "kanban/review/T-1.md").read_text().endswith(
        "Amended requirements.\n"
    )
    assert artifact.read_text().endswith("Review of original specification.\n")

    import json

    from devlegate.inspection import inspect_object
    from devlegate.worker_prompt import worker_prompt_artifact_path

    journal = worker_prompt_artifact_path(owner.state_dir, new_report.execution_id)
    payload = json.loads(journal.read_text())
    view = inspect_object(
        owner, "execution", new_report.execution_id, include_prompt=True,
    )
    prompt = view["observation"]["worker_prompt"]
    assert prompt["status"] == "present", prompt
    assert prompt["review_binding"] == "historical"
    assert prompt["text"] == payload["prompt"]

    payload["review_publication"] = new_report.control_head
    journal.write_text(json.dumps(payload))
    rejected = inspect_object(
        owner, "execution", new_report.execution_id, include_prompt=True,
    )
    assert rejected["observation"]["report"] == view["observation"]["report"]
    prompt = rejected["observation"]["worker_prompt"]
    assert prompt["status"] == "unavailable" and "review binding" in prompt["reason"]
    assert "text" not in prompt


@pytest.fixture
def interrupted_rework(tmp_path, monkeypatch):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    def first(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("reviewed\n")
        return completed()

    monkeypatch.setattr(owner._workers, "run", first)
    assert run_test_iteration(owner) == 0
    control = next((state / "worktrees").glob("*/control"))
    report = ExecutionReportStore(control).list("T-1")[0]
    artifact = control / "reviews" / "T-1" / f"{report.execution_id}.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "<!-- devlegate-review-v1\n"
        "ticket: T-1\n"
        f"execution: {report.execution_id}\n"
        f"checkpoint: {report.workspace_head}\n"
        f"control: {report.control_head}\n"
        "disposition: rework\n-->\nKeep all interrupted progress.\n"
    )
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "add", str(artifact.relative_to(control)))
    git(control, "commit", "-m", "request rework")
    git(control, "push", "origin", "devlegate/control")

    def lost_worker(workspace, prompt, *, execution_id, identity_handler, **_kwargs):
        assert "Keep all interrupted progress." in prompt
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            identity_handler(_capture_worker_identity(helper, execution_id))
            (workspace.path / "implementation.txt").write_text("dirty tracked WIP\n")
            (workspace.path / "untracked.txt").write_text("dirty untracked WIP\n")
        finally:
            os.killpg(helper.pid, signal.SIGKILL)
            helper.wait(timeout=10)
        raise ProcessLoss()

    monkeypatch.setattr(owner._workers, "run", lost_worker)
    with pytest.raises(ProcessLoss):
        run_test_iteration(owner)
    prior = owner._execution_generation_binding()
    assert owner._state["phase"] == "agent_running"
    assert owner._state["rework_provenance"]["preparation_stage"] == (
        "remote_predecessor_installed"
    )
    assert not list(prior.directory.glob("worker-retired.*.json"))
    return owner, config, prior, copy.deepcopy(owner._state["rework_provenance"])


@pytest.mark.parametrize("boundary", ["before-intent", "intent", "receipt"])
def test_rework_process_loss_replays_same_admitted_handoff(
    interrupted_rework, monkeypatch, boundary,
):
    owner, config, prior, review = interrupted_rework
    old_head = git(prior.manager.path, "rev-parse", "HEAD").stdout.strip()
    original_record = ResourceBinding.record_intent
    original_handoff = ResourceBinding.prepare_handoff

    def record(binding):
        if boundary != "before-intent":
            original_record(binding)
        if boundary in {"before-intent", "intent"}:
            raise ProcessLoss()

    def handoff(binding, predecessor, **kwargs):
        original_handoff(binding, predecessor, **kwargs)
        raise ProcessLoss()

    monkeypatch.setattr(ResourceBinding, "record_intent", record)
    if boundary == "receipt":
        monkeypatch.setattr(ResourceBinding, "prepare_handoff", handoff)
    resumed = Devlegate(config)
    with pytest.raises(ProcessLoss):
        run_test_iteration(resumed)
    admitted = copy.deepcopy(resumed._state["execution_resource_binding"])
    assert admitted["execution_id"] != prior.intent["execution_id"]
    assert admitted["generation"] != prior.intent["generation"]
    assert admitted["predecessor_execution"] == prior.intent["execution_id"]
    assert admitted["predecessor_generation"] == prior.intent["generation"]
    assert resumed._state["phase"] == "agent_pending"
    assert resumed._state["rework_provenance"] == review
    assert git(prior.manager.path, "rev-parse", "HEAD").stdout.strip() == old_head
    assert not list(prior.directory.glob("worker-retired.*.json"))
    monkeypatch.setattr(ResourceBinding, "record_intent", original_record)
    monkeypatch.setattr(ResourceBinding, "prepare_handoff", original_handoff)

    # Repeated pre-launch restart must neither admit nor publish another generation.
    for _ in range(2):
        pending = Devlegate(config)
        with monkeypatch.context() as patch:
            patch.setattr(pending, "_stop_before_admission", lambda: True)
            assert run_test_iteration(pending) == 0
        assert pending._state["execution_resource_binding"] == admitted

    final = Devlegate(config)
    launches = []

    def worker(workspace, prompt, **_kwargs):
        launches.append(final._state["execution_id"])
        assert "Keep all interrupted progress." in prompt
        assert workspace.path == prior.manager.path
        assert (workspace.path / "implementation.txt").read_text() == (
            "dirty tracked WIP\n"
        )
        assert (workspace.path / "untracked.txt").read_text() == "dirty untracked WIP\n"
        assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == old_head
        return completed()

    monkeypatch.setattr(final._workers, "run", worker)
    monkeypatch.setattr(
        ReworkTransfer, "publish_predecessor",
        lambda *_a: pytest.fail("duplicate reviewed predecessor publication"),
    )
    monkeypatch.setattr(
        ReworkTransfer, "prepare",
        lambda *_a: pytest.fail("duplicate review evidence publication"),
    )
    assert run_test_iteration(final) == 0
    assert launches == [admitted["execution_id"]]
    assert final._state["execution_resource_binding"] == admitted
    assert final._state["rework_provenance"] == review
    head = git(prior.manager.path, "rev-parse", "HEAD").stdout.strip()
    assert head != old_head
    assert git(prior.manager.path, "merge-base", old_head, head).stdout.strip() == (
        old_head
    )
    remote = git(owner.repo, "ls-remote", "origin", admitted["remote_ref"])
    assert remote.stdout.split()[0] == head
    # Completed lifecycle replay cannot launch a duplicate worker.
    again = Devlegate(config)
    monkeypatch.setattr(
        again._workers, "run", lambda *_a, **_k: pytest.fail("duplicate")
    )
    assert run_test_iteration(again) == 0
    assert Path(admitted["path"]).joinpath("untracked.txt").read_text() == (
        "dirty untracked WIP\n"
    )


@pytest.mark.parametrize("observation", ["matching-live", "indeterminate"])
def test_pending_rework_retry_reobserves_predecessor_worker(
    interrupted_rework, monkeypatch, observation,
):
    _owner, config, prior, _review = interrupted_rework
    resumed = Devlegate(config)
    record = ResourceBinding.record_intent

    def crash(binding):
        record(binding)
        raise ProcessLoss()

    with monkeypatch.context() as patch:
        patch.setattr(ResourceBinding, "record_intent", crash)
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
    admitted = resumed._state["execution_resource_binding"]
    pending = Devlegate(config)
    assert pending._state["worker_identity"] is None
    monkeypatch.setattr(pending._workers, "observe", lambda _identity: observation)
    monkeypatch.setattr(
        pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
    )
    with pytest.raises(
        (DevlegateError, ExecutionWorkspaceError), match="absence is unproven"
    ):
        run_test_iteration(pending)
    assert pending._state["execution_resource_binding"] == admitted
    assert (prior.manager.path / "untracked.txt").read_text() == "dirty untracked WIP\n"
    assert not list(prior.directory.glob("worker-retired.*.json"))


def test_repeated_rework_process_loss_uses_immediate_resource_predecessor(
    interrupted_rework, monkeypatch,
):
    owner, config, prior, review = interrupted_rework
    generation = prior.intent
    for _ in range(2):
        resumed = Devlegate(config)
        monkeypatch.setattr(resumed._workers, "run", owner._workers.run)
        monkeypatch.setattr(
            ReworkTransfer, "publish_predecessor",
            lambda *_a: pytest.fail("duplicate predecessor publication"),
        )
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
        current = resumed._state["execution_resource_binding"]
        assert current["predecessor_execution"] == generation["execution_id"]
        assert current["predecessor_generation"] == generation["generation"]
        assert resumed._state["rework_provenance"] == review
        assert current["predecessor_execution"] != review["previous_execution_id"]
        generation = current


def test_pending_rework_handoff_rejects_altered_control_lineage(
    interrupted_rework, monkeypatch,
):
    owner, config, prior, _review = interrupted_rework
    resumed = Devlegate(config)
    with monkeypatch.context() as patch:
        patch.setattr(
            resumed, "_stop_before_admission",
            lambda: resumed._state.get("phase") == "agent_pending",
        )
        assert run_test_iteration(resumed) == 0
    admitted = resumed._state["execution_resource_binding"]
    control = owner.control_worktree
    ticket = control / "kanban/todo/T-1.md"
    ticket.write_text(ticket.read_text() + "\nChanged assignment.\n")
    git(control, "add", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "alter pending ticket")
    git(control, "push", "origin", "devlegate/control")
    pending = Devlegate(config)
    monkeypatch.setattr(
        pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
    )
    with pytest.raises(DevlegateError):
        run_test_iteration(pending)
    assert pending._state["execution_resource_binding"] == admitted
    assert (prior.manager.path / "untracked.txt").read_text() == "dirty untracked WIP\n"


@pytest.mark.parametrize(
    "boundary", ["handoff-intent.json", "devlegate-generation.json"]
)
def test_uncertain_rework_handoff_never_admits_another_generation(
    interrupted_rework, monkeypatch, boundary,
):
    import devlegate.resource_binding as module

    _owner, config, prior, review = interrupted_rework
    write = module._write_once

    def crash(path, value):
        write(path, value)
        if path.name == boundary:
            raise ProcessLoss()

    resumed = Devlegate(config)
    with monkeypatch.context() as patch:
        patch.setattr(module, "_write_once", crash)
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
    admitted = resumed._state["execution_resource_binding"]
    bindings = set(prior.directory.parent.iterdir())
    for _ in range(2):
        pending = Devlegate(config)
        monkeypatch.setattr(
            pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
        )
        with pytest.raises(
            (DevlegateError, ExecutionWorkspaceError), match="operator recovery"
        ):
            run_test_iteration(pending)
        assert pending._state["execution_resource_binding"] == admitted
        assert pending._state["rework_provenance"] == review
        assert set(prior.directory.parent.iterdir()) == bindings
        assert (prior.manager.path / "implementation.txt").read_text() == (
            "dirty tracked WIP\n"
        )
        assert (prior.manager.path / "untracked.txt").read_text() == (
            "dirty untracked WIP\n"
        )
