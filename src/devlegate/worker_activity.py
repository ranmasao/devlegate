# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Thread-safe observation of the declared worker output boundary."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable


class WorkerActivity:
    """Track output silence for one worker attempt using a monotonic clock.

    Silence alone is not proof of a stall: callers must also establish child
    activity, ownership and completion before taking any destructive action.
    """

    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last = clock()
        self._generation = 0
        self._closed = False

    def observe(self, stream: str, data: bytes) -> None:
        if stream not in {"stdout", "stderr"} or not data:
            return
        with self._lock:
            if not self._closed:
                self._last = self._clock()
                self._generation += 1

    def snapshot(self) -> tuple[float, int] | None:
        with self._lock:
            if self._closed:
                return None
            return max(0.0, self._clock() - self._last), self._generation

    def close(self) -> None:
        with self._lock:
            self._closed = True
