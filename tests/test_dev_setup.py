# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Exercise the source-only setup command with real, offline Git repositories."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
MODULE = "src/devlegate/_vendor/nanoyaml"


def git(repo, *args):
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


def init(repo):
    repo.mkdir()
    git(repo, "init")
    git(repo, "config", "user.name", "Setup Test")
    git(repo, "config", "user.email", "setup@example.invalid")


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    monkeypatch.setenv("GIT_CONFIG_NOSYSTEM", "1")
    monkeypatch.setenv("GIT_CONFIG_GLOBAL", os.devnull)
    monkeypatch.setenv("PYTHON_BIN", sys.executable)
    dependency = tmp_path / "dependency"
    init(dependency)
    (dependency / "__init__.py").write_text("class NanoYAMLError(Exception): pass\n")
    (dependency / ".gitignore").write_text("__pycache__/\n")
    first = commit(dependency, "first dependency")
    project = tmp_path / "project"
    init(project)
    shutil.copy2(ROOT / "dev", project / "dev")
    (project / "tools").mkdir()
    for name in ("setup_submodules.py", "source_preflight.py"):
        shutil.copy2(ROOT / "tools" / name, project / "tools" / name)
    (project / ".gitignore").write_text(".venv/\n__pycache__/\n")
    git(project, "submodule", "add", str(dependency), MODULE)
    commit(project, "first pin")
    return project, dependency, first


def fake_environment(project):
    binary = project / ".venv/bin/python"
    binary.parent.mkdir(parents=True)
    binary.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$*" >> "$(dirname "$0")/calls"\n'
    )
    binary.chmod(0o755)
    return binary.parent / "calls"


def setup(project):
    return subprocess.run(
        [str(project / "dev"), "setup"], cwd=project.parent,
        capture_output=True, text=True,
    )


def advance(project, dependency):
    (dependency / "new.py").write_text("VALUE = 2\n")
    second = commit(dependency, "second dependency")
    module = project / MODULE
    git(module, "fetch", "origin")
    git(module, "checkout", second)
    commit(project, "advance pin")
    return second


def assert_stopped(result, calls):
    assert result.returncode != 0
    assert "submodule synchronization failed" in result.stderr
    assert "before environment preparation" in result.stderr
    assert "rerun ./dev setup" in result.stderr
    assert not calls.exists()


def test_fresh_detached_linked_worktree_initializes_and_imports(source, tmp_path):
    project, _dependency, first = source
    worktree = tmp_path / "linked checkout"
    git(project, "worktree", "add", "--detach", str(worktree), "HEAD")
    assert not (worktree / MODULE / ".git").exists()
    calls = fake_environment(worktree)

    result = setup(worktree)

    assert result.returncode == 0, result.stderr
    assert git(worktree / MODULE, "rev-parse", "HEAD") == first
    assert calls.read_text().splitlines() == [f"-m pip install -e {worktree}[dev]"]
    subprocess.run(
        [sys.executable, "-c", "from devlegate._vendor.nanoyaml import NanoYAMLError"],
        cwd=worktree / "src", check=True,
    )
    assert git(project / MODULE, "rev-parse", "HEAD") == first
    # Import caches must not break an already-correct, repeated setup.
    assert setup(worktree).returncode == 0


def test_clean_stale_checkout_tracks_current_superproject_pin(source):
    project, dependency, first = source
    second = advance(project, dependency)
    git(project / MODULE, "checkout", first)
    subprocess.run(
        [sys.executable, "-c", "from devlegate._vendor.nanoyaml import NanoYAMLError"],
        cwd=project / "src", check=True,
    )
    calls = fake_environment(project)
    # A local update strategy must not prevent exact checkout of the pin.
    git(project, "config", "submodule.nanoyaml.update", "none")

    result = setup(project)

    assert result.returncode == 0, result.stderr
    assert git(project / MODULE, "rev-parse", "HEAD") == second
    assert calls.exists()
    assert setup(project).returncode == 0


def test_already_correct_checkout_needs_no_remote_access(source, tmp_path):
    project, dependency, first = source
    dependency.rename(tmp_path / "offline")
    calls = fake_environment(project)
    for _ in range(2):
        result = setup(project)
        assert result.returncode == 0, result.stderr
        assert git(project / MODULE, "rev-parse", "HEAD") == first
    assert len(calls.read_text().splitlines()) == 2


def test_clean_published_checkout_can_rewind_to_older_pin(source):
    project, dependency, first = source
    advance(project, dependency)
    git(project, "checkout", "HEAD~1")
    fake_environment(project)
    result = setup(project)
    assert result.returncode == 0, result.stderr
    assert git(project / MODULE, "rev-parse", "HEAD") == first


@pytest.mark.parametrize("kind", ["tracked", "staged", "untracked", "ignored"])
def test_local_content_is_preserved_before_pin_transition(source, kind):
    project, dependency, first = source
    advance(project, dependency)
    module = project / MODULE
    git(module, "checkout", first)
    if kind in {"tracked", "staged"}:
        local = module / "__init__.py"
    else:
        local = module / ("new.py" if kind == "ignored" else "notes.txt")
    local.write_text("precious local work\n")
    if kind == "staged":
        git(module, "add", ".")
    if kind == "ignored":
        exclude = Path(git(module, "rev-parse", "--absolute-git-dir")) / "info/exclude"
        exclude.write_text("new.py\n")
    calls = fake_environment(project)

    result = setup(project)

    assert_stopped(result, calls)
    assert "content" in result.stderr
    assert local.read_text() == "precious local work\n"
    assert git(module, "rev-parse", "HEAD") == first


def test_divergent_unpublished_commit_is_preserved(source):
    project, _dependency, _first = source
    module = project / MODULE
    git(module, "config", "user.name", "Setup Test")
    git(module, "config", "user.email", "setup@example.invalid")
    (module / "local.py").write_text("LOCAL = True\n")
    local_head = commit(module, "unpublished work")
    calls = fake_environment(project)

    result = setup(project)

    assert_stopped(result, calls)
    assert "divergent/unpublished" in result.stderr
    assert git(module, "rev-parse", "HEAD") == local_head


def test_staged_gitlink_cannot_override_checked_out_commit(source):
    project, dependency, _first = source
    second = advance(project, dependency)
    git(project, "checkout", "HEAD~1")
    git(project, "add", MODULE)
    calls = fake_environment(project)
    result = setup(project)
    assert_stopped(result, calls)
    assert "staged submodule changes" in result.stderr
    assert git(project / MODULE, "rev-parse", "HEAD") == second


def test_fetch_failure_does_not_prepare_environment(source):
    project, dependency, first = source
    # Record a real commit without fetching it into the submodule clone.
    (dependency / "unavailable.py").write_text("VALUE = 3\n")
    missing = commit(dependency, "unavailable pin")
    git(project, "update-index", "--cacheinfo", f"160000,{missing},{MODULE}")
    git(project, "commit", "-m", "pin inaccessible dependency")
    git(project / MODULE, "remote", "set-url", "origin", str(dependency / "missing"))
    calls = fake_environment(project)

    result = setup(project)

    assert_stopped(result, calls)
    assert "failed" in result.stderr
    assert git(project / MODULE, "rev-parse", "HEAD") == first
    # Repairing access permits a retry, with exactly the recorded pin.
    git(project / MODULE, "remote", "set-url", "origin", str(dependency))
    result = setup(project)
    assert result.returncode == 0, result.stderr
    assert git(project / MODULE, "rev-parse", "HEAD") == missing


def test_uninitialized_clone_failure_does_not_create_environment(source, tmp_path):
    project, dependency, _first = source
    worktree = tmp_path / "fresh"
    git(project, "worktree", "add", "--detach", str(worktree), "HEAD")
    dependency.rename(tmp_path / "offline")

    result = setup(worktree)

    assert_stopped(result, worktree / ".venv/bin/calls")
    assert not (worktree / ".venv").exists()


def test_nonempty_uninitialized_worktree_is_preserved(source, tmp_path):
    project, _dependency, _first = source
    worktree = tmp_path / "fresh"
    git(project, "worktree", "add", "--detach", str(worktree), "HEAD")
    local = worktree / MODULE / "notes.txt"
    local.write_text("local notes\n")
    result = setup(worktree)
    assert_stopped(result, worktree / ".venv/bin/calls")
    assert "not empty" in result.stderr
    assert local.read_text() == "local notes\n"


def test_nested_submodule_is_initialized_and_dirty_content_is_protected(
    source, tmp_path,
):
    project, dependency, _first = source
    nested = tmp_path / "nested"
    init(nested)
    (nested / "data.txt").write_text("original\n")
    nested_pin = commit(nested, "nested dependency")
    git(dependency, "submodule", "add", str(nested), "nested")
    pin = commit(dependency, "add nested dependency")
    module = project / MODULE
    git(module, "fetch", "origin")
    git(module, "checkout", pin)
    commit(project, "pin nested dependency")
    calls = fake_environment(project)
    result = setup(project)
    assert result.returncode == 0, result.stderr
    assert git(module / "nested", "rev-parse", "HEAD") == nested_pin
    calls.unlink()
    local = module / "nested/data.txt"
    local.write_text("precious nested changes\n")
    result = setup(project)
    assert_stopped(result, calls)
    assert local.read_text() == "precious nested changes\n"


def test_materialized_source_archive_setup_preserves_offline_behavior(source, tmp_path):
    project, _dependency, _first = source
    archive = tmp_path / "archive"
    shutil.copytree(project, archive, ignore=shutil.ignore_patterns(".git"))
    calls = fake_environment(archive)
    result = setup(archive)
    assert result.returncode == 0, result.stderr
    assert calls.exists()
    calls.unlink()
    (archive / MODULE / "__init__.py").unlink()
    assert_stopped(setup(archive), calls)
