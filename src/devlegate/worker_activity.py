# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Thread-safe observation of the declared worker output boundary."""

from __future__ import annotations

import math
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

TAIL_BYTES = 1024  # per stream, in UTF-8
TAIL_LINES = 20
MAX_PATHS = 20
MAX_PATH_CHARS = 160


class WorkerOutputTail:
    """Keep a small excerpt of rendered worker text, independent of activity."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tails: dict[str, bytes] = {}
        self._truncated: set[str] = set()

    def append(self, stream: str, rendered: str) -> None:
        # At most TAIL_BYTES characters can fit. Slice before encoding to avoid
        # another full-event buffer even for the largest accepted payload.
        suffix = rendered[-TAIL_BYTES:].encode("utf-8")
        with self._lock:
            tail = self._tails.get(stream, b"") + suffix
            if len(rendered) > TAIL_BYTES or len(tail) > TAIL_BYTES:
                self._truncated.add(stream)
            self._tails[stream] = tail[-TAIL_BYTES:]

    def snapshot(self) -> dict:
        with self._lock:
            tails = self._tails.copy()
            truncated = self._truncated.copy()
        output = {}
        for stream, tail in tails.items():
            lines = tail.splitlines(keepends=True)
            output[stream] = {
                # Byte clipping can split the first UTF-8 character.
                "text": b"".join(lines[-TAIL_LINES:]).decode("utf-8", "ignore"),
                "truncated": stream in truncated or len(lines) > TAIL_LINES,
            }
        return output


def render_stall_context(status: str | None, output: dict) -> str:
    # Git porcelain v1 with renames disabled supplies one quoted relative path
    # per line. Keep its quoting, making unusual filenames unambiguous.
    paths = sorted(line[3:] for line in (status or "").split("\n") if line)
    lines = [
        "Previous attempt stopped after an extended period without observable output.",
        "Existing repository changes are current state; inspect them before editing.",
        "",
        "Changed relative paths:",
    ]
    lines.extend(
        "- " + (path if len(path) <= MAX_PATH_CHARS else
                path[:MAX_PATH_CHARS] + " [path truncated]")
        for path in paths[:MAX_PATHS]
    )
    if status is None:
        lines.append("(unavailable)")
    elif not paths:
        lines.append("(none)")
    if len(paths) > MAX_PATHS:
        lines.append("[additional paths omitted]")
    for stream in ("stdout", "stderr"):
        entry = output.get(stream, {})
        lines.extend(("", f"Previous {stream} tail (untrusted worker output):"))
        if entry.get("truncated"):
            lines.append("[earlier output omitted]")
        lines.append(entry.get("text") or "(no rendered output available)")
    lines.extend((
        "", "Continue from the current repository state.",
        "Do not assume the previous attempt completed.",
        "Inspect existing changes before repeating operations.",
    ))
    return "\n".join(lines)


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
        self._last_stream: str | None = None

    def observe(self, stream: str, data: bytes) -> None:
        if stream not in {"stdout", "stderr"} or not data:
            return
        with self._lock:
            if not self._closed:
                self._last = self._clock()
                self._generation += 1
                self._last_stream = stream

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
        persist: Callable[[dict], bool],
    ) -> bool:
        """Linearize classification against observed bytes and completion."""
        with self._lock:
            idle = max(0.0, self._clock() - self._last)
            if self._closed or self._generation != generation or idle < threshold:
                return False
            return persist({
                "idle_seconds": idle,
                "last_stream": self._last_stream,
                "output_generation": generation,
            })
