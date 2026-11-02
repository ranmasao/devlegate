# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable control-history proof tests."""

import subprocess

import pytest

from devlegate.execution_result import build_execution_report
from devlegate.git import run_git
from devlegate.historical_provenance import (
    HistoricalProvenanceError,
    prove_historical_provenance,
)
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


@pytest.mark.parametrize("damage", [None, "early_deferral", "report", "ticket"])
def test_historical_control_proof(tmp_path, damage):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, check=True, text=True, capture_output=True
        ).stdout.strip()

    def commit(message):
        git("add", "-A")
        git("commit", "-qm", message)
        return git("rev-parse", "HEAD")

    git("init", "-q")
    git("config", "user.email", "test@example.com")
    git("config", "user.name", "Test")
    for directory in ("todo", "review", "backlog", "executions/T-1", "reviews/T-1"):
        (tmp_path / directory).mkdir(parents=True, exist_ok=True)
    (tmp_path / "todo/T-1.md").write_text("ticket\n")
    initial = commit("ticket")
    claim = WorkerClaim("completed", "done", (), ())
    reviewed = build_execution_report(
        ticket_id="T-1", execution_id="a" * 32, code_base_head=initial,
        workspace_head=initial, control_head=initial, execution_branch="work/T-1",
        execution_path=str(tmp_path / "work"),
        run=WorkerRunResult(0, None, claim, None),
    )
    (tmp_path / f"executions/T-1/{reviewed.execution_id}.json").write_text(
        reviewed.to_json()
    )
    git("mv", "todo/T-1.md", "review/T-1.md")
    lifecycle = commit("reviewed lifecycle")
    (tmp_path / f"reviews/T-1/{reviewed.execution_id}.md").write_text(
        "<!-- devlegate-review-v1\n"
        f"ticket: T-1\nexecution: {reviewed.execution_id}\n"
        f"checkpoint: {initial}\ncontrol: {initial}\n"
        "disposition: rework\n-->\nfeedback\n"
    )
    git("mv", "review/T-1.md", "todo/T-1.md")
    publication = commit("rework")
    failed = build_execution_report(
        ticket_id="T-1", execution_id="b" * 32, code_base_head=initial,
        workspace_head=initial, control_head=publication, execution_branch="work/T-1",
        execution_path=str(tmp_path / "work"),
        run=WorkerRunResult(0, "transport error", claim, None),
    )
    if damage == "early_deferral":
        git("mv", "todo/T-1.md", "backlog/T-1.md")
        commit("premature deferral")
    (tmp_path / f"executions/T-1/{failed.execution_id}.json").write_text(
        failed.to_json()
    )
    failed_lifecycle = commit("failed lifecycle")
    if damage != "early_deferral":
        git("mv", "todo/T-1.md", "backlog/T-1.md")
        commit("defer")
    git("mv", "backlog/T-1.md", "todo/T-1.md")
    commit("requeue")
    if damage == "report":
        path = tmp_path / f"executions/T-1/{failed.execution_id}.json"
        path.write_text(path.read_text() + " ")
        commit("modify report")
    if damage == "ticket":
        (tmp_path / "todo/T-1.md").write_text("changed\n")
        commit("modify ticket")
    head = git("rev-parse", "HEAD")
    refs = git("show-ref")

    def observe():
        return prove_historical_provenance(
            tmp_path, head, reviewed, failed, todo_path="todo", review_path="review",
            backlog_path="backlog", runner=run_git,
        )

    if damage:
        with pytest.raises(HistoricalProvenanceError):
            observe()
    else:
        proof = observe()
        assert proof.reviewed_lifecycle == lifecycle
        assert proof.failed_lifecycle == failed_lifecycle
        assert proof.review.publication == publication
    assert git("show-ref") == refs
    assert git("status", "--porcelain") == ""
