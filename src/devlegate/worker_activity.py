# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Monotonic inactivity tracking for one worker attempt."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass


@dataclass(frozen=True)
class WatchdogPolicy:
    warning: float
    recovery: float

    @classmethod
    def parse(cls, warning: str, recovery: str) -> WatchdogPolicy:
        try:
            policy = cls(float(warning), float(recovery))
            if 0 < policy.warning < policy.recovery and math.isfinite(policy.recovery):
                return policy
        except ValueError:
            pass
        raise ValueError(
            "worker inactivity policy requires 0 < WORKER_IDLE_WARNING_SECONDS < "
            "WORKER_IDLE_RECOVERY_SECONDS, both finite"
        )


class WorkerActivity:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._clock = clock
        self._lock = threading.Lock()
        self._last = clock()
        self._generation = 0
        self._closed = False

    def start(self) -> None:
        with self._lock:
            self._last = self._clock()

    def read(self, ready_read: Callable[[], int]) -> int:
        # Readiness polling stays outside the lock. The single pipe owner
        # records a successful read before a classification can acquire it.
        with self._lock:
            size = ready_read()
            if size and not self._closed:
                self._last = self._clock()
                self._generation += 1
            return size

    def snapshot(self) -> tuple[float, int] | None:
        with self._lock:
            if self._closed:
                return None
            return max(0.0, self._clock() - self._last), self._generation

    def close(self) -> None:
        with self._lock:
            self._closed = True

    def classify(
        self, generation: int, threshold: float,
        persist: Callable[[float], bool],
    ) -> bool:
        """Linearize write-ahead classification against the raw pipe reads."""
        with self._lock:
            idle = max(0.0, self._clock() - self._last)
            if self._closed or self._generation != generation or idle < threshold:
                return False
            return persist(idle)
