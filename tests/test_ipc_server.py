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
from pathlib import Path

import pytest
from test_control_plane import control_fixture, invoke, persist_agent_running

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
from devlegate.runtime import DevlegateError, RetryCandidate
from devlegate.service import ServiceEngine


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
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        yield engine, state, server
    finally:
        server.stop()


def test_daemon_owns_socket_under_state_dir_and_removes_it(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    observed = []

    def host(_engine, _operation):
        assert engine.ipc_socket_path.is_socket()
        observed.append(True)
        return 0

    monkeypatch.setattr(daemon, "run_foreground", host)
    assert daemon.run_daemon(engine) == 0
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


def test_same_project_second_daemon_cannot_disturb_first_socket(
    tmp_path, monkeypatch, short_state_dir
):
    engine_a, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    authority = engine_a._lock()
    server_a = UnixIPCServer(engine_a, engine_a.ipc_socket_path)
    server_a.start()
    try:
        engine_b = ServiceEngine(engine_a.env_file)
        with pytest.raises(DevlegateError, match="already running"):
            daemon.run_daemon(engine_b)
        assert engine_a.ipc_socket_path.is_socket()
        assert request(engine_a.ipc_socket_path, "a", "ping").ok
    finally:
        server_a.stop()
        authority.close()


def test_stale_socket_is_replaced_after_runtime_authority_is_acquired(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ipc_socket_path.parent.mkdir(parents=True, exist_ok=True)
    stale = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    stale.bind(str(engine.ipc_socket_path))
    stale.close()

    def host(_engine, _operation):
        assert request(engine.ipc_socket_path, "1", "ping").ok
        return 0

    monkeypatch.setattr(daemon, "run_foreground", host)
    assert daemon.run_daemon(engine) == 0
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
        def status_view(self):
            return View()

        def plan_view(self):
            return View()

    assert dispatch_read_only(FakeEngine(), _request("status")) == {"view": True}
    assert dispatch_read_only(FakeEngine(), _request("plan")) == {"view": True}


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

    class View:
        def as_dict(self):
            return {"phase": "worker"}

    monkeypatch.setattr(engine, "status_view", lambda: View())

    def execute(_ticket_id, _stop_event=None):
        started.set()
        release.wait(2)
        return 0

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
        retry_response = request(
            engine.ipc_socket_path,
            "retry",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert retry_response.ok
        assert started.wait(2)
        status_response = request(engine.ipc_socket_path, "status", "status")
        assert status_response.ok
        assert status_response.result == {"phase": "worker"}
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=2)
        server.stop()
        authority.close()
    assert not owner.is_alive()


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

    monkeypatch.setattr(engine, "_run_once", lambda: 0)
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
        assert "already pending or running" in different_id.error["message"]
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
        result = original_retry_owned(ticket_id, stop_event)
        candidate_response.append(ticket_id)
        recovered.set()
        return result

    monkeypatch.setattr(engine, "_retry_owned", retry_owned)
    monkeypatch.setattr(engine, "_run_once", lambda: 0)
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

    with pytest.raises(DevlegateError, match="worker is already running"):
        engine.submit_retry("T-1", request_id="live-worker")
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

    with pytest.raises(DevlegateError, match="does not match"):
        engine.submit_retry("T-2", request_id="wrong-ticket")
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

    with pytest.raises(DevlegateError, match="not currently retryable"):
        engine.submit_retry("T-1", request_id="worker-launch")
    assert not engine._operator_command_pending()


def _request(method, payload=None):
    from devlegate.ipc_protocol import IPCRequest

    return IPCRequest(1, "id", method, payload or {})
