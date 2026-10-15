# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read operator-facing service and execution logs."""

from __future__ import annotations

import re
import stat
import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from devlegate.execution_result import ExecutionReportError, ExecutionReportStore
from devlegate.operational_log import OperationalLogError, execution_log_path
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore


class LogReaderError(RuntimeError):
    """A requested log could not be resolved or read."""


_TICKET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _lines(value: int) -> int:
    if value < 0:
        raise LogReaderError("--lines must be a non-negative integer")
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


def _current_execution_id(state_dir: Path, state_key: str) -> str | None:
    try:
        state = SQLiteRuntimeStore(state_dir, state_key).load()
    except RuntimeStoreError as error:
        raise LogReaderError(str(error)) from error
    if not isinstance(state, dict) or state.get("phase") not in {
        "agent_pending",
        "agent_running",
    }:
        return None
    current = state.get("execution_id")
    if not isinstance(current, str) or not current or any(
        character not in "0123456789abcdef" for character in current
    ):
        raise LogReaderError("invalid current execution identity")
    return current


def execution_id(
    control_worktree: Path,
    selector: str,
    *,
    state_dir: Path | None = None,
    state_key: str | None = None,
) -> str:
    if not selector or any(
        character not in "0123456789abcdef" for character in selector
    ):
        raise LogReaderError(
            "execution ID must be a lowercase hexadecimal ID or prefix"
        )
    if len(selector) == 32:
        if state_dir is not None and state_key is not None:
            current = _current_execution_id(state_dir, state_key)
            if current == selector:
                return selector
        if _execution_report(control_worktree, selector) is not None:
            return selector
        raise LogReaderError(f"execution not found: {selector}")
    identities = list(_execution_candidates(control_worktree, selector))
    current = None
    if state_dir is not None and state_key is not None:
        current = _current_execution_id(state_dir, state_key)
        if current is not None:
            identities.append(current)
    matches = tuple(
        dict.fromkeys(
            identity for identity in identities if identity.startswith(selector)
        )
    )
    if not matches:
        raise LogReaderError(f"execution not found: {selector}")
    if len(matches) > 1:
        matching = "\n".join(f"  {identifier}" for identifier in sorted(matches))
        raise LogReaderError(
            f"execution prefix is ambiguous: {selector}\nmatches:\n{matching}"
        )
    if (
        matches[0] != current
        and _execution_report(control_worktree, matches[0]) is None
    ):
        raise LogReaderError(f"execution not found: {selector}")
    return matches[0]


def _execution_candidates(control_worktree: Path, selector: str) -> tuple[str, ...]:
    """Enumerate report identities without deserializing unrelated reports."""
    root = control_worktree / "executions"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise LogReaderError("execution report root is unsafe")
    candidates: set[str] = set()
    if not root.is_dir():
        return ()
    try:
        ticket_roots = tuple(root.iterdir())
        for ticket_root in ticket_roots:
            if ticket_root.is_symlink():
                raise LogReaderError("execution report ticket directory is unsafe")
            if not ticket_root.is_dir():
                raise LogReaderError("execution report ticket directory is unsafe")
            for path in ticket_root.iterdir():
                if (
                    path.name.endswith(".json")
                    and path.stem
                    and all(
                        character in "0123456789abcdef" for character in path.stem
                    )
                    and path.stem.startswith(selector)
                ):
                    candidates.add(path.stem)
    except OSError as error:
        raise LogReaderError(f"cannot enumerate execution reports: {error}") from error
    return tuple(sorted(candidates))


class _LogCursor:
    """Read a log tail and continue from the exact byte position it used."""

    def __init__(self, path: Path, lines: int) -> None:
        self.path = path
        self.lines = _lines(lines)
        if not path.is_file():
            raise LogReaderError(f"execution log not found: {path}")
        try:
            self.handle = path.open("rb")
            self._tail()
        except (OSError, UnicodeError) as error:
            self.close()
            raise LogReaderError(
                f"cannot read execution log {path}: {error}"
            ) from error

    def _tail(self) -> None:
        handle = self.handle
        handle.seek(0, 2)
        size = handle.tell()
        if self.lines == 0 or size == 0:
            self.initial: tuple[str, ...] = ()
            return
        handle.seek(-1, 2)
        ends_with_newline = handle.read(1) == b"\n"
        required_newlines = self.lines + int(ends_with_newline)
        position = size
        chunks: list[bytes] = []
        newline_count = 0
        while position and newline_count < required_newlines:
            chunk_size = min(8192, position)
            position -= chunk_size
            handle.seek(position)
            chunk = handle.read(chunk_size)
            chunks.append(chunk)
            newline_count += chunk.count(b"\n")
        content = b"".join(reversed(chunks)).splitlines(keepends=True)
        self.initial = tuple(
            record.decode("utf-8") for record in content[-self.lines :]
        )
        # Keep follow mode at the EOF captured for this initial snapshot. Any
        # bytes appended while assembling the tail must be read exactly once.
        handle.seek(size)

    def read_available(self) -> tuple[str, ...]:
        records: list[str] = []
        try:
            while True:
                record = self.handle.readline()
                if not record:
                    break
                records.append(record.decode("utf-8"))
        except (OSError, UnicodeError) as error:
            raise LogReaderError(
                f"cannot read execution log {self.path}: {error}"
            ) from error
        return tuple(records)

    def close(self) -> None:
        handle = getattr(self, "handle", None)
        if handle is not None:
            handle.close()


def _read_file(path: Path, lines: int) -> Iterable[str]:
    cursor = _LogCursor(path, lines)
    try:
        return cursor.initial
    finally:
        cursor.close()


def execution_log(
    state_dir: Path, state_key: str, control_worktree: Path, selector: str, lines: int
) -> tuple[str, Iterable[str]]:
    resolved = execution_id(
        control_worktree,
        selector,
        state_dir=state_dir,
        state_key=state_key,
    )
    try:
        path = execution_log_path(state_dir, state_key, resolved)
    except OperationalLogError as error:
        raise LogReaderError(str(error)) from error
    return resolved, _read_file(path, lines)


def _ticket_observation(
    control_worktree: Path, ticket_id: str, workflow_paths: dict[str, str]
) -> str:
    if not _TICKET_ID.fullmatch(ticket_id):
        raise LogReaderError(f"invalid ticket ID: {ticket_id}")
    locations = []
    for state, relative in workflow_paths.items():
        path = control_worktree / relative / f"{ticket_id}.md"
        if path.exists():
            if path.is_symlink() or not path.is_file():
                raise LogReaderError(f"ticket state is unsafe: {path}")
            locations.append(state)
    if len(locations) != 1:
        if not locations:
            raise LogReaderError(f"ticket not found: {ticket_id}")
        raise LogReaderError(
            f"ticket state is ambiguous: {ticket_id} ({', '.join(locations)})"
        )
    return locations[0]


def _ticket_reports(control_worktree: Path, ticket_id: str):
    try:
        return ExecutionReportStore(control_worktree).list(ticket_id)
    except ExecutionReportError as error:
        raise LogReaderError(str(error)) from error


def _active_ticket_execution(
    state_dir: Path, state_key: str, ticket_id: str
) -> str | None:
    try:
        state = SQLiteRuntimeStore(state_dir, state_key).load()
    except RuntimeStoreError as error:
        raise LogReaderError(str(error)) from error
    if not isinstance(state, dict) or state.get("phase") not in {
        "agent_pending",
        "agent_running",
    }:
        return None
    if state.get("execution_ticket_id") != ticket_id:
        return None
    execution = state.get("execution_id")
    if not isinstance(execution, str) or not execution or any(
        character not in "0123456789abcdef" for character in execution
    ):
        raise LogReaderError("invalid current execution identity")
    return execution


def _execution_header(execution: str, conclusion: str | None) -> str:
    detail = f" conclusion={conclusion}" if conclusion is not None else " state=active"
    return f"\n===== execution {execution}{detail} =====\n"


def ticket_log(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    ticket_id: str,
    lines: int,
    workflow_paths: dict[str, str],
) -> tuple[str, Iterable[str]]:
    """Read every durable execution log belonging to one ticket."""
    _ticket_observation(control_worktree, ticket_id, workflow_paths)
    reports = _ticket_reports(control_worktree, ticket_id)
    known = {report.execution_id for report in reports}
    active = _active_ticket_execution(state_dir, state_key, ticket_id)
    records: list[str] = []
    for report in reports:
        path = execution_log_path(state_dir, state_key, report.execution_id)
        records.append(_execution_header(report.execution_id, report.result.conclusion))
        records.extend(_read_file(path, lines))
    if active is not None and active not in known:
        path = execution_log_path(state_dir, state_key, active)
        records.append(_execution_header(active, None))
        records.extend(_read_file(path, lines))
    return ticket_id, tuple(records)


def follow_ticket(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    ticket_id: str,
    lines: int,
    workflow_paths: dict[str, str],
) -> int:
    """Follow all executions for a ticket until it reaches its terminal state."""
    lines = _lines(lines)
    printed: set[str] = set()
    while True:
        state = _ticket_observation(control_worktree, ticket_id, workflow_paths)
        reports = _ticket_reports(control_worktree, ticket_id)
        active = _active_ticket_execution(state_dir, state_key, ticket_id)
        for report in reports:
            if report.execution_id in printed:
                continue
            # The active execution is read through the streaming cursor below.
            # This keeps its tail and cursor position from being separated.
            if active == report.execution_id and state != "done":
                continue
            path = execution_log_path(state_dir, state_key, report.execution_id)
            print(
                _execution_header(report.execution_id, report.result.conclusion),
                end="",
            )
            print("".join(_read_file(path, lines)), end="", flush=True)
            printed.add(report.execution_id)
        # Accepted is an intermediate integration boundary. Keep observing until
        # the ticket reaches the canonical workflow terminal state.
        if state == "done":
            return 0
        if active is not None:
            if active in printed:
                try:
                    time.sleep(0.1)
                except KeyboardInterrupt:
                    return 130
                continue
            if active not in printed:
                report = next(
                    (item for item in reports if item.execution_id == active), None
                )
                conclusion = report.result.conclusion if report is not None else None
                print(_execution_header(active, conclusion), end="", flush=True)
            result = _follow_execution(
                state_dir,
                state_key,
                control_worktree,
                active,
                lines,
                ticket_id=ticket_id,
            )
            if result != 0:
                return result
            printed.add(active)
            continue
        try:
            time.sleep(0.1)
        except KeyboardInterrupt:
            return 130


def journal_command(unit: str, lines: int, follow: bool) -> list[str]:
    command = [
        "journalctl",
        "--user-unit",
        unit,
        "--no-pager",
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
    cursor = _LogCursor(path, lines)
    try:
        print("".join(cursor.initial), end="")
        while True:
            records = cursor.read_available()
            if records:
                print("".join(records), end="")
            else:
                time.sleep(0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        cursor.close()


def _execution_active(state_dir: Path, state_key: str, execution: str) -> bool:
    try:
        state = SQLiteRuntimeStore(state_dir, state_key).load()
    except RuntimeStoreError as error:
        raise LogReaderError(str(error)) from error
    return (
        isinstance(state, dict)
        and state.get("phase") in {"agent_pending", "agent_running"}
        and state.get("execution_id") == execution
    )


def _execution_report(
    control_worktree: Path, execution: str, ticket_id: str | None = None
):
    store = ExecutionReportStore(control_worktree)
    if ticket_id is not None:
        try:
            report_path = store._path(ticket_id, execution)
        except ExecutionReportError as error:
            raise LogReaderError(str(error)) from error
        root = control_worktree / "executions"
        ticket_root = report_path.parent
        try:
            for path, description in (
                (control_worktree, "control worktree root"),
                (root, "execution report root"),
                (ticket_root, "execution report ticket directory"),
            ):
                try:
                    path_stat = path.lstat()
                except FileNotFoundError:
                    return None
                except OSError as error:
                    raise LogReaderError(
                        f"cannot inspect {description}"
                    ) from error
                if path.is_symlink() or not stat.S_ISDIR(path_stat.st_mode):
                    raise LogReaderError(f"{description} is unsafe")

            try:
                report_path.lstat()
            except FileNotFoundError:
                return None
            except OSError as error:
                raise LogReaderError("cannot inspect execution report") from error
            return store.read(ticket_id, execution)
        except ExecutionReportError as error:
            raise LogReaderError(str(error)) from error

    root = control_worktree / "executions"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise LogReaderError("execution report root is unsafe")
    candidates = []
    if root.is_dir():
        for ticket_root in root.iterdir():
            if ticket_root.is_symlink() or not ticket_root.is_dir():
                raise LogReaderError("execution report ticket directory is unsafe")
            candidate = ticket_root / f"{execution}.json"
            if candidate.exists() or candidate.is_symlink():
                candidates.append((ticket_root.name, candidate))
    if len(candidates) > 1:
        raise LogReaderError(f"execution identity is ambiguous: {execution}")
    if not candidates:
        return None
    try:
        return store.read(candidates[0][0], execution)
    except ExecutionReportError as error:
        raise LogReaderError(str(error)) from error


def _execution_terminal(
    control_worktree: Path, execution: str, ticket_id: str | None = None
) -> bool:
    return _execution_report(control_worktree, execution, ticket_id) is not None


def _follow_execution(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    execution: str,
    lines: int,
    *,
    ticket_id: str | None = None,
) -> int:
    """Follow one immutable execution until its durable report and log EOF."""
    lines = _lines(lines)
    try:
        path = execution_log_path(state_dir, state_key, execution)
    except OperationalLogError as error:
        raise LogReaderError(str(error)) from error
    cursor = None
    try:
        while cursor is None:
            terminal = _execution_terminal(control_worktree, execution, ticket_id)
            active = _execution_active(state_dir, state_key, execution)
            if path.is_file():
                cursor = _LogCursor(path, lines)
                print("".join(cursor.initial), end="", flush=True)
                break
            if terminal or not active:
                if terminal:
                    raise LogReaderError(f"execution log not found: {path}")
                raise LogReaderError(
                    f"execution {execution} is no longer active and has no report"
                )
            time.sleep(0.1)

        while True:
            records = cursor.read_available()
            if records:
                print("".join(records), end="", flush=True)
                continue
            if _execution_terminal(control_worktree, execution, ticket_id):
                # The report is the lifecycle boundary; consume everything that
                # was published before or during terminalization, then stop.
                time.sleep(0.1)
                print("".join(cursor.read_available()), end="", flush=True)
                return 0
            if not _execution_active(state_dir, state_key, execution):
                time.sleep(0.1)
                continue
            time.sleep(0.1)
    except KeyboardInterrupt:
        return 130
    finally:
        if cursor is not None:
            cursor.close()


def follow_execution(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    execution: str,
    lines: int,
) -> int:
    """Follow one immutable execution until its durable report and log EOF."""
    return _follow_execution(
        state_dir, state_key, control_worktree, execution, lines
    )
