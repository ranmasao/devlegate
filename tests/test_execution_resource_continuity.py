# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real-Git examples of observations that do not establish ownership."""

import json
import subprocess
from pathlib import Path

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


def test_recreated_upstream_ref_has_same_observed_oid(tmp_path):
    remote = tmp_path / "remote.git"
    repo = tmp_path / "repo"
    git(tmp_path, "init", "--bare", remote)
    git(tmp_path, "init", "-b", "main", repo)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    ref = "refs/heads/devlegate/work/T-1"
    git(repo, "push", remote, f"{base}:{ref}")
    before = git(repo, "ls-remote", remote, ref)

    git(repo, "push", remote, f":{ref}")
    git(repo, "push", remote, f"{base}:{ref}")

    assert git(repo, "ls-remote", remote, ref) == before == f"{base}\t{ref}"


def test_fresh_creation_marker_is_not_transferred_to_identical_recreation(tmp_path):
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-b", "main", repo)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    identity = {"execution_id": "generation-1", "nonce": "nonce-1"}
    manager = ExecutionWorkspaceManager(repo, tmp_path / "state", "T-1")
    workspace = manager.prepare(base, creation_identity=identity)
    marker = Path(manager.creation_marker)
    assert json.loads(marker.read_text()) == identity

    git(repo, "worktree", "remove", workspace.path)
    git(repo, "update-ref", "-d", f"refs/heads/{workspace.branch}", base)
    git(repo, "branch", workspace.branch, base)
    git(repo, "worktree", "add", workspace.path, workspace.branch)
    replacement_admin = Path(git(workspace.path, "rev-parse", "--absolute-git-dir"))
    assert not (replacement_admin / marker.name).exists()
    replay = ExecutionWorkspaceManager(repo, tmp_path / "state", "T-1")
    replay.prepare(base, creation_identity=identity)
    assert replay.creation_marker is None
    assert not (replacement_admin / marker.name).exists()


def test_verified_creation_observation_detects_marker_and_ref_drift(tmp_path):
    repo = tmp_path / "repo"
    git(tmp_path, "init", "-b", "main", repo)
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "config", "user.name", "Test User")
    git(repo, "commit", "--allow-empty", "-m", "base")
    base = git(repo, "rev-parse", "HEAD")
    manager = ExecutionWorkspaceManager(repo, tmp_path / "state", "T-1")
    identity = {
        "ticket_id": "T-1", "execution_id": "generation-1", "nonce": "nonce-1",
        "control_head": base, "admitted_base_head": base,
        "branch": manager.branch, "path": str(manager.path),
    }
    workspace = manager.prepare(base, creation_identity=identity)
    marker = manager.creation_marker
    assert manager.verify_creation_observation(identity, workspace, marker)
    Path(marker).write_text(json.dumps({**identity, "nonce": "other"}))
    assert not manager.verify_creation_observation(identity, workspace, marker)
    Path(marker).write_text(json.dumps(identity))
    git(workspace.path, "commit", "--allow-empty", "-m", "next")
    assert not manager.verify_creation_observation(identity, workspace, marker)
