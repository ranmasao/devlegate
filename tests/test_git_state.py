# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: I001
import json
import sqlite3
import subprocess

import pytest
from runtime_helpers import run_test_iteration
from _cli_support import _ensure_publisher, git, invoke, publish_control, ticket

from devlegate.cli import Devlegate, DevlegateError
import devlegate.runtime as runtime_module


def code_update(fixture, name="remote.txt", content="remote\n"):
    publisher = _ensure_publisher(fixture)
    path = publisher / name
    path.write_text(content)
    git(publisher, "add", name)
    git(publisher, "commit", "-m", "code update")
    git(publisher, "push", "origin", "HEAD:main")


def state_payload(fixture):
    database = next(fixture["state"].glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    payload = connection.execute(
        "SELECT payload FROM runtime_state WHERE id = 1"
    ).fetchone()[0]
    connection.close()
    return json.loads(payload)


def test_code_sync_completes_before_invalid_control_workflow(git_fixture):
    code_update(git_fixture)
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )

    result = invoke(git_fixture, "--once")

    assert result.returncode == 1
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    assert "invalid ticket" in result.stdout
    assert state_payload(git_fixture)["phase"] != "merge_pending"
    second = invoke(git_fixture, "--once")
    assert "resumed after completed merge" not in second.stdout


def test_invalid_workflow_repeated_run_has_no_stale_merge_recovery(git_fixture):
    code_update(git_fixture)
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )

    first = invoke(git_fixture, "--once")
    second = invoke(git_fixture, "--once")

    assert first.returncode == second.returncode == 1
    assert "resumed after completed merge" not in second.stdout
    assert "superseding pending revision" not in second.stdout
    assert state_payload(git_fixture)["phase"] != "merge_pending"


def test_descendant_control_revision_repairs_invalid_workflow(git_fixture):
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )
    assert invoke(git_fixture, "--once").returncode == 1
    publish_control(
        git_fixture,
        "repair workflow",
        {"kanban/todo/T-1.md": ticket("Repaired")},
        sync=False,
    )

    result = invoke(git_fixture, "--once")

    assert result.returncode == 1
    assert "execution failed" in result.stdout
    assert "superseding pending revision" not in result.stdout
    assert state_payload(git_fixture)["phase"] == "idle"


def test_interrupted_pre_sync_merge_pending_recovers(git_fixture, monkeypatch):
    code_update(git_fixture)
    target = git(_ensure_publisher(git_fixture), "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    assert run_test_iteration(devlegate) == 0
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == target
    assert state_payload(git_fixture)["phase"] == "idle"


def _fake_merge_runtime(
    tmp_path, monkeypatch, *, remote_after_interrupt="target", status_after_interrupt=""
):
    engine = object.__new__(Devlegate)
    engine._host_owner_thread_id = None
    engine.repo = tmp_path / "repo"
    engine.repo.mkdir()
    engine.current_branch = "main"
    engine._state = {
        "phase": "merge_pending",
        "local_head": "old",
        "remote_head": "target",
    }
    heads = {
        "remote": iter(["target", remote_after_interrupt]),
        "HEAD": iter(["old", "target"]),
    }
    calls = []

    engine._resolve_commit_runtime = lambda _repo, revision: next(
        heads["remote" if revision == "origin/main" else "HEAD"]
    )
    engine._branch_runtime = lambda _repo: "main"
    engine._worktree_root_runtime = lambda repo: repo.resolve()
    engine._status_runtime = lambda _repo: status_after_interrupt

    def git_runtime(_repo, *args, check=True):
        calls.append(args)
        if len(calls) == 1:
            raise runtime_module.ShutdownInterrupted
        return subprocess.CompletedProcess(["git", *args], 0, "", "")

    engine._git_runtime = git_runtime
    monkeypatch.setattr(
        runtime_module,
        "merge_fast_forward",
        lambda repo, reference, *, runner: runner(
            repo, "merge", "--ff-only", reference
        ),
    )
    return engine, calls


def test_interrupted_merge_retries_only_the_admitted_commit(tmp_path, monkeypatch):
    engine, calls = _fake_merge_runtime(tmp_path, monkeypatch)

    result = engine._merge_fast_forward_runtime(
        engine.repo, "origin/main", "old", "target"
    )

    assert result.returncode == 0
    assert calls == [
        ("merge", "--ff-only", "target"),
        ("merge", "--ff-only", "target"),
    ]


def test_interrupted_merge_blocks_when_tracking_ref_advances(tmp_path, monkeypatch):
    engine, calls = _fake_merge_runtime(
        tmp_path, monkeypatch, remote_after_interrupt="third"
    )

    with pytest.raises(DevlegateError, match="target changed"):
        engine._merge_fast_forward_runtime(
            engine.repo, "origin/main", "old", "target"
        )

    assert calls == [("merge", "--ff-only", "target")]


def test_interrupted_merge_blocks_on_ambiguous_checkout_state(tmp_path, monkeypatch):
    engine, calls = _fake_merge_runtime(
        tmp_path, monkeypatch, status_after_interrupt=" M file.txt"
    )

    with pytest.raises(DevlegateError, match="ambiguous"):
        engine._merge_fast_forward_runtime(
            engine.repo, "origin/main", "old", "target"
        )

    assert calls == [("merge", "--ff-only", "target")]


def test_post_sync_crash_state_is_cleared_without_second_merge(
    git_fixture, monkeypatch
):
    code_update(git_fixture)
    target = git(_ensure_publisher(git_fixture), "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    result = run_test_iteration(devlegate)

    assert result == 0
    assert state_payload(git_fixture)["phase"] == "idle"


def test_merge_pending_divergence_fails_closed(git_fixture, monkeypatch):
    code_update(git_fixture)
    target = git(_ensure_publisher(git_fixture), "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    (git_fixture["working"] / "local.txt").write_text("diverged\n")
    git(git_fixture["working"], "add", "local.txt")
    git(git_fixture["working"], "commit", "-m", "divergent local update")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    with pytest.raises(DevlegateError, match="does not match the local HEAD"):
        run_test_iteration(devlegate)
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() != target


def test_agent_running_fails_closed_without_dispatch(
    git_fixture, monkeypatch, capsys
):
    publish_control(
        git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Bound")}
    )
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch="devlegate/work/T-1",
        execution_path=str(devlegate.execution_worktree_root / "work" / "T-1"),
        execution_id="attempt-1",
        execution_remote_head=None,
    )

    restarted = Devlegate(git_fixture["config"])
    calls = []
    monkeypatch.setattr(
        restarted._workers,
        "run",
        lambda *_args, **_kwargs: calls.append(True),
    )
    with pytest.raises(DevlegateError, match="unsafe execution stage"):
        run_test_iteration(restarted)
    assert calls == []
    assert capsys.readouterr().out == ""
    assert state_payload(git_fixture)["phase"] == "agent_running"


def test_unresolved_agent_phase_survives_repeated_runs(
    git_fixture, monkeypatch
):
    publish_control(
        git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Bound")}
    )
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch="devlegate/work/T-1",
        execution_path=str(devlegate.execution_worktree_root / "work" / "T-1"),
        execution_id="attempt-1",
        execution_remote_head=None,
    )
    for _ in range(2):
        with pytest.raises(DevlegateError, match="unsafe execution stage"):
            run_test_iteration(devlegate)
    assert state_payload(git_fixture)["phase"] == "agent_running"
