import socket
import time

import pytest
from test_control_plane import control_fixture, invoke

import devlegate.daemon as daemon
from devlegate.ipc_protocol import (
    encode_request,
    parse_response,
    receive_frame,
    send_frame,
)
from devlegate.ipc_server import SOCKET_FILENAME, UnixIPCServer, dispatch_read_only
from devlegate.service import ServiceEngine


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), state


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
def running_server(tmp_path, monkeypatch):
    engine, state = make_engine(tmp_path, monkeypatch)
    server = UnixIPCServer(engine, state)
    server.start()
    try:
        yield engine, state, server
    finally:
        server.stop()


def test_daemon_owns_socket_under_state_dir_and_removes_it(tmp_path, monkeypatch):
    engine, state = make_engine(tmp_path, monkeypatch)
    observed = []

    def host(_engine, _operation):
        assert (state / SOCKET_FILENAME).is_socket()
        observed.append(True)
        return 0

    monkeypatch.setattr(daemon, "run_foreground", host)
    assert daemon.run_daemon(engine) == 0
    assert observed == [True]
    assert not (state / SOCKET_FILENAME).exists()


def test_ping_status_and_plan_work_over_unix_socket(running_server):
    engine, state, _server = running_server
    path = state / SOCKET_FILENAME

    ping = request(path, "1", "ping")
    status = request(path, "2", "status")
    plan = request(path, "3", "plan")

    assert ping.ok and ping.result == {"service": "devlegate", "protocol_version": 1}
    assert status.ok and status.result == engine.status_view().as_dict()
    assert plan.ok and plan.result == engine.plan_view().as_dict()


def test_unknown_method_returns_structured_error(running_server):
    _engine, state, _server = running_server
    response = request(state / SOCKET_FILENAME, "1", "unknown")

    assert not response.ok
    assert response.error == {
        "code": "unknown_method",
        "message": "unsupported method: unknown",
    }


def test_malformed_request_does_not_crash_server(running_server):
    _engine, state, _server = running_server
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(state / SOCKET_FILENAME))
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
    _engine, state, _server = running_server
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(state / SOCKET_FILENAME))
    connection.close()
    time.sleep(0.05)

    response = request(state / SOCKET_FILENAME, "2", "ping")
    assert response.ok


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
