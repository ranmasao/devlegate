# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read operator-facing service and execution logs."""

from __future__ import annotations

import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from devlegate.execution_result import ExecutionReportError, ExecutionReportStore
from devlegate.operational_log import OperationalLogError, execution_log_path
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore


class LogReaderError(RuntimeError):
    """A requested log could not be resolved or read."""


def _lines(value: int) -> int:
    if value < 1:
        raise LogReaderError("--lines must be a positive integer")
    return value


def service_unit(state_dir: Path, state_key: str) -> str:
    store = SQLiteRuntimeStore(state_dir, state_key)
    try:
        authority = store.supervision_authority()
    except RuntimeStoreError as error:
        raise LogReaderError(str(error)) from error
    if authority is None or authority.get("authority") != "systemd":
        raise LogReaderError("no persisted systemd service authority")
    unit = authority.get("unit_name")
    if not unit:
        raise LogReaderError("persisted systemd service authority has no unit")
    return unit


def execution_id(control_worktree: Path, selector: str) -> str:
    if not selector or any(
        character not in "0123456789abcdef" for character in selector
    ):
        raise LogReaderError(
            "execution ID must be a lowercase hexadecimal ID or prefix"
        )
    try:
        reports = ExecutionReportStore(control_worktree).list()
    except ExecutionReportError as error:
        raise LogReaderError(str(error)) from error
    matches = tuple(
        report.execution_id
        for report in reports
        if report.execution_id.startswith(selector)
    )
    if not matches:
        raise LogReaderError(f"execution not found: {selector}")
    if len(matches) > 1:
        raise LogReaderError(f"execution prefix is ambiguous: {selector}")
    return matches[0]


def _read_file(path: Path, lines: int) -> Iterable[str]:
    lines = _lines(lines)
    if not path.is_file():
        raise LogReaderError(f"execution log not found: {path}")
    try:
        content = path.read_text(encoding="utf-8").splitlines(keepends=True)
    except OSError as error:
        raise LogReaderError(f"cannot read execution log {path}: {error}") from error
    return content[-lines:]


def execution_log(
    state_dir: Path, state_key: str, control_worktree: Path, selector: str, lines: int
) -> tuple[str, Iterable[str]]:
    resolved = execution_id(control_worktree, selector)
    try:
        path = execution_log_path(state_dir, state_key, resolved)
    except OperationalLogError as error:
        raise LogReaderError(str(error)) from error
    return resolved, _read_file(path, lines)


def journal_command(unit: str, lines: int, follow: bool) -> list[str]:
    command = [
        "journalctl",
        "--user-unit",
        unit,
        "--no-pager",
        "--output=cat",
        "--lines",
        str(_lines(lines)),
    ]
    if follow:
        command.append("--follow")
    return command


def stream_journal(
    unit: str,
    lines: int,
    follow: bool,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
) -> int:
    command = journal_command(unit, lines, follow)
    if not follow:
        try:
            result = runner(command, text=True, capture_output=True, check=False)
        except OSError as error:
            raise LogReaderError(f"cannot run journalctl: {error}") from error
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise LogReaderError(f"journalctl failed: {detail}")
        print(result.stdout, end="")
        return 0
    try:
        process = subprocess.Popen(command, text=True, stdout=subprocess.PIPE)
        assert process.stdout is not None
        for line in process.stdout:
            print(line, end="")
        return process.wait()
    except KeyboardInterrupt:
        process.terminate()
        process.wait()
        return 130
    except OSError as error:
        raise LogReaderError(f"cannot run journalctl: {error}") from error


def follow_file(path: Path, lines: int) -> int:
    lines = _lines(lines)
    print("".join(_read_file(path, lines)), end="")
    with path.open(encoding="utf-8") as handle:
        handle.seek(0, 2)
        try:
            while True:
                line = handle.readline()
                if line:
                    print(line, end="")
                else:
                    time.sleep(0.1)
        except KeyboardInterrupt:
            return 130
