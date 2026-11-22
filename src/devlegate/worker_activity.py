# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Monotonic, process-local observation of a worker's output boundary."""

from __future__ import annotations

import base64
import binascii
import math
import threading
import time
from collections.abc import Callable

OUTPUT_TAIL_BYTES = 8192


def valid_output_evidence(value: object) -> bool:
    if not isinstance(value, dict) or set(value) != {"stdout", "stderr"}:
        return False
    for entry in value.values():
        if (
            not isinstance(entry, dict)
            or set(entry) != {"base64", "truncated"}
            or not isinstance(entry["base64"], str)
            or len(entry["base64"]) > 4 * ((OUTPUT_TAIL_BYTES + 2) // 3)
            or not isinstance(entry["truncated"], bool)
        ):
            return False
        try:
            decoded = base64.b64decode(entry["base64"], validate=True)
        except (ValueError, binascii.Error):
            return False
        if len(decoded) > OUTPUT_TAIL_BYTES or (
            base64.b64encode(decoded).decode("ascii") != entry["base64"]
        ):
            return False
    return True


class WorkerActivity:
    """Track output silence without inferring why a worker is quiet."""

    def __init__(
        self, warning_seconds: float, *, clock: Callable[[], float] = time.monotonic
    ):
        if not math.isfinite(warning_seconds) or warning_seconds <= 0:
            raise ValueError("worker warning threshold must be positive and finite")
        self._warning_seconds = warning_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._last_output = clock()
        self._tails = {"stdout": b"", "stderr": b""}
        self._truncated = {"stdout": False, "stderr": False}

    def observe(self, stream: str, data: bytes) -> None:
        if stream not in {"stdout", "stderr"} or not data:
            return
        with self._lock:
            self._last_output = self._clock()
            tail = self._tails[stream] + data
            if len(tail) > OUTPUT_TAIL_BYTES:
                self._truncated[stream] = True
                tail = tail[-OUTPUT_TAIL_BYTES:]
            self._tails[stream] = tail

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            idle = max(0.0, self._clock() - self._last_output)
        return {"idle_seconds": int(idle), "warning": idle >= self._warning_seconds}

    def if_idle(self, seconds: float, action: Callable[[], bool]) -> bool:
        """Serialize an idle decision with output observation and its intent write."""
        with self._lock:
            if self._clock() - self._last_output < seconds:
                return False
            return action()

    def if_idle_with_output(
        self, seconds: float, action: Callable[[dict[str, object]], bool]
    ) -> bool:
        """Capture byte-exact bounded output under the idle decision lock."""
        with self._lock:
            if self._clock() - self._last_output < seconds:
                return False
            evidence = {
                stream: {
                    "base64": base64.b64encode(self._tails[stream]).decode("ascii"),
                    "truncated": self._truncated[stream],
                }
                for stream in ("stdout", "stderr")
            }
            return action(evidence)
