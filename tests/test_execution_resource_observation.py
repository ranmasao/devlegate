# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

from devlegate.execution_inspection_evidence import (
    observe_execution_evidence,
    prove_single_execution_admission,
)
from devlegate.execution_resource_observation import observe_execution_resources
from devlegate.execution_result import build_execution_report
from devlegate.git import GitObservationError, run_git
from devlegate.runtime import ServiceEngine
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def test_historical_admission_proof_rejects_changed_ticket_and_reports(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    ticket = repo / "todo" / "TASK-1.md"
    ticket.parent.mkdir()
    ticket.write_text("original\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "ticket")
    original = git(repo, "rev-parse", "HEAD")
    blob = git(repo, "rev-parse", "HEAD:todo/TASK-1.md")
    report = build_execution_report(
        execution_id="first", ticket_id="TASK-1", code_base_head=original,
        control_head=original, execution_branch="devlegate/work/TASK-1",
        execution_path=str(tmp_path / "work" / "TASK-1"), workspace_head=original,
        run=WorkerRunResult(
            0, None, WorkerClaim("incomplete", "summary", (), (), None), None
        ),
    )
    report_path = repo / "executions" / "TASK-1" / "first.json"
    report_path.parent.mkdir(parents=True)
    report_path.write_text(report.to_json())
    git(repo, "add", ".")
    git(repo, "commit", "-m", "historical report")
    admission = git(repo, "rev-parse", "HEAD")
    proof = prove_single_execution_admission(
        repo, admission, "TASK-1", blob, todo_path="todo", review_path="review"
    )
    assert proof["admission_commit"] == admission
    assert proof["checkpoint"] == original
    backlog = repo / "backlog" / "TASK-1.md"
    backlog.parent.mkdir()
    ticket.rename(backlog)
    git(repo, "add", "-A")
    git(repo, "commit", "-m", "defer")
    git(repo, "remote", "add", "origin", str(repo))
    engine = SimpleNamespace(
        repo=repo, control_worktree=repo, execution_worktree_root=tmp_path / "managed",
        remote_name="origin", backlog_path="backlog", todo_path="todo",
        review_path="review", accepted_path="accepted", done_path="done",
        _state={"phase": "idle"}, _git_runtime=run_git,
    )
    observed = ServiceEngine._inspect_execution(engine, "TASK-1")
    assert observed["generation"] == proof
    assert observed["safe"] is False
    with pytest.raises(ValueError, match="ticket identity"):
        prove_single_execution_admission(
            repo, admission, "TASK-1", "different", todo_path="todo",
            review_path="review",
        )
    (report_path.parent / "second.json").write_text(report.to_json())
    git(repo, "add", ".")
    git(repo, "commit", "-m", "another report")
    with pytest.raises(ValueError, match="ambiguous"):
        prove_single_execution_admission(
            repo, git(repo, "rev-parse", "HEAD"), "TASK-1", blob,
            todo_path="todo", review_path="review",
        )


def test_committed_evidence_and_pins_are_inventoried_without_writes(tmp_path: Path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    report = repo / "executions" / "TASK-1" / "first.json"
    report.parent.mkdir(parents=True)
    report.write_text("{}\n")
    review = repo / "reviews" / "TASK-1" / "first.md"
    review.parent.mkdir(parents=True)
    review.write_text("review\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "evidence")
    head = git(repo, "rev-parse", "HEAD")
    pin = "refs/devlegate/reconciliation/TASK-1/first"
    git(repo, "update-ref", pin, head)
    report.write_text("uncommitted change\n")
    before = git(repo, "status", "--porcelain=v1")
    result = observe_execution_evidence(repo, repo, head, "TASK-1")
    assert [item["path"] for item in result["files"]] == [
        "executions/TASK-1/first.json", "reviews/TASK-1/first.md"
    ]
    assert result["files"][0]["object"] == git(
        repo, "rev-parse", "HEAD:executions/TASK-1/first.json"
    )
    assert result["local_pins"] == [{"ref": pin, "object": head}]
    assert result["history"]["executions"] == [{
        "commit": head, "changes": ["A\texecutions/TASK-1/first.json"]
    }]
    assert observe_execution_evidence(repo, repo, head, "TASK-1") == result
    assert git(repo, "status", "--porcelain=v1") == before


def test_real_git_resource_observation(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    (repo / "file").write_text("content\n")
    git(repo, "add", "file")
    git(repo, "commit", "-m", "initial")
    remote = tmp_path / "remote.git"
    subprocess.run(
        ["git", "init", "--bare", str(remote)], check=True, capture_output=True
    )
    git(repo, "remote", "add", "origin", str(remote))
    root = tmp_path / "managed"
    root.mkdir()
    empty = observe_execution_resources(repo, root, "TASK-1", "origin")
    assert empty["local_head"] is None
    assert empty["remote_head"] is None
    assert empty["worktrees"] == []

    path = root / "work" / "TASK-1"
    path.parent.mkdir()
    git(repo, "worktree", "add", "-b", "devlegate/work/TASK-1", str(path))
    git(repo, "push", "origin", "devlegate/work/TASK-1")
    git(repo, "worktree", "lock", "--reason", "kept for review", str(path))
    (path / "untracked").write_text("dirty\n")
    before = (path / ".git").read_bytes()
    index = Path(git(path, "rev-parse", "--git-path", "index"))
    if not index.is_absolute():
        index = path / index
    index_before = (index.read_bytes(), index.stat().st_mtime_ns)
    observation = observe_execution_resources(repo, root, "TASK-1", "origin")
    assert observation["local_head"] == git(repo, "rev-parse", "HEAD")
    assert observation["remote_head"] == observation["local_head"]
    assert observation["worktrees"][0]["dirty"] is True
    assert observation["worktrees"][0]["status"] == "?? untracked\0"
    assert observation["worktrees"][0]["locked"] == "kept for review"
    assert (path / ".git").read_bytes() == before
    assert observation == observe_execution_resources(repo, root, "TASK-1", "origin")
    assert (index.read_bytes(), index.stat().st_mtime_ns) == index_before

    git(repo, "remote", "set-url", "origin", str(tmp_path / "missing"))
    with pytest.raises(GitObservationError):
        observe_execution_resources(repo, root, "TASK-1", "origin")


def test_foreign_branch_registration_is_visible(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "initial")
    git(repo, "remote", "add", "origin", str(repo))
    foreign = tmp_path / "foreign"
    git(repo, "worktree", "add", "-b", "devlegate/work/TASK-1", str(foreign))
    observation = observe_execution_resources(
        repo, tmp_path / "managed", "TASK-1", "origin"
    )
    assert observation["path_exists"] is False
    assert len(observation["worktrees"]) == 1
    assert observation["worktrees"][0]["path"] == str(foreign)
    assert observation["worktrees"][0]["expected"] is False


def test_missing_worktree_is_reported_without_pruning(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "commit", "--allow-empty", "-m", "initial")
    git(repo, "remote", "add", "origin", str(repo))
    root = tmp_path / "managed"
    path = root / "work" / "TASK-1"
    path.parent.mkdir(parents=True)
    git(repo, "worktree", "add", "-b", "devlegate/work/TASK-1", str(path))
    shutil.rmtree(path)
    before = git(repo, "worktree", "list", "--porcelain")
    result = observe_execution_resources(repo, root, "TASK-1", "origin")
    assert result["worktrees"][0]["prunable"] is not None
    assert result["worktrees"][0]["exists"] is False
    assert result["worktrees"][0]["status"] is None
    assert git(repo, "worktree", "list", "--porcelain") == before


def test_backlog_is_not_rejected_only_for_being_unfinished(tmp_path: Path) -> None:
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    ticket = repo / "backlog" / "TASK-1.md"
    ticket.parent.mkdir()
    ticket.write_text("deferred\n")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "defer ticket")
    git(repo, "remote", "add", "origin", str(repo))
    engine = SimpleNamespace(
        repo=repo, control_worktree=repo, execution_worktree_root=tmp_path / "managed",
        remote_name="origin", backlog_path="backlog", todo_path="todo",
        review_path="review", accepted_path="accepted", done_path="done",
        _state={"phase": "idle"}, _git_runtime=run_git,
    )
    result = ServiceEngine._inspect_execution(engine, "TASK-1")
    assert result["tickets"][0]["state"] == "backlog"
    assert result["ticket_history"]["backlog"] == [{
        "commit": git(repo, "rev-parse", "HEAD"),
        "changes": ["A\tbacklog/TASK-1.md"],
    }]
    assert result["unsafe_reasons"] == ["execution generation ownership is not proven"]
    assert result["safe"] is False

    engine._state = {"phase": "agent_running", "execution_ticket_id": "TASK-1"}
    active = ServiceEngine._inspect_execution(engine, "TASK-1")
    assert "ticket has persisted execution ownership" in active["unsafe_reasons"]
