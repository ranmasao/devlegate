# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-polled Unix request transport. One framed exchange per connection."""

from __future__ import annotations

import os
import socket
import struct
import time
from collections.abc import Callable
from pathlib import Path


class WorkerRequestError(RuntimeError):
    pass


class WorkerRequestChannel:
    def __init__(
        self, path: Path, reply: Callable[[bytes], bytes], *,
        requests: int, response_bytes: int, request_limit: int = 4096,
        response_limit: int = 1024 * 1024, io_seconds: float = 30,
        session_seconds: float = 600, label: str = "worker request",
    ) -> None:
        if min(requests, response_bytes, io_seconds, session_seconds,
               request_limit, response_limit) <= 0:
            raise WorkerRequestError(f"{label} channel budgets must be positive")
        self._reply = reply
        self.path = path
        self._label = label
        self._requests = requests
        self._remaining = response_bytes
        self._request_limit = request_limit
        self._response_limit = response_limit
        self._io_seconds = io_seconds
        self._deadline = time.monotonic() + session_seconds
        self._client: socket.socket | None = None
        self._input = bytearray()
        self._output = b""
        self._position = 0
        self._client_deadline = 0.0
        self._listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            # Never unlink an existing socket or follow a symlink on admission.
            self._listener.bind(str(path))
            os.chmod(path, 0o600)
            self._listener.listen(1)
            self._listener.setblocking(False)
            self._identity = path.lstat()
        except BaseException:
            self._listener.close()
            raise

    def _drop_client(self) -> None:
        if self._client is not None:
            self._client.close()
        self._client = None
        self._input.clear()
        self._output = b""
        self._position = 0

    def close(self) -> None:
        self._drop_client()
        self._listener.close()
        try:
            current = self.path.lstat()
            if (current.st_dev, current.st_ino) == (
                self._identity.st_dev, self._identity.st_ino,
            ):
                self.path.unlink()
        except FileNotFoundError:
            pass

    def __enter__(self):
        return self

    def __exit__(self, *_exc: object) -> None:
        self.close()

    def peer_credentials(self) -> tuple[int, int, int] | None:
        if self._client is None:
            return None
        return struct.unpack("3i", self._client.getsockopt(
            socket.SOL_SOCKET, socket.SO_PEERCRED, struct.calcsize("3i"),
        ))

    def poll(self) -> None:
        """Perform bounded socket work on the owner thread only."""
        if time.monotonic() >= self._deadline:
            raise WorkerRequestError(
                f"{self._label} channel session deadline exhausted"
            )
        if self._client is None:
            try:
                self._client, _ = self._listener.accept()
            except BlockingIOError:
                return
            self._client.setblocking(False)
            self._client_deadline = time.monotonic() + self._io_seconds
            self._requests -= 1
            if self._requests < 0:
                self._drop_client()
                raise WorkerRequestError(
                    f"{self._label} channel request budget exhausted"
                )
        if time.monotonic() >= self._client_deadline:
            self._drop_client()
            return
        try:
            if not self._output:
                chunk = self._client.recv(self._request_limit + 5 - len(self._input))
                if not chunk:
                    self._drop_client()
                    return
                self._input.extend(chunk)
                if len(self._input) < 4:
                    return
                length = struct.unpack(">I", self._input[:4])[0]
                if not 1 <= length <= self._request_limit:
                    self._drop_client()
                    return
                if len(self._input) < length + 4:
                    return
                if len(self._input) != length + 4:
                    self._drop_client()
                    return
                self._output = self._reply(bytes(self._input[4:]))
                if not self._output:
                    self._drop_client()
                    return
                if len(self._output) > self._response_limit + 4:
                    self._drop_client()
                    raise WorkerRequestError(
                        f"{self._label} response frame exceeds its byte limit"
                    )
                # Charge before delivery, including abandoned responses and framing.
                self._remaining -= len(self._output)
                if self._remaining < 0:
                    self._drop_client()
                    raise WorkerRequestError(
                        f"{self._label} aggregate response budget exhausted"
                    )
            self._position += self._client.send(
                self._output[self._position:self._position + 65536]
            )
            if self._position == len(self._output):
                self._drop_client()
        except BlockingIOError:
            pass
        except (BrokenPipeError, ConnectionResetError):
            self._drop_client()
