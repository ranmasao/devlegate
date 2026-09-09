"""Versioned local IPC messages and length-prefixed byte-stream framing."""

from __future__ import annotations

import json
import struct
from dataclasses import dataclass
from typing import BinaryIO

PROTOCOL_VERSION = 1
MAX_PAYLOAD_BYTES = 1_048_576
_HEADER = struct.Struct(">I")
_REQUEST_FIELDS = {"version", "id", "method", "payload"}


class IPCProtocolError(ValueError):
    """A protocol or framing error with a stable machine-readable code."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class IPCRequest:
    version: int
    request_id: str
    method: str
    payload: dict[str, object]


@dataclass(frozen=True)
class IPCResponse:
    version: int
    request_id: str
    ok: bool
    result: dict[str, object] | None = None
    error: dict[str, str] | None = None

    def as_dict(self) -> dict[str, object]:
        body: dict[str, object] = {
            "version": self.version,
            "id": self.request_id,
            "ok": self.ok,
        }
        if self.ok:
            body["result"] = self.result
        else:
            body["error"] = self.error
        return body


def encode_frame(payload: bytes) -> bytes:
    """Return one length-prefixed frame for a bounded payload."""
    if len(payload) > MAX_PAYLOAD_BYTES:
        raise IPCProtocolError("oversized_frame", "payload exceeds the maximum size")
    return _HEADER.pack(len(payload)) + payload


def send_frame(stream: BinaryIO, payload: bytes) -> None:
    """Write one complete frame to a binary stream."""
    frame = encode_frame(payload)
    offset = 0
    while offset < len(frame):
        written = stream.write(frame[offset:])
        if written is None:
            written = len(frame) - offset
        if written <= 0:
            raise IPCProtocolError(
                "malformed_protocol", "stream write made no progress"
            )
        offset += written


def receive_frame(stream: BinaryIO) -> bytes | None:
    """Read one frame, returning None only for clean EOF before its header."""
    header = _read_exact(stream, _HEADER.size, allow_clean_eof=True)
    if header is None:
        return None
    length = _HEADER.unpack(header)[0]
    if length > MAX_PAYLOAD_BYTES:
        raise IPCProtocolError(
            "oversized_frame", "advertised payload exceeds the maximum size"
        )
    return _read_exact(stream, length)


def parse_request(payload: bytes) -> IPCRequest:
    """Decode and strictly validate one request payload."""
    value = _decode_json(payload)
    if not isinstance(value, dict):
        raise IPCProtocolError("invalid_request", "request must be a JSON object")
    if set(value) != _REQUEST_FIELDS:
        raise IPCProtocolError("invalid_request", "request fields are invalid")
    version = value["version"]
    if not isinstance(version, int) or isinstance(version, bool):
        raise IPCProtocolError("invalid_request", "request version must be an integer")
    if version != PROTOCOL_VERSION:
        raise IPCProtocolError("unsupported_version", "request version is unsupported")
    request_id = value["id"]
    method = value["method"]
    request_payload = value["payload"]
    if not isinstance(request_id, str) or not request_id:
        raise IPCProtocolError("invalid_request", "request id must be non-empty text")
    if not isinstance(method, str) or not method:
        raise IPCProtocolError(
            "invalid_request", "request method must be non-empty text"
        )
    if not isinstance(request_payload, dict):
        raise IPCProtocolError("invalid_request", "request payload must be an object")
    return IPCRequest(version, request_id, method, request_payload)


def encode_success_response(
    request_id: str, result: dict[str, object]
) -> bytes:
    """Encode a successful response payload."""
    _require_text(request_id, "response id")
    if not isinstance(result, dict):
        raise IPCProtocolError("invalid_request", "response result must be an object")
    response = IPCResponse(PROTOCOL_VERSION, request_id, True, result=result)
    return _encode_json(response.as_dict())


def encode_error_response(request_id: str, code: str, message: str) -> bytes:
    """Encode a protocol error response payload."""
    _require_text(request_id, "response id")
    _require_text(code, "error code")
    _require_text(message, "error message")
    response = IPCResponse(
        PROTOCOL_VERSION,
        request_id,
        False,
        error={"code": code, "message": message},
    )
    return _encode_json(response.as_dict())


def _read_exact(
    stream: BinaryIO, size: int, *, allow_clean_eof: bool = False
) -> bytes | None:
    chunks: list[bytes] = []
    remaining = size
    while remaining:
        chunk = stream.read(remaining)
        if not chunk:
            if allow_clean_eof and not chunks:
                return None
            raise IPCProtocolError("malformed_protocol", "frame is truncated")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _require_text(value: object, label: str) -> None:
    if not isinstance(value, str) or not value:
        raise IPCProtocolError("invalid_request", f"{label} must be non-empty text")


def _decode_json(payload: bytes) -> object:
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as error:
        raise IPCProtocolError(
            "malformed_protocol", "payload is not valid UTF-8"
        ) from error
    try:
        return json.loads(text)
    except json.JSONDecodeError as error:
        raise IPCProtocolError(
            "malformed_protocol", "payload is not valid JSON"
        ) from error


def _encode_json(value: dict[str, object]) -> bytes:
    try:
        return json.dumps(
            value, ensure_ascii=False, separators=(",", ":"), sort_keys=True
        ).encode("utf-8")
    except (TypeError, ValueError) as error:
        raise IPCProtocolError(
            "invalid_request", "message is not JSON encodable"
        ) from error


__all__ = [
    "IPCProtocolError",
    "IPCRequest",
    "IPCResponse",
    "MAX_PAYLOAD_BYTES",
    "PROTOCOL_VERSION",
    "encode_error_response",
    "encode_frame",
    "encode_success_response",
    "parse_request",
    "receive_frame",
    "send_frame",
]
