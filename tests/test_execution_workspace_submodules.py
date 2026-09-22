# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import subprocess

import pytest

from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True, capture_output=True, check=True
    )


@pytest.fixture(params=["dependency", "vendor/dependency"])
def linked_submodule_fixture(tmp_path, monkeypatch, request):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    sub_remote = tmp_path / "submodule.git"
    sub_seed = tmp_path / "submodule-seed"
    super_remote = tmp_path / "superproject.git"
    super_seed = tmp_path / "superproject-seed"
    product = tmp_path / "product"

    git(tmp_path, "init", "--bare", sub_remote)
    git(tmp_path, "init", "-b", "main", sub_seed)
    for repo in (sub_seed,):
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")
    (sub_seed / "dependency.txt").write_text("S1\n")
    git(sub_seed, "add", ".")
    git(sub_seed, "commit", "-m", "S1")
    git(sub_seed, "push", sub_remote, "HEAD:main")
    git(sub_remote, "symbolic-ref", "HEAD", "refs/heads/main")
    s1 = git(sub_seed, "rev-parse", "HEAD").stdout.strip()

    git(tmp_path, "init", "--bare", super_remote)
    git(tmp_path, "init", "-b", "main", super_seed)
    git(super_seed, "config", "user.email", "test@example.com")
    git(super_seed, "config", "user.name", "Test User")
    (super_seed / "source.txt").write_text("project\n")
    git(super_seed, "add", ".")
    git(super_seed, "commit", "-m", "project")
    dependency_path = request.param
    git(
        super_seed,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-b",
        "main",
        sub_remote,
        dependency_path,
    )
    git(super_seed, "commit", "-am", "pin dependency")
    git(super_seed, "push", super_remote, "HEAD:main")

    git(tmp_path, "clone", "-b", "main", super_remote, product)
    git(product, "config", "user.email", "test@example.com")
    git(product, "config", "user.name", "Test User")
    git(product, "config", "protocol.file.allow", "always")
    git(product, "submodule", "update", "--init", "--recursive")
    base = git(product, "rev-parse", "HEAD").stdout.strip()
    root = tmp_path / "execution-state"
    manager = ExecutionWorkspaceManager(product, root, "T-1")
    return manager, base, s1, dependency_path


def test_linked_execution_materializes_and_resumes_submodule(
    linked_submodule_fixture,
):
    manager, base, s1, dependency_path = linked_submodule_fixture

    workspace = manager.prepare(base)
    dependency = workspace.path / dependency_path
    assert git(dependency, "rev-parse", "HEAD").stdout.strip() == s1
    manager.verify_submodules(workspace)

    resumed = manager.prepare(base)
    assert resumed.path == workspace.path
    manager.verify_submodules(resumed)


def test_dirty_submodule_is_preserved_and_rejected(linked_submodule_fixture):
    manager, base, _s1, dependency_path = linked_submodule_fixture
    workspace = manager.prepare(base)
    dependency = workspace.path / dependency_path
    (dependency / "dependency.txt").write_text("unique evidence\n")

    with pytest.raises(ExecutionWorkspaceError, match="dirty"):
        manager.prepare(base)
    assert (dependency / "dependency.txt").read_text() == "unique evidence\n"


def test_wrong_submodule_head_is_preserved_and_rejected(
    linked_submodule_fixture,
):
    manager, base, _s1, dependency_path = linked_submodule_fixture
    workspace = manager.prepare(base)
    dependency = workspace.path / dependency_path
    (dependency / "second.txt").write_text("S2\n")
    git(dependency, "config", "user.email", "test@example.com")
    git(dependency, "config", "user.name", "Test User")
    git(dependency, "add", "second.txt")
    git(dependency, "commit", "-m", "S2")
    s2 = git(dependency, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(ExecutionWorkspaceError, match="unexpected"):
        manager.prepare(base)
    assert git(dependency, "rev-parse", "HEAD").stdout.strip() == s2


def test_clean_submodule_execution_worktree_can_be_retired(
    linked_submodule_fixture,
):
    manager, base, _s1, _dependency_path = linked_submodule_fixture
    workspace = manager.prepare(base)

    manager.retire(workspace, workspace.head)

    assert not workspace.path.exists()
    assert all(
        item.get("branch") != manager.branch
        for item in manager._registrations().values()
    )


def test_dirty_submodule_cannot_be_force_retired(linked_submodule_fixture):
    manager, base, _s1, dependency_path = linked_submodule_fixture
    workspace = manager.prepare(base)
    dependency = workspace.path / dependency_path
    (dependency / "dependency.txt").write_text("uncommitted evidence\n")

    with pytest.raises(ExecutionWorkspaceError, match="changed before removal"):
        manager.retire(workspace, workspace.head)
    assert workspace.path.exists()
    assert any(
        item.get("branch") == manager.branch
        for item in manager._registrations().values()
    )


def test_wrong_submodule_head_cannot_be_force_retired(linked_submodule_fixture):
    manager, base, _s1, dependency_path = linked_submodule_fixture
    workspace = manager.prepare(base)
    dependency = workspace.path / dependency_path
    (dependency / "second.txt").write_text("S2\n")
    git(dependency, "config", "user.email", "test@example.com")
    git(dependency, "config", "user.name", "Test User")
    git(dependency, "add", "second.txt")
    git(dependency, "commit", "-m", "S2")

    with pytest.raises(ExecutionWorkspaceError, match="changed before removal"):
        manager.retire(workspace, workspace.head)
    assert workspace.path.exists()
