# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Unix socket transport, framing, endpoint ownership, and cleanup."""

from _ipc_support import *  # noqa: F403,F405

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

    assert ping.ok
    assert ping.result["service"] == "devlegate"
    assert ping.result["version"] == ipc_server.__version__
    assert ping.result["protocol_version"] == 1
    assert ping.result["pid"] == os.getpid()
    assert isinstance(ping.result["instance_id"], str)
    assert ping.result["lifecycle"]["phase"] == "running"
    assert status.ok
    assert status.result == {
        **engine.status_view().as_dict(),
        "service": ping.result,
    }
    assert plan.ok and plan.result == engine.plan_view().as_dict()

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

def test_stop_drains_admitted_response_before_closing_socket(
    running_server, monkeypatch
):
    _engine, _state, server = running_server
    original_send = ipc_server.send_frame
    response_started = threading.Event()
    release_response = threading.Event()

    def blocked_send(stream, payload):
        response_started.set()
        assert release_response.wait(timeout=5)
        return original_send(stream, payload)

    monkeypatch.setattr(ipc_server, "send_frame", blocked_send)
    client_result = []
    client = threading.Thread(
        target=lambda: client_result.append(request(server.path, "admitted", "ping"))
    )
    stopper_done = threading.Event()
    stopper = threading.Thread(
        target=lambda: (server.stop(), stopper_done.set())
    )
    try:
        client.start()
        assert response_started.wait(timeout=5)
        stopper.start()
        assert not stopper_done.wait(timeout=0.1)
        release_response.set()
        client.join(timeout=5)
        stopper.join(timeout=5)
    finally:
        release_response.set()
        if client.is_alive():
            client.join(timeout=5)
        if stopper.is_alive():
            stopper.join(timeout=5)

    assert not client.is_alive()
    assert not stopper.is_alive()
    assert client_result and client_result[0].ok

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
            state.handler for state in server._active_connections.values()
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
            config.read_text().replace(str(config.parent / "state"), str(shared_state))
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

def test_excessively_long_socket_path_fails_as_devlegate_error(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    long_state = Path("/") / ("state-" + "x" * 120)
    server = UnixIPCServer(
        engine, long_state / "sockets" / f"{engine.ipc_socket_path.stem}.sock"
    )

    with pytest.raises(DevlegateError, match="IPC socket path is too long"):
        server.start()

def test_fallback_socket_directory_is_removed_after_owned_socket_stop(
    tmp_path, monkeypatch
):
    long_state = tmp_path / ("state-" + "x" * 80)
    engine, _state = make_engine(tmp_path, monkeypatch, long_state)
    fallback_directory = engine.ipc_socket_path.parent
    assert fallback_directory.parent == Path("/tmp")
    server = UnixIPCServer(engine, engine.ipc_socket_path)

    server.start()
    try:
        assert engine.ipc_socket_path.is_socket()
        assert fallback_directory.is_dir()
    finally:
        server.stop()

    assert not engine.ipc_socket_path.exists()
    assert not fallback_directory.exists()

def test_fallback_socket_directory_keeps_unrelated_entry(tmp_path, monkeypatch):
    long_state = tmp_path / ("state-" + "y" * 80)
    engine, _state = make_engine(tmp_path, monkeypatch, long_state)
    fallback_directory = engine.ipc_socket_path.parent
    unrelated = fallback_directory / "unrelated-entry"
    fallback_directory.mkdir(parents=True, exist_ok=True)
    unrelated.write_text("keep\n")
    server = UnixIPCServer(engine, engine.ipc_socket_path)

    try:
        server.start()
        assert engine.ipc_socket_path.is_socket()
    finally:
        server.stop()

    try:
        assert not engine.ipc_socket_path.exists()
        assert unrelated.read_text() == "keep\n"
        assert fallback_directory.is_dir()
    finally:
        unrelated.unlink(missing_ok=True)
        fallback_directory.rmdir()

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
