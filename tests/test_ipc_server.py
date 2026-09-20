# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import os
import shutil
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace

import pytest
from runtime_helpers import run_test_iteration
from test_control_plane import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli
import devlegate.daemon as daemon
import devlegate.ipc_server as ipc_server
from devlegate.ipc_protocol import (
    MAX_PAYLOAD_BYTES,
    IPCProtocolError,
    encode_frame,
    encode_request,
    parse_response,
    receive_frame,
    send_frame,
)
from devlegate.ipc_server import (
    UNIX_SOCKET_PATH_MAX_BYTES,
    UnixIPCServer,
    dispatch_mutation,
    dispatch_read_only,
)
from devlegate.runtime import (
    DevlegateError,
    OperatorCommand,
    RetryCandidate,
    _reconcile_resume_request_fingerprint,
)
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def make_engine(tmp_path, monkeypatch, state_override=None):
    working, config, state = control_fixture(tmp_path)
    if state_override is not None:
        config.write_text(
            config.read_text().replace(str(state), str(state_override))
        )
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), state


@pytest.fixture
def short_state_dir():
    path = Path(tempfile.mkdtemp(prefix="c-state-", dir="/tmp"))
    try:
        yield path
    finally:
        shutil.rmtree(path, ignore_errors=True)


def request(socket_path, request_id, method, payload=None):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    connection.connect(str(socket_path))
    stream = connection.makefile("rwb")
    send_frame(stream, encode_request(request_id, method, payload or {}))
    response = parse_response(receive_frame(stream))
    stream.close()
    connection.close()
    return response


@pytest.fixture
def running_server(tmp_path, monkeypatch, short_state_dir):
    engine, state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        yield engine, state, server
    finally:
        server.stop()
        engine.end_hosted_owner()


def test_daemon_owns_socket_under_state_dir_and_removes_it(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    observed = []

    def host(_host, _stop_intent, **_kwargs):
        assert engine.ipc_socket_path.is_socket()
        observed.append(True)
        return 0

    monkeypatch.setattr(daemon.ServiceHost, "_serve_engine", host)
    assert daemon.run_service(engine) == 0
    assert observed == [True]
    assert not engine.ipc_socket_path.exists()


def test_ping_status_and_plan_work_over_unix_socket(running_server):
    engine, _state, _server = running_server
    path = engine.ipc_socket_path

    ping = request(path, "1", "ping")
    status = request(path, "2", "status")
    plan = request(path, "3", "plan")

    assert ping.ok and ping.result == {"service": "devlegate", "protocol_version": 1}
    assert status.ok and status.result == engine.status_view().as_dict()
    assert plan.ok and plan.result == engine.plan_view().as_dict()


def test_unknown_method_returns_structured_error(running_server):
    _engine, _state, _server = running_server
    response = request(_server.path, "1", "unknown")

    assert not response.ok
    assert response.error == {
        "code": "unknown_method",
        "message": "unsupported method: unknown",
    }


def test_reconcile_resume_routes_over_live_unix_socket(running_server):
    engine, _state, server = running_server
    calls = []

    def submit(ticket_id, *, request_id):
        calls.append((ticket_id, request_id))
        return {"accepted": True, "ticket_id": ticket_id}

    engine.submit_reconcile_resume = submit
    response = request(
        server.path,
        "resume-id",
        "reconcile-resume",
        {"ticket_id": "LAB-111"},
    )

    assert response.ok
    assert response.result == {"accepted": True, "ticket_id": "LAB-111"}
    assert calls == [("LAB-111", "resume-id")]


@pytest.mark.parametrize("retained_report", [True, False])
def test_reconcile_resume_runs_full_live_owner_path(
    tmp_path, monkeypatch, short_state_dir, retained_report
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(_state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def initial_worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", initial_worker)
    assert run_test_iteration(engine) == 1
    reconciliation = dict(engine._state["reconciliation"])
    checkpoint = reconciliation["worker_checkpoint"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    (working / "dirty-product.txt").unlink()
    if not retained_report:
        reconciliation.pop("execution_report")
        engine._save_state("idle", reconciliation=reconciliation)

    resumed = threading.Event()
    original_resume = engine._reconcile_resume_owned

    def resume(ticket_id, **kwargs):
        result = original_resume(ticket_id, **kwargs)
        resumed.set()
        return result

    monkeypatch.setattr(engine, "_reconcile_resume_owned", resume)
    if retained_report:
        monkeypatch.setattr(
            engine,
            "_run_worker",
            lambda *_args: pytest.fail("retained resume launched a worker"),
        )
    else:
        def resumed_worker(workspace, prompt):
            assert "Continue the existing implementation" in prompt
            assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == checkpoint
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "validated", (), ()), None
            )

        monkeypatch.setattr(engine, "_run_worker", resumed_worker)

    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            server.path,
            "resume-live",
            "reconcile-resume",
            {"ticket_id": "T-1"},
        )
        assert response.ok
        assert response.result == {"accepted": True, "ticket_id": "T-1"}
        assert resumed.wait(5)
        assert owner.is_alive()
        assert engine._state["reconciliation"]["status"] == "resolved"
        assert engine._state["reconciliation"]["resolution"] == "resume"
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint
        assert (
            git(
                execution,
                "ls-remote",
                "origin",
                f"refs/heads/{reconciliation['execution_branch']}",
            ).stdout.split()[0]
            == checkpoint
        )
        assert (execution / "implementation.txt").read_text() == "worker\n"
        if retained_report:
            assert (
                next((short_state_dir / "worktrees").glob("*/control"))
                / "kanban/review/T-1.md"
            ).is_file()
        else:
            assert engine._state["resume_required"]["status"] == "required"
    finally:
        stop_event.set()
        engine.wake()
        owner.join(timeout=5)
        server.stop()
        authority.close()
    assert not owner.is_alive()


def test_unknown_owner_command_fails_closed(running_server):
    engine, _state, _server = running_server
    command = OperatorCommand("unknown", "unknown", "fingerprint", "T-1")

    with pytest.raises(DevlegateError, match="unsupported operator command: unknown"):
        engine._dispatch_operator_command(command, None)


def test_resolving_resume_recovers_on_fresh_owner_and_fences_plan(
    tmp_path, monkeypatch, short_state_dir
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(_state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert run_test_iteration(engine) == 1
    reconciliation = dict(engine._state["reconciliation"])
    checkpoint = reconciliation["worker_checkpoint"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    (working / "dirty-product.txt").unlink()
    command = OperatorCommand(
        "resume-crash",
        "reconcile-resume",
        _reconcile_resume_request_fingerprint("T-1"),
        "T-1",
    )
    engine._record_operator_admission(command)

    restarted = ServiceEngine(config)
    assert restarted._state["reconciliation"]["status"] == "resolving"
    assert restarted.plan_view().action == "blocked"
    assert "recovery in progress" in restarted.plan_view().reason
    monkeypatch.setattr(
        restarted,
        "_run_worker",
        lambda *_args: pytest.fail("ordinary worker launched before recovery"),
    )
    recovered = threading.Event()
    original_resume = restarted._reconcile_resume_owned

    def resume(ticket_id, **kwargs):
        result = original_resume(ticket_id, **kwargs)
        recovered.set()
        return result

    monkeypatch.setattr(restarted, "_reconcile_resume_owned", resume)
    authority = restarted._lock()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=restarted.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert recovered.wait(5)
        assert owner.is_alive()
        assert restarted._state["reconciliation"]["status"] == "resolved"
        assert restarted._state["reconciliation"]["resolution"] == "resume"
        assert restarted._state["mutable_receipts"]["resume-crash"]["accepted"]
        assert (
            git(
                execution,
                "ls-remote",
                "origin",
                f"refs/heads/{reconciliation['execution_branch']}",
            ).stdout.split()[0]
            == checkpoint
        )
    finally:
        stop_event.set()
        restarted.wake()
        owner.join(timeout=5)
        authority.close()
    assert not owner.is_alive()


def test_stop_dispatch_invokes_host_shutdown_callback(running_server):
    engine, _state, server = running_server
    called = []
    response = dispatch_mutation(
        engine, _request("stop"), shutdown=lambda: called.append(True)
    )
    assert response == {"accepted": True}
    assert called == [True]


def test_control_reconciliation_dispatch_preserves_both_identities(running_server):
    engine, _state, _server = running_server
    from_head = "a" * 40
    to_head = "b" * 40
    observed = []
    engine.submit_reconcile_control = lambda actual_from, actual_to, request_id: (
        observed.append((actual_from, actual_to, request_id))
        or {"accepted": True, "from": actual_from, "to": actual_to}
    )
    response = dispatch_mutation(
        engine,
        _request(
            "reconcile-control",
            {"from": from_head, "to": to_head},
        ),
    )
    assert response == {"accepted": True, "from": from_head, "to": to_head}
    assert observed == [(from_head, to_head, "id")]


def test_malformed_request_does_not_crash_server(running_server):
    _engine, _state, _server = running_server
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(_server.path))
    stream = connection.makefile("rwb")
    send_frame(stream, b"not-json")
    response = parse_response(receive_frame(stream))
    assert not response.ok
    assert response.error["code"] == "malformed_protocol"
    send_frame(stream, encode_request("2", "ping", {}))
    assert parse_response(receive_frame(stream)).ok
    stream.close()
    connection.close()


def test_client_disconnect_does_not_stop_server(running_server):
    _engine, _state, _server = running_server
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(_server.path))
    connection.close()
    time.sleep(0.05)

    response = request(_server.path, "2", "ping")
    assert response.ok


def test_idle_connection_does_not_block_unrelated_client(running_server):
    _engine, _state, server = running_server
    idle = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    idle.settimeout(2)
    idle.connect(str(server.path))
    try:
        assert request(server.path, "other", "ping").ok
    finally:
        idle.close()


def test_incomplete_frame_does_not_block_unrelated_client(running_server):
    _engine, _state, server = running_server
    partial = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    partial.settimeout(2)
    partial.connect(str(server.path))
    partial.sendall(b"\x00")
    try:
        assert request(server.path, "other", "status").ok
    finally:
        partial.close()


def test_many_read_only_clients_overlap_without_mutating_runtime(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    monkeypatch.setattr(
        engine, "status_view", lambda: pytest.fail("IPC read touched repository")
    )
    before = engine._state_file.read_bytes()
    before_product = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    before_control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    start = threading.Event()

    def call(method, request_id):
        assert start.wait(2)
        return request(engine.ipc_socket_path, request_id, method)

    try:
        with ThreadPoolExecutor(max_workers=5) as pool:
            futures = [
                pool.submit(call, "status", f"status-{index}")
                for index in range(3)
            ]
            futures.append(pool.submit(call, "plan", "plan-1"))
            futures.append(pool.submit(call, "ping", "ping-1"))
            start.set()
            responses = [future.result(timeout=5) for future in futures]
        assert all(response.ok for response in responses)
        assert engine._state_file.read_bytes() == before
        assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == before_product
        assert (
            git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
            == before_control
        )
    finally:
        server.stop()


def test_published_reads_do_not_observe_repository_during_owner_iteration(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    def run_iteration(_intent=None):
        started.set()
        assert release.wait(3)
        return 0

    def forbidden_repository_observation():
        raise DevlegateError("IPC read performed repository observation")

    monkeypatch.setattr(engine, "run_iteration", run_iteration)
    for method in ("status_view", "plan_view", "retry_candidates_view"):
        monkeypatch.setattr(
            engine,
            method,
            forbidden_repository_observation,
        )
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert started.wait(2)
        responses = [
            request(engine.ipc_socket_path, f"read-{method}", method)
            for method in ("status", "plan", "retry-candidates")
        ]
        assert all(response.ok for response in responses)
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()


def test_mutation_after_owner_commits_iteration_is_definitely_busy(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    def run_iteration(_intent=None):
        started.set()
        assert release.wait(3)
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_iteration)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert started.wait(2)
        response = request(
            engine.ipc_socket_path,
            "busy-retry",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not response.ok
        assert response.error["message"] == (
            "service busy; mutable request was not admitted"
        )
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()


def test_shutdown_closes_multiple_active_connection_handlers(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    clients = []
    try:
        for _index in range(3):
            client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            client.connect(str(server.path))
            client.sendall(b"\x00")
            clients.append(client)
        deadline = time.monotonic() + 2
        while time.monotonic() < deadline:
            if len(server._active_connections) == len(clients):
                break
            time.sleep(0.01)
        assert len(server._active_connections) == len(clients)
        handlers = tuple(
            handler for _connection, handler in server._active_connections.values()
        )
        server.stop()
        assert not server._active_connections
        assert all(not handler.is_alive() for handler in handlers)
    finally:
        for client in clients:
            client.close()
        server.stop()


def test_distinct_projects_share_state_dir_without_socket_collision(
    tmp_path, monkeypatch, short_state_dir
):
    shared_state = short_state_dir
    project_a = tmp_path / "project-a"
    project_b = tmp_path / "project-b"
    project_a.mkdir()
    project_b.mkdir()
    working_a, config_a, _state_a = control_fixture(project_a)
    working_b, config_b, _state_b = control_fixture(project_b)
    for config in (config_a, config_b):
        config.write_text(
            config.read_text().replace(
                str(config.parent / "state"), str(shared_state)
            )
        )
    assert invoke(working_a, "control", "init", config=config_a).returncode == 0
    assert invoke(working_b, "control", "init", config=config_b).returncode == 0

    monkeypatch.chdir(working_a)
    engine_a = ServiceEngine(config_a)
    monkeypatch.chdir(working_b)
    engine_b = ServiceEngine(config_b)
    assert engine_a.ipc_socket_path != engine_b.ipc_socket_path
    server_a = UnixIPCServer(engine_a, engine_a.ipc_socket_path)
    server_b = UnixIPCServer(engine_b, engine_b.ipc_socket_path)
    server_a.start()
    try:
        server_b.start()
        try:
            assert engine_a.ipc_socket_path.is_socket()
            assert engine_b.ipc_socket_path.is_socket()
            assert request(engine_a.ipc_socket_path, "a", "ping").ok
            assert request(engine_b.ipc_socket_path, "b", "ping").ok
        finally:
            server_b.stop()
        assert engine_a.ipc_socket_path.is_socket()
        assert not engine_b.ipc_socket_path.exists()
    finally:
        server_a.stop()
    assert not engine_a.ipc_socket_path.exists()


def test_socket_uses_compact_key_and_preserves_full_runtime_identity(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    socket_key = engine.ipc_socket_path.stem

    assert len(socket_key) == 32
    assert engine._state_key.startswith(socket_key)
    assert engine._runtime_store.path == (
        engine.state_dir / f"{engine._state_key}.sqlite3"
    )
    assert engine.control_worktree == (
        engine.state_dir / "worktrees" / engine._state_key / "control"
    )
    assert engine.execution_worktree_root == (
        engine.state_dir / "worktrees" / engine._state_key
    )


def test_default_like_linux_state_path_fits_socket_limit(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    representative = Path("/home/example-user/.local/state/devlegate")
    path = representative / "sockets" / f"{engine.ipc_socket_path.stem}.sock"

    assert len(str(path).encode()) <= UNIX_SOCKET_PATH_MAX_BYTES


def test_excessively_long_socket_path_fails_as_devlegate_error(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    long_state = Path("/") / ("state-" + "x" * 120)
    server = UnixIPCServer(
        engine, long_state / "sockets" / f"{engine.ipc_socket_path.stem}.sock"
    )

    with pytest.raises(DevlegateError, match="IPC socket path is too long"):
        server.start()


@pytest.mark.parametrize("host", [daemon.run_service])
def test_same_project_second_service_host_cannot_disturb_first_socket(
    tmp_path, monkeypatch, short_state_dir, host
):
    engine_a, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    authority = engine_a._lock()
    server_a = UnixIPCServer(engine_a, engine_a.ipc_socket_path)
    server_a.start()
    try:
        engine_b = ServiceEngine(engine_a.env_file)
        with pytest.raises(DevlegateError, match="already running"):
            host(engine_b)
        assert engine_a.ipc_socket_path.is_socket()
        assert request(engine_a.ipc_socket_path, "a", "ping").ok
    finally:
        server_a.stop()
        authority.close()


def test_service_host_once_owns_authority_and_socket_lifecycle(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    calls = []

    def iteration():
        assert request(engine.ipc_socket_path, "once", "ping").ok
        calls.append(True)
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)
    assert daemon.run_service(engine, once=True) == 0
    assert calls == [True]
    assert not engine.ipc_socket_path.exists()
    authority = engine._lock()
    authority.close()


def test_service_host_startup_failure_releases_authority_and_socket(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)

    def fail_startup():
        raise RuntimeError("startup report failed")

    with pytest.raises(RuntimeError, match="startup report failed"):
        daemon.run_service(engine, startup_report=fail_startup)

    assert not engine.ipc_socket_path.exists()
    authority = engine._lock()
    authority.close()


def test_stale_socket_is_replaced_after_runtime_authority_is_acquired(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ipc_socket_path.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(engine.ipc_socket_path))
    stale.close()

    def host(_host, _stop_intent, **_kwargs):
        assert request(engine.ipc_socket_path, "1", "ping").ok
        return 0

    monkeypatch.setattr(daemon.ServiceHost, "_serve_engine", host)
    assert daemon.run_service(engine) == 0
    assert not engine.ipc_socket_path.exists()


def test_active_endpoint_fails_closed_without_unlinking_it(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server_a = UnixIPCServer(engine, engine.ipc_socket_path)
    server_b = UnixIPCServer(engine, engine.ipc_socket_path)
    server_a.start()
    try:
        with pytest.raises(DevlegateError, match="endpoint is active"):
            server_b.start()
        assert request(engine.ipc_socket_path, "1", "ping").ok
    finally:
        server_b.stop()
        server_a.stop()


def test_socket_directory_and_endpoint_permissions(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    directory = engine.ipc_socket_path.parent
    directory.mkdir(parents=True)
    os.chmod(directory, 0o755)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        assert stat.S_IMODE(os.stat(directory).st_mode) == 0o700
        assert stat.S_IMODE(os.stat(engine.ipc_socket_path).st_mode) == 0o600
    finally:
        server.stop()


@pytest.mark.parametrize("object_kind", ["file", "symlink"])
def test_unexpected_socket_path_objects_fail_closed(
    tmp_path, monkeypatch, short_state_dir, object_kind
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    path = engine.ipc_socket_path
    path.parent.mkdir(parents=True)
    if object_kind == "file":
        path.write_text("not a socket")
    else:
        target = path.parent / "target"
        target.write_text("target")
        path.symlink_to(target)
    with pytest.raises(DevlegateError, match="not a socket"):
        UnixIPCServer(engine, path).start()


def test_foreign_socket_directory_owner_fails_closed(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    real_uid = os.geteuid()
    monkeypatch.setattr(ipc_server.os, "geteuid", lambda: real_uid + 1)

    with pytest.raises(DevlegateError, match="directory has unsafe ownership"):
        UnixIPCServer(engine, engine.ipc_socket_path).start()


def test_foreign_socket_owner_fails_closed(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    path = engine.ipc_socket_path
    path.parent.mkdir(parents=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(path))
    stale.close()
    real_uid = os.geteuid()
    calls = 0

    def fake_uid():
        nonlocal calls
        calls += 1
        return real_uid if calls == 1 else real_uid + 1

    monkeypatch.setattr(ipc_server.os, "geteuid", fake_uid)
    try:
        with pytest.raises(DevlegateError, match="path has unsafe ownership"):
            UnixIPCServer(engine, path).start()
    finally:
        path.unlink(missing_ok=True)


def test_cleanup_does_not_remove_replaced_endpoint(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        server.path.unlink()
        replacement.bind(str(server.path))
        server.stop()
        assert server.path.exists()
    finally:
        replacement.close()
        server.path.unlink(missing_ok=True)


def test_wrong_peer_credential_is_rejected_without_stopping_server(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    monkeypatch.setattr(
        "devlegate.ipc_server._peer_credentials_are_current_user", lambda _socket: False
    )
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    try:
        connection.connect(str(server.path))
        assert receive_frame(connection.makefile("rb")) is None
    finally:
        connection.close()
        server.stop()


def test_real_socket_framing_failures_leave_server_usable(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()

    def raw_response(data: bytes, shutdown_write: bool = False):
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2)
        connection.connect(str(server.path))
        connection.sendall(data)
        if shutdown_write:
            connection.shutdown(socket.SHUT_WR)
        stream = connection.makefile("rwb")
        response = parse_response(receive_frame(stream))
        stream.close()
        connection.close()
        return response

    try:
        invalid_utf8 = raw_response(encode_frame(b"\xff"))
        invalid_json = raw_response(encode_frame(b"{"))
        invalid_shape = raw_response(encode_frame(b"[]"))
        unsupported = raw_response(
            encode_frame(
                json.dumps(
                    {"version": 2, "id": "v", "method": "ping", "payload": {}}
                ).encode()
            )
        )
        oversized = raw_response(struct.pack(">I", MAX_PAYLOAD_BYTES + 1))
        truncated_header = raw_response(b"\x00", shutdown_write=True)
        truncated_payload = raw_response(
            struct.pack(">I", 3) + b"ab", shutdown_write=True
        )
        assert invalid_utf8.error["code"] == "malformed_protocol"
        assert invalid_json.error["code"] == "malformed_protocol"
        assert invalid_shape.error["code"] == "invalid_request"
        assert unsupported.error["code"] == "unsupported_version"
        assert oversized.error["code"] == "oversized_frame"
        assert truncated_header.error["code"] == "malformed_protocol"
        assert truncated_payload.error["code"] == "malformed_protocol"
        assert request(server.path, "ok", "ping").ok
    finally:
        server.stop()


def test_huge_integer_json_is_normalized_and_server_remains_usable(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        limit = sys.get_int_max_str_digits()
        digits = (limit + 1) if limit else 100_000
        payload = (
            b'{"version":1,"id":"huge","method":"ping","payload":{"value":'
            + b"9" * digits
            + b"}}"
        )
        assert len(payload) < MAX_PAYLOAD_BYTES
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2)
        connection.connect(str(server.path))
        stream = connection.makefile("rwb")
        send_frame(stream, payload)
        response = parse_response(receive_frame(stream))
        stream.close()
        connection.close()
        assert response.error["code"] == "malformed_protocol"
        assert request(server.path, "after-huge", "ping").ok
    finally:
        server.stop()


def test_deeply_nested_json_is_normalized_and_server_remains_usable(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        depth = max(sys.getrecursionlimit() * 10, 10_000)
        payload = b"[" * depth + b"]" * depth
        assert len(payload) < MAX_PAYLOAD_BYTES
        connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        connection.settimeout(2)
        connection.connect(str(server.path))
        stream = connection.makefile("rwb")
        send_frame(stream, payload)
        response = parse_response(receive_frame(stream))
        stream.close()
        connection.close()
        assert response.error["code"] == "malformed_protocol"
        assert request(server.path, "after-deep", "ping").ok
    finally:
        server.stop()


def test_shutdown_unblocks_partial_client(tmp_path, monkeypatch, short_state_dir):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(server.path))
    connection.sendall(b"\x00")
    started = time.monotonic()
    server.stop()
    elapsed = time.monotonic() - started
    connection.close()
    assert elapsed < 1
    assert not server.path.exists()


def test_dispatch_uses_service_views_without_persistence_access():
    class View:
        def as_dict(self):
            return {"view": True}

    class FakeEngine:
        def published_status_payload(self):
            return {"view": True}

        def published_plan_view(self):
            return View()

    assert dispatch_read_only(FakeEngine(), _request("status")) == {"view": True}
    assert dispatch_read_only(FakeEngine(), _request("plan")) == {"view": True}


def test_status_ipc_keeps_raw_worker_identity_private():
    class View:
        def as_dict(self):
            return {"view": True}

    class FakeEngine:
        def published_status_payload(self):
            return {
                "view": True,
                "live_execution": {
                    "ticket_id": "T-1",
                    "execution_id": "exec-1",
                    "stage": "worker-running",
                    "ownership": "current-service",
                    "identity_state": "matching-live",
                },
            }

    result = dispatch_read_only(FakeEngine(), _request("status"))

    assert result["live_execution"] == {
        "ticket_id": "T-1",
        "execution_id": "exec-1",
        "stage": "worker-running",
        "ownership": "current-service",
        "identity_state": "matching-live",
    }


def test_retry_submission_runs_on_service_owner_thread(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    submitted_thread = []
    executed_thread = []
    executed = threading.Event()
    stop_event = threading.Event()

    original_submit = engine.submit_retry

    def submit(ticket_id, *, request_id):
        submitted_thread.append(threading.get_ident())
        return original_submit(ticket_id, request_id=request_id)

    def execute(ticket_id, _stop_event=None):
        assert ticket_id == "T-1"
        executed_thread.append(threading.get_ident())
        executed.set()
        return 0

    monkeypatch.setattr(engine, "submit_retry", submit)
    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            engine.ipc_socket_path,
            "retry",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert response.ok
        assert response.result == {"accepted": True, "ticket_id": "T-1"}
        assert executed.wait(2)
        assert submitted_thread[0] != executed_thread[0]
        assert executed_thread[0] == owner.ident
    finally:
        stop_event.set()
        engine.wake()
        owner.join(timeout=2)
        server.stop()
        authority.close()
    assert not owner.is_alive()


def test_read_only_ipc_remains_available_while_owner_retry_is_active(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    engine.ensure_hosted_views()

    def execute(_ticket_id, _stop_event=None):
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    original_wake = engine.wake
    queued = threading.Event()

    def wake_after_queue():
        original_wake()
        queued.set()

    monkeypatch.setattr(engine, "wake", wake_after_queue)
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    retry_result = {}
    retry_client = threading.Thread(
        target=lambda: retry_result.setdefault(
            "response",
            request(
                engine.ipc_socket_path,
                "retry",
                "retry",
                {"ticket_id": "T-1"},
            ),
        )
    )
    retry_client.start()
    assert queued.wait(2)
    owner.start()
    try:
        retry_client.join(timeout=3)
        retry_response = retry_result["response"]
        assert retry_response.ok
        assert started.wait(2)
        status_response = request(engine.ipc_socket_path, "status", "status")
        assert status_response.ok
        assert status_response.result["execution"]["phase"] == "idle"
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=2)
        server.stop()
        authority.close()
    assert not owner.is_alive()


def test_shutdown_rejects_operator_command_waiting_for_owner_admission(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    admission_started = threading.Event()
    release_admission = threading.Event()
    stop_event = threading.Event()
    original_validate = engine._validate_operator_admission

    def validate(command):
        admission_started.set()
        assert release_admission.wait(3)
        original_validate(command)

    monkeypatch.setattr(engine, "_validate_operator_admission", validate)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    result = {}

    def submit():
        result["response"] = request(
            engine.ipc_socket_path,
            "shutdown-race",
            "retry",
            {"ticket_id": "T-1"},
        )

    client = threading.Thread(target=submit)
    client.start()
    try:
        assert admission_started.wait(2)
        stop_event.set()
        engine.wake()
        release_admission.set()
        owner.join(timeout=2)
        assert not owner.is_alive()
        release_admission.set()
        client.join(timeout=3)
        assert not client.is_alive()
        response = result["response"]
        assert response.ok
    finally:
        release_admission.set()
        stop_event.set()
        engine.wake()
        client.join(timeout=2)
        owner.join(timeout=2)
        server.stop()
        authority.close()


def test_retry_request_receipt_coalesces_duplicates_and_survives_restart(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    started = threading.Event()
    release = threading.Event()
    executions = []

    def execute(ticket_id, _stop_event=None):
        executions.append(ticket_id)
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(engine, "run_iteration", lambda _intent=None: 0)
    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        first = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert first.ok
        assert started.wait(2)
        duplicate = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert duplicate.ok
        assert duplicate.result == first.result
        different_id = request(
            engine.ipc_socket_path,
            "request-y",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not different_id.ok
        assert "service busy" in different_id.error["message"]
        collision = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-2"},
        )
        assert not collision.ok
        assert "request id collision" in collision.error["message"]
        assert executions == ["T-1"]
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()

    restarted = ServiceEngine(engine.env_file)
    replay = restarted.submit_retry("T-1", request_id="request-x")
    assert replay == {"accepted": True, "ticket_id": "T-1"}
    assert not restarted._operator_command_pending()


def test_reconcile_update_base_runs_on_owner_thread_and_resolves_pending_state(
    tmp_path, monkeypatch, short_state_dir
):
    working, config, state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker work\n")
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert run_test_iteration(engine) == 1
    reconciliation = engine._state["reconciliation"]
    target = reconciliation["observed_product"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    git(working, "pull", "--ff-only", "origin", "main")

    submitted_thread = []
    executed_thread = []
    reconciliation_started = threading.Event()
    reconciliation_release = threading.Event()
    executed = threading.Event()
    original_owned = engine._reconcile_update_base_owned

    def owned(ticket_id, onto, **kwargs):
        executed_thread.append(threading.get_ident())
        reconciliation_started.set()
        assert reconciliation_release.wait(3)
        result = original_owned(ticket_id, onto, **kwargs)
        executed.set()
        return result

    monkeypatch.setattr(engine, "_reconcile_update_base_owned", owned)
    original_submit = engine.submit_reconcile_update_base

    def submit(ticket_id, onto, *, request_id):
        submitted_thread.append(threading.get_ident())
        return original_submit(ticket_id, onto, request_id=request_id)

    monkeypatch.setattr(engine, "submit_reconcile_update_base", submit)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1", "onto": target},
        )
        assert response.ok
        assert response.result == {
            "accepted": True,
            "ticket_id": "T-1",
            "onto": target,
        }
        assert reconciliation_started.wait(2)
        busy = request(
            engine.ipc_socket_path,
            "retry-while-reconcile",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not busy.ok
        assert "service busy" in busy.error["message"]
        reconciliation_release.set()
        assert executed.wait(3)
        assert submitted_thread[0] != executed_thread[0]
        assert executed_thread[0] == owner.ident
        duplicate = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1", "onto": target},
        )
        assert duplicate.ok
        collision = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1", "onto": "different-target"},
        )
        assert not collision.ok
        assert "request id collision" in collision.error["message"]
        cross_method = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not cross_method.ok
        assert "request id collision" in cross_method.error["message"]
    finally:
        reconciliation_release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()

    assert engine._state["reconciliation"]["status"] == "resolved"
    assert engine._state["resume_required"]["status"] == "required"
    assert (execution / "implementation.txt").read_text() == "worker work\n"
    restarted = ServiceEngine(config)
    assert restarted.submit_reconcile_update_base(
        "T-1", target, request_id="reconcile-request"
    ) == {"accepted": True, "ticket_id": "T-1", "onto": target}


@pytest.mark.parametrize("interactive", [False, True])
def test_interrupted_retry_candidate_is_admitted_and_recovered_end_to_end(
    tmp_path, monkeypatch, short_state_dir, capsys, interactive
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    engine._save_state(
        "agent_running",
        execution_stage="pre-checkpoint",
        worker_identity=None,
    )
    candidate_response = []
    recovered = threading.Event()
    original_retry_owned = engine._retry_owned

    def retry_owned(ticket_id, stop_event=None):
        recovered.set()
        candidate_response.append(ticket_id)
        result = original_retry_owned(ticket_id, stop_event)
        return result

    monkeypatch.setattr(engine, "_retry_owned", retry_owned)
    monkeypatch.setattr(engine, "run_iteration", lambda _intent=None: 0)
    engine.ensure_hosted_views()
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        candidates = request(
            engine.ipc_socket_path,
            "candidates",
            "retry-candidates",
        )
        assert candidates.ok
        assert candidates.result["candidates"][0]["id"] == "T-1"
        assert candidates.result["candidates"][0]["kind"] == "interrupted"
        if interactive:
            monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
            monkeypatch.setattr("builtins.input", lambda _prompt: "1")
            argv = ["devlegate", "retry", "--env", str(engine.env_file)]
        else:
            argv = ["devlegate", "retry", "T-1", "--env", str(engine.env_file)]
        monkeypatch.setattr(sys, "argv", argv)
        assert cli.main() == 0
        assert "retry accepted: T-1" in capsys.readouterr().out
        assert recovered.wait(5)
        assert candidate_response == ["T-1"]
    finally:
        stop_event.set()
        engine.wake()
        if owner is not None:
            owner.join(timeout=3)
        server.stop()
        authority.close()
    assert owner is None or not owner.is_alive()


@pytest.mark.parametrize(
    "payload",
    [{}, {"ticket_id": ""}, {"ticket_id": None}, {"ticket_id": "T-1", "extra": 1}],
)
def test_retry_mutation_payload_is_strict(payload):
    class FakeEngine:
        def submit_retry(self, _ticket_id):
            pytest.fail("invalid retry payload was queued")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("retry", payload))


def test_matching_live_worker_rejects_retry_without_queueing(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    identity = engine._state["worker_identity"]
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=identity,
    )
    monkeypatch.setattr(
        "devlegate.runtime.observe_worker_identity", lambda _: "matching-live"
    )

    engine.begin_hosted_owner()
    try:
        with pytest.raises(DevlegateError, match="worker is already running"):
            engine._validate_operator_admission(
                OperatorCommand("live-worker", "retry", "", "T-1")
            )
    finally:
        engine.end_hosted_owner()
    assert not engine._operator_command_pending()


def test_wrong_ticket_is_rejected_for_recoverable_execution(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    engine._save_state(
        "agent_running",
        execution_stage="pre-checkpoint",
        worker_identity=None,
    )

    engine.begin_hosted_owner()
    try:
        with pytest.raises(DevlegateError, match="does not match"):
            engine._validate_operator_admission(
                OperatorCommand("wrong-ticket", "retry", "", "T-2")
            )
    finally:
        engine.end_hosted_owner()
    assert not engine._operator_command_pending()


def test_worker_launch_stage_is_rejected_before_durable_ack(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    engine._save_state(
        "agent_running",
        execution_stage="worker-launch",
        worker_identity=None,
    )

    engine.begin_hosted_owner()
    try:
        with pytest.raises(DevlegateError, match="not currently retryable"):
            engine._validate_operator_admission(
                OperatorCommand("worker-launch", "retry", "", "T-1")
            )
    finally:
        engine.end_hosted_owner()
    assert not engine._operator_command_pending()


@pytest.mark.parametrize(
    "payload",
    [
        {"ticket_id": "T-1"},
        {"onto": "B"},
        {"ticket_id": "T-1", "onto": "B", "extra": "nope"},
        {"ticket_id": "", "onto": "B"},
        {"ticket_id": "T-1", "onto": ""},
    ],
)
def test_reconcile_mutation_payload_is_strict(payload):
    class FakeEngine:
        def submit_reconcile_update_base(self, *_args, **_kwargs):
            pytest.fail("invalid reconciliation payload was queued")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("reconcile-update-base", payload))


def test_reconcile_resume_mutation_is_strict_and_owner_routed():
    calls = []

    class FakeEngine:
        def submit_reconcile_resume(self, ticket_id, *, request_id):
            calls.append((ticket_id, request_id))
            return {"accepted": True, "ticket_id": ticket_id}

    result = dispatch_mutation(
        FakeEngine(), _request("reconcile-resume", {"ticket_id": "T-1"})
    )
    assert result == {"accepted": True, "ticket_id": "T-1"}
    assert calls == [("T-1", "id")]
    with pytest.raises(IPCProtocolError):
        dispatch_mutation(
            FakeEngine(),
            _request("reconcile-resume", {"ticket_id": "T-1", "onto": "B"}),
        )


def test_reconcile_resume_admission_persists_receipt_with_resolving_state(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    engine._state["reconciliation"] = {
        "status": "pending",
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "original_base": "a" * 40,
        "observed_product": "b" * 40,
        "worker_checkpoint": "c" * 40,
        "product_remote_head": "a" * 40,
        "control_head": "d" * 40,
        "execution_branch": "devlegate/work/T-1",
        "execution_path": "/tmp/execution",
        "evidence_ref": "refs/devlegate/reconciliation/T-1/execution-1",
    }
    command = SimpleNamespace(
        request_id="resume-request",
        method="reconcile-resume",
        fingerprint=_reconcile_resume_request_fingerprint("T-1"),
        ticket_id="T-1",
        onto=None,
    )
    engine._record_operator_admission(command)
    restarted = ServiceEngine(engine.env_file)
    assert restarted._state["mutable_receipts"]["resume-request"]["accepted"] is True
    assert restarted._state["reconciliation"]["status"] == "resolving"
    assert restarted._state["reconciliation"]["resolution"] == "resume"
    assert restarted.submit_reconcile_resume(
        "T-1", request_id="resume-request"
    ) == {"accepted": True, "ticket_id": "T-1"}
    assert restarted._state["reconciliation"]["status"] == "resolving"


def _request(method, payload=None):
    from devlegate.ipc_protocol import IPCRequest

    return IPCRequest(1, "id", method, payload or {})
