# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import inspect
import json
import os
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from git_support import control_publisher
from runtime_helpers import run_test_iteration
from test_control_plane import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli
import devlegate.daemon as daemon
import devlegate.runtime as runtime
from devlegate.cli import DevlegateError
from devlegate.execution_result import build_execution_report
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.runtime import (
    IterationIntent,
    OperatorCommand,
    ServiceEngine,
    ShutdownInterrupted,
    WorkflowBlockedError,
)
from devlegate.service_diagnostics import read as read_service_failure
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), config, state


def test_explicit_retry_intent_cannot_leak_to_next_iteration(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    intents = []
    monkeypatch.setattr(
        engine,
        "_retry_candidates",
        lambda: (("T-1", "ticket", "retry"),),
    )

    def iteration(intent=None):
        intents.append(intent)
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)

    assert engine._retry_locked("T-1") == 0
    assert engine.run_iteration() == 0
    assert intents == [
        IterationIntent(retry_ticket_id="T-1"),
        None,
    ]


def test_lifecycle_drain_rejects_submitted_command_before_owner_admission(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    submitted = []

    def submit():
        try:
            submitted.append(
                engine.submit_retry("T-1", request_id="retry-before-drain")
            )
        except DevlegateError as error:
            submitted.append(error)

    thread = threading.Thread(
        target=submit
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not engine._operator_command_pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine._operator_command_pending()
    engine.request_lifecycle("stop", "stop-request")
    command, scheduler = engine._claim_owner_work(True)
    assert scheduler is False
    assert isinstance(command, OperatorCommand)
    with pytest.raises(DevlegateError, match="draining"):
        engine._admit_operator_command(command)
    engine._release_operator_command()
    thread.join(5)
    assert not thread.is_alive()
    assert len(submitted) == 1
    assert isinstance(submitted[0], DevlegateError)


def test_drop_rejects_lifecycle_drain_before_owner_admission(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine.request_lifecycle("stop", "stop-request")
    with pytest.raises(DevlegateError, match="service is draining"):
        engine.submit_drop("T-1", "execution-1", request_id="drop-request")


def test_drop_rejects_live_or_ambiguous_execution_state(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine._state.update(
        {
            "phase": "agent_running",
            "execution_ticket_id": "T-1",
            "execution_id": "execution-1",
            "execution_stage": "lifecycle",
            "worker_identity": {"execution_id": "execution-1"},
        }
    )
    with pytest.raises(DevlegateError, match="worker ownership"):
        engine._validate_drop_admission("T-1", "execution-1")
    engine._state["worker_identity"] = None
    engine._state["execution_stage"] = "post-checkpoint"
    with pytest.raises(DevlegateError, match="limited to blocked lifecycle"):
        engine._validate_drop_admission("T-1", "execution-1")


@pytest.mark.parametrize(
    "fault_stage",
    ["after-evidence", "after-disposition", "after-remote", "after-worktree"],
)
def test_restart_after_drop_disposition_finishes_drop_without_lifecycle(
    tmp_path, monkeypatch, fault_stage
):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    workspace, control = persist_agent_running(
        engine,
        state,
        execution_id="drop-replay",
        checkpointed=True,
        record_start=True,
        publish=True,
    )
    git(
        workspace.path,
        "commit",
        "--amend",
        "-m",
        "Devlegate checkpoint T-1 drop-replay",
    )
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "--force",
        "origin",
        f"HEAD:refs/heads/{workspace.branch}",
    )
    workspace = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    ).prepare(workspace.base_head)
    report = build_execution_report(
        execution_id="drop-replay",
        ticket_id="T-1",
        code_base_head=workspace.base_head,
        control_head=engine._state["execution_control_head"],
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        workspace_head=workspace.head,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        ),
    )
    engine._save_state(
        "agent_running",
        execution_stage="lifecycle",
        worker_identity=None,
        execution_start_head=workspace.base_head,
        execution_remote_head=checkpoint,
        pending_execution_report=report.as_dict(),
    )
    original_retire = engine._retire_drop_workspace
    original_pin = engine._pin_drop_evidence

    def crash_after_disposition(*args):
        raise RuntimeError("simulated crash after disposition")

    def crash_after_evidence(report):
        git(
            engine.repo,
            "update-ref",
            engine._drop_evidence_ref(report.ticket_id, report.execution_id),
            report.workspace_head,
        )
        raise RuntimeError("simulated crash after evidence")

    def crash_after_remote(report, workspace):
        git(
            engine.repo,
            "push",
            f"--force-with-lease=refs/heads/{report.execution_branch}:{report.workspace_head}",
            "origin",
            f":refs/heads/{report.execution_branch}",
        )
        if fault_stage == "after-worktree":
            ExecutionWorkspaceManager(
                engine.repo, engine.execution_worktree_root, report.ticket_id
            ).retire(workspace, report.workspace_head)
        raise RuntimeError(f"simulated crash {fault_stage}")

    if fault_stage == "after-evidence":
        monkeypatch.setattr(engine, "_pin_drop_evidence", crash_after_evidence)
        expected_error = "after evidence"
    elif fault_stage == "after-disposition":
        monkeypatch.setattr(engine, "_retire_drop_workspace", crash_after_disposition)
        expected_error = "after disposition"
    else:
        monkeypatch.setattr(engine, "_retire_drop_workspace", crash_after_remote)
        expected_error = f"after-{fault_stage.removeprefix('after-')}"
    with pytest.raises(RuntimeError, match=expected_error):
        engine._drop_owned("T-1", "drop-replay")
    evidence_ref = engine._drop_evidence_ref("T-1", "drop-replay")
    disposition_ref = engine._drop_disposition_ref("T-1", "drop-replay")
    assert git(engine.repo, "rev-parse", "--verify", evidence_ref).stdout.strip() == (
        workspace.head
    )
    assert git(engine.repo, "cat-file", "-t", disposition_ref).stdout.strip() == "blob"
    disposition = json.loads(
        git(engine.repo, "cat-file", "-p", disposition_ref).stdout
    )
    assert disposition["disposition"] == (
        "dropping" if fault_stage == "after-evidence" else "dropped"
    )
    assert engine._state["phase"] == "agent_running"

    monkeypatch.setattr(engine, "_retire_drop_workspace", original_retire)
    monkeypatch.setattr(engine, "_pin_drop_evidence", original_pin)
    restarted = ServiceEngine(config)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["phase"] == "idle"
    assert "execution_id" not in restarted._state
    assert not workspace.path.exists()
    assert not list((control / "executions").glob("**/*.json"))


def test_iteration_body_does_not_reenter_scheduler():
    source = inspect.getsource(ServiceEngine._run_iteration_body)
    assert "run_iteration(" not in source


def _prepare_drop_owner_command(tmp_path, monkeypatch):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    workspace, control = persist_agent_running(
        engine,
        state,
        execution_id="drop-final-boundary",
        checkpointed=True,
        record_start=True,
        publish=True,
    )
    git(
        workspace.path,
        "commit",
        "--amend",
        "-m",
        "Devlegate checkpoint T-1 drop-final-boundary",
    )
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "--force",
        "origin",
        f"HEAD:refs/heads/{workspace.branch}",
    )
    workspace = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    ).prepare(workspace.base_head)
    report = build_execution_report(
        execution_id="drop-final-boundary",
        ticket_id="T-1",
        code_base_head=workspace.base_head,
        control_head=engine._state["execution_control_head"],
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        workspace_head=workspace.head,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        ),
    )
    engine._save_state(
        "agent_running",
        execution_stage="lifecycle",
        worker_identity=None,
        execution_start_head=workspace.base_head,
        execution_remote_head=checkpoint,
        pending_execution_report=report.as_dict(),
    )
    command = OperatorCommand(
        request_id="drop-final-boundary-request",
        method="drop",
        fingerprint=runtime._drop_request_fingerprint(
            "T-1", "drop-final-boundary"
        ),
        ticket_id="T-1",
        execution_id="drop-final-boundary",
    )
    return engine, config, control, workspace, command


@pytest.mark.parametrize("boundary", ["after-clear", "before-receipt"])
def test_drop_final_durable_boundaries_replay_without_active_state(
    tmp_path, monkeypatch, boundary
):
    engine, config, control, workspace, command = _prepare_drop_owner_command(
        tmp_path, monkeypatch
    )
    original_save = engine._save_state

    def faulted_save(phase, **fields):
        if boundary == "before-receipt" and "mutable_receipts" in fields:
            raise RuntimeError("simulated crash before receipt")
        original_save(phase, **fields)
        if boundary == "after-clear" and fields.get("clear_execution"):
            raise RuntimeError("simulated crash after clear")

    monkeypatch.setattr(engine, "_save_state", faulted_save)
    with pytest.raises(RuntimeError, match=boundary.replace("-", " ")):
        engine._admit_operator_command(command)

    disposition_ref = engine._drop_disposition_ref("T-1", "drop-final-boundary")
    disposition = json.loads(
        git(engine.repo, "cat-file", "-p", disposition_ref).stdout
    )
    assert disposition["disposition"] == "dropped"
    assert engine._state["phase"] == "idle"
    assert "execution_id" not in engine._state
    assert "drop-final-boundary-request" not in engine._state.get(
        "mutable_receipts", {}
    )

    restarted = ServiceEngine(config)
    assert restarted._state["phase"] == "idle"
    assert "execution_id" not in restarted._state
    assert not workspace.path.exists()
    assert not list((control / "executions").glob("**/*.json"))

    applied = []
    monkeypatch.setattr(
        restarted,
        "_apply_execution_lifecycle",
        lambda report: applied.append(report.execution_id),
    )
    replay = OperatorCommand(
        request_id="drop-final-boundary-request",
        method="drop",
        fingerprint=runtime._drop_request_fingerprint(
            "T-1", "drop-final-boundary"
        ),
        ticket_id="T-1",
        execution_id="drop-final-boundary",
    )
    restarted._admit_operator_command(replay)
    assert applied == []
    assert replay.admission_result == {
        "accepted": True,
        "ticket_id": "T-1",
        "execution_id": "drop-final-boundary",
    }
    assert restarted._state["mutable_receipts"][
        "drop-final-boundary-request"
    ]["accepted"] is True


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
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

    engine.run_iteration = run_once

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
        (signal.SIGTERM, None, 0),
    ],
)
def test_daemon_host_signal_handler_splits_abort_from_graceful_lifecycle(
    monkeypatch, signum, expected_kind, expected_status
):
    installed = {}
    lifecycle_calls = []

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_event, *, lock_handle=None, once=False):
            installed[signum](signum, None)
            if expected_kind is None:
                assert not stop_event.is_set()
                assert lifecycle_calls == ["stop"]
            else:
                assert stop_event.is_set()
                assert stop_event.kind == expected_kind
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    intent = daemon.ShutdownIntent()
    host = daemon.ServiceHost(FakeServiceEngine())
    with host._signal_ownership(
        intent, lambda lifecycle, _request_id: lifecycle_calls.append(lifecycle) or {}
    ):
        result = host._serve_engine(intent)
    assert result == expected_status
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}


def test_foreground_failure_reports_worker_diagnostic(tmp_path, monkeypatch, capsys):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )

    assert daemon.run_service(engine, once=True) == 1
    output = capsys.readouterr().out
    assert "worker failed: worker exited with status 1" in output
    assert "run failed with status 1" not in output
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None


def test_unhandled_host_failure_is_reported_by_offline_status(
    tmp_path, monkeypatch, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    failure = DevlegateError("unexpected owner failure")

    def fail_iteration(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(engine, "run_iteration", fail_iteration)
    with pytest.raises(DevlegateError, match="unexpected owner failure"):
        host.run()

    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is not None
    assert diagnostic.stage == "runtime"
    assert diagnostic.exception_type == "DevlegateError"
    assert diagnostic.message == "unexpected owner failure"

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "status", "--json", "--env", str(config)],
    )
    assert cli.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"]["state"] == "stopped"
    assert payload["service"]["failure"]["message"] == "unexpected owner failure"
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "status", "--env", str(config)],
    )
    assert cli.main() == 1
    human = capsys.readouterr().out
    assert "Service failure:" in human
    assert "Detail: unexpected owner failure" in human


def test_unhandled_startup_failure_is_persisted_as_startup_diagnostic(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        engine,
        "ensure_hosted_views",
        lambda: (_ for _ in ()).throw(DevlegateError("startup views failed")),
    )
    with pytest.raises(DevlegateError, match="startup views failed"):
        host.run()
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is not None
    assert diagnostic.stage == "startup"
    assert diagnostic.message == "startup views failed"


def test_successful_ready_clears_terminal_service_failure(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda stop_intent, **_kwargs: (
            stop_intent.request("operator_abort", source="test") or 0
        ),
    )
    from devlegate.service_diagnostics import create, write

    write(engine._locator, create("runtime", DevlegateError("old failure")))
    assert host.run() == 0
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None


def test_operator_abort_does_not_create_terminal_service_failure(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda stop_intent, **_kwargs: (
            stop_intent.request("operator_abort", source="test") or 130
        ),
    )
    assert host.run() == 130
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None


def test_diagnostic_write_failure_does_not_replace_original_failure(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DevlegateError("original failure")
        ),
    )
    monkeypatch.setattr(
        daemon,
        "write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(DevlegateError, match="original failure"):
        host.run()


def test_corrupt_service_diagnostic_is_reported_without_blocking_status(
    tmp_path, monkeypatch, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    diagnostic_path = engine._locator.state_dir / "diagnostics"
    diagnostic_path.mkdir(parents=True)
    (diagnostic_path / f"{engine._locator.state_key}.json").write_text("not json")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "status", "--json", "--env", str(config)],
    )
    assert cli.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"]["failure"] == {
        "state": "unavailable",
        "message": "service diagnostic unavailable/corrupt",
    }


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

    monkeypatch.setattr(engine, "run_iteration", run_once)
    engine.poll_interval = "0"
    assert engine.serve(stop_event) == 0
    output = capsys.readouterr().out
    assert output.count("workflow blocked: execution recovery is blocked") == 1
    assert output.count("workflow blocked: execution recovery changed") == 1


@pytest.mark.parametrize(
    "signals", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)]
)
def test_daemon_host_operator_abort_precedes_graceful_lifecycle(monkeypatch, signals):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_intent, *, lock_handle=None, once=False):
            for signum in signals:
                installed[signum](signum, None)
            assert stop_intent.kind == "operator_abort"
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    intent = daemon.ShutdownIntent()
    host = daemon.ServiceHost(FakeServiceEngine())
    with host._signal_ownership(intent, lambda _intent, _request_id: {}):
        result = host._serve_engine(intent)
    assert result == 130


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

    monkeypatch.setattr(engine, "run_iteration", iteration)
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


def test_daemon_stop_already_requested_does_not_admit_iteration(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    stop_event.set()
    calls = []
    monkeypatch.setattr(
        engine, "run_iteration", lambda _intent=None: calls.append(True)
    )
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
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    assert engine.serve(stop_event) == 0
    assert calls == []
    assert engine._state["phase"] == "idle"


def test_stop_before_merge_commit_does_not_fast_forward(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
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
    publisher = control_publisher(tmp_path)
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
    publisher = control_publisher(tmp_path)
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
    ],
)
def test_merge_pending_retries_matching_shutdown_fetch(
    tmp_path, monkeypatch, capsys, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
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
                return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_first_fetch)

    expected_result = 130 if kind == "operator_abort" else 0
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    host = daemon.ServiceHost(engine)
    with host._signal_ownership(stop_intent, lambda _intent, _request_id: {}):
        result = host._serve_engine(stop_intent)
    assert result == expected_result
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

    monkeypatch.setattr(engine, "run_iteration", iteration)
    thread = threading.Thread(target=lambda: engine.serve(stop_event), daemon=True)
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
        "run_iteration",
        lambda: (_ for _ in ()).throw(DevlegateError("one-shot failure")),
    )

    with pytest.raises(DevlegateError, match="one-shot failure"):
        daemon.run_service(engine, once=True)


def test_unrelated_devlegate_error_still_escapes_service(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine,
        "run_iteration",
        lambda: (_ for _ in ()).throw(DevlegateError("fatal invariant")),
    )

    with pytest.raises(DevlegateError, match="fatal invariant"):
        engine.serve(threading.Event())


@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
    ],
)
def test_merge_pending_retries_matching_product_merge(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
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

    expected_result = 130 if kind == "operator_abort" else 0
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    host = daemon.ServiceHost(engine)
    with host._signal_ownership(stop_intent, lambda _intent, _request_id: {}):
        result = host._serve_engine(stop_intent)
    assert result == expected_result
    assert merge_calls == 2
    assert engine._state["phase"] == "idle"
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target


def test_merge_pending_retries_product_merge_then_preserves_real_failure(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
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
                return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
            return subprocess.CompletedProcess(["git"], 128, "", "fatal: merge failed")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", fail_product_retry)

    with engine._stop_context(stop_intent):
        assert engine.run_iteration() == 1
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
        if repo == engine.control_worktree and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
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
            engine.run_iteration()
    assert fetch_calls == 1


def test_existing_agent_pending_is_preserved_on_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    workspace, _control = persist_agent_running(engine, state)
    engine._save_state("agent_pending")
    before = dict(engine._state)
    calls = []
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )
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
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker")
    )
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
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker")
    )
    assert engine.serve(stop_event) == 0
    assert prepared and prepared[0].path.is_dir()
    assert engine._state["phase"] == "agent_pending"


def test_stop_after_agent_running_commit_still_drains_attempt(tmp_path, monkeypatch):
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

    def worker(_workspace, _prompt, **_kwargs):
        worker_calls.append(True)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_event) == 0
    assert worker_calls == [True]
    assert engine._state["phase"] == "idle"


def test_precheckpoint_integrity_failure_with_stop_keeps_retry_semantics(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt, **_kwargs):
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule changed")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        run_test_iteration(engine, stop_event)
    assert engine._state["phase"] == "idle"
    assert "interrupted" not in engine._state["failed_executions"]["T-1"]


def test_later_stage_failure_with_stop_remains_ambiguous(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt, **_kwargs):
        stop_event.set()
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        engine,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        run_test_iteration(engine, stop_event)
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == "publishing"
    assert engine._state.get("failed_executions", {}) == {}


def test_checkpoint_failure_during_lifecycle_drain_has_no_completion_boundary(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)

    def worker(_workspace, _prompt, **_kwargs):
        engine.request_lifecycle("stop", "stop-request")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "checkpoint",
        lambda *_args: (_ for _ in ()).throw(
            ExecutionWorkspaceError("checkpoint unavailable")
        ),
    )
    with pytest.raises(DevlegateError, match="execution checkpoint failed"):
        run_test_iteration(engine)
    assert engine.lifecycle_intent() == "stop"
    assert engine._state["execution_stage"] == "checkpointing"


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

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_event) == 0

    control = next((state / "worktrees").glob("*/control"))
    assert engine._state["phase"] == "idle"
    assert (control / "kanban/review/T-1.md").is_file()
    assert engine.service_snapshot().worker_running is False


def test_stop_before_accepted_integration_leaves_ticket_accepted(tmp_path, monkeypatch):
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

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
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

    def worker(workspace, _prompt, **_kwargs):
        calls.append(workspace.ticket_id)
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
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

    monkeypatch.setattr(engine, "run_iteration", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)

    second = ServiceEngine(config)
    before = dict(second._state)
    with pytest.raises(DevlegateError, match="another devlegate instance"):
        daemon.run_service(second, once=True)
    assert second.status_view().phase == "idle"
    assert second.plan_view().action == "run-worker"
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

    def worker(_workspace, _prompt, **_kwargs):
        worker_calls.append(True)
        worker_entered.set()
        assert worker_release.wait(10)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
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


@pytest.mark.parametrize("kind", ["operator_abort"])
def test_controlled_worker_interruption_is_persisted_and_preserves_workspace(
    tmp_path, monkeypatch, kind, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    workspace_path = []

    def worker(workspace, _prompt, **_kwargs):
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        stop_intent.request(kind)
        return WorkerRunResult(-2, None, None, None, kind)

    monkeypatch.setattr(engine._workers, "run", worker)
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
        fresh._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: (
            calls.append(True),
            WorkerRunResult(1, None, None, None),
        )[1],
    )
    assert run_test_iteration(fresh) == (0 if kind == "operator_abort" else 1)
    assert calls == ([] if kind == "operator_abort" else [True])


@pytest.mark.parametrize("once", [False, True])
def test_once_operator_abort_stops_after_one_iteration(tmp_path, monkeypatch, once):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    calls = []
    workspace_path = []

    def worker(workspace, _prompt, **_kwargs):
        calls.append(True)
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        signal.raise_signal(signal.SIGINT)
        return WorkerRunResult(-2, None, None, None, "operator_abort")

    monkeypatch.setattr(engine._workers, "run", worker)
    assert daemon.run_service(engine, once=once) == 130
    assert calls == [True]
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    assert engine._state["phase"] == "idle"
    assert engine._state["failed_executions"]["T-1"]["interruption_kind"] == (
        "operator_abort"
    )


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_sigkill_parent_and_retry_refuses_duplicate_worker(
    tmp_path, monkeypatch, short_state_dir
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(
        config.read_text().replace(str(tmp_path / "state"), str(short_state_dir))
    )
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
    assert run_test_iteration(ServiceEngine(config)) == 1
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
        assert "service busy; mutable request was not admitted" in stderr
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
    original_worker = engine._workers.run

    def run_worker(workspace, prompt, **_kwargs):
        result = original_worker(workspace, prompt, **_kwargs)
        assert result.worker_group_retired is False
        return result

    monkeypatch.setattr(engine._workers, "run", run_worker)
    try:
        with pytest.raises(DevlegateError, match="process group is still alive"):
            run_test_iteration(engine)
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
            restarted._workers,
            "run",
            lambda *_args, **_kwargs: pytest.fail(
                "restart reconciliation launched worker"
            ),
        )
        with pytest.raises(DevlegateError, match="ownership is indeterminate"):
            run_test_iteration(restarted)
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
            restarted._workers,
            "run",
            lambda workspace, prompt, **_kwargs: (
                launches.append((workspace.path, prompt)),
                WorkerRunResult(1, None, None, None),
            )[1],
        )
        assert run_test_iteration(restarted) == 1
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
