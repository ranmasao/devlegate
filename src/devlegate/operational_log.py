# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small, explicit service and execution operational log sinks."""

from __future__ import annotations

import os
import re
import sys
import threading
import time
from pathlib import Path
from typing import TextIO


class OperationalLogError(OSError):
    """An operational log sink could not be opened or written."""


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _timestamp() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def _record(message: str) -> str:
    return f"[{_timestamp()}] {message}\n"


def service_log(message: str, stream: TextIO | None = None) -> None:
    """Write one service-scoped operational record."""
    target = sys.stdout if stream is None else stream
    target.write(_record(message))
    target.flush()
    try:
        os.fsync(target.fileno())
    except (OSError, ValueError, AttributeError):
        pass


def execution_log_path(state_dir: Path, state_key: str, execution_id: str) -> Path:
    """Return the canonical path for one validated execution identity."""
    if not _IDENTIFIER.fullmatch(execution_id):
        raise OperationalLogError("invalid execution log identity")
    return state_dir / "logs" / state_key / "executions" / f"{execution_id}.log"


class ExecutionLog:
    """Devlegate-owned diagnostic output for one durable execution identity."""

    def __init__(self, path: Path) -> None:
        self.path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            self._stream = path.open("a", encoding="utf-8")
        except OSError as error:
            raise OperationalLogError(
                f"cannot open execution log {path}: {error}"
            ) from error
        self._lock = threading.Lock()

    def write(self, text: str) -> None:
        if not text:
            return
        with self._lock:
            try:
                self._stream.write(_record(text.rstrip("\n")))
                self._stream.flush()
                os.fsync(self._stream.fileno())
            except (OSError, ValueError) as error:
                raise OperationalLogError(
                    f"cannot write execution log {self.path}: {error}"
                ) from error

    def close(self) -> None:
        try:
            self._stream.close()
        except OSError as error:
            raise OperationalLogError(
                f"cannot close execution log {self.path}: {error}"
            ) from error

    def __enter__(self) -> ExecutionLog:
        return self

    def __exit__(self, _exc_type, _exc_value, _traceback) -> None:
        self.close()


def open_execution_log(
    state_dir: Path, state_key: str, execution_id: str
) -> ExecutionLog:
    return ExecutionLog(execution_log_path(state_dir, state_key, execution_id))
