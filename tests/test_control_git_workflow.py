# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

import devlegate.control_git_workflow as control_git_workflow
from devlegate.control_git_workflow import sync_control
from devlegate.runtime import ShutdownInterrupted


def test_sync_control_trace_fetches_then_fast_forwards_once() -> None:
    trace: list[tuple[str, ...]] = []
    heads = iter(["remote-head", "local-head", "remote-head"])

    def runner(_cwd: Path, *args: str, check: bool = False):
        trace.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = sync_control(
        control_worktree=Path("control"),
        control_branch="devlegate/control",
        remote_name="origin",
        state={},
        validate_worktree=lambda: None,
        observe_worktree=lambda _path, _branch: SimpleNamespace(
            working_tree_clean=True
        ),
        runner=runner,
        resolve=lambda _path, _revision: next(heads),
        ancestor=lambda _path, _left, _right: True,
        accepted_integration=lambda *_args: ("ambiguous", ""),
        log=lambda _message: None,
    )

    assert result == ("remote-head", "remote-head")
    assert trace == [
        ("fetch", "--prune", "origin", "devlegate/control"),
        ("merge", "--ff-only", "origin/devlegate/control"),
    ]


def test_sync_control_does_not_merge_an_already_current_checkout() -> None:
    trace: list[tuple[str, ...]] = []

    def runner(_cwd: Path, *args: str, check: bool = False):
        trace.append(args)
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    result = sync_control(
        control_worktree=Path("control"),
        control_branch="devlegate/control",
        remote_name="origin",
        state={},
        validate_worktree=lambda: None,
        observe_worktree=lambda _path, _branch: SimpleNamespace(
            working_tree_clean=True
        ),
        runner=runner,
        resolve=lambda _path, _revision: "same-head",
        ancestor=lambda _path, _left, _right: True,
        accepted_integration=lambda *_args: ("ambiguous", ""),
        log=lambda _message: None,
    )

    assert result == ("same-head", "same-head")
    assert trace == [("fetch", "--prune", "origin", "devlegate/control")]


def test_sync_control_does_not_retry_an_interrupted_merge() -> None:
    trace: list[tuple[str, ...]] = []

    def runner(_cwd: Path, *args: str, check: bool = False):
        trace.append(args)
        if args[:2] == ("merge", "--ff-only"):
            raise ShutdownInterrupted
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    with pytest.raises(ShutdownInterrupted):
        sync_control(
            control_worktree=Path("control"),
            control_branch="devlegate/control",
            remote_name="origin",
            state={"phase": "merge_pending"},
            validate_worktree=lambda: None,
            observe_worktree=lambda _path, _branch: SimpleNamespace(
                working_tree_clean=True
            ),
            runner=runner,
            resolve=lambda _path, revision: (
                "remote-head"
                if revision == "origin/devlegate/control"
                else "local-head"
            ),
            ancestor=lambda _path, _left, _right: True,
            accepted_integration=lambda *_args: ("ambiguous", ""),
            log=lambda _message: None,
        )

    assert trace == [
        ("fetch", "--prune", "origin", "devlegate/control"),
        ("merge", "--ff-only", "origin/devlegate/control"),
    ]


def test_attach_existing_control_owns_fetch_validation_and_worktree_add(
    monkeypatch, tmp_path
) -> None:
    trace: list[str] = []
    worktree = tmp_path / "control"
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        control_git_workflow,
        "fetch_ref",
        lambda *_args, **_kwargs: trace.append("fetch") or result,
    )
    monkeypatch.setattr(
        control_git_workflow,
        "resolve_commit",
        lambda *_args, **_kwargs: "remote-head",
    )
    monkeypatch.setattr(
        control_git_workflow, "ref_exists", lambda *_args, **_kwargs: False
    )
    monkeypatch.setattr(
        control_git_workflow,
        "add_worktree",
        lambda *_args, **_kwargs: trace.append("worktree-add") or result,
    )

    attached = control_git_workflow.attach_existing_control(
        repo=tmp_path / "repo",
        control_worktree=worktree,
        control_branch="devlegate/control",
        remote_name="origin",
        workflow_paths={"ready": "workflow/ready"},
        runner=lambda *_args, **_kwargs: result,
    )

    assert attached["result"] == "attached_existing_remote"
    assert trace == ["fetch", "worktree-add"]


def test_bootstrap_control_preserves_publish_and_refresh_order(
    monkeypatch, tmp_path
) -> None:
    trace: list[str] = []
    worktree = tmp_path / "control"
    result = SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(
        control_git_workflow, "ref_exists", lambda *_args, **_kwargs: False
    )

    def add_worktree(*_args, **_kwargs):
        trace.append("worktree-add")
        worktree.mkdir()
        return result

    monkeypatch.setattr(control_git_workflow, "add_worktree", add_worktree)
    monkeypatch.setattr(
        control_git_workflow,
        "add_all",
        lambda *_args, **_kwargs: trace.append("add") or result,
    )
    monkeypatch.setattr(
        control_git_workflow,
        "git_commit",
        lambda *_args, **_kwargs: trace.append("commit") or result,
    )
    monkeypatch.setattr(
        control_git_workflow,
        "push_refs",
        lambda *_args, **_kwargs: trace.append("push") or result,
    )
    monkeypatch.setattr(
        control_git_workflow,
        "fetch_ref",
        lambda *_args, **_kwargs: trace.append("refresh") or result,
    )

    initialized = control_git_workflow.bootstrap_control(
        repo=tmp_path / "repo",
        control_worktree=worktree,
        control_branch="devlegate/control",
        remote_name="origin",
        workflow_paths={"ready": "workflow/ready"},
        runner=lambda *_args, **_kwargs: result,
    )

    assert initialized["result"] == "initialized_new_control_plane"
    assert trace == ["worktree-add", "add", "commit", "push", "refresh"]
