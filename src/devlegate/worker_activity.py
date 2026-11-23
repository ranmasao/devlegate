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
MAX_STDOUT_EVENT_BYTES = 1024 * 1024  # raw JSONL framing limit
MAX_PATHS = 20
MAX_PATH_CHARS = 160
_PRIVATE_LINE = re.compile(
    r"(?i)\b(?:pids?|pgids?|ppids?|sid|cgroups?|systemd|sandbox\w*|worktrees?|"
    r"checkpoints?|containment|service[ _-]+(?:id|instance)|"
    r"(?:execution|instance|boot|session)[ _-]*id)\b|\b[0-9a-f]{32,64}\b|"
    r"\b[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}\b|"
    r"(?<![\w./-])/(?!/)[\w.~]"
)


def project_stall_output(
    evidence: dict, private_values: tuple[str, ...], *,
    decoded_stdout: str | None = None, stdout_truncated: bool = False,
    stdout_starts_at_line: bool = True,
) -> dict:
    """Keep the private raw tails separate from their worker-visible projection."""
    projected = {}
    private = tuple(
        re.compile(rf"(?<!\w){re.escape(value)}(?!\w)") if value.isdecimal()
        else re.compile(re.escape(value), re.IGNORECASE)
        for value in private_values if value
    )
    for stream in ("stdout", "stderr"):
        if stream == "stdout" and decoded_stdout is not None:
            text, truncated, starts_at_line = (
                decoded_stdout, stdout_truncated, stdout_starts_at_line,
            )
        else:
            entry = evidence["output"][stream]
            text = bytes.fromhex(entry["hex"])[-TAIL_BYTES:].decode("utf-8", "replace")
            truncated, starts_at_line = entry["truncated"], entry.get("starts_at_line")
        # Retain safe bytes verbatim, including whitespace. Omit whole lines
        # carrying private diagnostics, rather than expose partial identifiers.
        lines = []
        bodies = text.rsplit("\n", TAIL_LINES + 1)
        raw_lines = [body + "\n" for body in bodies[:-1]]
        if bodies[-1]:
            raw_lines.append(bodies[-1])
        if raw_lines and truncated and not starts_at_line:
            # A clipped line can contain an identifier suffix without the
            # context needed to recognize it as private.
            raw_lines.pop(0)
        for line in raw_lines[-TAIL_LINES:]:
            visible = line
            try:
                # Worker payloads and diagnostics can themselves contain JSON
                # with escaped private values.
                visible += json.dumps(json.loads(line), ensure_ascii=False)
            except (ValueError, RecursionError):
                pass
            if _PRIVATE_LINE.search(visible) or any(
                pattern.search(visible) for pattern in private
            ) or any(not char.isprintable() and char not in "\n\r\t" for char in line):
                lines.append("[private output omitted]\n")
            else:
                lines.append(line)
        safe = "".join(lines).encode("utf-8")
        # Clip only after checking complete lines, so an excerpt of a large
        # payload cannot expose the suffix of a private line or identifier.
        projected[stream] = {
            "text": safe[-TAIL_BYTES:].decode("utf-8", "ignore"),
            "truncated": (
                truncated or len(raw_lines) > TAIL_LINES or len(safe) > TAIL_BYTES
            ),
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
        self._decoded_stdout = ""
        self._stdout_truncated = False
        self._stdout_starts_at_line = True

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

    def observe_decoded_stdout(self, text: str) -> None:
        """Keep payloads for privacy filtering without counting them as activity."""
        with self._lock:
            if self._closed:
                return
            # One full accepted JSONL event fits even when its payload greatly
            # exceeds the raw/visible tails. This buffer is never persisted.
            limit = MAX_STDOUT_EVENT_BYTES + 1
            tail = self._decoded_stdout + text
            if len(tail) > limit:
                self._stdout_truncated = True
                self._stdout_starts_at_line = tail[-limit - 1] == "\n"
            self._decoded_stdout = tail[-limit:]

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
        *, private_values: tuple[str, ...] | None = None,
    ) -> bool:
        """Linearize classification against observed bytes and completion."""
        with self._lock:
            idle = max(0.0, self._clock() - self._last)
            if self._closed or self._generation != generation or idle < threshold:
                return False
            evidence = {
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
            }
            if private_values is not None:
                evidence["prompt_output"] = project_stall_output(
                    evidence, private_values, decoded_stdout=self._decoded_stdout,
                    stdout_truncated=self._stdout_truncated,
                    stdout_starts_at_line=self._stdout_starts_at_line,
                )
            return persist(evidence)
