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
from devlegate.git import GitObservationError, history_changes, run_git
from devlegate.operational_log import OperationalLogError, execution_log_path
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore


class LogReaderError(RuntimeError):
    """A requested log could not be resolved or read."""


_TICKET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")


def _lines(value: int | None) -> int | None:
    if value is not None and value < 0:
        raise LogReaderError("--tail must be a non-negative integer")
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


def _active_state(state_dir: Path, state_key: str) -> dict | None:
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
    return state


def execution_id(
    control_worktree: Path,
    selector: str,
    *,
    state_dir: Path | None = None,
    state_key: str | None = None,
) -> str:
    return resolve_execution(control_worktree, selector, state_dir, state_key)[0]


def resolve_execution(control_worktree, selector, state_dir, state_key):
    if not selector or any(
        character not in "0123456789abcdef" for character in selector
    ):
        raise LogReaderError(
            "execution ID must be a lowercase hexadecimal ID or prefix"
        )
    state = None
    if state_dir is not None and state_key is not None:
        state = _active_state(state_dir, state_key)
    current = state["execution_id"] if state is not None else None
    if len(selector) == 32 and current == selector:
        owner = state.get("execution_ticket_id")
        if not isinstance(owner, str) or not _TICKET_ID.fullmatch(owner):
            owner = None
        report = _execution_report(control_worktree, selector, owner)
        if owner is None and report is not None:
            owner = report.ticket_id
        return selector, owner, report
    candidates = _report_candidates(control_worktree, selector)
    identities = [identity for _, identity in candidates]
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
    selected = matches[0]
    owners = [owner for owner, identity in candidates if identity == selected]
    if len(owners) > 1:
        raise LogReaderError(f"execution identity is ambiguous: {selected}")
    owner = owners[0] if owners else None
    if selected == current and state is not None:
        active_owner = state.get("execution_ticket_id")
        if isinstance(active_owner, str) and _TICKET_ID.fullmatch(active_owner):
            owner = active_owner
    report = _execution_report(control_worktree, selected, owner) if owner else None
    if selected != current and report is None:
        raise LogReaderError(f"execution not found: {selector}")
    return selected, owner, report


def _report_candidates(
    control_worktree: Path, selector: str
) -> tuple[tuple[str, str], ...]:
    """Enumerate matching paths without deserializing unrelated reports."""
    root = control_worktree / "executions"
    if root.is_symlink() or (root.exists() and not root.is_dir()):
        raise LogReaderError("execution report root is unsafe")
    candidates: set[tuple[str, str]] = set()
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
                    candidates.add((ticket_root.name, path.stem))
    except OSError as error:
        raise LogReaderError(f"cannot enumerate execution reports: {error}") from error
    return tuple(sorted(candidates))


class _LogCursor:
    """Read a log tail and continue from the exact byte position it used."""

    def __init__(self, path: Path, lines: int | None) -> None:
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
        if self.lines is None:
            handle.seek(0)
            self.initial = tuple(
                record.decode("utf-8")
                for record in handle.read(size).splitlines(keepends=True)
            )
            return
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


def _read_file(path: Path, lines: int | None) -> Iterable[str]:
    cursor = _LogCursor(path, lines)
    try:
        return cursor.initial
    finally:
        cursor.close()


def execution_log(
    state_dir: Path, state_key: str, control_worktree: Path, selector: str, lines: int,
    *, markers: bool = False,
) -> tuple[str, Iterable[str]]:
    resolved, _, report = resolve_execution(
        control_worktree, selector, state_dir, state_key
    )
    try:
        path = execution_log_path(state_dir, state_key, resolved)
    except OperationalLogError as error:
        raise LogReaderError(str(error)) from error
    content = tuple(_read_file(path, lines))
    if markers:
        status = report.result.conclusion if report else "active/nonterminal"
        content += (
            f"\n===== execution {resolved}: snapshot complete; {status} =====\n",
        )
    return resolved, content


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


def _ordered_ticket_reports(control_worktree, ticket_id, active=None, order=()):
    reports = _ticket_reports(control_worktree, ticket_id)
    if len(reports) < 2:
        return reports
    by_id = {report.execution_id: report for report in reports}
    if len(order) == len(reports) and set(order) == set(by_id):
        return [by_id[execution] for execution in order]
    by_path = {
        f"executions/{ticket_id}/{report.execution_id}.json": report
        for report in reports
    }
    def first_parent_history(cwd, *args, **kwargs):
        return run_git(cwd, args[0], "--first-parent", *args[1:], **kwargs)

    try:
        history = history_changes(
            control_worktree, "HEAD", f"executions/{ticket_id}",
            runner=first_parent_history,
        )
    except (GitObservationError, OSError) as error:
        raise LogReaderError("cannot establish execution chronology") from error
    ordered = []
    seen = set()
    for _, changes in reversed(history):
        touched = [change for change in changes if change[2:] in by_path]
        if len(touched) > 1:
            raise LogReaderError("ambiguous execution chronology")
        for change in touched:
            path = change[2:]
            if not change.startswith("A\t") or path in seen:
                raise LogReaderError("ambiguous execution chronology")
            seen.add(path)
            ordered.append(by_path[path])
    missing = [report for path, report in by_path.items() if path not in seen]
    if len(missing) == 1 and missing[0].execution_id == active:
        ordered.extend(missing)
    elif missing:
        raise LogReaderError("cannot establish execution chronology")
    return ordered


class _TicketReportObservation:
    """Reuse validated immutable reports while checking their filesystem evidence."""

    def __init__(self, control_worktree: Path, ticket_id: str):
        self.control_worktree = control_worktree
        self.ticket_id = ticket_id
        self.reports = None
        self.directory = None
        self.evidence = {}
        self.active = None

    @staticmethod
    def _stat(path: Path):
        try:
            value = path.lstat()
        except FileNotFoundError:
            return None
        except OSError as error:
            raise LogReaderError(
                f"cannot inspect execution evidence: {path}"
            ) from error
        return (value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_gid,
                value.st_size, value.st_mtime_ns, value.st_ctime_ns)

    def _directory(self):
        root = self.control_worktree / "executions"
        parent = self._stat(self.control_worktree)
        root_stat = self._stat(root)
        ticket_stat = self._stat(root / self.ticket_id)
        for value in (parent, root_stat, ticket_stat):
            if value is not None and not stat.S_ISDIR(value[2]):
                raise LogReaderError("execution report directory is unsafe")
        # Changes in unrelated ticket directories do not invalidate this ticket.
        return (parent[:5] if parent else None, root_stat[:5] if root_stat else None,
                ticket_stat)

    def _evidence(self, reports):
        root = self.control_worktree / "executions" / self.ticket_id
        result = {}
        for report in reports:
            path = root / f"{report.execution_id}.json"
            result[path] = self._stat(path)
            if report.report_artifact is not None:
                artifact = path.with_suffix(".md")
                result[artifact] = self._stat(artifact)
        return result

    def _candidate_evidence(self):
        root = self.control_worktree / "executions" / self.ticket_id
        try:
            paths = tuple(root.glob("*.json")) if root.is_dir() else ()
            return {
                candidate: self._stat(candidate)
                for path in paths
                for candidate in (path, path.with_suffix(".md"))
            }
        except OSError as error:
            raise LogReaderError("cannot enumerate execution evidence") from error

    def observe(self, active):
        directory = self._directory()
        if self.reports is not None:
            if self._evidence(self.reports) != self.evidence:
                raise LogReaderError(
                    "execution report evidence changed or is inaccessible"
                )
            if directory == self.directory and active == self.active:
                return self.reports
        before = self._candidate_evidence()
        reports = _ordered_ticket_reports(self.control_worktree, self.ticket_id, active)
        evidence = self._evidence(reports)
        if (
            any(value is None for value in evidence.values())
            or self._directory() != directory
            or self._candidate_evidence() != before
        ):
            raise LogReaderError("execution report evidence changed during observation")
        self.reports, self.directory, self.evidence = reports, directory, evidence
        self.active = active
        return reports


def _active_ticket_execution(
    state_dir: Path, state_key: str, ticket_id: str
) -> str | None:
    state = _active_state(state_dir, state_key)
    if state is None or state.get("execution_ticket_id") != ticket_id:
        return None
    return state["execution_id"]


def _execution_header(execution: str, conclusion: str | None) -> str:
    detail = f" conclusion={conclusion}" if conclusion is not None else " state=active"
    return f"\n===== execution {execution}{detail} =====\n"


def _ticket_selection(
    state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths,
    *, allow_pending_log=False, observation=None,
):
    """Select one chronological snapshot, with a single global line budget."""
    state = _ticket_observation(control_worktree, ticket_id, workflow_paths)
    active = _active_ticket_execution(state_dir, state_key, ticket_id)
    reports = (
        observation.observe(active) if observation is not None
        else _ordered_ticket_reports(control_worktree, ticket_id, active)
    )
    entries = [(r.execution_id, r.result.conclusion) for r in reports]
    if active is not None:
        entries.sort(key=lambda entry: entry[0] == active)
    if active is not None and active not in {item[0] for item in entries}:
        entries.append((active, None))
    selected = []
    remaining = _lines(lines)
    active_cursor = None
    try:
        for execution, conclusion in reversed(entries):
            path = execution_log_path(state_dir, state_key, execution)
            if allow_pending_log and execution == active and not path.is_file():
                selected.append((execution, conclusion, ()))
                continue
            if remaining == 0 and execution != active:
                if not path.is_file():
                    raise LogReaderError(f"execution log not found: {path}")
                selected.append((execution, conclusion, ()))
                continue
            cursor = _LogCursor(path, remaining)
            records = cursor.initial
            if execution == active:
                active_cursor = cursor
            else:
                cursor.close()
            selected.append((execution, conclusion, records))
            if remaining is not None:
                remaining -= len(records)
        return state, active, list(reversed(selected)), active_cursor
    except BaseException:
        if active_cursor is not None:
            active_cursor.close()
        raise


def ticket_log(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    ticket_id: str,
    lines: int,
    workflow_paths: dict[str, str],
    *, markers: bool = True, final_marker: bool = False,
) -> tuple[str, Iterable[str]]:
    """Read every durable execution log belonging to one ticket."""
    state, _, selected, cursor = _ticket_selection(
        state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths
    )
    if cursor is not None:
        cursor.close()
    records: list[str] = []
    for execution, conclusion, content in selected:
        if markers:
            records.append(_execution_header(execution, conclusion))
        records.extend(content)
    if final_marker and markers:
        details = {
            "done": "done (integrated)",
            "accepted": "accepted (awaiting integration)",
            "review": "review (awaiting review)",
            "todo": "todo (pending work or rework)",
            "backlog": "backlog",
        }
        status = details.get(state, state)
        if selected:
            execution, conclusion, _ = selected[-1]
            status += f"; execution {execution}: {conclusion or 'active/nonterminal'}"
        records.append(
            f"\n===== ticket {ticket_id}: snapshot complete; {status} =====\n"
        )
    return ticket_id, tuple(records)


def follow_ticket(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    ticket_id: str,
    lines: int,
    workflow_paths: dict[str, str],
    *, markers: bool = True, final_marker: bool = False,
) -> int:
    """Follow all executions for a ticket until it reaches its terminal state."""
    lines = _lines(lines)
    observation = _TicketReportObservation(control_worktree, ticket_id)
    state, active, selected, cursor = _ticket_selection(
        state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths,
        allow_pending_log=True, observation=observation,
    )
    printed: set[str] = set()
    for execution, conclusion, records in selected:
        if markers:
            print(_execution_header(execution, conclusion), end="")
        print("".join(records), end="", flush=True)
        if execution != active or state == "done":
            printed.add(execution)
    if state == "done" and active is not None and any(
        execution == active and conclusion is None
        for execution, conclusion, _ in selected
    ):
        if cursor is not None:
            cursor.close()
        raise LogReaderError(f"execution {active} has no durable report")
    if cursor is not None or (active is not None and state != "done"):
        if state == "done":
            cursor.close()
        else:
            result = _follow_execution(
                state_dir, state_key, control_worktree, active, lines,
                ticket_id=ticket_id, cursor=cursor,
            )
            if result != 0:
                return result
            printed.add(active)
    while True:
        state = _ticket_observation(control_worktree, ticket_id, workflow_paths)
        active = _active_ticket_execution(state_dir, state_key, ticket_id)
        reports = observation.observe(active)
        for report in reports:
            if report.execution_id in printed:
                continue
            # The active execution is read through the streaming cursor below.
            # This keeps its tail and cursor position from being separated.
            if active == report.execution_id and state != "done":
                continue
            path = execution_log_path(state_dir, state_key, report.execution_id)
            if markers:
                print(
                    _execution_header(report.execution_id, report.result.conclusion),
                    end="",
                )
            print("".join(_read_file(path, None)), end="", flush=True)
            printed.add(report.execution_id)
        # Accepted is an intermediate integration boundary. Keep observing until
        # the ticket reaches the canonical workflow terminal state.
        if state == "done":
            if active is not None and active not in printed:
                raise LogReaderError(f"execution {active} has no durable report")
            if markers and final_marker:
                print(f"\n===== ticket {ticket_id}: done (integrated) =====")
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
                if markers:
                    print(_execution_header(active, conclusion), end="", flush=True)
            result = _follow_execution(
                state_dir,
                state_key,
                control_worktree,
                active,
                None,
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


def journal_command(unit: str, lines: int | None, follow: bool) -> list[str]:
    command = [
        "journalctl",
        "--user-unit",
        unit,
        "--no-pager",
    ]
    if lines is None:
        command.append("--no-tail")
    else:
        command.extend(("--lines", str(_lines(lines))))
    if follow:
        command.append("--follow")
    return command


def stream_journal(
    unit: str,
    lines: int | None,
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


def _execution_active(state_dir: Path, state_key: str, execution: str) -> bool:
    state = _active_state(state_dir, state_key)
    return state is not None and state["execution_id"] == execution


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

    candidates = [
        (ticket, identifier)
        for ticket, identifier in _report_candidates(control_worktree, execution)
        if identifier == execution
    ]
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
    cursor: _LogCursor | None = None,
    markers: bool = False,
) -> int:
    """Follow one immutable execution until its durable report and log EOF."""
    lines = _lines(lines)
    try:
        path = execution_log_path(state_dir, state_key, execution)
    except OperationalLogError as error:
        raise LogReaderError(str(error)) from error
    try:
        if ticket_id is None:
            state = _active_state(state_dir, state_key)
            if state is not None and state["execution_id"] == execution:
                owner = state.get("execution_ticket_id")
                if isinstance(owner, str) and _TICKET_ID.fullmatch(owner):
                    ticket_id = owner
            if ticket_id is None:
                report = _execution_report(control_worktree, execution)
                if report is not None:
                    ticket_id = report.ticket_id
        # If the file does not yet exist, everything it later contains arrived
        # after follow began and is continuation, not an initial tail.
        pending_log = cursor is None and not path.is_file()
        while cursor is None:
            if path.is_file():
                cursor = _LogCursor(path, None if pending_log else lines)
                print("".join(cursor.initial), end="", flush=True)
                break
            terminal = _execution_terminal(control_worktree, execution, ticket_id)
            active = _execution_active(state_dir, state_key, execution)
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
            report = _execution_report(control_worktree, execution, ticket_id)
            if report is not None:
                # The report is the lifecycle boundary; consume everything that
                # was published before or during terminalization, then stop.
                time.sleep(0.1)
                print("".join(cursor.read_available()), end="", flush=True)
                if markers:
                    conclusion = report.result.conclusion
                    print(f"\n===== execution {execution}: {conclusion} =====")
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
    *, markers: bool = False, ticket_id: str | None = None,
) -> int:
    """Follow one immutable execution until its durable report and log EOF."""
    return _follow_execution(
        state_dir, state_key, control_worktree, execution, lines,
        markers=markers, ticket_id=ticket_id,
    )
