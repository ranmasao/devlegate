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
from devlegate.cli import Devlegate, DevlegateError
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


def test_daemon_command_constructs_one_service_engine(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

    def host(engine):
        calls.append(("host", engine))
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
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
def test_daemon_host_signal_handler_only_sets_stop_intent(
    monkeypatch, signum, expected_kind, expected_status
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

    assert daemon.run_daemon(FakeServiceEngine()) == expected_status
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}


def test_foreground_failure_reports_worker_diagnostic(tmp_path, monkeypatch, capsys):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )

    assert engine.run(once=True) == 1
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
    assert output.count("workflow blocked: execution recovery is blocked") == 2
    assert output.count("workflow blocked: execution recovery changed") == 1
@pytest.mark.parametrize(
    "signals", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)]
)
def test_daemon_host_operator_abort_precedes_service_shutdown(monkeypatch, signals):
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

    assert daemon.run_daemon(FakeServiceEngine()) == 130


def test_daemon_cli_remains_foreground_until_host_returns(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result = []

    class FakeServiceEngine:
        def __init__(self, _env_file, *, read_only=False):
            assert not read_only

    def host(_engine):
        entered.set()
        assert release.wait(10)
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
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
        second.run(once=True)
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
def test_foreground_operator_abort_stops_after_one_iteration(
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
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    marker = tmp_path / "retry-processes.json"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
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
    environment = {
        **os.environ,
        "DEVLEGATE_TEST_MARKER": str(marker),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
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
            pytest.fail(f"retry worker did not start: {stdout}\n{stderr}")
    finally:
        process.send_signal(signal.SIGTERM)
        stdout, stderr = process.communicate(timeout=10)
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
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    marker = tmp_path / "retry-processes.jsonl"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
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
    environment = {
        **os.environ,
        "DEVLEGATE_TEST_MARKER": str(marker),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
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
        first.kill()
        first.wait(timeout=10)
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
        assert "worker is still alive" in stderr
        assert len(marker.read_text().splitlines()) == 1
    finally:
        if first.poll() is None:
            first.kill()
            first.wait(timeout=10)
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
