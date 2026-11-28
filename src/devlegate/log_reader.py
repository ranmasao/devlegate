# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read operator-facing service and execution logs."""

from __future__ import annotations

import hashlib
import json
import re
import stat
import subprocess
import time
from collections.abc import Callable, Iterable
from pathlib import Path

from devlegate.abandonment_evidence import EvidenceError
from devlegate.clean_preview import _blob, _tree, observe_git
from devlegate.execution_result import (
    ExecutionReport,
    ExecutionReportError,
    ExecutionReportStore,
)
from devlegate.git import GitObservationError, file_history_commit, history_changes
from devlegate.operational_log import OperationalLogError, execution_log_path
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore


class LogReaderError(RuntimeError):
    """A requested log could not be resolved or read."""


_TICKET_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_POLL_INTERVAL = 0.1
_sleep = time.sleep


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


def _log_exists(path: Path) -> bool:
    try:
        mode = path.stat().st_mode
    except FileNotFoundError:
        return False
    except OSError as error:
        raise LogReaderError(f"cannot inspect execution log {path}: {error}") from error
    if not stat.S_ISREG(mode):
        raise LogReaderError(f"execution log is not a regular file: {path}")
    return True


def observe_execution_log(state_dir: Path, state_key: str, execution_id: str) -> dict:
    """Observe the canonical local log for an already resolved execution."""
    path = None
    try:
        path = execution_log_path(state_dir, state_key, execution_id)
        if path.resolve() != path:
            raise LogReaderError("canonical execution log path contains a symlink")
        status = "present" if _log_exists(path) else "missing"
    except (OperationalLogError, LogReaderError, OSError, RuntimeError) as error:
        return {"path": str(path) if path is not None else None,
                "status": "unavailable", "reason": str(error)}
    return {"path": str(path), "status": status}


class _LogCursor:
    """Read a log tail and continue from the exact byte position it used."""

    def __init__(self, path: Path, lines: int | None) -> None:
        self.path = path
        self.lines = _lines(lines)
        if not _log_exists(path):
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
        try:
            mode = path.lstat().st_mode
        except FileNotFoundError:
            continue
        except OSError as error:
            raise LogReaderError(f"cannot inspect ticket state: {path}") from error
        if not stat.S_ISREG(mode):
            raise LogReaderError(f"ticket state is unsafe: {path}")
        locations.append(state)
    return _unique_ticket_state(ticket_id, locations)


def _unique_ticket_state(ticket_id, locations):
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


def _ordered_ticket_reports(control_worktree, ticket_id, active=None):
    reports = _ticket_reports(control_worktree, ticket_id)
    if len(reports) < 2:
        return reports
    by_path = {
        f"executions/{ticket_id}/{report.execution_id}.json": report
        for report in reports
    }
    history = _report_history(control_worktree, "HEAD", f"executions/{ticket_id}")
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


def _first_parent_git(cwd, command, *args, **kwargs):
    return observe_git(cwd, command, "--first-parent", *args, **kwargs)


def _report_history(control, head, scope):
    try:
        return history_changes(control, head, scope, runner=_first_parent_git)
    except (GitObservationError, OSError, subprocess.SubprocessError) as error:
        raise LogReaderError("cannot establish execution chronology") from error


class _TicketControlObservation:
    """Read ticket location, report identities and chronology at one control commit."""

    def __init__(self, control, ticket_id, workflow_paths):
        if not _TICKET_ID.fullmatch(ticket_id):
            raise LogReaderError(f"invalid ticket ID: {ticket_id}")
        self.control = control
        self.ticket_id = ticket_id
        self.locations = tuple(
            (f"{path}/{ticket_id}.md", state) for state, path in workflow_paths.items()
        )
        self.scope = f"executions/{ticket_id}"
        self.head = None
        self.report_tip = None
        self.inventory = {}
        self.publications = ()
        self.reports = ()

    def observe(self):
        try:
            head = observe_git(
                self.control, "rev-parse", "--verify", "HEAD^{commit}",
            ).stdout.strip()
            if head == self.head:
                return self.state, self.reports
            if not re.fullmatch(r"[0-9a-f]{40}|[0-9a-f]{64}", head):
                raise LogReaderError("invalid control commit identity")
            tree = _tree(self.control, head, *(path for path, _ in self.locations),
                         self.scope)
        except (
            EvidenceError, OSError, UnicodeError, subprocess.SubprocessError,
        ) as error:
            raise LogReaderError(
                f"cannot read committed ticket or log evidence: {error}"
            ) from error
        locations = [state for path, state in self.locations if path in tree]
        state = _unique_ticket_state(self.ticket_id, locations)
        if self.scope in tree:
            raise LogReaderError(f"committed report directory is unsafe: {self.scope}")
        inventory = {
            path: oid for path, oid in tree.items() if path.startswith(self.scope + "/")
        }
        if self.inventory.items() - inventory.items():
            raise LogReaderError(
                "committed execution report evidence changed or was removed"
            )

        # A workflow-only advance need not walk the report history again. Compare
        # the publication commit even when the tree bytes match, to catch rewrites.
        try:
            tip = file_history_commit(
                self.control, head, self.scope, runner=_first_parent_git,
            )
        except (GitObservationError, OSError, subprocess.SubprocessError) as exc:
            raise LogReaderError("cannot establish execution chronology") from exc
        if tip == self.report_tip and inventory == self.inventory:
            self.head, self.state = head, state
            return state, self.reports

        publications = []
        seen = set()
        if tip is not None:
            history = _report_history(self.control, tip, self.scope)
            for commit, changes in reversed(history):
                added = []
                for change in changes:
                    status, path = change.split("\t", 1)
                    if path not in inventory or status != "A" or path in seen:
                        raise LogReaderError(f"invalid execution chronology: {path}")
                    seen.add(path)
                    if path.endswith(".json"):
                        added.append(path)
                if len(added) > 1:
                    raise LogReaderError("ambiguous execution chronology")
                if added:
                    publications.append((commit, added[0]))
        previous_count = len(self.publications)
        if (seen != inventory.keys()
                or tuple(publications[:previous_count]) != self.publications):
            raise LogReaderError("execution chronology changed or is incomplete")

        reports = self.reports + tuple(
            self._read_report(path, inventory)
            for _, path in publications[previous_count:]
        )
        self.head, self.state, self.inventory = head, state, inventory
        self.publications, self.reports = tuple(publications), reports
        self.report_tip = tip
        return state, reports

    def _read_report(self, path, inventory):
        try:
            payload = _blob(self.control, inventory[path])
            report = ExecutionReport.from_dict(json.loads(payload))
            if (path != f"{self.scope}/{report.execution_id}.json"
                    or report.ticket_id != self.ticket_id):
                raise ExecutionReportError("report identity does not match path")
            if report.report_artifact is not None:
                oid = inventory.get(report.report_artifact)
                if oid is None:
                    raise ExecutionReportError("execution report artifact is missing")
                content = _blob(self.control, oid)
                digest = hashlib.sha256(content.encode("utf-8")).hexdigest()
                if digest != report.report_sha256:
                    raise ExecutionReportError("report artifact integrity mismatch")
        except (ExecutionReportError, OSError, UnicodeError, json.JSONDecodeError,
                subprocess.SubprocessError) as error:
            raise LogReaderError(
                f"cannot read committed execution report {path}: {error}"
            ) from error
        return report


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
    *, observation=None,
):
    """Select one chronological snapshot, with a single global line budget."""
    active = _active_ticket_execution(state_dir, state_key, ticket_id)
    if observation is None:
        state = _ticket_observation(control_worktree, ticket_id, workflow_paths)
        reports = _ordered_ticket_reports(control_worktree, ticket_id, active)
    else:
        state, reports = observation.observe()
    entries = [(r.execution_id, r.result.conclusion) for r in reports]
    if active is not None and active not in {item[0] for item in entries}:
        entries.append((active, None))
    selected = []
    remaining = _lines(lines)
    active_cursor = None
    try:
        for execution, conclusion in reversed(entries):
            path = execution_log_path(state_dir, state_key, execution)
            if (observation is not None and execution == active
                    and not _log_exists(path)):
                selected.append((execution, conclusion, ()))
                continue
            if remaining == 0 and execution != active:
                if not _log_exists(path):
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
    observation = _TicketControlObservation(control_worktree, ticket_id, workflow_paths)
    try:
        return _follow_ticket(
            state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths,
            markers=markers, final_marker=final_marker, observation=observation,
        )
    except KeyboardInterrupt:
        return 130


def _follow_ticket(
    state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths,
    *, markers, final_marker, observation,
):
    _, active, selected, cursor = _ticket_selection(
        state_dir, state_key, control_worktree, ticket_id, lines, workflow_paths,
        observation=observation,
    )
    terminal_states = {"done": "done (integrated)", "abandoned": "abandoned"}
    printed: set[str] = set()
    current = active
    pending: dict[str, None] = {}

    def emit(execution, conclusion, records):
        if markers:
            print(_execution_header(execution, conclusion), end="")
        print("".join(records), end="", flush=True)

    def drain_current(conclusions, *, ended=False):
        nonlocal cursor
        if current is None:
            return
        if cursor is None:
            path = execution_log_path(state_dir, state_key, current)
            conclusion = conclusions.get(current)
            if not _log_exists(path):
                if ended and conclusion is not None:
                    raise LogReaderError(f"execution log not found: {path}")
                return
            # A file created after the snapshot is all continuation, even -t 0.
            cursor = _LogCursor(path, None)
            emit(current, conclusion, cursor.initial)
        print("".join(cursor.read_available()), end="", flush=True)

    try:
        for execution, conclusion, records in selected:
            # Delay a pending log's header so it still precedes its own bytes.
            if execution != current or cursor is not None:
                emit(execution, conclusion, records)
            if execution != current:
                printed.add(execution)

        while True:
            active = _active_ticket_execution(state_dir, state_key, ticket_id)
            state, reports = observation.observe()
            terminal = state in terminal_states
            conclusions = {r.execution_id: r.result.conclusion for r in reports}
            later_reports = [
                execution for execution in conclusions
                if execution not in printed and execution not in (current, active)
            ]
            if current is not None and current != active:
                # Observe departure first, then drain the retiring log before
                # emitting any newer header. Retain the cursor through idle so
                # a restart or a late report cannot reopen it at the beginning.
                drain_current(conclusions, ended=terminal)
                if active is not None or later_reports:
                    if cursor is not None:
                        cursor.close()
                        cursor = None
                        printed.add(current)
                    else:
                        pending[current] = None
                    current = None

            for execution in tuple(pending):
                if execution == active or execution in later_reports:
                    del pending[execution]
                    continue
                path = execution_log_path(state_dir, state_key, execution)
                if _log_exists(path):
                    emit(execution, None, _read_file(path, None))
                    printed.add(execution)
                    del pending[execution]

            for execution in later_reports:
                path = execution_log_path(state_dir, state_key, execution)
                if not terminal and not _log_exists(path):
                    continue
                emit(execution, conclusions[execution], _read_file(path, None))
                printed.add(execution)

            if active is not None and active not in printed:
                current = active
                drain_current(conclusions, ended=terminal)

            if terminal:
                if markers and final_marker:
                    print(f"\n===== ticket {ticket_id}: {terminal_states[state]} =====")
                return 0
            _sleep(_POLL_INTERVAL)
    finally:
        if cursor is not None:
            cursor.close()


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


def follow_execution(
    state_dir: Path,
    state_key: str,
    control_worktree: Path,
    execution: str,
    lines: int | None,
    *,
    ticket_id: str | None = None,
    markers: bool = False,
) -> int:
    """Follow one execution through its report or departure from runtime state."""
    lines = _lines(lines)
    cursor = None
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
        if _log_exists(path):
            cursor = _LogCursor(path, lines)
            print("".join(cursor.initial), end="", flush=True)

        def drain():
            nonlocal cursor
            if cursor is None and _log_exists(path):
                cursor = _LogCursor(path, None)
                print("".join(cursor.initial), end="", flush=True)
            if cursor is not None:
                print("".join(cursor.read_available()), end="", flush=True)

        departed = False
        while True:
            drain()
            report = _execution_report(control_worktree, execution, ticket_id)
            if report is None:
                active = _execution_active(state_dir, state_key, execution)
                if active or not departed:
                    # Allow one re-observation for a report racing departure.
                    departed = not active
                    time.sleep(_POLL_INTERVAL)
                    continue
                report = _execution_report(control_worktree, execution, ticket_id)
                if report is None:
                    drain()
                    raise LogReaderError(
                        f"execution {execution} is no longer active and has no report"
                    )
            if cursor is None:
                drain()
                if cursor is None:
                    raise LogReaderError(f"execution log not found: {path}")
            # Give bytes racing terminal publication a final drain.
            time.sleep(_POLL_INTERVAL)
            drain()
            if markers:
                conclusion = report.result.conclusion
                print(f"\n===== execution {execution}: {conclusion} =====")
            return 0
    except KeyboardInterrupt:
        return 130
    finally:
        if cursor is not None:
            cursor.close()
