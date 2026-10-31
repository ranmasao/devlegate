# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real-Git examples of observations that do not establish ownership."""

import subprocess

from devlegate.execution_workspace import ExecutionWorkspaceManager


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True, capture_output=True, check=True
    ).stdout.strip()


def test_recreated_worktree_and_ref_have_identical_topology(tmp_path):
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-b", "main", repo)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    manager = ExecutionWorkspaceManager(repo, tmp_path / "state", "T-1")
    first = manager.prepare(base)
    before = manager.inspect(base, base)
    ref = f"refs/heads/{first.branch}"

    git(repo, "worktree", "remove", first.path)
    git(repo, "update-ref", "-d", ref, base)
    git(repo, "update-ref", ref, base, "0" * len(base))
    git(repo, "worktree", "add", first.path, first.branch)

    after = manager.inspect(base, base)
    assert before.classification == after.classification == "REUSABLE"
    assert before.workspace == after.workspace
    assert git(repo, "rev-parse", ref) == base
