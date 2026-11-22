# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Monotonic, process-local observation of a worker's output boundary."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable


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

    def observe(self, stream: str, data: bytes) -> None:
        if stream not in {"stdout", "stderr"} or not data:
            return
        with self._lock:
            self._last_output = self._clock()

    def snapshot(self) -> dict[str, object]:
        with self._lock:
            idle = max(0.0, self._clock() - self._last_output)
        return {"idle_seconds": int(idle), "warning": idle >= self._warning_seconds}
