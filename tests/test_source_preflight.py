# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

SPEC = importlib.util.spec_from_file_location(
    "source_preflight", Path(__file__).parents[1] / "tools/source_preflight.py"
)
PREFLIGHT = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = PREFLIGHT
SPEC.loader.exec_module(PREFLIGHT)


EXPECTED = "a" * 40
OTHER = "b" * 40


def fake_git(monkeypatch, repo, *, observed=EXPECTED, tree=True):
    submodule = repo / "src/devlegate/_vendor/nanoyaml"

    def run(path, *arguments):
        if arguments[:3] == ("ls-tree", "-r", "-z"):
            if path != repo:
                return subprocess.CompletedProcess([], 0, "", "")
            output = f"160000 commit {EXPECTED}\tsrc/devlegate/_vendor/nanoyaml\0"
            if not tree:
                output = ""
            return subprocess.CompletedProcess([], 0, output, "")
        if arguments == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess([], 0, str(submodule) + "\n", "")
        if arguments == ("rev-parse", "HEAD"):
            return subprocess.CompletedProcess([], 0, observed + "\n", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(PREFLIGHT, "_git", run)


def test_uninitialized_gitlink_fails_with_path_and_expected_identity(
    monkeypatch, tmp_path
):
    fake_git(monkeypatch, tmp_path)
    with pytest.raises(PREFLIGHT.SourcePreflightError, match="nanoyaml") as error:
        PREFLIGHT.validate_source_submodules(tmp_path, EXPECTED)
    assert EXPECTED in str(error.value)
    assert "git submodule update --init --recursive" in str(error.value)


def test_missing_submodule_directory_fails(monkeypatch, tmp_path):
    fake_git(monkeypatch, tmp_path)
    (tmp_path / "src/devlegate/_vendor").mkdir(parents=True)
    with pytest.raises(PREFLIGHT.SourcePreflightError, match="not initialized"):
        PREFLIGHT.validate_source_submodules(tmp_path, EXPECTED)


def test_pinned_submodule_passes_and_does_not_mutate(monkeypatch, tmp_path):
    fake_git(monkeypatch, tmp_path)
    submodule = tmp_path / "src/devlegate/_vendor/nanoyaml"
    submodule.mkdir(parents=True)
    calls = []
    original = PREFLIGHT._git

    def observe(repo, *arguments):
        calls.append(arguments)
        return original(repo, *arguments)

    monkeypatch.setattr(PREFLIGHT, "_git", observe)
    PREFLIGHT.validate_source_submodules(tmp_path, EXPECTED)
    assert all("update" not in call for call in calls)


def test_wrong_submodule_commit_fails(monkeypatch, tmp_path):
    fake_git(monkeypatch, tmp_path, observed=OTHER)
    (tmp_path / "src/devlegate/_vendor/nanoyaml").mkdir(parents=True)
    with pytest.raises(PREFLIGHT.SourcePreflightError, match="mismatch") as error:
        PREFLIGHT.validate_source_submodules(tmp_path, EXPECTED)
    assert "nanoyaml" in str(error.value)
    assert EXPECTED in str(error.value)
    assert OTHER in str(error.value)


def test_nested_submodule_is_checked_recursively(monkeypatch, tmp_path):
    outer = tmp_path / "outer"
    inner = outer / "inner"
    inner.mkdir(parents=True)
    outer_expected = "c" * 40
    inner_expected = "d" * 40

    def links(repo, _commit):
        return (
            [(PREFLIGHT.PurePosixPath("outer"), outer_expected)]
            if repo == tmp_path
            else [(PREFLIGHT.PurePosixPath("inner"), inner_expected)]
            if repo == outer
            else []
        )

    def git(repo, *arguments):
        if arguments == ("rev-parse", "--show-toplevel"):
            return subprocess.CompletedProcess([], 0, str(repo) + "\n", "")
        if arguments == ("rev-parse", "HEAD"):
            expected = inner_expected if repo == inner else outer_expected
            return subprocess.CompletedProcess([], 0, expected + "\n", "")
        raise AssertionError(arguments)

    monkeypatch.setattr(PREFLIGHT, "_gitlinks", links)
    monkeypatch.setattr(PREFLIGHT, "_git", git)
    PREFLIGHT.validate_source_submodules(tmp_path, "e" * 40)
