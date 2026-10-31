# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import shutil
import subprocess
from pathlib import Path

import pytest

from devlegate.execution_inspection_evidence import observe_execution_evidence
from devlegate.execution_resource_observation import observe_execution_resources
from devlegate.git import GitObservationError


def git(path: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(path), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


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
