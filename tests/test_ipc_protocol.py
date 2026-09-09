import io
import json
import struct

import pytest

from devlegate.ipc_protocol import (
    MAX_PAYLOAD_BYTES,
    IPCProtocolError,
    encode_error_response,
    encode_frame,
    encode_request,
    encode_success_response,
    parse_request,
    parse_response,
    receive_frame,
    send_frame,
)


class ChunkedReader(io.BytesIO):
    def __init__(self, value: bytes, chunk_size: int) -> None:
        super().__init__(value)
        self.chunk_size = chunk_size
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        self.read_sizes.append(size)
        return super().read(min(size, self.chunk_size))


def request_payload(**overrides: object) -> bytes:
    value: dict[str, object] = {
        "version": 1,
        "id": "request-1",
        "method": "ping",
        "payload": {},
    }
    value.update(overrides)
    return json.dumps(value, separators=(",", ":")).encode()


def test_request_round_trip_and_non_ascii_payload():
    payload = request_payload(payload={"message": chr(0xE9)})
    request = parse_request(receive_frame(io.BytesIO(encode_frame(payload))))

    assert request.request_id == "request-1"
    assert request.payload == {"message": chr(0xE9)}


def test_success_and_error_response_payloads():
    success = json.loads(encode_success_response("request-1", {}).decode())
    error = json.loads(
        encode_error_response("request-1", "invalid_request", "bad input").decode()
    )

    assert success == {"id": "request-1", "ok": True, "result": {}, "version": 1}
    assert error == {
        "error": {"code": "invalid_request", "message": "bad input"},
        "id": "request-1",
        "ok": False,
        "version": 1,
    }


def test_request_and_response_codecs_round_trip():
    request = parse_request(encode_request("request-1", "ping", {"value": 1}))
    success = parse_response(encode_success_response("request-1", {"value": 1}))
    error = parse_response(
        encode_error_response("request-1", "invalid_request", "bad input")
    )

    assert request.id == "request-1"
    assert request.method == "ping"
    assert success.ok and success.result == {"value": 1}
    assert not error.ok and error.error == {
        "code": "invalid_request",
        "message": "bad input",
    }


def test_multiple_frames_and_chunked_reads():
    stream = io.BytesIO(encode_frame(b"one") + encode_frame(b"two"))
    assert receive_frame(stream) == b"one"
    assert receive_frame(stream) == b"two"
    assert receive_frame(stream) is None

    chunked = ChunkedReader(encode_frame(b"chunked"), 1)
    assert receive_frame(chunked) == b"chunked"
    assert len(chunked.read_sizes) > 2


def test_send_frame_handles_partial_writes():
    class PartialWriter(io.BytesIO):
        def write(self, value: bytes) -> int:
            return super().write(value[:1])

    stream = PartialWriter()
    send_frame(stream, b"payload")
    assert stream.getvalue() == encode_frame(b"payload")


def test_send_frame_none_write_is_a_failure():
    class NoneWriter:
        def write(self, _value: bytes) -> None:
            return None

    with pytest.raises(IPCProtocolError, match="no progress"):
        send_frame(NoneWriter(), b"payload")


def test_exact_limit_is_accepted_and_over_limit_is_rejected_before_payload_read():
    exact = encode_frame(b"x" * MAX_PAYLOAD_BYTES)
    assert receive_frame(io.BytesIO(exact)) == b"x" * MAX_PAYLOAD_BYTES

    class HeaderOnly(io.BytesIO):
        def __init__(self) -> None:
            super().__init__(struct.pack(">I", MAX_PAYLOAD_BYTES + 1) + b"ignored")
            self.read_count = 0

        def read(self, size: int = -1) -> bytes:
            self.read_count += 1
            return super().read(size)

    stream = HeaderOnly()
    with pytest.raises(IPCProtocolError) as error:
        receive_frame(stream)
    assert error.value.code == "oversized_frame"
    assert stream.read_count == 1


def test_clean_eof_and_truncated_frames():
    assert receive_frame(io.BytesIO()) is None
    with pytest.raises(IPCProtocolError, match="truncated"):
        receive_frame(io.BytesIO(b"\x00"))
    with pytest.raises(IPCProtocolError, match="truncated"):
        receive_frame(io.BytesIO(struct.pack(">I", 3) + b"ab"))


@pytest.mark.parametrize(
    ("payload", "code"),
    [
        (b"\xff", "malformed_protocol"),
        (b"{", "malformed_protocol"),
        (b"[]", "invalid_request"),
        (request_payload(version=2), "unsupported_version"),
        (request_payload(extra=True), "invalid_request"),
        (request_payload(id=""), "invalid_request"),
        (request_payload(method=7), "invalid_request"),
        (request_payload(payload=[]), "invalid_request"),
    ],
)
def test_request_validation_errors(payload: bytes, code: str):
    with pytest.raises(IPCProtocolError) as error:
        parse_request(payload)
    assert error.value.code == code


def test_response_encoders_validate_shapes():
    with pytest.raises(IPCProtocolError, match="result"):
        encode_success_response("request-1", [])  # type: ignore[arg-type]
    with pytest.raises(IPCProtocolError, match="response id"):
        encode_success_response("", {})


@pytest.mark.parametrize(
    "value",
    [
        {"version": 1, "id": "x", "ok": True, "error": {}},
        {"version": 1, "id": "x", "ok": False, "result": {}},
        {"version": 1, "id": "x", "ok": True, "result": []},
        {"version": 1, "id": "x", "ok": False, "error": {"code": "x"}},
    ],
)
def test_inconsistent_responses_are_rejected(value):
    with pytest.raises(IPCProtocolError, match="response|error"):
        parse_response(json.dumps(value).encode())
