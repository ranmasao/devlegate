# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Thread-safe observation of the declared worker output boundary."""

from __future__ import annotations

import json
import math
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

TAIL_BYTES = 1024  # per stream, before UTF-8 decoding
TAIL_LINES = 20
MAX_PATHS = 20
MAX_PATH_CHARS = 160
_PRIVATE_LINE = re.compile(
    r"(?i)\b(?:pids?|pgids?|ppids?|sid|cgroups?|systemd|sandbox\w*|worktrees?|"
    r"checkpoints?|containment|service[ _-]+(?:id|instance)|"
    r"(?:execution|instance|boot|session)[ _-]*id)\b|\b[0-9a-f]{32,64}\b|"
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|"
    r"(?<![\w./-])/(?!/)[\w.~]"
)


def project_stall_output(evidence: dict, private_values: tuple[str, ...]) -> dict:
    """Keep the private raw tails separate from their worker-visible projection."""
    projected = {}
    private = tuple(
        re.compile(rf"(?<!\w){re.escape(value)}(?!\w)") if value.isdecimal()
        else re.compile(re.escape(value), re.IGNORECASE)
        for value in private_values if value
    )
    for stream in ("stdout", "stderr"):
        entry = evidence["output"][stream]
        text = bytes.fromhex(entry["hex"])[-TAIL_BYTES:].decode("utf-8", "replace")
        # Retain safe bytes verbatim, including whitespace. Omit whole lines
        # carrying private diagnostics, rather than expose partial identifiers.
        lines = []
        raw_lines = text.splitlines(keepends=True)
        if raw_lines and entry["truncated"] and not entry.get("starts_at_line"):
            # A clipped line can contain an identifier suffix without the
            # context needed to recognize it as private.
            raw_lines.pop(0)
        for line in raw_lines[-TAIL_LINES:]:
            visible = line
            try:
                # Inspect decoded JSON strings as well, while replaying only
                # the original bytes. OpenCode can escape internal paths.
                visible += json.dumps(json.loads(line), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
            if _PRIVATE_LINE.search(visible) or any(
                pattern.search(visible) for pattern in private
            ) or any(ord(char) < 32 and char not in "\n\r\t" for char in line):
                lines.append("[private output omitted]\n")
            else:
                lines.append(line)
        projected[stream] = {
            "text": "".join(lines),
            "truncated": entry["truncated"] or len(raw_lines) > TAIL_LINES,
        }
    return projected


def render_stall_context(status: str, output: dict) -> str:
    # Git porcelain v1 with renames disabled supplies one quoted relative path
    # per line. Keep its quoting, making unusual filenames unambiguous.
    paths = sorted(line[3:] for line in status.split("\n") if line)
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
    if not paths:
        lines.append("(none)")
    if len(paths) > MAX_PATHS:
        lines.append("[additional paths omitted]")
    for stream in ("stdout", "stderr"):
        entry = output[stream]
        lines.extend(("", f"Previous {stream} tail (observed output):"))
        if entry["truncated"]:
            lines.append("[earlier output omitted]")
        lines.append(entry["text"] or "(no bytes observed)")
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
        self._tails = {"stdout": b"", "stderr": b""}
        self._truncated: set[str] = set()
        self._starts_at_line = {"stdout": True, "stderr": True}
        self._last_stream: str | None = None

    def observe(self, stream: str, data: bytes) -> None:
        if stream not in {"stdout", "stderr"} or not data:
            return
        with self._lock:
            if not self._closed:
                self._last = self._clock()
                self._generation += 1
                self._last_stream = stream
                tail = self._tails[stream] + data
                if len(tail) > TAIL_BYTES:
                    self._truncated.add(stream)
                    self._starts_at_line[stream] = tail[-TAIL_BYTES - 1] == 10
                self._tails[stream] = tail[-TAIL_BYTES:]

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
                "output": {
                    name: {
                        "hex": tail.hex(), "truncated": name in self._truncated,
                        "starts_at_line": self._starts_at_line[name],
                    }
                    for name, tail in self._tails.items()
                },
            })
