# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Private bounded artifact pipes, without access to privileged service dispatch.

The repository-owner thread calls serve_one; no helper thread receives Git
capability. Only the two worker pipe ends cross the logical evidence boundary.
This is a scoped tool channel, not kernel-enforced worker isolation.
"""

from __future__ import annotations

import json
import os
import selectors
import struct
import time
from pathlib import Path

from devlegate.abandonment_evidence import EvidenceBundle, EvidenceError
from devlegate.ipc_protocol import encode_frame
from devlegate.worker_request_channel import WorkerRequestChannel, WorkerRequestError

MAX_REQUEST_BYTES = 4096
MAX_RESPONSE_BYTES = 1024 * 1024
MAX_REQUESTS = 8192
MAX_RESPONSE_TOTAL = 2 * 1024 * 1024 * 1024
IO_SECONDS = 30
SESSION_SECONDS = 600


def _read_reply(bundle: EvidenceBundle, raw: bytes) -> bytes:
    try:
        request = json.loads(raw)
        if not isinstance(request, dict) or set(request) != {
            "manifest_id", "artifact_id", "offset", "length",
        }:
            raise EvidenceError("evidence requests must have exact read fields")
        if not all(
            isinstance(request[key], str) and len(request[key]) == 64
            for key in ("manifest_id", "artifact_id")
        ):
            raise EvidenceError("invalid manifest/artifact identity")
        result = bundle.read(**request)
        reply = {"ok": True, "result": result}
    except (EvidenceError, UnicodeError, json.JSONDecodeError) as error:
        reply = {"ok": False, "error": str(error)}
    payload = json.dumps(reply, ensure_ascii=True, separators=(",", ":")).encode()
    if len(payload) > MAX_RESPONSE_BYTES:
        raise EvidenceError("evidence response frame exceeds its byte limit")
    return encode_frame(payload)


class EvidenceSocket(WorkerRequestChannel):
    """Owner-polled, manifest-scoped read tool for trusted OpenCode workers."""
    def __init__(
        self, bundle: EvidenceBundle, path: Path, *,
        requests: int = MAX_REQUESTS, response_bytes: int = MAX_RESPONSE_TOTAL,
        io_seconds: float = IO_SECONDS, session_seconds: float = SESSION_SECONDS,
    ) -> None:
        self.bundle = bundle
        try:
            super().__init__(
                path, lambda raw: _read_reply(bundle, raw), requests=requests,
                response_bytes=response_bytes, request_limit=MAX_REQUEST_BYTES,
                response_limit=MAX_RESPONSE_BYTES, io_seconds=io_seconds,
                session_seconds=session_seconds, label="evidence",
            )
        except WorkerRequestError as error:
            raise EvidenceError(str(error)) from error

    def poll(self) -> None:
        try:
            super().poll()
        except WorkerRequestError as error:
            raise EvidenceError(str(error)) from error


class EvidencePipe:
    """One manifest-scoped, single-worker artifact session with shared quotas."""

    def __init__(
        self, bundle: EvidenceBundle, *, requests: int = MAX_REQUESTS,
        response_bytes: int = MAX_RESPONSE_TOTAL,
        io_seconds: float = IO_SECONDS, session_seconds: float = SESSION_SECONDS,
    ) -> None:
        if (
            requests < 1 or response_bytes < 1
            or io_seconds <= 0 or session_seconds <= 0
        ):
            raise EvidenceError("evidence channel budgets must be positive")
        self.bundle = bundle
        self._requests = requests
        self._response_bytes = response_bytes
        self._io_seconds = io_seconds
        self._deadline = time.monotonic() + session_seconds
        self._request_r, self._request_w = os.pipe()
        self._response_r, self._response_w = os.pipe()
        for fd in (self._request_r, self._response_w):
            os.set_blocking(fd, False)

    @property
    def worker_fds(self) -> tuple[int, int]:
        """Request write end, response read end; pass only these to the child."""
        if self._request_w < 0 or self._response_r < 0:
            raise EvidenceError("worker evidence descriptors already transferred")
        return self._request_w, self._response_r

    def worker_started(self) -> None:
        for field in ("_request_w", "_response_r"):
            fd = getattr(self, field)
            if fd >= 0:
                os.close(fd)
                setattr(self, field, -1)

    def close(self) -> None:
        for field in ("_request_r", "_request_w", "_response_r", "_response_w"):
            fd = getattr(self, field, -1)
            if fd >= 0:
                os.close(fd)
                setattr(self, field, -1)

    def __enter__(self) -> EvidencePipe:
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def _transfer(
        self, fd: int, *, length: int = 0, value: bytes | None = None,
        clean_eof: bool = False,
    ) -> bytes | None:
        output = bytearray()
        position = 0
        writing = value is not None
        total = len(value) if value is not None else length
        deadline = min(self._deadline, time.monotonic() + self._io_seconds)
        with selectors.DefaultSelector() as selector:
            selector.register(
                fd, selectors.EVENT_WRITE if writing else selectors.EVENT_READ,
            )
            while position < total:
                timeout = deadline - time.monotonic()
                if timeout <= 0 or not selector.select(timeout):
                    raise EvidenceError(
                        "evidence channel I/O/session deadline exhausted"
                    )
                try:
                    if value is not None:
                        position += os.write(fd, value[position:position + 65536])
                    else:
                        chunk = os.read(fd, min(65536, total - position))
                        if not chunk:
                            if clean_eof and position == 0:
                                return None
                            raise EvidenceError("incomplete evidence request frame")
                        output.extend(chunk)
                        position += len(chunk)
                except BlockingIOError:
                    continue
        return bytes(output)

    def serve_one(self) -> bool:
        """Serve exactly one bounded read; false means clean worker EOF.

        Call only on the repository-owner thread. Protocol/budget failures close
        the session. Invalid artifact requests receive an inert error and still
        consume request/response quotas. No response history is accumulated.
        """
        try:
            if self._requests <= 0 or time.monotonic() >= self._deadline:
                raise EvidenceError("evidence channel request/session budget exhausted")
            self._requests -= 1
            header = self._transfer(self._request_r, length=4, clean_eof=True)
            if header is None:
                self.close()
                return False
            length = struct.unpack(">I", header)[0]
            if not 1 <= length <= MAX_REQUEST_BYTES:
                raise EvidenceError("evidence request frame exceeds its byte limit")
            raw = self._transfer(self._request_r, length=length)
            assert raw is not None
            frame = _read_reply(self.bundle, raw)
            self._response_bytes -= len(frame)
            if self._response_bytes < 0:
                raise EvidenceError(
                    "evidence channel aggregate response budget exhausted"
                )
            self._transfer(self._response_w, value=frame)
            return True
        except BaseException:
            self.close()
            raise
