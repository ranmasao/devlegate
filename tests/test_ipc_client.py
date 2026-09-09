import socket
import threading

import pytest

from devlegate.ipc_client import IPCClientError, request
from devlegate.ipc_protocol import (
    encode_error_response,
    encode_success_response,
    parse_request,
    receive_frame,
    send_frame,
)


def serve_once(path, response):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        stream = connection.makefile("rwb")
        try:
            request_message = parse_request(receive_frame(stream))
            send_frame(stream, response(request_message))
        finally:
            stream.close()
            connection.close()
            listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    return thread


def test_request_returns_structured_success(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(message):
        return encode_success_response(message.request_id, {"value": "ok"})

    thread = serve_once(path, response)
    assert request(path, "ping") == {"value": "ok"}
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_request_rejects_mismatched_response_id(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(_message):
        return encode_success_response("different", {"value": "ok"})

    thread = serve_once(path, response)
    with pytest.raises(IPCClientError, match="response id does not match"):
        request(path, "ping")
    thread.join(timeout=2)


def test_request_surfaces_application_error(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(message):
        return encode_error_response(message.request_id, "blocked", "not ready")

    thread = serve_once(path, response)
    with pytest.raises(IPCClientError, match="not ready") as raised:
        request(path, "status")
    assert raised.value.application is True
    thread.join(timeout=2)
