# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from test_control_plane import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli
import devlegate.daemon as daemon
import devlegate.runtime as runtime
from devlegate.cli import DevlegateError
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.runtime import ShutdownInterrupted, WorkflowBlockedError
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), config, state


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
        ("service_shutdown", -signal.SIGTERM),
    ],
)
def test_matching_shutdown_signal_is_control_flow(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    result = subprocess.CompletedProcess([], returncode, "", "")

    with engine._stop_context(stop_intent):
        with pytest.raises(ShutdownInterrupted):
            engine._raise_on_shutdown_interruption(result)


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        (None, -signal.SIGINT),
        ("operator_abort", -signal.SIGTERM),
        ("service_shutdown", 1),
    ],
)
def test_unmatched_git_failure_remains_diagnostic(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    if kind is not None:
        stop_intent.request(kind)
    result = subprocess.CompletedProcess([], returncode, "", "")

    with engine._stop_context(stop_intent):
        engine._raise_on_shutdown_interruption(result)


def test_polling_exits_cleanly_after_shutdown_interrupted_git(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()

    def run_once():
        stop_intent.request("operator_abort")
        raise ShutdownInterrupted

    engine.run_once = run_once

    assert engine.serve(stop_intent) == 0
    assert engine.service_snapshot().lifecycle == "ready"


def test_foreground_service_command_constructs_one_service_engine(
    tmp_path, monkeypatch
):
    working, config, _state = control_fixture(tmp_path)
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

    def host(engine, **_kwargs):
        calls.append(("host", engine))
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_service", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "foreground", "--env", str(config)],
    )
    monkeypatch.chdir(working)

    assert cli.main() == 0
    assert len(calls) == 2
    assert calls[0] == ("init", config, False)
    assert calls[1][0] == "host"
    assert isinstance(calls[1][1], FakeServiceEngine)


@pytest.mark.parametrize(
    ("signum", "expected_kind", "expected_status"),
    [
        (signal.SIGINT, "operator_abort", 130),
        (signal.SIGTERM, "service_shutdown", 0),
    ],
)
@pytest.mark.parametrize("host", [daemon.run_service])
def test_daemon_host_signal_handler_only_sets_stop_intent(
    monkeypatch, signum, expected_kind, expected_status, host
):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_event):
            installed[signum](signum, None)
            assert stop_event.is_set()
            assert stop_event.kind == expected_kind
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    assert host(FakeServiceEngine()) == expected_status
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}


def test_foreground_failure_reports_worker_diagnostic(tmp_path, monkeypatch, capsys):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )

    assert daemon.run_service(engine, once=True) == 1
    output = capsys.readouterr().out
    assert "worker failed: worker exited with status 1" in output
    assert "run failed with status 1" not in output


def test_foreground_repeated_blocker_is_reported_until_changed(
    tmp_path, monkeypatch, capsys
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    calls = 0

    def run_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowBlockedError("execution recovery is blocked")
        if calls == 2:
            raise WorkflowBlockedError("execution recovery is blocked")
        if calls == 3:
            raise WorkflowBlockedError("execution recovery changed")
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_once", run_once)
    engine.poll_interval = "0"
    assert engine.serve(stop_event) == 0
    output = capsys.readouterr().out
    assert output.count("workflow blocked: execution recovery is blocked") == 1
    assert output.count("workflow blocked: execution recovery changed") == 1


@pytest.mark.parametrize(
    "signals", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)]
)
@pytest.mark.parametrize("host", [daemon.run_service])
def test_daemon_host_operator_abort_precedes_service_shutdown(
    monkeypatch, signals, host
):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_intent):
            for signum in signals:
                installed[signum](signum, None)
            assert stop_intent.kind == "operator_abort"
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    assert host(FakeServiceEngine()) == 130


def test_foreground_cli_remains_attached_until_host_returns(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result = []

    class FakeServiceEngine:
        def __init__(self, _env_file, *, read_only=False):
            assert not read_only

    def host(_engine, **_kwargs):
        entered.set()
        assert release.wait(10)
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_service", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "foreground", "--env", str(config)],
    )
    monkeypatch.chdir(working)
    thread = threading.Thread(target=lambda: result.append(cli.main()), daemon=True)
    thread.start()
    assert entered.wait(10)
    assert thread.is_alive()
    release.set()
    thread.join(10)

    assert result == [0]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_daemon_signal_wakes_poll_wait_without_second_iteration(
    tmp_path, monkeypatch, signum
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    stop_event = threading.Event()
    calls = []
    result = []

    def iteration():
        calls.append(True)
        entered.set()
        return 0

    monkeypatch.setattr(engine, "run_once", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)
    stop_event.set()
    thread.join(10)

    assert not thread.is_alive()
    assert result == [0]
    assert len(calls) == 1


def test_daemon_stop_already_requested_does_not_admit_iteration(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    stop_event.set()
    calls = []
    monkeypatch.setattr(engine, "run_once", lambda: calls.append(True))
    monkeypatch.setattr(
        "devlegate.runtime._git",
        lambda *_args, **_kwargs: pytest.fail("unexpected observation"),
    )

    assert engine.serve(stop_event) == 0
    assert calls == []


def test_stop_during_observation_prevents_fresh_mutation(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_sync = engine._sync_control
    calls = []

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: calls.append(True))

    assert engine.serve(stop_event) == 0
    assert calls == []
    assert engine._state["phase"] == "idle"


def test_stop_before_merge_commit_does_not_fast_forward(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    before = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    stop_event = threading.Event()
    original_sync = engine._sync_control

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    assert engine.serve(stop_event) == 0

    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == before
    assert engine._state["phase"] == "idle"
    assert engine._state.get("remote_head") != target


def test_committed_merge_pending_drains_before_stop(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "merge_pending":
            stop_event.set()

    monkeypatch.setattr(engine, "_save_state", save_state)
    assert engine.serve(stop_event) == 0

    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    assert engine._state["phase"] == "idle"


def test_persisted_merge_pending_drains_even_when_stop_already_set(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=target,
        changed_paths="remote-change.txt\n",
        control_head=control,
    )
    stop_event = threading.Event()
    stop_event.set()

    assert engine.serve(stop_event) == 0
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    assert engine._state["phase"] == "idle"


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
        ("service_shutdown", -signal.SIGTERM),
    ],
)
def test_merge_pending_retries_matching_shutdown_fetch(
    tmp_path, monkeypatch, capsys, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=target,
        changed_paths="remote-change.txt\n",
        control_head=control,
    )
    original_git = runtime._git
    fetch_calls = 0

    def interrupt_first_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            if fetch_calls == 1:
                return subprocess.CompletedProcess(
                    ["git"], returncode, "", ""
                )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_first_fetch)

    def operation(stop_intent):
        stop_intent.request(kind)
        return engine.serve(stop_intent)

    expected_result = 130 if kind == "operator_abort" else 0
    assert daemon.run_foreground(engine, operation) == expected_result
    assert fetch_calls == 3
    assert engine._state["phase"] == "idle"
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    output = capsys.readouterr().out
    assert "workflow blocked" not in output
    assert "execution failed" not in output
    assert "unknown git error" not in output


@pytest.mark.parametrize(
    "stage",
    ["checkpointing", "post-checkpoint", "publishing", "post-publication", "lifecycle"],
)
def test_service_survives_recoverable_stage_devlegate_error(
    tmp_path, monkeypatch, capsys, stage
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine.poll_interval = "1"
    first_iteration = threading.Event()
    allow_failure = threading.Event()
    calls = []
    stop_event = threading.Event()

    def iteration():
        calls.append(True)
        if len(calls) == 1:
            engine._state["phase"] = "agent_running"
            engine._state["execution_stage"] = stage
            first_iteration.set()
            allow_failure.wait(2)
            raise DevlegateError("simulated publication transport failure")
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "_run_once", iteration)
    thread = threading.Thread(
        target=lambda: engine.serve(stop_event), daemon=True
    )
    thread.start()
    assert first_iteration.wait(2)
    allow_failure.set()
    engine.wake()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert calls == [True, True]
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == stage
    assert "simulated publication transport failure" in capsys.readouterr().out


def test_recoverable_stage_error_keeps_once_semantics(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine._state["phase"] = "agent_running"
    engine._state["execution_stage"] = "publishing"
    monkeypatch.setattr(
        engine,
        "_run_once",
        lambda: (_ for _ in ()).throw(DevlegateError("one-shot failure")),
    )

    with pytest.raises(DevlegateError, match="one-shot failure"):
        daemon.run_service(engine, once=True)


def test_unrelated_devlegate_error_still_escapes_service(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine,
        "_run_once",
        lambda: (_ for _ in ()).throw(DevlegateError("fatal invariant")),
    )

    with pytest.raises(DevlegateError, match="fatal invariant"):
        engine.serve(threading.Event())


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
        ("service_shutdown", -signal.SIGTERM),
    ],
)
def test_merge_pending_retries_matching_product_merge(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=target,
        changed_paths="remote-change.txt\n",
        control_head=control,
    )
    original_git = runtime._git
    merge_calls = 0

    def interrupt_product_merge(repo, *args, check=True):
        nonlocal merge_calls
        if repo == engine.repo and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_product_merge)

    def operation(stop_intent):
        stop_intent.request(kind)
        return engine.serve(stop_intent)

    expected_result = 130 if kind == "operator_abort" else 0
    assert daemon.run_foreground(engine, operation) == expected_result
    assert merge_calls == 2
    assert engine._state["phase"] == "idle"
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target


def test_merge_pending_retries_product_merge_then_preserves_real_failure(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=target,
        changed_paths="remote-change.txt\n",
        control_head=control,
    )
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")
    original_git = runtime._git
    merge_calls = 0

    def fail_product_retry(repo, *args, check=True):
        nonlocal merge_calls
        if repo == engine.repo and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(
                    ["git"], -signal.SIGINT, "", ""
                )
            return subprocess.CompletedProcess(
                ["git"], 128, "", "fatal: merge failed"
            )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", fail_product_retry)

    with engine._stop_context(stop_intent):
        assert engine._run_once() == 1
    assert merge_calls == 2
    assert engine._state["phase"] == "merge_pending"


def test_merge_pending_retries_matching_control_merge(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    remote_url = git(
        engine.control_worktree, "remote", "get-url", "origin"
    ).stdout.strip()
    publisher = tmp_path / "control-seed"
    git(tmp_path, "clone", remote_url, publisher)
    git(publisher, "switch", "--track", "origin/devlegate/control")
    git(publisher, "config", "user.name", "Devlegate Test")
    git(publisher, "config", "user.email", "devlegate@example.test")
    (publisher / "control-change.txt").write_text("control\n")
    git(publisher, "add", "control-change.txt")
    git(publisher, "commit", "-m", "control change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:refs/heads/devlegate/control")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    original_git = runtime._git
    merge_calls = 0

    def interrupt_control_merge(repo, *args, check=True):
        nonlocal merge_calls
        if (
            repo == engine.control_worktree
            and args[:2] == ("merge", "--ff-only")
        ):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(
                    ["git"], -signal.SIGINT, "", ""
                )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_control_merge)
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")

    assert engine.serve(stop_intent) == 0
    assert merge_calls == 2
    assert engine._state["phase"] == "idle"
    assert git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip() == target


def test_merge_pending_repeated_matching_git_interrupt_is_bounded(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")
    original_git = runtime._git
    fetch_calls = 0

    def interrupt_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_fetch)

    with engine._stop_context(stop_intent):
        with pytest.raises(ShutdownInterrupted):
            engine._git_runtime(
                engine.control_worktree,
                "fetch",
                "--prune",
                engine.remote_name,
                engine.control_branch,
            )
    assert fetch_calls == 2


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", 128),
        ("operator_abort", -signal.SIGTERM),
    ],
)
def test_merge_pending_nonmatching_fetch_failure_is_not_retried(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    original_git = runtime._git
    fetch_calls = 0

    def fail_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", fail_fetch)

    with engine._stop_context(stop_intent):
        with pytest.raises(WorkflowBlockedError):
            engine._run_once()
    assert fetch_calls == 1


def test_existing_agent_pending_is_preserved_on_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    workspace, _control = persist_agent_running(engine, state)
    engine._save_state("agent_pending")
    before = dict(engine._state)
    calls = []
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: calls.append(True))
    stop_event = threading.Event()
    stop_event.set()

    assert engine.serve(stop_event) == 0
    assert engine._state == before
    assert calls == []
    assert workspace.path.is_dir()


def test_stop_after_agent_pending_commit_preserves_binding(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "agent_pending":
            stop_event.set()

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: pytest.fail("worker"))
    assert engine.serve(stop_event) == 0
    assert engine._state["phase"] == "agent_pending"
    assert engine._state["execution_id"]


def test_stop_after_workspace_preparation_preserves_pending_execution(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_prepare = engine._prepare_execution_workspace
    prepared = []

    def prepare(plan):
        workspace = original_prepare(plan)
        prepared.append(workspace)
        stop_event.set()
        return workspace

    monkeypatch.setattr(engine, "_prepare_execution_workspace", prepare)
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: pytest.fail("worker"))
    assert engine.serve(stop_event) == 0
    assert prepared and prepared[0].path.is_dir()
    assert engine._state["phase"] == "agent_pending"


def test_stop_after_agent_running_commit_still_drains_attempt(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state
    worker_calls = []

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if (
            phase == "agent_running"
            and fields.get("execution_stage") == "worker-launch"
        ):
            stop_event.set()

    def worker(_workspace, _prompt):
        worker_calls.append(True)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0
    assert worker_calls == [True]
    assert engine._state["phase"] == "idle"


def test_precheckpoint_integrity_failure_with_stop_keeps_retry_semantics(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt):
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule changed")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        engine.run_once(stop_event)
    assert engine._state["phase"] == "idle"
    assert "interrupted" not in engine._state["failed_executions"]["T-1"]


def test_later_stage_failure_with_stop_remains_ambiguous(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt):
        stop_event.set()
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    monkeypatch.setattr(
        engine,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        engine.run_once(stop_event)
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == "publishing"
    assert engine._state.get("failed_executions", {}) == {}


@pytest.mark.parametrize("drain_stage", ["post-checkpoint", "publishing", "lifecycle"])
def test_stop_at_committed_execution_stage_drains_to_idle(
    tmp_path, monkeypatch, drain_stage
):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "agent_running" and fields.get("execution_stage") == drain_stage:
            stop_event.set()

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0

    control = next((state / "worktrees").glob("*/control"))
    assert engine._state["phase"] == "idle"
    assert (control / "kanban/review/T-1.md").is_file()
    assert engine.service_snapshot().worker_running is False


def test_stop_before_accepted_integration_leaves_ticket_accepted(
    tmp_path, monkeypatch
):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    todo = control / "kanban/todo/T-1.md"
    todo.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    original_sync = engine._sync_control

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    assert engine.serve(stop_event) == 0
    assert (control / "kanban/accepted/T-1.md").is_file()
    assert not (engine.repo / "implementation.txt").exists()


def test_started_accepted_integration_drains_before_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.run_once() == 0
    review = control / "kanban/review/T-1.md"
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    original_integrate = engine._integrate_accepted

    def integrate(*args):
        result = original_integrate(*args)
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_integrate_accepted", integrate)
    assert engine.serve(stop_event) == 0
    assert (control / "kanban/done/T-1.md").is_file()
    assert engine._state["phase"] == "idle"


def test_stop_during_worker_prevents_next_ticket_admission(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    git(control, "add", "kanban/todo/T-2.md")
    git(control, "commit", "-m", "add second ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    calls = []

    def worker(workspace, _prompt):
        calls.append(workspace.ticket_id)
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0
    assert calls == ["T-1"]
    assert (control / "kanban/todo/T-2.md").is_file()


def test_daemon_holds_project_lock_while_service_is_active(tmp_path, monkeypatch):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()
    result = []

    def iteration():
        entered.set()
        assert release.wait(10)
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_once", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)

    second = ServiceEngine(config)
    before = dict(second._state)
    with pytest.raises(DevlegateError, match="another devlegate instance"):
        daemon.run_service(second, once=True)
    assert second.status().phase == "idle"
    assert second.plan().action == "run-worker"
    assert second._state == before

    release.set()
    thread.join(10)
    assert result == [0]
    assert state.exists()


def test_daemon_stop_during_worker_finishes_attempt_without_next_ticket(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    worker_entered = threading.Event()
    worker_release = threading.Event()
    stop_event = threading.Event()
    worker_calls = []
    result = []

    def worker(_workspace, _prompt):
        worker_calls.append(True)
        worker_entered.set()
        assert worker_release.wait(10)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert worker_entered.wait(10)
    stop_event.set()

    assert engine.service_snapshot().worker_running is True
    assert thread.is_alive()
    assert worker_calls == [True]

    worker_release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert result == [0]
    assert worker_calls == [True]
    assert engine.service_snapshot().worker_running is False
    assert engine._state["phase"] == "idle"


@pytest.mark.parametrize("kind", ["operator_abort", "service_shutdown"])
def test_controlled_worker_interruption_is_persisted_and_preserves_workspace(
    tmp_path, monkeypatch, kind, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    workspace_path = []

    def worker(workspace, _prompt):
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        stop_intent.request(kind)
        return WorkerRunResult(-2, None, None, None, kind)

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_intent) == 0
    assert engine._state["phase"] == "idle"
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    metadata = engine._state["failed_executions"]["T-1"]
    assert metadata["interrupted"] is True
    assert metadata["interruption_kind"] == kind
    assert metadata["reason"] == "interrupted: " + kind.replace("_", " ")
    output = capsys.readouterr().out
    assert "execution interrupted: " + kind.replace("_", " ") in output
    assert "execution failed;" not in output

    fresh = ServiceEngine(config)
    failure = fresh.status_view().failed_executions[0]
    assert failure.interruption_kind == kind
    calls = []
    monkeypatch.setattr(
        fresh,
        "_run_worker",
        lambda _workspace, _prompt: (
            calls.append(True),
            WorkerRunResult(1, None, None, None),
        )[1],
    )
    assert fresh.run_once() == (0 if kind == "operator_abort" else 1)
    assert calls == ([] if kind == "operator_abort" else [True])


@pytest.mark.parametrize("once", [False, True])
def test_once_operator_abort_stops_after_one_iteration(
    tmp_path, monkeypatch, once
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    calls = []
    workspace_path = []

    def worker(workspace, _prompt):
        calls.append(True)
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        return WorkerRunResult(-2, None, None, None, "operator_abort")

    monkeypatch.setattr(engine, "_run_worker", worker)
    # Compatibility seam only; production CLI uses run_service/serve.
    assert engine.run(once=once) == 130
    assert calls == [True]
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    assert engine._state["phase"] == "idle"
    assert engine._state["failed_executions"]["T-1"]["interruption_kind"] == (
        "operator_abort"
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_explicit_retry_sigterm_owns_worker_process_group(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    state = Path("/tmp") / f"c-f2-{tmp_path.name}"
    config.write_text(config.read_text().replace(str(tmp_path / "state"), str(state)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    marker = tmp_path / "retry-processes.json"
    attempt = tmp_path / "attempted"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        f"attempt = pathlib.Path({str(attempt)!r})\n"
        "if not attempt.exists():\n"
        "    attempt.touch()\n"
        "    raise SystemExit(1)\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_MARKER']).write_text(\n"
        "    json.dumps({'worker': os.getpid(), 'child': child.pid})\n"
        ")\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    assert ServiceEngine(config).run_once() == 1
    environment = {
        **os.environ,
        "DEVLEGATE_TEST_MARKER": str(marker),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    daemon_process = subprocess.Popen(
        [sys.executable, "-m", "devlegate", "foreground", "--env", str(config)],
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    socket_path = ServiceEngine(config).ipc_socket_path
    for _ in range(500):
        if socket_path.exists():
            break
        time.sleep(0.01)
    process = subprocess.Popen(
        [sys.executable, "-m", "devlegate", "retry", "T-1", "--env", str(config)],
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(500):
            if marker.exists():
                break
            time.sleep(0.01)
        if not marker.exists():
            process.send_signal(signal.SIGTERM)
            stdout, stderr = process.communicate(timeout=10)
            daemon_status = daemon_process.poll()
            if daemon_process.poll() is None:
                daemon_process.kill()
            try:
                daemon_stdout, daemon_stderr = daemon_process.communicate(timeout=2)
            except subprocess.TimeoutExpired:
                daemon_stdout, daemon_stderr = "", "daemon cleanup timed out"
            pytest.fail(
                f"retry worker did not start: {stdout}\n{stderr}\n"
                f"daemon status: {daemon_status}\n"
                f"daemon output: {daemon_stdout}\n{daemon_stderr}"
            )
    finally:
        if process.poll() is None:
            stdout, stderr = process.communicate(timeout=10)
        else:
            stdout, stderr = process.communicate(timeout=10)
        if daemon_process.poll() is None:
            daemon_process.send_signal(signal.SIGTERM)
        daemon_stdout, daemon_stderr = daemon_process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    processes = json.loads(marker.read_text())
    for process_id in (processes["worker"], processes["child"]):
        for _ in range(100):
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"process {process_id} survived retry SIGTERM")
    fresh = ServiceEngine(config)
    failure = fresh.status_view().failed_executions[0]
    assert failure.interruption_kind == "service_shutdown"
    assert failure.retryable


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_sigkill_parent_and_retry_refuses_duplicate_worker(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    state = Path("/tmp") / f"c-f2-{tmp_path.name}"
    config.write_text(config.read_text().replace(str(tmp_path / "state"), str(state)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    marker = tmp_path / "retry-processes.jsonl"
    attempt = tmp_path / "attempted"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        f"attempt = pathlib.Path({str(attempt)!r})\n"
        "if not attempt.exists():\n"
        "    attempt.touch()\n"
        "    raise SystemExit(1)\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "with pathlib.Path(os.environ['DEVLEGATE_TEST_MARKER']).open('a') as file:\n"
        "    file.write(json.dumps({'worker': os.getpid(), "
        "'child': child.pid}) + '\\n')\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    assert ServiceEngine(config).run_once() == 1
    environment = {
        **os.environ,
        "DEVLEGATE_TEST_MARKER": str(marker),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    daemon_process = subprocess.Popen(
        [sys.executable, "-m", "devlegate", "foreground", "--env", str(config)],
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    socket_path = ServiceEngine(config).ipc_socket_path
    for _ in range(500):
        if socket_path.exists():
            break
        time.sleep(0.01)
    first = subprocess.Popen(
        [sys.executable, "-m", "devlegate", "retry", "T-1", "--env", str(config)],
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(500):
            if marker.exists():
                break
            time.sleep(0.01)
        assert marker.exists()
        first.communicate(timeout=10)
        processes = [json.loads(line) for line in marker.read_text().splitlines()]
        assert len(processes) == 1
        assert os.kill(processes[0]["worker"], 0) is None

        second = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "devlegate",
                "retry",
                "T-1",
                "--env",
                str(config),
            ],
            cwd=working,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = second.communicate(timeout=10)
        assert second.returncode == 1, (stdout, stderr)
        assert "service runtime command already pending or running" in stderr
        assert len(marker.read_text().splitlines()) == 1
    finally:
        if first.poll() is None:
            first.communicate(timeout=10)
        if daemon_process.poll() is None:
            daemon_process.kill()
            daemon_process.wait(timeout=10)
        if marker.exists():
            processes = [json.loads(line) for line in marker.read_text().splitlines()]
            if processes:
                try:
                    os.killpg(processes[0]["worker"], signal.SIGKILL)
                except ProcessLookupError:
                    pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_leader_exit_with_live_descendant_blocks_post_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    marker = tmp_path / "natural-worker.json"
    worker = tmp_path / "natural-worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_MARKER']).write_text("
        "json.dumps({'worker': os.getpid(), 'child': child.pid}))\n"
        "sys.stdout.close(); sys.stderr.close(); os._exit(0)\n"
    )
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    worker.chmod(0o755)
    monkeypatch.chdir(working)
    monkeypatch.setenv("DEVLEGATE_TEST_MARKER", str(marker))
    engine = ServiceEngine(config)
    original_worker = engine._run_worker

    def run_worker(workspace, prompt):
        result = original_worker(workspace, prompt)
        assert result.worker_group_retired is False
        return result

    monkeypatch.setattr(engine, "_run_worker", run_worker)
    try:
        with pytest.raises(
            DevlegateError, match="process group is still alive"
        ):
            engine.run_once()
        assert marker.exists()
        processes = json.loads(marker.read_text())
        assert os.kill(processes["child"], 0) is None
        assert engine._state["phase"] == "agent_running"
        assert engine._state["execution_stage"] == "worker-running"
        assert engine._state["worker_identity"]["pid"] == processes["worker"]
        control = next((state / "worktrees").glob("*/control"))
        assert not list((control / "executions").glob("**/*.json"))
        restarted = ServiceEngine(config)
        monkeypatch.setattr(
            restarted,
            "_run_worker",
            lambda *_args: pytest.fail("restart reconciliation launched worker"),
        )
        with pytest.raises(DevlegateError, match="ownership is indeterminate"):
            restarted.run_once()
        assert os.kill(processes["child"], 0) is None
        os.killpg(processes["worker"], signal.SIGKILL)
        for _ in range(100):
            try:
                os.kill(processes["child"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        launches = []
        monkeypatch.setattr(
            restarted,
            "_run_worker",
            lambda workspace, prompt: (
                launches.append((workspace.path, prompt)),
                WorkerRunResult(1, None, None, None),
            )[1],
        )
        assert restarted.run_once() == 1
        assert len(launches) == 1
        assert "Continue the existing implementation" in launches[0][1]
        assert restarted._state["phase"] == "idle"
    finally:
        try:
            os.killpg(processes["worker"], signal.SIGKILL)
        except (NameError, ProcessLookupError):
            pass


def test_daemon_has_no_unix_daemonization_path():
    source = open(daemon.__file__, encoding="ascii").read()
    assert "os.fork" not in source
    assert "setsid" not in source
