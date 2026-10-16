from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from devlegate.control_git_workflow import sync_control


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
