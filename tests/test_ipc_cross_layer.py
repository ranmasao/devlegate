# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""IPC recovery/topology bridge tests not owned by dispatch, transport, or handoff."""

from _ipc_support import *  # noqa: F403,F405

def test_short_state_directory_context_cleans_external_resource():
    with owned_short_state_dir() as path:
        assert path.is_dir()
    assert not path.exists()

def test_ping_reports_loaded_service_module_version(monkeypatch):
    monkeypatch.setattr(ipc_server, "__version__", "0.5.2.dev0-loaded")

    ping = dispatch_read_only(object(), _request("ping"))

    assert ping["version"] == "0.5.2.dev0-loaded"

def test_lifecycle_callback_type_error_is_not_retried(running_server):
    engine, _state, _server = running_server
    calls = []

    def lifecycle(_intent, _request_id, _force):
        calls.append(True)
        raise TypeError("callback failure")

    with pytest.raises(TypeError, match="callback failure"):
        dispatch_mutation(engine, _request("stop"), lifecycle=lifecycle)
    assert calls == [True]

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
                pool.submit(call, "status", f"status-{index}") for index in range(3)
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

def test_foreign_socket_directory_owner_fails_closed(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    real_uid = os.geteuid()
    monkeypatch.setattr(ipc_server.os, "geteuid", lambda: real_uid + 1)

    with pytest.raises(DevlegateError, match="directory has unsafe ownership"):
        UnixIPCServer(engine, engine.ipc_socket_path).start()

def test_foreign_socket_owner_fails_closed(tmp_path, monkeypatch, short_state_dir):
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
