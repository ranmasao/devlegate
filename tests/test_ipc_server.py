import shutil
import socket
import tempfile
import time
from pathlib import Path

import pytest
from test_control_plane import control_fixture, invoke

import devlegate.daemon as daemon
from devlegate.ipc_protocol import (
    encode_request,
    parse_response,
    receive_frame,
    send_frame,
)
from devlegate.ipc_server import (
    UNIX_SOCKET_PATH_MAX_BYTES,
    UnixIPCServer,
    dispatch_read_only,
)
from devlegate.runtime import DevlegateError
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


def _request(method):
    from devlegate.ipc_protocol import IPCRequest

    return IPCRequest(1, "id", method, {})
