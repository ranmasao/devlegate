"""Runtime and orchestration implementation for Devlegate."""

import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Callable

from devlegate import __version__
from devlegate.agent_protocol import (
    AgentProtocolError,
    RenderContext,
    initialize_project,
    render_project,
)
from devlegate.execution_result import (
    ExecutionReport,
    ExecutionReportError,
    ExecutionReportStore,
    build_execution_report,
    new_execution_id,
)
from devlegate.execution_workspace import (
    ExecutionWorkspace,
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
    parse_worktree_porcelain,
)
from devlegate.project_context import ProjectContextError, load_project_context
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore
from devlegate.tickets import (
    TicketError,
    TicketStore,
    is_canonical_ticket_name,
    load_ticket_store,
)
from devlegate.worker_egress import (
    OpenCodeRunResult,
    WorkerEgressParser,
    WorkerRunResult,
)
from devlegate.worker_prompt import (
    WorkDirective,
    WorkerPromptInput,
    build_worker_prompt,
)


class DevlegateError(Exception):
    """A user-facing startup error."""


class WorkflowBlockedError(DevlegateError):
    """The repository cannot currently be interpreted safely for scheduling."""


class SnapshotChanged(Exception):
    """The observed project changed during a status snapshot attempt."""


_UNSET = object()


@dataclasses.dataclass(frozen=True)
class GitObservation:
    branch: str | None
    detached: bool
    local_head: str
    remote_ref: str
    remote_head: str | None
    working_tree_clean: bool
    working_tree_fingerprint: str

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


@dataclasses.dataclass(frozen=True)
class ExecutionPlan:
    """The immutable scheduling decision for one observed project state."""

    action: str
    reason: str
    ticket_id: str | None = None
    ticket_title: str | None = None
    ticket_state: str | None = None
    bound: bool = False
    code: GitObservation | None = None
    control: GitObservation | None = None

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reason": self.reason,
            "ticket": (
                {
                    "id": self.ticket_id,
                    "title": self.ticket_title,
                    "state": self.ticket_state,
                }
                if self.ticket_id is not None
                else None
            ),
            "bound": self.bound,
            "observation": {
                "code": self.code.as_dict() if self.code else None,
                "control": self.control.as_dict() if self.control else None,
            },
        }


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    phase: str
    bound_ticket_id: str | None
    persisted_body_present: bool
    code: GitObservation
    control: GitObservation | None
    counts: tuple[tuple[str, int], ...]
    runnable: tuple[tuple[str, str], ...]
    blocked: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]
    review: tuple[tuple[str, str], ...]
    accepted: tuple[tuple[str, str], ...]
    next_ticket: tuple[str, str] | None
    plan: ExecutionPlan
    failed_executions: tuple["FailedExecution", ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "execution": {
                "phase": self.phase,
                "bound_ticket": self.bound_ticket_id,
                "persisted_body": self.persisted_body_present,
            },
            "observation": {
                "code": self.code.as_dict(),
                "control": self.control.as_dict() if self.control else None,
            },
            "tickets": {
                "counts": dict(self.counts),
                "runnable": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.runnable
                ],
                "blocked": [
                    {
                        "id": ticket_id,
                        "title": title,
                        "blocked_by": [
                            {"id": dependency_id, "state": state}
                            for dependency_id, state in blockers
                        ],
                    }
                    for ticket_id, title, blockers in self.blocked
                ],
                "review": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.review
                ],
                "accepted": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.accepted
                ],
                "next": (
                    {"id": self.next_ticket[0], "title": self.next_ticket[1]}
                    if self.next_ticket is not None
                    else None
                ),
            },
            "plan": self.plan.as_dict(),
            "failed_executions": [
                failure.as_dict() for failure in self.failed_executions
            ],
        }


@dataclasses.dataclass(frozen=True)
class ServiceSnapshot:
    """Immutable, last-published observation of one live service owner."""

    lifecycle: str
    phase: str
    execution_stage: str | None
    worker_running: bool
    selected_ticket_id: str | None
    execution_id: str | None
    product: GitObservation | None
    control: GitObservation | None
    counts: tuple[tuple[str, int], ...]
    runnable: tuple[tuple[str, str], ...]
    review: tuple[tuple[str, str], ...]
    accepted: tuple[tuple[str, str], ...]
    blocked_reason: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "service": {
                "lifecycle": self.lifecycle,
                "worker_running": self.worker_running,
            },
            "runtime": {
                "phase": self.phase,
                "execution_stage": self.execution_stage,
                "selected_ticket_id": self.selected_ticket_id,
                "execution_id": self.execution_id,
            },
            "observation": {
                "product": self.product.as_dict() if self.product else None,
                "control": self.control.as_dict() if self.control else None,
            },
            "workflow": {
                "counts": dict(self.counts),
                "runnable": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.runnable
                ],
                "review": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.review
                ],
                "accepted": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.accepted
                ],
            },
            "blocked_reason": self.blocked_reason,
        }


@dataclasses.dataclass(frozen=True)
class FailedExecution:
    ticket_id: str
    title: str
    reason: str
    retryable: bool
    nonretryable_reason: str | None = None
    interruption_kind: str | None = None

    @property
    def display_reason(self) -> str:
        return (
            self.reason
            if self.retryable
            else (self.nonretryable_reason or "unknown")
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "id": self.ticket_id,
            "title": self.title,
            "reason": self.reason,
            "retryable": self.retryable,
            "nonretryable_reason": self.nonretryable_reason,
            "interruption_kind": self.interruption_kind,
        }


@dataclasses.dataclass(frozen=True)
class RetryCandidate:
    """Read-only interactive retry option; not persisted runtime state."""

    ticket_id: str
    title: str
    reason: str
    kind: str


def _workflow_fingerprint(repo: Path, workflow_paths: dict[str, str]) -> str:
    entries: list[str] = []
    for state in ("backlog", "todo", "review", "accepted", "done"):
        directory = repo / workflow_paths[state]
        if directory.is_dir():
            entries.append(f"{state}\0dir\n")
        elif directory.exists():
            entries.append(f"{state}\0non-directory\n")
        else:
            entries.append(f"{state}\0missing\n")
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not is_canonical_ticket_name(path.name):
                continue
            if path.is_symlink():
                kind = "symlink"
                digest = ""
            elif path.is_file():
                kind = "file"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                kind = "other"
                digest = ""
            entries.append(f"{state}/{path.name}\0{kind}\0{digest}\n")
    return hashlib.sha256("".join(entries).encode()).hexdigest()


def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise DevlegateError(
            f"cannot read configuration file: {path}: {error}"
        ) from error
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip().replace("_", "a").isalnum():
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name.strip()] = value
    return values


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {message}")


@contextmanager
def _preserve_terminal():
    terminal_fd = None
    terminal_state = None
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
            if not os.isatty(fd):
                continue
            terminal_state = termios.tcgetattr(fd)
            terminal_fd = fd
            break
        except (OSError, ValueError):
            continue
    try:
        yield
    finally:
        if terminal_fd is not None and terminal_state is not None:
            try:
                termios.tcsetattr(terminal_fd, termios.TCSANOW, terminal_state)
            except OSError as error:
                _log(f"could not restore terminal state: {error}")


def _render_worker_text(text: str) -> str:
    """Make worker-controlled text inert before it reaches an operator TTY."""
    rendered: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character in "\n\t":
            rendered.append(character)
        elif 0x20 <= codepoint <= 0x7E or character.isprintable():
            rendered.append(character)
        elif codepoint <= 0x7F:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0x9F:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\u{codepoint:04x}")
    return "".join(rendered)


_OPENCODE_JSON_TYPES = {
    "error",
    "reasoning",
    "step_finish",
    "step_start",
    "text",
    "tool_use",
}


def _write_worker_text(text: str, stream) -> None:
    rendered = _render_worker_text(text)
    if not rendered:
        return
    stream.write(rendered)
    stream.flush()


def _extract_error_message(error: object) -> str | None:
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if isinstance(message, str):
        return message
    data = error.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str):
            return message
    name = error.get("name")
    return name if isinstance(name, str) else None


def _write_worker_line(text: str, stream) -> None:
    _write_worker_text(text + ("" if text.endswith("\n") else "\n"), stream)


# Caps raw JSONL events before decode and parsing while leaving normal events intact.
MAX_STDOUT_EVENT_BYTES = 1024 * 1024
WORKER_TERMINATION_TIMEOUT = 1.0
WORKER_WAIT_INTERVAL = 0.05


def _run_opencode(
    command: list[str],
    prompt: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    event_handler: Callable[[object], None] | None = None,
    stop_request: object | None = None,
) -> OpenCodeRunResult:
    """Run OpenCode headlessly and render its worker output as inert text."""
    process = subprocess.Popen(
        command + [prompt],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    output_lock = threading.Lock()
    transport_error: str | None = None

    def write(text: str, stream) -> None:
        with output_lock:
            _write_worker_text(text, stream)

    def consume_stdout() -> None:
        nonlocal transport_error
        assert process.stdout is not None
        while raw_line := process.stdout.readline(MAX_STDOUT_EVENT_BYTES + 1):
            if len(raw_line) > MAX_STDOUT_EVENT_BYTES:
                transport_error = "stdout event exceeds maximum size"
                _log(f"OpenCode protocol error: {transport_error}")
                if not raw_line.endswith(b"\n"):
                    while discarded := process.stdout.readline(
                        MAX_STDOUT_EVENT_BYTES + 1
                    ):
                        if discarded.endswith(b"\n"):
                            break
                continue
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                transport_error = "invalid JSON event on stdout"
                _log(f"OpenCode protocol error: {transport_error}")
                continue
            if event_handler is not None:
                event_handler(event)
            if (
                not isinstance(event, dict)
                or event.get("type") not in _OPENCODE_JSON_TYPES
            ):
                transport_error = "unsupported event on stdout"
                _log(f"OpenCode protocol error: {transport_error}")
                continue
            event_type = event["type"]
            if event_type == "text" or event_type == "reasoning":
                part = event.get("part")
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    transport_error = "text event has no text part"
                    _log(f"OpenCode protocol error: {transport_error}")
                    continue
                write(part["text"] + "\n", sys.stdout)
            elif event_type == "tool_use":
                part = event.get("part")
                if (
                    not isinstance(part, dict)
                    or not isinstance(part.get("tool"), str)
                    or not isinstance(part.get("state"), dict)
                    or part["state"].get("status")
                    not in {"pending", "running", "completed", "error"}
                ):
                    transport_error = "invalid tool event on stdout"
                    _log(f"OpenCode protocol error: {transport_error}")
                    continue
                tool = part["tool"]
                write(f"OpenCode tool: {tool}\n", sys.stdout)
                state = part["state"]
                if state.get("status") == "completed":
                    output = state.get("output")
                    if isinstance(output, str):
                        _write_worker_line(output, sys.stdout)
                elif state.get("status") == "error":
                    error = _extract_error_message(state.get("error"))
                    if error:
                        _write_worker_line(f"OpenCode tool failed: {error}", sys.stdout)
            elif event_type == "error":
                error = _extract_error_message(event.get("error"))
                if error:
                    write(f"OpenCode error: {error}\n", sys.stdout)

    def consume_stderr() -> None:
        assert process.stderr is not None
        while raw_chunk := process.stderr.read(4096):
            write(raw_chunk.decode("utf-8", errors="replace"), sys.stderr)

    stdout_thread = threading.Thread(target=consume_stdout)
    stderr_thread = threading.Thread(target=consume_stderr)
    stdout_thread.start()
    stderr_thread.start()
    interruption_kind: str | None = None

    def interrupt(kind: str) -> None:
        nonlocal interruption_kind
        if interruption_kind is not None:
            return
        interruption_kind = kind
        try:
            process_group = os.getpgid(process.pid)
            os.killpg(
                process_group,
                signal.SIGINT if kind == "operator_abort" else signal.SIGTERM,
            )
        except (OSError, ProcessLookupError):
            return

    def finish_interrupted() -> int:
        try:
            return process.wait(timeout=WORKER_TERMINATION_TIMEOUT)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(process.pid), signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            return process.wait()

    if stop_request is None:
        try:
            returncode = process.wait()
        except KeyboardInterrupt:
            interrupt("operator_abort")
            returncode = finish_interrupted()
    else:
        while True:
            try:
                returncode = process.wait(timeout=WORKER_WAIT_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                kind = getattr(stop_request, "kind", None)
                if kind in {"operator_abort", "service_shutdown"}:
                    interrupt(kind)
                    returncode = finish_interrupted()
                    break
    stdout_thread.join()
    stderr_thread.join()
    return OpenCodeRunResult(returncode, transport_error, interruption_kind)


def _todo_fingerprint(repo: Path, todo_path: str) -> tuple[str, int]:
    todo_dir = repo / todo_path
    entries: list[str] = []
    if todo_dir.is_dir():
        for item in todo_dir.iterdir():
            if (
                is_canonical_ticket_name(item.name)
                and item.is_file()
                and not item.is_symlink()
            ):
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                entries.append(f"{item.name}\0{digest}\n")
    entries.sort()
    return hashlib.sha256("".join(entries).encode()).hexdigest(), len(entries)


_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _status_summary(status: str) -> dict[str, int]:
    summary = {
        "tracked_modified": 0,
        "staged": 0,
        "untracked": 0,
        "deleted": 0,
        "renamed": 0,
        "conflicted": 0,
    }
    for line in status.splitlines():
        code = line[:2]
        if code == "??":
            summary["untracked"] += 1
            continue
        if code in _CONFLICT_CODES:
            summary["conflicted"] += 1
        if code[0] != " ":
            summary["staged"] += 1
        if code[1] != " ":
            summary["tracked_modified"] += 1
        if "D" in code:
            summary["deleted"] += 1
        if "R" in code:
            summary["renamed"] += 1
    return summary


def _status_fingerprint(status: str) -> str:
    return hashlib.sha256(status.encode()).hexdigest()


class ServiceEngine:
    """Own foreground workflow orchestration and mutable runtime operations."""

    def __init__(self, env_file: Path, *, read_only: bool = False) -> None:
        if not env_file.is_file():
            raise DevlegateError(
                f"configuration file not found: {env_file} "
                f"(copy devlegate's .env.example to $PWD/.env)"
            )
        self.env_file = env_file
        config = _read_env(env_file)
        self.repo = self._repository_root()
        if Path.cwd().resolve() != self.repo:
            raise DevlegateError(f"run devlegate from repository root: {self.repo}")

        def setting(name: str, default: str) -> str:
            return config.get(name, os.environ.get(name, default))

        self.remote_name = setting("REMOTE_NAME", "origin")
        self.remote_branch = setting("REMOTE_BRANCH", "")
        self.control_branch = setting("CONTROL_BRANCH", "devlegate/control")
        self.backlog_path = setting("BACKLOG_PATH", "kanban/backlog")
        self.todo_path = setting("TODO_PATH", "kanban/todo")
        self.review_path = setting("REVIEW_PATH", "kanban/review")
        self.accepted_path = setting("ACCEPTED_PATH", "kanban/accepted")
        self.done_path = setting("DONE_PATH", "kanban/done")
        self.poll_interval = setting("POLL_INTERVAL", "300") or "300"
        self.opencode_bin = setting("OPENCODE_BIN", "opencode")
        self.opencode_model = setting("OPENCODE_MODEL", "")
        self.opencode_agent = setting("OPENCODE_AGENT", "")
        self.read_only = read_only
        self._retry_ticket_id: str | None = None
        state_default = (
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "devlegate"
        )
        self.state_dir = (
            Path(config.get("STATE_DIR", os.environ.get("STATE_DIR", state_default)))
            .expanduser()
            .resolve()
        )
        self._state_key = hashlib.sha256(str(self.repo).encode()).hexdigest()
        self._runtime_store = SQLiteRuntimeStore(self.state_dir, self._state_key)
        self._state_file = self._runtime_store.path
        self.control_worktree = (
            self.state_dir / "worktrees" / self._state_key / "control"
        )
        self.execution_worktree_root = self.state_dir / "worktrees" / self._state_key
        self._state: dict[str, object] = {"phase": "idle"}
        self._dirty_fingerprint: str | None = None
        self._workflow_blocker_fingerprint: str | None = None
        self._workflow_validation_succeeded = False
        self._retry_ticket_id = None
        self._stop_event: threading.Event | None = None
        self._validate()
        self._state = self._load_state()
        self._snapshot_lock = threading.Lock()
        self._published_snapshot = ServiceSnapshot(
            lifecycle="initialized",
            phase=str(self._state["phase"]),
            execution_stage=(
                self._state.get("execution_stage")
                if isinstance(self._state.get("execution_stage"), str)
                else None
            ),
            worker_running=False,
            selected_ticket_id=(
                self._state.get("selected_ticket_id")
                if str(self._state["phase"]) in {"agent_pending", "agent_running"}
                and isinstance(self._state.get("selected_ticket_id"), str)
                else None
            ),
            execution_id=(
                self._state.get("execution_id")
                if str(self._state["phase"]) in {"agent_pending", "agent_running"}
                and isinstance(self._state.get("execution_id"), str)
                else None
            ),
            product=None,
            control=None,
            counts=(),
            runnable=(),
            review=(),
            accepted=(),
            blocked_reason=None,
        )

    def service_snapshot(self) -> ServiceSnapshot:
        """Return the latest published snapshot without performing observation I/O."""
        with self._snapshot_lock:
            return self._published_snapshot

    def _publish_service_snapshot(
        self,
        *,
        lifecycle: str | None = None,
        worker_running: bool | None = None,
        product: GitObservation | None | object = _UNSET,
        control: GitObservation | None | object = _UNSET,
        counts: tuple[tuple[str, int], ...] | object = _UNSET,
        runnable: tuple[tuple[str, str], ...] | object = _UNSET,
        review: tuple[tuple[str, str], ...] | object = _UNSET,
        accepted: tuple[tuple[str, str], ...] | object = _UNSET,
        blocked_reason: str | None | object = _UNSET,
    ) -> None:
        with self._snapshot_lock:
            current = self._published_snapshot
            phase = str(self._state.get("phase", "idle"))
            active = phase in {"agent_pending", "agent_running"}
            selected_ticket_id = self._state.get("selected_ticket_id")
            execution_id = self._state.get("execution_id")
            replacement = ServiceSnapshot(
                lifecycle=current.lifecycle if lifecycle is None else lifecycle,
                phase=phase,
                execution_stage=(
                    self._state.get("execution_stage")
                    if active and isinstance(self._state.get("execution_stage"), str)
                    else None
                ),
                worker_running=(
                    current.worker_running
                    if worker_running is None
                    else worker_running
                ),
                selected_ticket_id=(
                    selected_ticket_id
                    if active and isinstance(selected_ticket_id, str)
                    else None
                ),
                execution_id=(
                    execution_id if active and isinstance(execution_id, str) else None
                ),
                product=(current.product if product is _UNSET else product),
                control=(current.control if control is _UNSET else control),
                counts=(current.counts if counts is _UNSET else counts),
                runnable=(current.runnable if runnable is _UNSET else runnable),
                review=(current.review if review is _UNSET else review),
                accepted=(current.accepted if accepted is _UNSET else accepted),
                blocked_reason=(
                    current.blocked_reason
                    if blocked_reason is _UNSET
                    else blocked_reason
                ),
            )
            self._published_snapshot = replacement

    def _publish_status_snapshot(self, snapshot: StatusSnapshot) -> None:
        blocked_reason = (
            snapshot.plan.reason if snapshot.plan.action == "blocked" else None
        )
        worker_running = self.service_snapshot().worker_running
        lifecycle = "worker" if worker_running else (
            "blocked" if blocked_reason else "ready"
        )
        if worker_running:
            blocked_reason = None
        self._publish_service_snapshot(
            lifecycle=lifecycle,
            product=snapshot.code,
            control=snapshot.control,
            counts=snapshot.counts,
            runnable=snapshot.runnable,
            review=snapshot.review,
            accepted=snapshot.accepted,
            blocked_reason=blocked_reason,
        )

    def _publish_ticket_projection(
        self,
        ticket_store: TicketStore,
        *,
        product: GitObservation,
        control: GitObservation | None,
        blocked_reason: str | None = None,
    ) -> None:
        counts = tuple(
            (
                state,
                sum(ticket.state == state for ticket in ticket_store.tickets),
            )
            for state in ("backlog", "todo", "review", "accepted", "done")
        )
        self._publish_service_snapshot(
            lifecycle="blocked" if blocked_reason else "processing",
            product=product,
            control=control,
            counts=counts,
            runnable=tuple(
                (ticket.id, ticket.title) for ticket in ticket_store.runnable
            ),
            review=tuple(
                (ticket.id, ticket.title)
                for ticket in ticket_store.tickets
                if ticket.state == "review"
            ),
            accepted=tuple(
                (ticket.id, ticket.title)
                for ticket in ticket_store.tickets
                if ticket.state == "accepted"
            ),
            blocked_reason=blocked_reason,
        )

    def _workflow_paths(self) -> dict[str, str]:
        return {
            "backlog": self.backlog_path,
            "todo": self.todo_path,
            "review": self.review_path,
            "accepted": self.accepted_path,
            "done": self.done_path,
        }

    @contextmanager
    def _stop_context(self, stop_event: threading.Event | None):
        previous = self._stop_event
        self._stop_event = stop_event
        try:
            yield
        finally:
            self._stop_event = previous

    def _stop_requested(self) -> bool:
        return self._stop_event is not None and self._stop_event.is_set()

    def _stop_before_admission(self) -> bool:
        """Return whether a new mutable operation must not be committed."""
        return self._stop_requested()

    def _ticket_store(self) -> TicketStore:
        product_workflow = [
            self.repo / configured_path
            for configured_path in self._workflow_paths().values()
        ]
        if any(path.exists() for path in product_workflow):
            raise WorkflowBlockedError(
                "configured managed workflow path exists in the product checkout; "
                "canonical workflow belongs only to the control plane; resolve "
                "the conflicting project path explicitly"
            )
        self._validate_control_worktree()
        for state, configured_path in self._workflow_paths().items():
            directory = self.control_worktree / configured_path
            if not directory.is_dir():
                raise WorkflowBlockedError(
                    f"managed workflow directory is unavailable: {directory} ({state})"
                )
        try:
            store = load_ticket_store(self.control_worktree, self._workflow_paths())
            self._workflow_validation_succeeded = True
            return store
        except TicketError as error:
            raise WorkflowBlockedError(str(error)) from error
        except FileNotFoundError as error:
            raise WorkflowBlockedError(
                "managed workflow changed while it was being observed"
            ) from error

    def _validate_control_worktree(self) -> None:
        if not self.control_worktree.is_dir():
            raise WorkflowBlockedError(
                "control worktree is missing; run 'devlegate control init' "
                f"to attach {self.control_branch}"
            )
        registered = _git(self.repo, "worktree", "list", "--porcelain")
        try:
            registered_paths = set(parse_worktree_porcelain(registered.stdout))
        except ExecutionWorkspaceError as error:
            raise WorkflowBlockedError(str(error)) from error
        if self.control_worktree.resolve() not in registered_paths:
            raise WorkflowBlockedError(
                "control path is not a registered Git worktree for this repository"
            )
        root = _git(self.control_worktree, "rev-parse", "--show-toplevel", check=False)
        if (
            root.returncode
            or Path(root.stdout.strip()).resolve() != self.control_worktree.resolve()
        ):
            raise WorkflowBlockedError(
                "control worktree does not resolve to its configured checkout"
            )
        branch = _git(
            self.control_worktree,
            "symbolic-ref",
            "--quiet",
            "--short",
            "HEAD",
            check=False,
        )
        if branch.returncode or branch.stdout.strip() != self.control_branch:
            raise WorkflowBlockedError(
                f"control worktree is not on configured branch {self.control_branch}"
            )

    @staticmethod
    def _repository_root() -> Path:
        result = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise DevlegateError(
                f"current directory is not a git repository: {Path.cwd()}"
            )
        return Path(result.stdout.strip()).resolve()

    def _validate(self) -> None:
        if not self.read_only:
            if not self.poll_interval.isdecimal():
                raise DevlegateError("POLL_INTERVAL must be an integer")
            if not self.opencode_model:
                raise DevlegateError("OPENCODE_MODEL is required")
            if shutil.which(self.opencode_bin) is None:
                raise DevlegateError(
                    f"OpenCode executable not found: {self.opencode_bin}"
                )
        branch = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode:
            if not self.read_only:
                raise DevlegateError("detached HEAD is not supported")
            self.current_branch = None
        else:
            self.current_branch = branch.stdout.strip()
        self.remote_branch = self.remote_branch or self.current_branch or ""
        if not self.read_only and (
            self.current_branch is None or self.current_branch != self.remote_branch
        ):
            raise DevlegateError(
                f"current branch '{self.current_branch}' does not match "
                f"REMOTE_BRANCH '{self.remote_branch}'"
            )
        if (
            not self.read_only
            and _git(
                self.repo, "remote", "get-url", self.remote_name, check=False
            ).returncode
        ):
            raise DevlegateError(f"git remote not found: {self.remote_name}")
        if not self.read_only:
            self._validate_state_access()

    def _validate_state_access(self) -> None:
        try:
            self._probe_state_access()
        except OSError as error:
            raise DevlegateError(
                f"state directory is not writable: {self.state_dir}: {error}"
            ) from error

    def _assert_product_checkout_unchanged(
        self, expected_branch: str | None, expected_head: str
    ) -> None:
        branch_result = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        observed_branch = (
            branch_result.stdout.strip() if branch_result.returncode == 0 else None
        )
        head_result = _git(self.repo, "rev-parse", "HEAD", check=False)
        observed_head = (
            head_result.stdout.strip() if head_result.returncode == 0 else None
        )
        status_result = _git(
            self.repo, "status", "--porcelain", check=False
        )
        if status_result.returncode:
            raise DevlegateError(
                "cannot verify product checkout after worker execution; "
                "execution isolation cannot be proven"
            )
        status = status_result.stdout
        if (
            observed_branch != expected_branch
            or observed_head != expected_head
            or bool(status)
        ):
            raise DevlegateError(
                "product checkout changed while worker execution was active; "
                "execution isolation cannot be proven"
            )

    def _control_remote_exists(self) -> bool:
        """Observe the control ref without conflating transport failure and absence."""
        observed = _git(
            self.repo,
            "ls-remote",
            "--heads",
            self.remote_name,
            f"refs/heads/{self.control_branch}",
            check=False,
        )
        if observed.returncode:
            detail = observed.stderr.strip() or "remote could not be observed"
            raise DevlegateError(
                f"cannot observe control branch {self.control_branch!r} on "
                f"remote {self.remote_name}: {detail}"
            )
        return bool(observed.stdout.strip())

    def _attach_existing_control(self) -> int:
        fetch = _git(
            self.repo, "fetch", self.remote_name, self.control_branch, check=False
        )
        if fetch.returncode:
            raise DevlegateError(
                f"cannot fetch existing control branch {self.control_branch!r}: "
                f"{fetch.stderr.strip()}"
            )
        remote = _git(
            self.repo,
            "rev-parse",
            "--verify",
            f"{self.remote_name}/{self.control_branch}",
            check=False,
        )
        if remote.returncode:
            raise DevlegateError(
                f"configured control branch {self.control_branch!r} is unavailable "
                f"on remote {self.remote_name}"
            )
        if self.control_worktree.exists() or self.control_worktree.is_symlink():
            registered = _git(self.repo, "worktree", "list", "--porcelain")
            try:
                registered_paths = set(parse_worktree_porcelain(registered.stdout))
            except ExecutionWorkspaceError as error:
                raise DevlegateError(str(error)) from error
            if self.control_worktree.resolve() not in registered_paths:
                raise DevlegateError(
                    "existing control path is not a registered Git worktree"
                )
            root = _git(
                self.control_worktree, "rev-parse", "--git-common-dir", check=False
            )
            branch = _git(
                self.control_worktree,
                "symbolic-ref",
                "--quiet",
                "--short",
                "HEAD",
                check=False,
            )
            expected_git = _git(
                self.repo, "rev-parse", "--git-common-dir"
            ).stdout.strip()
            if (
                root.returncode
                or Path(root.stdout.strip()).resolve() != Path(expected_git).resolve()
            ):
                raise DevlegateError(
                    "existing control worktree belongs to another repository"
                )
            if branch.returncode or branch.stdout.strip() != self.control_branch:
                raise DevlegateError("existing control worktree is on the wrong branch")
            for state, configured_path in self._workflow_paths().items():
                if not (self.control_worktree / configured_path).is_dir():
                    raise DevlegateError(
                        f"managed workflow directory is unavailable: "
                        f"{self.control_worktree / configured_path} ({state})"
                    )
            local_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
            remote_head = remote.stdout.strip()
            if (
                local_head != remote_head
                and _git(
                    self.control_worktree,
                    "merge-base",
                    "--is-ancestor",
                    local_head,
                    remote_head,
                    check=False,
                ).returncode
            ):
                raise DevlegateError(
                    "existing control worktree is not compatible with the remote "
                    "control branch"
                )
            print(f"control worktree already attached: {self.control_worktree}")
            return 0
        self.control_worktree.parent.mkdir(parents=True, exist_ok=True)
        local_branch = _git(
            self.repo,
            "show-ref",
            "--verify",
            f"refs/heads/{self.control_branch}",
            check=False,
        )
        if local_branch.returncode == 0:
            local_head = _git(
                self.repo, "rev-parse", self.control_branch
            ).stdout.strip()
            remote_head = remote.stdout.strip()
            if (
                local_head != remote_head
                and _git(
                    self.repo,
                    "merge-base",
                    "--is-ancestor",
                    local_head,
                    remote_head,
                    check=False,
                ).returncode
            ):
                raise DevlegateError(
                    "local control branch diverges from the remote control branch"
                )
        args = ["worktree", "add"]
        if local_branch.returncode:
            args += ["--track", "-b", self.control_branch]
            args += [
                str(self.control_worktree),
                f"{self.remote_name}/{self.control_branch}",
            ]
        else:
            args += [str(self.control_worktree), self.control_branch]
        created = _git(self.repo, *args, check=False)
        if created.returncode:
            raise DevlegateError(
                f"cannot attach control worktree: {created.stderr.strip()}"
            )
        print(f"attached control worktree: {self.control_worktree}")
        return 0

    def _bootstrap_control(self) -> int:
        if self.control_worktree.exists() or self.control_worktree.is_symlink():
            raise DevlegateError(
                "expected control worktree path already exists: "
                f"{self.control_worktree}"
            )
        local_branch = _git(
            self.repo,
            "show-ref",
            "--verify",
            f"refs/heads/{self.control_branch}",
            check=False,
        )
        if local_branch.returncode == 0:
            raise DevlegateError(
                f"local control branch {self.control_branch!r} exists while the "
                "remote branch is absent"
            )
        self.control_worktree.parent.mkdir(parents=True, exist_ok=True)
        created = _git(
            self.repo,
            "worktree",
            "add",
            "--orphan",
            "-b",
            self.control_branch,
            str(self.control_worktree),
            check=False,
        )
        if created.returncode:
            raise DevlegateError(
                f"cannot create fresh control worktree: {created.stderr.strip()}"
            )
        try:
            for configured_path in self._workflow_paths().values():
                directory = self.control_worktree / configured_path
                directory.mkdir(parents=True, exist_ok=True)
                (directory / ".gitkeep").touch()
            added = _git(self.control_worktree, "add", "--all", check=False)
            if added.returncode:
                raise DevlegateError(
                    f"cannot stage initial control state: {added.stderr.strip()}"
                )
            committed = _git(
                self.control_worktree,
                "commit",
                "-m",
                "Initialize Devlegate control plane",
                check=False,
            )
            if committed.returncode:
                raise DevlegateError(
                    f"cannot commit initial control state: {committed.stderr.strip()}"
                )
            pushed = _git(
                self.control_worktree,
                "push",
                self.remote_name,
                f"HEAD:refs/heads/{self.control_branch}",
                check=False,
            )
            if pushed.returncode:
                raise DevlegateError(
                    f"cannot publish initial control branch: {pushed.stderr.strip()}"
                )
            refreshed = _git(
                self.repo, "fetch", self.remote_name, self.control_branch, check=False
            )
            if refreshed.returncode:
                raise DevlegateError(
                    "cannot refresh published control branch: "
                    f"{refreshed.stderr.strip()}"
                )
        except DevlegateError:
            raise
        print(f"initialized control worktree: {self.control_worktree}")
        return 0

    def control_init(self) -> int:
        """Attach an existing control branch or bootstrap a fresh control plane."""
        if self._control_remote_exists():
            return self._attach_existing_control()
        return self._bootstrap_control()

    def _probe_state_access(self) -> None:
        self._runtime_store.probe()

    def _lock(self):
        lock_root = self.state_dir / "locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_file = lock_root / f"{self._state_key}.lock"
        handle = lock_file.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise DevlegateError(
                "another devlegate instance is already running for this checkout"
            ) from error
        return handle

    def _load_state(self) -> dict[str, object]:
        try:
            state = self._runtime_store.load()
        except RuntimeStoreError as error:
            raise DevlegateError(str(error)) from error
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise DevlegateError(f"invalid state file: {self._state_file}")
        self._validate_state_invariant(state)
        return state

    @staticmethod
    def _validate_state_invariant(state: dict[str, object]) -> None:
        phase = state["phase"]
        valid_phases = {
            "idle",
            "merge_pending",
            "agent_pending",
            "agent_running",
        }
        if phase not in valid_phases:
            raise DevlegateError(f"invalid state phase: {phase}")
        failures = state.get("failed_executions", {})
        if not isinstance(failures, dict) or not all(
            isinstance(ticket_id, str)
            and isinstance(value, dict)
            and all(
                isinstance(value.get(field), str)
                for field in (
                    "execution_id",
                    "product_head",
                    "remote_head",
                    "control_head",
                    "todo_fingerprint",
                    "reason",
                )
            )
            for ticket_id, value in failures.items()
        ):
            raise DevlegateError("invalid state: failed execution metadata is invalid")
        if phase == "idle":
            return
        if phase == "merge_pending":
            required_fields = (
                "local_head",
                "remote_head",
                "changed_paths",
                "control_head",
            )
            if not all(isinstance(state.get(field), str) for field in required_fields):
                raise DevlegateError(
                    "invalid merge_pending state: synchronization coordinates "
                    "are incomplete"
                )
            return
        required_fields = ("local_head", "remote_head", "changed_paths")
        if not all(isinstance(state.get(field), str) for field in required_fields):
            raise DevlegateError(
                f"invalid {phase} state: synchronization coordinates are incomplete"
            )
        has_id = isinstance(state.get("selected_ticket_id"), str) and bool(
            state["selected_ticket_id"]
        )
        has_body = isinstance(state.get("selected_ticket_body"), str)
        if not has_id or not has_body:
            raise DevlegateError(
                f"invalid {phase} state: selected ticket binding is incomplete"
            )
        if not isinstance(state.get("control_head"), str):
            raise DevlegateError(
                f"invalid {phase} state: control revision identity is incomplete"
            )
        execution_fields = (
            "execution_ticket_id",
            "execution_base_head",
            "execution_control_head",
            "execution_branch",
            "execution_path",
            "execution_id",
        )
        if phase in {"agent_pending", "agent_running"} and (
            not all(
                field in state
                for field in (*execution_fields, "execution_remote_head")
            )
            or not all(
                isinstance(state.get(field), str) and state[field]
                for field in execution_fields
            )
            or not (
                state.get("execution_remote_head") is None
                or (
                    isinstance(state.get("execution_remote_head"), str)
                    and bool(state["execution_remote_head"])
                )
            )
        ):
            raise DevlegateError(
                "invalid state: execution workspace binding is incomplete"
            )
        if "execution_remote_head" in state and not (
            state["execution_remote_head"] is None
            or (
                isinstance(state["execution_remote_head"], str)
                and bool(state["execution_remote_head"])
            )
        ):
            raise DevlegateError(
                "invalid state: execution remote revision identity is invalid"
            )

    def _save_state(self, phase: str, **fields: object) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state: dict[str, object] = {**self._state, "phase": phase, **fields}
        if phase == "idle":
            state.pop("selected_ticket_id", None)
            state.pop("selected_ticket_body", None)
        self._validate_state_invariant(state)
        try:
            self._runtime_store.replace(state)
        except RuntimeStoreError as error:
            raise DevlegateError(str(error)) from error
        self._state = state

    def _observe_worktree(self, status: str) -> bool:
        if status:
            fingerprint = _status_fingerprint(status)
            if fingerprint == self._dirty_fingerprint:
                return False
            self._dirty_fingerprint = fingerprint
            summary = _status_summary(status)
            _log("working tree became dirty; automatic work suspended")
            for line in status.rstrip().splitlines():
                _log(f"  {line}")
            for name in (
                "tracked_modified",
                "staged",
                "untracked",
                "deleted",
                "renamed",
                "conflicted",
            ):
                _log(f"{name.replace('_', ' ')}: {summary[name]}")
            return True
        elif self._dirty_fingerprint is not None:
            self._dirty_fingerprint = None
            _log("working tree is clean again")
            return True
        return False

    def _log_sync_failure(
        self, local_head: str, remote_ref: str, stderr: str = ""
    ) -> None:
        remote_result = _git(self.repo, "rev-parse", remote_ref, check=False)
        remote_head = remote_result.stdout.strip() or "<unknown>"
        base_result = _git(
            self.repo, "merge-base", local_head, remote_head, check=False
        )
        merge_base = base_result.stdout.strip() or "<none>"
        counts_result = _git(
            self.repo,
            "rev-list",
            "--left-right",
            "--count",
            f"{local_head}...{remote_head}",
            check=False,
        )
        counts = counts_result.stdout.strip().split()
        ahead = counts[0] if len(counts) == 2 else "unknown"
        behind = counts[1] if len(counts) == 2 else "unknown"
        if ahead != "unknown" and behind != "unknown":
            if ahead != "0" and behind != "0":
                classification = "histories have diverged"
            elif ahead != "0":
                classification = "local branch is ahead of remote"
            else:
                classification = "remote branch is ahead of local"
        else:
            classification = "fast-forward was rejected"
        _log("cannot fast-forward local checkout")
        _log(f"local HEAD: {local_head}")
        _log(f"remote HEAD: {remote_head}")
        _log(f"merge base: {merge_base}")
        _log(f"local ahead: {ahead}")
        _log(f"local behind: {behind}")
        _log(f"classification: {classification}")
        if stderr.strip():
            _log(f"git stderr: {stderr.strip()}")

    def _sync_control(self) -> tuple[str, str]:
        """Fetch and fast-forward the already initialized control checkout."""
        self._validate_control_worktree()
        observation = self._git_observation(self.control_worktree, self.control_branch)
        if not observation.working_tree_clean:
            raise WorkflowBlockedError("control working tree is dirty")
        fetch = _git(
            self.control_worktree,
            "fetch",
            "--prune",
            self.remote_name,
            self.control_branch,
            check=False,
        )
        if fetch.returncode:
            raise WorkflowBlockedError(
                f"control fetch failed: {fetch.stderr.strip() or 'unknown git error'}"
            )
        remote_ref = f"{self.remote_name}/{self.control_branch}"
        remote = _git(
            self.control_worktree,
            "rev-parse",
            "--verify",
            remote_ref,
            check=False,
        )
        if remote.returncode:
            raise WorkflowBlockedError(f"control remote branch not found: {remote_ref}")
        remote_head = remote.stdout.strip()
        local_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        if local_head != remote_head:
            ancestor = _git(
                self.control_worktree,
                "merge-base",
                "--is-ancestor",
                local_head,
                remote_head,
                check=False,
            )
            if ancestor.returncode:
                raise WorkflowBlockedError(
                    "control branch cannot be fast-forwarded; histories diverged"
                )
            merge = _git(
                self.control_worktree, "merge", "--ff-only", remote_ref, check=False
            )
            if merge.returncode:
                raise WorkflowBlockedError(
                    f"control fast-forward failed: {merge.stderr.strip()}"
                )
            local_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        return local_head, remote_head

    def run_once(self, stop_event: threading.Event | None = None) -> int:
        if stop_event is None:
            return self._run_once()
        with self._stop_context(stop_event):
            return self._run_once()

    def _run_once(self) -> int:
        self._publish_service_snapshot(lifecycle="processing")
        if (
            self._stop_requested()
            and self._state.get("phase") != "merge_pending"
        ):
            return 0
        self._workflow_validation_succeeded = False
        branch = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode:
            raise WorkflowBlockedError(
                f"current checkout is detached; expected branch '{self.current_branch}'"
            )
        actual_branch = branch.stdout.strip()
        if actual_branch != self.current_branch:
            raise WorkflowBlockedError(
                f"current checkout branch '{actual_branch}' does not match "
                f"expected branch '{self.current_branch}'"
            )
        if self._state.get("phase") == "agent_running":
            if self._stop_requested():
                return 0
            ticket_id = self._state.get("execution_ticket_id") or self._state.get(
                "selected_ticket_id", "unknown"
            )
            execution_id = self._state.get("execution_id", "unknown")
            reason = (
                f"interrupted execution is ambiguous for ticket {ticket_id} "
                f"(execution {execution_id}); refusing to launch a worker or "
                "automatically reconcile it; explicit operator handling is "
                "required"
            )
            self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
            raise DevlegateError(reason)
        status = _git(self.repo, "status", "--porcelain").stdout
        dirty_changed = self._observe_worktree(status)
        if status:
            if dirty_changed:
                _log("working tree is dirty; refusing to pull or start the agent")
            return 1
        initial_phase = self._state.get("phase")
        # Observation may finish, but stop before a new synchronization admission.
        if self._stop_before_admission() and initial_phase != "merge_pending":
            return 0
        control_head, control_remote_head = self._sync_control()
        if self._stop_before_admission() and initial_phase != "merge_pending":
            return 0
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        state_phase = self._state.get("phase")
        pending_sync = state_phase in {"agent_pending", "merge_pending"}
        pending_agent_execution = state_phase == "agent_pending"
        had_remote_change = False
        local_ahead = False
        fetch = _git(
                self.repo,
                "fetch",
                "--prune",
                self.remote_name,
                self.remote_branch,
                check=False,
            )
        if fetch.returncode:
            _log(f"fetch failed: {fetch.stderr.strip() or 'unknown git error'}")
            return 1
        else:
            remote_result = _git(
                self.repo, "rev-parse", "--verify", remote_ref, check=False
            )
            if remote_result.returncode:
                _log(
                    f"remote branch not found: {remote_ref}; "
                    f"git stderr: {remote_result.stderr.strip() or 'unknown git error'}"
                )
                return 1
            remote_head = _git(self.repo, "rev-parse", remote_ref).stdout.strip()
            persisted_head = str(self._state.get("remote_head", ""))
            if pending_sync:
                persisted_local_head = str(self._state.get("local_head", ""))
                target_head = persisted_head
                if state_phase == "merge_pending":
                    if local_head not in {persisted_local_head, target_head}:
                        raise DevlegateError(
                            "persisted merge revision does not match the local HEAD"
                        )
                elif local_head != persisted_local_head:
                    persisted_local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            persisted_local_head,
                            local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    current_local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            local_head,
                            persisted_local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    if current_local_is_ancestor:
                        raise DevlegateError(
                            "current local HEAD is behind the persisted execution "
                            "HEAD; refusing rollback "
                            f"(persisted {persisted_local_head[:12]}, "
                            f"current {local_head[:12]}, remote {target_head[:12]})"
                        )
                    if not persisted_local_is_ancestor:
                        raise DevlegateError(
                            "current local HEAD diverged from the persisted execution "
                            f"history (persisted {persisted_local_head[:12]}, "
                            f"current {local_head[:12]}, remote {target_head[:12]})"
                        )

                target_is_current = target_head == remote_head
                target_is_ancestor = (
                    _git(
                        self.repo,
                        "merge-base",
                        "--is-ancestor",
                        target_head,
                        remote_head,
                        check=False,
                    ).returncode
                    == 0
                )
                if not target_is_current and not target_is_ancestor:
                    raise DevlegateError(
                        "new remote revision is not a descendant of the pending "
                        "revision"
                    )
                if target_is_ancestor:
                    target_head = remote_head
                    if target_head != persisted_head:
                        had_remote_change = True
                        _log(
                            f"superseding pending revision {persisted_head[:12]} "
                            f"with descendant {target_head[:12]}"
                        )
                local_ahead_pending = state_phase == "agent_pending" and (
                    local_head != target_head
                )
                preserve_local_ahead = False
                if local_ahead_pending:
                    local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            local_head,
                            target_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    remote_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            target_head,
                            local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    if not local_is_ancestor and not remote_is_ancestor:
                        self._log_sync_failure(local_head, remote_ref)
                        return 1
                    if remote_is_ancestor:
                        preserve_local_ahead = True
                        changed_paths = str(self._state.get("changed_paths", ""))
                        _log(
                            "resuming local-ahead pending execution; preserving "
                            f"local HEAD {local_head[:12]}"
                        )
                if local_head != target_head and not preserve_local_ahead:
                    changed_paths = _git(
                        self.repo,
                        "diff",
                        "--name-only",
                        local_head,
                        target_head,
                        check=False,
                    ).stdout.rstrip()
                    if (
                        self._stop_before_admission()
                        and initial_phase != "merge_pending"
                    ):
                        return 0
                    # A durable merge_pending record commits this drain region.
                    self._save_state(
                        "merge_pending",
                        local_head=local_head,
                        remote_head=target_head,
                        changed_paths=changed_paths,
                        control_head=control_head,
                    )
                    merge = _git(
                        self.repo, "merge", "--ff-only", remote_ref, check=False
                    )
                    if merge.returncode:
                        self._log_sync_failure(local_head, remote_ref, merge.stderr)
                        return 1
                    local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
                    if local_head != target_head:
                        raise DevlegateError(
                            "fast-forward completed without reaching the target "
                            "revision"
                        )
                    _log(f"updated {self.current_branch} to {target_head[:12]}")
                else:
                    changed_paths = str(self._state.get("changed_paths", ""))
                remote_head = target_head
                synchronized_phase = (
                    "agent_pending" if pending_agent_execution else "idle"
                )
                synchronized_fields = {
                    "local_head": local_head,
                    "remote_head": target_head,
                    "changed_paths": changed_paths,
                    "control_head": control_head,
                }
                self._save_state(
                    synchronized_phase,
                    **synchronized_fields,
                )
                if state_phase == "merge_pending":
                    _log("resumed after completed merge")
            elif local_head == remote_head:
                changed_paths = ""
            elif (
                _git(
                    self.repo,
                    "merge-base",
                    "--is-ancestor",
                    local_head,
                    remote_head,
                    check=False,
                ).returncode
                == 0
            ):
                had_remote_change = True
                changed_paths = _git(
                    self.repo,
                    "diff",
                    "--name-only",
                    local_head,
                    remote_head,
                    check=False,
                ).stdout.rstrip()
                if self._stop_before_admission():
                    return 0
                self._save_state(
                    "merge_pending",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                    control_head=control_head,
                )
                merge = _git(self.repo, "merge", "--ff-only", remote_ref, check=False)
                if merge.returncode:
                    self._log_sync_failure(local_head, remote_ref, merge.stderr)
                    return 1
                actual_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
                if actual_head != remote_head:
                    raise DevlegateError(
                        "fast-forward completed without reaching the remote revision"
                    )
                _log(
                    f"updated {self.current_branch} from {local_head[:12]} "
                    f"to {remote_head[:12]}"
                )
                local_head = actual_head
                self._save_state(
                    "idle",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                )
            elif (
                _git(
                    self.repo,
                    "merge-base",
                    "--is-ancestor",
                    remote_head,
                    local_head,
                    check=False,
                ).returncode
                == 0
            ):
                local_ahead = True
                changed_paths = _git(
                    self.repo,
                    "diff",
                    "--name-only",
                    remote_head,
                    local_head,
                    check=False,
                ).stdout.rstrip()
                _log(
                    f"local branch is ahead of {self.remote_name}/"
                    f"{self.remote_branch}; preserving local HEAD {local_head[:12]}"
                )
            else:
                had_remote_change = True
                self._log_sync_failure(local_head, remote_ref)
                return 1
        if self._stop_before_admission():
            return 0
        ticket_store = self._ticket_store()
        try:
            load_project_context(self.repo)
        except ProjectContextError as error:
            raise WorkflowBlockedError(str(error)) from error
        bound_execution = pending_agent_execution
        execution_plan = self._make_execution_plan(
            self._state,
            ticket_store,
            False,
            observation={
                "code": GitObservation(
                    self.current_branch,
                    False,
                    local_head,
                    remote_ref,
                    remote_head,
                    True,
                    _status_fingerprint(""),
                ),
                "control": GitObservation(
                    self.control_branch,
                    False,
                    control_head,
                    f"{self.remote_name}/{self.control_branch}",
                    control_remote_head,
                    True,
                    _status_fingerprint(""),
                ),
            },
        )
        self._publish_ticket_projection(
            ticket_store,
            product=execution_plan.code,
            control=execution_plan.control,
            blocked_reason=(
                execution_plan.reason
                if execution_plan.action == "blocked"
                else None
            ),
        )
        if self._stop_before_admission():
            return 0
        if self._retry_ticket_id is not None:
            if execution_plan.action != "run-worker" or execution_plan.bound:
                raise DevlegateError(execution_plan.reason)
            retry_ticket = ticket_store.by_id.get(self._retry_ticket_id)
            if (
                retry_ticket is None
                or retry_ticket.state != "todo"
                or retry_ticket not in ticket_store.runnable
            ):
                raise DevlegateError(
                    f"ticket {self._retry_ticket_id} is no longer eligible for retry"
                )
            execution_plan = ExecutionPlan(
                "run-worker", "explicit retry of current failed execution",
                retry_ticket.id, retry_ticket.title, retry_ticket.state, False,
                execution_plan.code, execution_plan.control,
            )
        if execution_plan.action == "blocked":
            raise DevlegateError(execution_plan.reason)
        if execution_plan.action == "integrate":
            assert execution_plan.ticket_id is not None
            try:
                control_head = self._integrate_accepted(
                    execution_plan.ticket_id,
                    execution_plan.code,
                    execution_plan.control,
                )
            except (DevlegateError, OSError, ExecutionReportError) as error:
                raise DevlegateError(f"accepted integration failed: {error}") from error
            todo_fingerprint, _todo_count = _todo_fingerprint(
                self.control_worktree, self.todo_path
            )
            self._save_state(
                "idle",
                handled_remote_head=remote_head,
                handled_control_head=control_head,
                handled_todo_fingerprint=todo_fingerprint,
            )
            self._publish_service_snapshot(lifecycle="ready", worker_running=False)
            _log(f"accepted ticket {execution_plan.ticket_id} integrated")
            return 0
        if execution_plan.action == "none":
            selected_ticket = None
        else:
            assert execution_plan.ticket_id is not None
            selected_ticket = ticket_store.by_id[execution_plan.ticket_id]
            if bound_execution:
                body = self._state.get("selected_ticket_body")
                if not isinstance(body, str):
                    raise DevlegateError(
                        "invalid execution state: selected ticket binding is incomplete"
                    )
                selected_ticket = dataclasses.replace(selected_ticket, body=body)
        execution_id: str | None = None
        if state_phase in {"idle", "agent_pending", "merge_pending"}:
            todo_fingerprint, todo_count = _todo_fingerprint(
                self.control_worktree, self.todo_path
            )
            generation_is_same = (
                str(self._state.get("handled_remote_head", "")) == remote_head
                and str(self._state.get("handled_control_head", "")) == control_head
                and str(self._state.get("handled_todo_fingerprint", ""))
                == todo_fingerprint
            )
            if selected_ticket is None and not pending_agent_execution:
                self._save_state(
                    "idle",
                    handled_remote_head=remote_head,
                    handled_control_head=control_head,
                    handled_todo_fingerprint=todo_fingerprint,
                )
                if todo_count and ticket_store.tickets:
                    _log(
                        "todo tickets are currently blocked by unfinished dependencies"
                    )
                elif local_ahead:
                    _log(
                        "local branch is ahead; no actionable ticket files in "
                        f"{self.todo_path}"
                    )
                elif not had_remote_change:
                    _log("no remote changes")
                else:
                    _log(f"no actionable ticket files in {self.todo_path}")
                return 0
            failed = self._state.get("failed_executions", {})
            failed_for_ticket = (
                failed.get(selected_ticket.id) if isinstance(failed, dict) else None
            )
            if self._retry_ticket_id is not None:
                if not isinstance(failed_for_ticket, dict) or any(
                    failed_for_ticket.get(field) != expected
                    for field, expected in (
                        ("product_head", local_head),
                        ("remote_head", remote_head),
                        ("control_head", control_head),
                        ("todo_fingerprint", todo_fingerprint),
                    )
                ):
                    raise DevlegateError(
                        f"ticket {self._retry_ticket_id} failed execution is stale; "
                        "current project state must be reviewed before retry"
                    )
            if (
                self._retry_ticket_id is None
                and not pending_agent_execution
                and isinstance(failed_for_ticket, dict)
                and generation_is_same
                and failed_for_ticket.get("execution_id")
            ):
                _log(
                    f"execution failed for {selected_ticket.id}; automatic retry "
                    "suppressed; explicit retry is required"
                )
                return 0
            if self._retry_ticket_id is not None:
                if selected_ticket.id != self._retry_ticket_id:
                    raise DevlegateError(
                        f"ticket {self._retry_ticket_id} is no longer the current "
                        "runnable ticket"
                    )
            if (
                generation_is_same
                and not pending_agent_execution
                and self._retry_ticket_id is None
                and self._state.get("execution_ticket_id") == selected_ticket.id
            ):
                _log("no new work generation; unchanged todo is already handled")
                return 0
            existing_lineage = (
                self._state.get("execution_ticket_id") == selected_ticket.id
                and isinstance(self._state.get("execution_base_head"), str)
                and isinstance(self._state.get("execution_control_head"), str)
            )
            bound_execution_control = (
                self._state.get("execution_control_head")
                if pending_agent_execution and existing_lineage
                else control_head
            )
            execution_base_head = (
                self._state["execution_base_head"]
                if existing_lineage
                else local_head
            )
            execution_remote_head = (
                self._state.get("execution_remote_head")
                if pending_agent_execution
                else self._execution_remote_head(f"devlegate/work/{selected_ticket.id}")
            )
            if execution_remote_head is not None and not isinstance(
                execution_remote_head, str
            ):
                raise DevlegateError("invalid persisted execution remote identity")
            execution_id = (
                self._state.get("execution_id")
                if pending_agent_execution
                else new_execution_id()
            )
            if not isinstance(execution_id, str) or not execution_id:
                raise DevlegateError("invalid persisted execution identity")
            self._save_state(
                "agent_pending",
                local_head=local_head,
                remote_head=remote_head,
                changed_paths=changed_paths,
                handled_remote_head=remote_head,
                handled_control_head=control_head,
                control_head=control_head,
                handled_todo_fingerprint=todo_fingerprint,
                selected_ticket_id=selected_ticket.id,
                selected_ticket_body=selected_ticket.body,
                execution_ticket_id=selected_ticket.id,
                execution_base_head=execution_base_head,
                execution_control_head=bound_execution_control,
                execution_branch=f"devlegate/work/{selected_ticket.id}",
                execution_path=str(
                    self.execution_worktree_root / "work" / selected_ticket.id
                ),
                execution_id=execution_id,
                execution_remote_head=execution_remote_head,
            )
            if self._stop_before_admission():
                return 0
            self._publish_service_snapshot(worker_running=False)

        try:
            workspace = self._prepare_execution_workspace(execution_plan)
        except ExecutionWorkspaceError as error:
            raise DevlegateError(str(error)) from error
        _log(
            "execution workspace "
            f"{'resumed' if workspace.head != workspace.base_head else 'prepared'}: "
            f"{workspace.path} ({workspace.branch} at {workspace.head[:12]})"
        )
        directive = WorkDirective.FRESH
        if bound_execution or (
            self._retry_ticket_id is not None and workspace.head != workspace.base_head
        ):
            directive = WorkDirective.RESUME
        if execution_id is None:
            execution_id = self._state.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            execution_id = new_execution_id()
        execution_remote_head = self._state.get("execution_remote_head")
        if "execution_remote_head" not in self._state:
            execution_remote_head = self._execution_remote_head(workspace.branch)
        prompt = build_worker_prompt(
            WorkerPromptInput(assignment=selected_ticket.body, directive=directive)
        )
        # This is the last safe gate before committing agent_running.
        if self._stop_before_admission():
            return 0
        self._save_state(
            "agent_running",
            execution_stage="pre-checkpoint",
            execution_start_head=workspace.head,
            execution_ticket_id=selected_ticket.id,
            execution_base_head=workspace.base_head,
            execution_control_head=str(self._state["control_head"]),
            execution_branch=workspace.branch,
            execution_path=str(workspace.path),
            execution_id=execution_id,
            execution_remote_head=execution_remote_head,
        )
        self._publish_service_snapshot(lifecycle="worker", worker_running=True)
        try:
            worker_run = self._run_worker(workspace, prompt)
        finally:
            self._publish_service_snapshot(
                lifecycle="processing", worker_running=False
            )
        try:
            self._assert_product_checkout_unchanged(self.current_branch, local_head)
            manager = ExecutionWorkspaceManager(
                self.repo, self.execution_worktree_root, selected_ticket.id
            )
            manager.verify_submodules(workspace)
        except (ExecutionWorkspaceError, OSError) as error:
            self._record_post_worker_failure(
                selected_ticket.id,
                execution_id,
                local_head,
                remote_head,
                str(error),
            )
            raise DevlegateError(
                "post-worker execution integrity failed; "
                f"execution dependency integrity cannot be proven: {error}"
            ) from error
        except DevlegateError as error:
            self._record_post_worker_failure(
                selected_ticket.id,
                execution_id,
                local_head,
                remote_head,
                str(error),
            )
            raise
        execution_result = build_execution_report(
            execution_id=execution_id,
            ticket_id=selected_ticket.id,
            code_base_head=workspace.base_head,
            control_head=str(self._state["execution_control_head"]),
            execution_branch=workspace.branch,
            execution_path=str(workspace.path),
            workspace_head=None,
            run=worker_run,
        )
        self._save_state("agent_running", execution_stage="checkpointing")
        try:
            checkpoint = manager.checkpoint(workspace, execution_id)
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(f"execution checkpoint failed: {error}") from error
        self._save_state("agent_running", execution_stage="post-checkpoint")
        _log(
            "execution checkpoint "
            f"{'created' if checkpoint.commit_created else 'not needed'}: "
            f"{checkpoint.after_head[:12]}"
        )
        self._save_state("agent_running", execution_stage="publishing")
        try:
            self._publish_execution_branch(
                workspace,
                checkpoint.after_head,
                self._state.get("execution_remote_head"),
            )
        except (DevlegateError, OSError) as error:
            raise DevlegateError(
                f"execution branch publication failed: {error}"
            ) from error
        self._save_state("agent_running", execution_stage="post-publication")
        report = dataclasses.replace(
            execution_result,
            workspace_head=checkpoint.after_head,
        )
        self._save_state("agent_running", execution_stage="lifecycle")
        try:
            control_head = self._apply_execution_lifecycle(report)
        except (DevlegateError, OSError, ExecutionReportError) as error:
            raise DevlegateError(f"control-plane lifecycle failed: {error}") from error
        todo_fingerprint, _todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        failed_executions = self._state.get("failed_executions", {})
        if not isinstance(failed_executions, dict):
            failed_executions = {}
        if report.result.conclusion == "failed":
            failure = {
                "execution_id": report.execution_id,
                "product_head": local_head,
                "remote_head": remote_head,
                "control_head": control_head,
                "todo_fingerprint": todo_fingerprint,
                "reason": report.result.reason,
            }
            if worker_run.interruption_kind in {
                "operator_abort",
                "service_shutdown",
            }:
                failure["interruption_kind"] = worker_run.interruption_kind
                failure["reason"] = (
                    "interrupted: "
                    + worker_run.interruption_kind.replace("_", " ")
                )
            prior_failure = failed_executions.get(report.ticket_id)
            if (
                isinstance(prior_failure, dict)
                and prior_failure.get("interrupted") is True
            ):
                failure["interrupted_execution_id"] = prior_failure.get("execution_id")
            failed_executions = {
                **failed_executions,
                report.ticket_id: failure,
            }
        else:
            failed_executions = {
                ticket_id: metadata
                for ticket_id, metadata in failed_executions.items()
                if ticket_id != report.ticket_id
            }
        self._save_state(
            "idle",
            handled_remote_head=remote_head,
            handled_control_head=control_head,
            handled_todo_fingerprint=todo_fingerprint,
            execution_control_head=control_head,
            failed_executions=failed_executions,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)
        _log(
            f"execution {report.result.conclusion}; lifecycle control HEAD "
            f"{control_head[:12]}"
        )
        if worker_run.interruption_kind is not None:
            return 0
        return 1 if report.result.conclusion == "failed" else 0

    def _record_post_worker_failure(
        self,
        ticket_id: str,
        execution_id: str,
        local_head: str,
        remote_head: str,
        reason: str,
    ) -> None:
        control_head = self._state.get("execution_control_head")
        if not isinstance(control_head, str) or not control_head:
            control_head = str(self._state.get("control_head", ""))
        todo_fingerprint, _todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        failed_executions = self._state.get("failed_executions", {})
        if not isinstance(failed_executions, dict):
            failed_executions = {}
        failed_executions = {
            **failed_executions,
            ticket_id: {
                "execution_id": execution_id,
                "product_head": local_head,
                "remote_head": remote_head,
                "control_head": control_head,
                "todo_fingerprint": todo_fingerprint,
                "reason": f"post-worker dependency integrity failure: {reason}",
                "interrupted": True,
            },
        }
        self._save_state(
            "idle",
            handled_remote_head=remote_head,
            handled_control_head=control_head,
            handled_todo_fingerprint=todo_fingerprint,
            execution_control_head=control_head,
            failed_executions=failed_executions,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)

    def _prepare_execution_workspace(self, plan: ExecutionPlan) -> ExecutionWorkspace:
        if plan.ticket_id is None or plan.code is None or plan.control is None:
            raise ExecutionWorkspaceError(
                "execution plan has no complete revision binding"
            )
        same_ticket = self._state.get("execution_ticket_id") == plan.ticket_id
        bound_base = (
            self._state.get("execution_base_head") if same_ticket else None
        )
        bound_control = (
            self._state.get("execution_control_head")
            if same_ticket
            and str(self._state.get("phase"))
            in {
                "agent_pending",
                "agent_running",
            }
            else None
        )
        base_head = bound_base if isinstance(bound_base, str) else plan.code.local_head
        if isinstance(bound_control, str) and bound_control != plan.control.local_head:
            raise ExecutionWorkspaceError(
                "control revision changed after execution was planned; refusing "
                "stale execution"
            )
        if not isinstance(bound_base, str) and plan.code.local_head != base_head:
            raise ExecutionWorkspaceError(
                "planned code revision changed during preparation"
            )
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, plan.ticket_id
        )
        persisted_branch = self._state.get("execution_branch") if same_ticket else None
        persisted_path = self._state.get("execution_path") if same_ticket else None
        if isinstance(persisted_branch, str) and persisted_branch != manager.branch:
            raise ExecutionWorkspaceError(
                f"persisted execution branch {persisted_branch} does not match "
                f"expected branch {manager.branch}"
            )
        if isinstance(persisted_path, str) and Path(persisted_path) != manager.path:
            raise ExecutionWorkspaceError(
                f"persisted execution path {persisted_path} does not match "
                f"expected path {manager.path}"
            )
        workspace = manager.prepare(base_head)
        current_control = _git(
            self.control_worktree, "rev-parse", "HEAD"
        ).stdout.strip()
        if current_control != plan.control.local_head:
            raise ExecutionWorkspaceError(
                "control revision changed during workspace preparation; refusing "
                "stale execution"
            )
        return workspace

    def _publish_execution_branch(
        self,
        workspace: ExecutionWorkspace,
        checkpoint_head: str,
        expected_remote_head: str | None,
    ) -> None:
        observed = _git(workspace.path, "rev-parse", "HEAD").stdout.strip()
        if observed != checkpoint_head:
            raise DevlegateError("execution HEAD changed before branch publication")
        observed_remote_head = self._execution_remote_head(workspace.branch)
        if observed_remote_head != expected_remote_head:
            raise DevlegateError(
                "execution branch remote changed during worker execution"
            )
        pushed = _git(
            workspace.path,
            "push",
            self.remote_name,
            f"HEAD:refs/heads/{workspace.branch}",
            check=False,
        )
        if pushed.returncode:
            raise DevlegateError(pushed.stderr.strip() or "unknown Git push error")

    def _execution_remote_head(self, branch: str) -> str | None:
        remote = _git(
            self.repo,
            "ls-remote",
            self.remote_name,
            f"refs/heads/{branch}",
            check=False,
        )
        if remote.returncode:
            raise DevlegateError(
                remote.stderr.strip() or "cannot observe execution branch remote"
            )
        fields = remote.stdout.split()
        return fields[0] if fields else None

    def _accepted_checkpoint(self, ticket_id: str) -> str:
        root = self.control_worktree / "executions" / ticket_id
        if not root.is_dir() or root.is_symlink():
            raise WorkflowBlockedError(
                f"accepted ticket {ticket_id} has no durable execution evidence"
            )
        reports: list[ExecutionReport] = []
        for path in sorted(root.glob("*.json")):
            if path.is_symlink() or not path.is_file():
                raise WorkflowBlockedError("accepted execution evidence is unsafe")
            try:
                report = ExecutionReport.from_dict(json.loads(path.read_text()))
            except (OSError, json.JSONDecodeError, ExecutionReportError) as error:
                raise WorkflowBlockedError(
                    f"malformed execution evidence for accepted ticket {ticket_id}"
                ) from error
            if (
                report.ticket_id == ticket_id
                and report.result.conclusion == "completed"
            ):
                reports.append(report)
        expected_branch = f"devlegate/work/{ticket_id}"
        remote_head = self._execution_remote_head(expected_branch)
        matches = [
            report for report in reports
            if report.execution_branch == expected_branch
            and isinstance(report.workspace_head, str)
            and report.workspace_head == remote_head
        ]
        if remote_head is None or len(matches) != 1:
            raise WorkflowBlockedError(
                f"accepted ticket {ticket_id} has ambiguous or missing completed "
                "execution evidence"
            )
        checkpoint = matches[0].workspace_head
        assert checkpoint is not None
        if not all(
            len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())
            for value in (
                checkpoint,
                matches[0].code_base_head,
                matches[0].control_head,
            )
        ):
            raise WorkflowBlockedError(
                "accepted execution evidence has invalid Git identity"
            )
        return checkpoint

    def _integrate_accepted(
        self,
        ticket_id: str,
        code: GitObservation | None,
        control: GitObservation | None,
    ) -> str:
        if code is None or control is None:
            raise WorkflowBlockedError("accepted integration lacks Git observations")
        self._validate_control_worktree()
        if not code.working_tree_clean or not control.working_tree_clean:
            raise WorkflowBlockedError("product or control working tree is dirty")
        checkpoint = self._accepted_checkpoint(ticket_id)
        product_ref = f"refs/heads/{self.remote_branch}"
        observed = _git(
            self.repo, "ls-remote", self.remote_name, product_ref, check=False
        )
        if observed.returncode:
            raise DevlegateError(
                observed.stderr.strip() or "cannot observe product branch"
            )
        fields = observed.stdout.split()
        if not fields:
            raise WorkflowBlockedError("configured product branch is unavailable")
        product_head = fields[0]
        def is_ancestor(older: str, newer: str) -> bool:
            return (
                _git(
                    self.repo,
                    "merge-base",
                    "--is-ancestor",
                    older,
                    newer,
                    check=False,
                ).returncode
                == 0
            )

        if product_head == checkpoint or is_ancestor(checkpoint, product_head):
            pass
        elif is_ancestor(product_head, checkpoint):
            pushed = _git(
                self.repo,
                "push",
                self.remote_name,
                f"{checkpoint}:{product_ref}",
                check=False,
            )
            if pushed.returncode:
                raise DevlegateError(
                    pushed.stderr.strip() or "product fast-forward push failed"
                )
        else:
            raise WorkflowBlockedError(
                "product history diverged from accepted checkpoint; v0.4 does not "
                "rebase or merge"
            )
        reobserved = _git(
            self.repo, "ls-remote", self.remote_name, product_ref, check=False
        )
        observed_fields = reobserved.stdout.split()
        if (
            reobserved.returncode
            or not observed_fields
            or (
                observed_fields[0] != checkpoint
                and not is_ancestor(checkpoint, observed_fields[0])
            )
        ):
            raise WorkflowBlockedError(
                "published product checkpoint could not be proven"
            )
        current = _git(self.repo, "rev-parse", "HEAD", check=False)
        status = _git(self.repo, "status", "--porcelain", check=False)
        if current.returncode or status.returncode or status.stdout:
            raise WorkflowBlockedError(
                "product checkout is not safely synchronized"
            )
        if current.stdout.strip() != checkpoint:
            fetch = _git(
                self.repo,
                "fetch",
                self.remote_name,
                self.remote_branch,
                check=False,
            )
            if fetch.returncode:
                raise WorkflowBlockedError("cannot refresh published product branch")
            sync = _git(
                self.repo,
                "merge",
                "--ff-only",
                f"{self.remote_name}/{self.remote_branch}",
                check=False,
            )
            if sync.returncode:
                raise WorkflowBlockedError(
                    "product checkout cannot be fast-forwarded safely"
                )
        return self._complete_accepted(ticket_id, control.local_head)

    def _complete_accepted(self, ticket_id: str, expected_head: str) -> str:
        current = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        if current != expected_head:
            raise WorkflowBlockedError(
                "control HEAD changed during accepted integration"
            )
        store = load_ticket_store(self.control_worktree, self._workflow_paths())
        ticket = store.by_id.get(ticket_id)
        if ticket is None or ticket.state != "accepted":
            raise WorkflowBlockedError("accepted ticket changed during integration")
        target = self.control_worktree / self.done_path / f"{ticket_id}.md"
        ticket.path.rename(target)
        _git(self.control_worktree, "add", "-A")
        commit = _git(
            self.control_worktree,
            "commit",
            "-m",
            f"Devlegate integrate {ticket_id}",
            check=False,
        )
        if commit.returncode:
            raise DevlegateError(
                commit.stderr.strip() or "cannot create control transition"
            )
        push = _git(
            self.control_worktree,
            "push",
            self.remote_name,
            f"HEAD:refs/heads/{self.control_branch}",
            check=False,
        )
        if push.returncode:
            raise DevlegateError(
                push.stderr.strip() or "cannot publish control transition"
            )
        return _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()

    def _apply_execution_lifecycle(self, report: ExecutionReport) -> str:
        self._validate_control_worktree()
        status = _git(self.control_worktree, "status", "--porcelain").stdout
        if status:
            raise WorkflowBlockedError("control working tree is dirty")
        expected_head = report.control_head
        current_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        if current_head != expected_head:
            raise WorkflowBlockedError(
                "control HEAD changed after execution admission; refusing "
                "lifecycle mutation"
            )
        try:
            ticket_store = load_ticket_store(
                self.control_worktree, self._workflow_paths()
            )
        except TicketError as error:
            raise WorkflowBlockedError(str(error)) from error
        ticket = ticket_store.by_id.get(report.ticket_id)
        if ticket is None:
            raise WorkflowBlockedError(
                "execution ticket is no longer in managed storage"
            )
        ExecutionReportStore(self.control_worktree).write(report)
        if report.result.conclusion == "completed" and ticket.state == "todo":
            review = self.control_worktree / self.review_path / f"{ticket.id}.md"
            if review.exists() or review.is_symlink():
                raise WorkflowBlockedError("review ticket already exists")
            ticket.path.rename(review)
        try:
            load_ticket_store(self.control_worktree, self._workflow_paths())
        except TicketError as error:
            raise WorkflowBlockedError(
                f"resulting ticket store is invalid: {error}"
            ) from error
        _git(self.control_worktree, "add", "-A")
        committed = _git(
            self.control_worktree,
            "commit",
            "-m",
            f"Devlegate lifecycle {report.ticket_id} {report.execution_id} "
            f"{report.result.conclusion}",
            check=False,
        )
        if committed.returncode:
            raise DevlegateError(
                committed.stderr.strip() or "cannot create control-plane commit"
            )
        pushed = _git(
            self.control_worktree,
            "push",
            self.remote_name,
            f"HEAD:refs/heads/{self.control_branch}",
            check=False,
        )
        if pushed.returncode:
            raise DevlegateError(pushed.stderr.strip() or "unknown Git push error")
        return _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()

    def _run_worker(
        self, workspace: ExecutionWorkspace, prompt: str
    ) -> WorkerRunResult:
        """Run a worker only in the validated execution workspace."""
        if not isinstance(workspace, ExecutionWorkspace):
            raise TypeError("worker workspace must be ExecutionWorkspace")
        if not isinstance(prompt, str):
            raise TypeError("worker prompt must be text")
        execution_path = workspace.path.resolve()
        command = [
            self.opencode_bin,
            "run",
            "--dir",
            str(execution_path),
            "--format",
            "json",
            "--model",
            self.opencode_model,
        ]
        if self.opencode_agent:
            command.extend(("--agent", self.opencode_agent))
        parser = WorkerEgressParser()
        config_dir = Path(tempfile.mkdtemp(prefix="devlegate-opencode-"))
        tool_dir = config_dir / "tools"
        tool_dir.mkdir()
        (tool_dir / "devlegate_report.ts").write_text(
            '''import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Report the worker's semantic claim to Devlegate.",
  args: {
    outcome: tool.schema.enum(["completed", "incomplete", "blocked"]),
    summary: tool.schema.string(),
    remaining: tool.schema.array(tool.schema.string()),
    questions: tool.schema.array(tool.schema.string()),
  },
  async execute() {
    return "devlegate_report accepted"
  },
})
'''
        )
        config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {
                "external_directory": {
                    "*": "deny",
                    f"{execution_path}/**": "allow",
                }
            },
        }
        environment = os.environ.copy()
        environment["PWD"] = str(execution_path)
        environment["OPENCODE_CONFIG_DIR"] = str(config_dir)
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(
            config, sort_keys=True
        )
        try:
            stop_request = getattr(self, "_stop_event", None)
            opencode_result = _run_opencode(
                command,
                prompt,
                cwd=execution_path,
                env=environment,
                event_handler=parser.consume,
                **(
                    {"stop_request": stop_request}
                    if stop_request is not None
                    else {}
                ),
            )
            claim, egress_error = parser.finish()
            return WorkerRunResult(
                opencode_result.process_returncode,
                opencode_result.transport_error,
                claim,
                egress_error,
                opencode_result.interruption_kind,
            )
        except OSError as error:
            return WorkerRunResult(-1, str(error), None, None)
        finally:
            shutil.rmtree(config_dir, ignore_errors=True)

    def run(self, once: bool) -> int:
        """Run the legacy foreground polling fallback."""
        return self._run_polling(once=once)

    def serve(self, stop_event: threading.Event) -> int:
        """Run the foreground service until the host requests a stop."""
        return self._run_polling(once=False, stop_event=stop_event)

    def _run_polling(
        self,
        *,
        once: bool,
        stop_event: threading.Event | None = None,
    ) -> int:
        with self._stop_context(stop_event), self._lock():
            self._publish_service_snapshot(lifecycle="polling", worker_running=False)
            while True:
                if (
                    stop_event is not None
                    and stop_event.is_set()
                    and self._state.get("phase") != "merge_pending"
                ):
                    self._publish_service_snapshot(lifecycle="ready")
                    return 0
                workflow_blocked = False
                try:
                    status = self.run_once()
                except WorkflowBlockedError as error:
                    workflow_blocked = True
                    message = str(error)
                    self._publish_service_snapshot(
                        lifecycle="blocked",
                        worker_running=False,
                        blocked_reason=message,
                    )
                    fingerprint = hashlib.sha256(message.encode()).hexdigest()
                    if self._workflow_blocker_fingerprint is None:
                        _log(f"workflow became blocked: {message}")
                    elif self._workflow_blocker_fingerprint != fingerprint:
                        _log(f"workflow remains blocked: {message}")
                    self._workflow_blocker_fingerprint = fingerprint
                    status = 1
                except (OSError, subprocess.CalledProcessError) as error:
                    detail = getattr(error, "stderr", None) or str(error)
                    _log(f"run failed: {detail.strip()}")
                    status = 1
                else:
                    if (
                        self._workflow_blocker_fingerprint is not None
                        and self._workflow_validation_succeeded
                    ):
                        _log("workflow is valid again")
                        self._workflow_blocker_fingerprint = None
                if status and not workflow_blocked:
                    if not (status == 1 and self._dirty_fingerprint is not None):
                        _log(f"run failed with status {status}")
                if once:
                    if not self.service_snapshot().worker_running:
                        self._publish_service_snapshot(
                            lifecycle="blocked" if workflow_blocked else "ready"
                        )
                    return status
                if stop_event is not None:
                    if stop_event.wait(int(self.poll_interval)):
                        self._publish_service_snapshot(lifecycle="ready")
                        return 0
                else:
                    time.sleep(int(self.poll_interval))

    def _status_snapshot_hook(self, _point: str) -> None:
        """Testing hook for deterministic snapshot-race simulations."""

    def _status_state_observation(self) -> tuple[dict[str, object], str]:
        try:
            state, fingerprint = self._runtime_store.observe()
        except RuntimeStoreError as error:
            raise DevlegateError(str(error)) from error
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise DevlegateError(f"invalid state file: {self._state_file}")
        self._validate_state_invariant(state)
        return state, fingerprint

    def _git_observation(self, repo: Path, remote_branch: str) -> GitObservation:
        branch_result = _git(
            repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        detached = branch_result.returncode != 0
        branch = branch_result.stdout.strip() if not detached else None
        local_head = _git(repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{remote_branch}"
        remote_result = _git(repo, "rev-parse", "--verify", remote_ref, check=False)
        status = _git(repo, "status", "--porcelain").stdout
        return GitObservation(
            branch,
            detached,
            local_head,
            remote_ref,
            remote_result.stdout.strip() if remote_result.returncode == 0 else None,
            not bool(status),
            _status_fingerprint(status),
        )

    def _status_git_observation(self) -> dict[str, GitObservation | None]:
        control = None
        if self.control_worktree.is_dir():
            try:
                self._validate_control_worktree()
            except WorkflowBlockedError:
                pass
            else:
                control = self._git_observation(
                    self.control_worktree, self.control_branch
                )
        return {
            "code": self._git_observation(self.repo, self.remote_branch),
            "control": control,
        }

    def _evaluate_failed_executions(
        self,
        failures: object,
        ticket_store: TicketStore,
        code: GitObservation,
        control: GitObservation | None,
        admission_reason: str | None = None,
    ) -> tuple[FailedExecution, ...]:
        if not isinstance(failures, dict):
            return ()
        todo_fingerprint = (
            _todo_fingerprint(self.control_worktree, self.todo_path)[0]
            if control is not None
            else None
        )
        evaluated: list[FailedExecution] = []
        for ticket_id, metadata in sorted(failures.items()):
            if not isinstance(ticket_id, str) or not isinstance(metadata, dict):
                continue
            ticket = ticket_store.by_id.get(ticket_id)
            reason = str(metadata.get("reason", "unknown failure"))
            invalid_reason: str | None = None
            if ticket is None:
                invalid_reason = "ticket is no longer present"
                title = ticket_id
            else:
                title = ticket.title
                if ticket.state != "todo":
                    invalid_reason = f"ticket is no longer todo ({ticket.state})"
                elif ticket not in ticket_store.runnable:
                    invalid_reason = "ticket is no longer runnable"
                elif control is None:
                    invalid_reason = "control worktree is unavailable"
                elif metadata.get("product_head") != code.local_head:
                    invalid_reason = "product HEAD changed since the failure"
                elif metadata.get("control_head") != control.local_head:
                    invalid_reason = "control generation changed since the failure"
                elif metadata.get("remote_head") != code.remote_head:
                    invalid_reason = "product generation changed since the failure"
                elif metadata.get("todo_fingerprint") != todo_fingerprint:
                    invalid_reason = "todo workflow changed since the failure"
            report = None
            if invalid_reason is None:
                try:
                    report = ExecutionReportStore(self.control_worktree).read(
                        ticket_id, str(metadata["execution_id"])
                    )
                except (ExecutionReportError, KeyError):
                    if metadata.get("interrupted") is not True:
                        invalid_reason = "failed execution report is unavailable"
            if invalid_reason is None and report is not None:
                if report.result.conclusion != "failed":
                    invalid_reason = "stored execution report is not failed"
            if invalid_reason is None and admission_reason is not None:
                invalid_reason = admission_reason
            evaluated.append(
                FailedExecution(
                    ticket_id,
                    title,
                    reason,
                    invalid_reason is None,
                    invalid_reason,
                    metadata.get("interruption_kind")
                    if metadata.get("interruption_kind")
                    in {"operator_abort", "service_shutdown"}
                    else None,
                )
            )
        return tuple(evaluated)

    def _collect_status_attempt(
        self, *, allow_workflow_blocked: bool = False
    ) -> StatusSnapshot:
        state_before, state_token_before = self._status_state_observation()
        self._status_snapshot_hook("after-state-before")
        git_before = self._status_git_observation()
        self._status_snapshot_hook("after-git-before")
        try:
            workflow_token_before = _workflow_fingerprint(
                self.control_worktree, self._workflow_paths()
            )
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            raise DevlegateError(f"cannot observe workflow files: {error}") from error
        self._status_snapshot_hook("after-workflow-before")

        ticket_error: DevlegateError | None = None
        ticket_store = None
        try:
            ticket_store = self._ticket_store()
        except DevlegateError as error:
            ticket_error = error
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            ticket_error = DevlegateError(f"cannot read ticket storage: {error}")

        self._status_snapshot_hook("before-verify")
        try:
            workflow_token_after = _workflow_fingerprint(
                self.control_worktree, self._workflow_paths()
            )
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            raise DevlegateError(f"cannot observe workflow files: {error}") from error
        git_after = self._status_git_observation()
        state_after, state_token_after = self._status_state_observation()
        if (
            state_token_before != state_token_after
            or git_before != git_after
            or workflow_token_before != workflow_token_after
        ):
            raise SnapshotChanged
        if ticket_error is not None and not allow_workflow_blocked:
            raise ticket_error
        return self._make_status_snapshot(
            state_after, git_after, ticket_store, workflow_error=ticket_error
        )

    def _make_status_snapshot(
        self,
        state: dict[str, object],
        git_observation: dict[str, GitObservation | None],
        ticket_store: TicketStore | None,
        workflow_error: DevlegateError | None = None,
    ) -> StatusSnapshot:
        code = git_observation["code"]
        control = git_observation["control"]
        assert code is not None
        if ticket_store is None:
            return StatusSnapshot(
                phase=str(state["phase"]),
                bound_ticket_id=None,
                persisted_body_present=False,
                code=code,
                control=control,
                counts=tuple(
                    (state_name, 0)
                    for state_name in ("backlog", "todo", "review", "accepted", "done")
                ),
                runnable=(),
                blocked=(),
                review=(),
                accepted=(),
                next_ticket=None,
                plan=ExecutionPlan(
                    "blocked",
                    str(workflow_error or "workflow is invalid"),
                    code=code,
                    control=control,
                ),
            )
        counts = tuple(
            (
                state_name,
                sum(ticket.state == state_name for ticket in ticket_store.tickets),
            )
            for state_name in ("backlog", "todo", "review", "accepted", "done")
        )
        runnable = tuple((ticket.id, ticket.title) for ticket in ticket_store.runnable)
        blocked = tuple(
            (
                ticket.id,
                ticket.title,
                tuple(
                    (dependency, ticket_store.by_id[dependency].state)
                    for dependency in sorted(ticket.depends_on)
                    if ticket_store.by_id[dependency].state != "done"
                ),
            )
            for ticket in ticket_store.tickets
            if ticket.state == "todo"
            and any(
                ticket_store.by_id[dependency].state != "done"
                for dependency in ticket.depends_on
            )
        )
        review = tuple(
            (ticket.id, ticket.title)
            for ticket in ticket_store.tickets
            if ticket.state == "review"
        )
        accepted = tuple(
            (ticket.id, ticket.title)
            for ticket in ticket_store.tickets
            if ticket.state == "accepted"
        )
        selected_id = state.get("selected_ticket_id")
        bound_phase = str(state["phase"]) in {
            "agent_pending",
            "agent_running",
        }
        plan = self._make_execution_plan(
            state,
            ticket_store,
            not code.working_tree_clean or not (control and control.working_tree_clean),
            not code.detached
            and code.branch == self.remote_branch
            and control is not None
            and not control.detached
            and control.branch == self.control_branch,
            observation=git_observation,
        )
        admission_reason = (
            None
            if plan.action == "run-worker" and not plan.bound
            else plan.reason
        )
        failures = state.get("failed_executions", {})
        failed_executions = self._evaluate_failed_executions(
            failures, ticket_store, code, control, admission_reason
        )
        next_ticket = (
            (
                ticket_store.by_id[plan.ticket_id].id,
                ticket_store.by_id[plan.ticket_id].title,
            )
            if plan.ticket_id is not None and plan.ticket_id in ticket_store.by_id
            else None
        )
        return StatusSnapshot(
            phase=str(state["phase"]),
            bound_ticket_id=(
                selected_id if bound_phase and isinstance(selected_id, str) else None
            ),
            persisted_body_present=bound_phase
            and isinstance(state.get("selected_ticket_body"), str),
            code=code,
            control=control,
            counts=counts,
            runnable=runnable,
            blocked=blocked,
            review=review,
            accepted=accepted,
            next_ticket=next_ticket,
            plan=plan,
            failed_executions=failed_executions,
        )

    def _make_execution_plan(
        self,
        state: dict[str, object],
        ticket_store: TicketStore,
        dirty: bool,
        branch_ok: bool = True,
        observation: dict[str, GitObservation | None] | None = None,
    ) -> ExecutionPlan:
        observation = observation or {}
        code = observation.get("code")
        control = observation.get("control")
        assert isinstance(code, GitObservation)
        if not branch_ok:
            reason = "control worktree is unavailable or on the wrong branch"
            if code.detached:
                reason = "detached HEAD is not supported"
            return ExecutionPlan(
                "blocked",
                reason,
                code=code,
                control=control,
            )
        if dirty:
            reason = "code or control working tree is dirty"
            blocked = True
        else:
            reason = ""
            blocked = False
        identity = {"code": code, "control": control}
        if blocked:
            return ExecutionPlan("blocked", reason, **identity)
        boundary = tuple(
            ticket
            for ticket in ticket_store.tickets
            if ticket.state in {"review", "accepted"}
        )
        if len(boundary) > 1:
            return ExecutionPlan(
                "blocked",
                "multiple tickets occupy the review/accepted serial boundary",
                **identity,
            )
        bound_phase = str(state["phase"]) in {
            "agent_pending",
            "agent_running",
        }
        if bound_phase:
            if state["phase"] == "agent_running":
                return ExecutionPlan(
                    "blocked",
                    "interrupted execution has ambiguous outcome; explicit "
                    "operator handling is required",
                    **identity,
                )
            ticket_id = state.get("selected_ticket_id")
            ticket = (
                ticket_store.by_id.get(ticket_id)
                if isinstance(ticket_id, str)
                else None
            )
            if ticket is None:
                return ExecutionPlan(
                    "blocked",
                    "persisted selected ticket is not in managed storage",
                    **identity,
                )
            if ticket.state not in {"todo", "review"}:
                return ExecutionPlan(
                    "blocked",
                    "selected ticket "
                    f"{ticket.id} has unexpected bound execution state {ticket.state}",
                    **identity,
                )
            return ExecutionPlan(
                "run-worker",
                "resume persisted bound execution",
                ticket.id,
                ticket.title,
                ticket.state,
                True,
                **identity,
            )
        if boundary:
            ticket = boundary[0]
            if ticket.state == "review":
                return ExecutionPlan(
                    "none", f"waiting for review: {ticket.id}", ticket.id,
                    ticket.title, ticket.state, False, **identity
                )
            return ExecutionPlan(
                "integrate", f"accepted, ready for integration: {ticket.id}",
                ticket.id, ticket.title, ticket.state, False, **identity
            )
        selected = ticket_store.selected()
        if selected is None:
            return ExecutionPlan("none", "no runnable tickets", **identity)
        return ExecutionPlan(
            "run-worker",
            "deterministic runnable ticket selection",
            selected.id,
            selected.title,
            selected.state,
            False,
            **identity,
        )

    def status_view(self) -> StatusSnapshot:
        """Collect a stable, read-only external status view."""
        for _attempt in range(3):
            try:
                snapshot = self._collect_status_attempt(allow_workflow_blocked=True)
                break
            except SnapshotChanged:
                continue
        else:
            raise DevlegateError(
                "project state changed while status snapshot was being collected"
            )
        self._publish_status_snapshot(snapshot)
        return snapshot

    def status(self) -> StatusSnapshot:
        """Compatibility spelling for the read-only status projection."""
        return self.status_view()

    def _retry_candidates(self) -> tuple[tuple[str, str, str], ...]:
        snapshot = self._collect_status_attempt(allow_workflow_blocked=False)
        candidates: list[tuple[str, str, str]] = []
        for failure in snapshot.failed_executions:
            if failure.retryable:
                candidates.append((failure.ticket_id, failure.title, failure.reason))
        return tuple(candidates)

    def _interrupted_retry_candidate(self) -> RetryCandidate | None:
        state = getattr(self, "_state", {})
        if not isinstance(state, dict) or state.get("phase") != "agent_running":
            return None
        if state.get("execution_stage") not in {None, "pre-checkpoint"}:
            return None
        ticket_id = state.get("execution_ticket_id")
        if not isinstance(ticket_id, str) or not ticket_id:
            return None
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(ticket_id)
        if (
            ticket is None
            or ticket.state != "todo"
            or ticket not in ticket_store.runnable
        ):
            return None
        return RetryCandidate(
            ticket_id=ticket.id,
            title=ticket.title,
            reason="interrupted execution; recovery validation required",
            kind="interrupted",
        )

    def _interactive_retry_candidates(self) -> tuple[RetryCandidate, ...]:
        candidates = [
            RetryCandidate(ticket_id, title, reason, "failed")
            for ticket_id, title, reason in self._retry_candidates()
        ]
        interrupted = self._interrupted_retry_candidate()
        if interrupted is not None and all(
            candidate.ticket_id != interrupted.ticket_id for candidate in candidates
        ):
            candidates.append(interrupted)
        return tuple(candidates)

    def _retry_interactive_candidate(self, candidate: RetryCandidate) -> int:
        with self._lock():
            if candidate.kind == "interrupted":
                self._recover_interrupted_execution(candidate.ticket_id)
            return self._retry_locked(candidate.ticket_id)

    def retry(self, ticket_id: str | None = None) -> int:
        """Authorize exactly one fresh execution after current-state validation."""
        if ticket_id is not None and self._state.get("phase") == "agent_running":
            with self._lock():
                self._recover_interrupted_execution(ticket_id)
                return self._retry_locked(ticket_id)
        if ticket_id is None:
            try:
                interactive = os.isatty(sys.stdin.fileno()) and os.isatty(
                    sys.stdout.fileno()
                )
            except (OSError, ValueError):
                interactive = False
            if not interactive:
                raise DevlegateError(
                    "interactive retry requires a terminal; specify a ticket ID:\n"
                    "devlegate retry <ticket-id>"
                )
            candidates = self._interactive_retry_candidates()
            if not candidates:
                raise DevlegateError(
                    "no current executions are retryable or recoverable"
                )
            print("Retry candidates:")
            for index, candidate in enumerate(candidates, 1):
                print(f"  {index}) {candidate.ticket_id}  {candidate.title}")
                print(f"     {candidate.reason}")
            print("  0) Cancel")
            answer = input("Select number (Enter = cancel): ").strip()
            if not answer or answer == "0":
                return 0
            try:
                index = int(answer)
                if not 1 <= index <= len(candidates):
                    raise ValueError
            except ValueError as error:
                raise DevlegateError("invalid retry selection") from error
            return self._retry_interactive_candidate(candidates[index - 1])
        with self._lock():
            return self._retry_locked(ticket_id)

    def _retry_locked(self, ticket_id: str) -> int:
        candidates = self._retry_candidates()
        candidate_ids = {candidate[0] for candidate in candidates}
        if ticket_id not in candidate_ids:
            raise DevlegateError(f"ticket {ticket_id} is not currently retryable")
        self._retry_ticket_id = ticket_id
        try:
            return self.run_once()
        finally:
            self._retry_ticket_id = None

    def _recover_interrupted_execution(self, ticket_id: str) -> None:
        """Authorize recovery of a stranded post-worker execution only by identity."""
        state = self._state
        if state.get("execution_ticket_id") != ticket_id:
            raise DevlegateError(
                f"ticket {ticket_id} does not match the persisted interrupted execution"
            )
        required = (
            "local_head",
            "remote_head",
            "control_head",
            "selected_ticket_id",
            "selected_ticket_body",
            "execution_base_head",
            "execution_control_head",
            "execution_branch",
            "execution_path",
            "execution_id",
        )
        if not all(
            isinstance(state.get(field), str) and state[field] for field in required
        ):
            raise DevlegateError(
                "persisted interrupted execution binding is incomplete"
            )
        if state.get("execution_stage") not in {None, "pre-checkpoint"}:
            raise DevlegateError(
                "persisted execution reached an ambiguous post-worker stage; "
                "manual operator handling is required"
            )
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(ticket_id)
        if (
            ticket is None
            or ticket.state != "todo"
            or ticket not in ticket_store.runnable
            or state["selected_ticket_id"] != ticket_id
            or state["selected_ticket_body"] != ticket.body
        ):
            raise DevlegateError(
                f"ticket {ticket_id} is not the same managed todo execution"
            )
        current_local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if current_local_head != state["local_head"]:
            raise DevlegateError("product checkout changed since interrupted execution")
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        product_fetch = _git(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        )
        if product_fetch.returncode:
            raise DevlegateError(
                "cannot freshly observe product remote before interrupted recovery: "
                f"{product_fetch.stderr.strip() or 'unknown git error'}"
            )
        current_remote = _git(
            self.repo, "rev-parse", "--verify", remote_ref, check=False
        )
        if (
            current_remote.returncode
            or current_remote.stdout.strip() != state["remote_head"]
        ):
            raise DevlegateError(
                "product remote generation changed since interrupted execution"
            )
        self._validate_control_worktree()
        control_fetch = _git(
            self.control_worktree,
            "fetch",
            "--prune",
            self.remote_name,
            self.control_branch,
            check=False,
        )
        if control_fetch.returncode:
            raise DevlegateError(
                "cannot freshly observe control remote before interrupted recovery: "
                f"{control_fetch.stderr.strip() or 'unknown git error'}"
            )
        control_remote_ref = f"{self.remote_name}/{self.control_branch}"
        current_control_remote = _git(
            self.control_worktree,
            "rev-parse",
            "--verify",
            control_remote_ref,
            check=False,
        )
        if (
            current_control_remote.returncode
            or current_control_remote.stdout.strip() != state["control_head"]
        ):
            raise DevlegateError(
                "control remote generation changed since interrupted execution"
            )
        current_control_head = _git(
            self.control_worktree, "rev-parse", "HEAD"
        ).stdout.strip()
        execution_control_head = state["execution_control_head"]
        if (
            current_control_head != state["control_head"]
            or current_control_head != execution_control_head
        ):
            raise DevlegateError(
                "control generation changed since interrupted execution"
            )
        todo_fingerprint, _todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        handled_fingerprint = state.get("handled_todo_fingerprint")
        if (
            isinstance(handled_fingerprint, str)
            and handled_fingerprint
            and handled_fingerprint != todo_fingerprint
        ):
            raise DevlegateError("todo workflow changed since interrupted execution")
        base_head = state["execution_base_head"]
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        if (
            state["execution_branch"] != manager.branch
            or Path(state["execution_path"]) != manager.path
        ):
            raise DevlegateError("persisted execution workspace binding is invalid")
        try:
            manager._validate_base(base_head)
            manager._validate_branch(base_head)
            registrations = manager._registrations()
            workspace = manager._validate_existing(
                registrations.get(manager.path.resolve()), base_head
            )
            execution_start_head = state.get("execution_start_head")
            execution_remote_head = state.get("execution_remote_head")
            if execution_start_head is not None and (
                not isinstance(execution_start_head, str) or not execution_start_head
            ):
                raise ExecutionWorkspaceError(
                    "persisted execution start HEAD is invalid"
                )
            if execution_start_head is not None:
                expected_start_head = execution_start_head
            elif isinstance(execution_remote_head, str) and execution_remote_head:
                observed_remote_head = self._execution_remote_head(manager.branch)
                if observed_remote_head != execution_remote_head:
                    raise ExecutionWorkspaceError(
                        "execution worktree remote HEAD changed before recovery"
                    )
                expected_start_head = execution_remote_head
            elif execution_remote_head is None:
                expected_start_head = base_head
            else:
                raise ExecutionWorkspaceError(
                    "persisted execution remote HEAD is invalid"
                )
            if workspace.head != expected_start_head:
                raise ExecutionWorkspaceError(
                    "execution worktree HEAD does not match the execution start HEAD"
                )
            manager.verify_submodules(workspace)
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(
                f"cannot safely recover interrupted execution: {error}"
            ) from error
        failed_executions = state.get("failed_executions", {})
        if not isinstance(failed_executions, dict):
            failed_executions = {}
        failed_executions = {
            **failed_executions,
            ticket_id: {
                "execution_id": state["execution_id"],
                "product_head": state["local_head"],
                "remote_head": state["remote_head"],
                "control_head": state["control_head"],
                "todo_fingerprint": todo_fingerprint,
                "reason": "interrupted execution requires explicit retry",
                "interrupted": True,
            },
        }
        self._save_state(
            "idle",
            handled_remote_head=state["remote_head"],
            handled_control_head=state["control_head"],
            handled_todo_fingerprint=todo_fingerprint,
            execution_control_head=state["execution_control_head"],
            interrupted_execution_id=state["execution_id"],
            failed_executions=failed_executions,
        )

    def _agent_render_context(self) -> RenderContext:
        return RenderContext(
            control_branch=self.control_branch,
            backlog_path=self.backlog_path,
            todo_path=self.todo_path,
            review_path=self.review_path,
            accepted_path=self.accepted_path,
            done_path=self.done_path,
            product_branch=self.remote_branch,
        )

    def init_project(self, conflicts: str = "abort") -> int:
        try:
            _total, changed = initialize_project(
                self.repo,
                self._agent_render_context(),
                conflicts=conflicts,
                state_dir=self.state_dir,
            )
        except AgentProtocolError as error:
            raise DevlegateError(str(error)) from error
        print(
            "initialized Devlegate project protocol templates "
            f"and rendered {changed} agent protocol artifacts"
        )
        return 0

    def render(self, check: bool = False) -> int:
        try:
            total, changed = render_project(
                self.repo, self._agent_render_context(), check=check
            )
        except AgentProtocolError as error:
            raise DevlegateError(str(error)) from error
        if check:
            print("generated agent protocol artifacts are current")
        else:
            print(f"rendered {total} agent protocol artifacts ({changed} changed)")
        return 0

    def plan_view(self) -> ExecutionPlan:
        """Collect the current read-only scheduling decision."""
        for _attempt in range(3):
            try:
                snapshot = self._collect_status_attempt(allow_workflow_blocked=True)
                break
            except SnapshotChanged:
                continue
        else:
            raise DevlegateError(
                "project state changed while execution plan was being collected"
            )
        blocked_reason = (
            snapshot.plan.reason if snapshot.plan.action == "blocked" else None
        )
        worker_running = self.service_snapshot().worker_running
        lifecycle = "worker" if worker_running else (
            "blocked" if blocked_reason else "ready"
        )
        if worker_running:
            blocked_reason = None
        self._publish_service_snapshot(
            lifecycle=lifecycle,
            product=snapshot.code,
            control=snapshot.control,
            blocked_reason=blocked_reason,
        )
        return snapshot.plan

    def plan(self) -> ExecutionPlan:
        """Compatibility spelling for the read-only plan projection."""
        return self.plan_view()

    def check(self) -> int:
        """Run read-only configuration and checkout diagnostics."""
        print(f"Devlegate {__version__} preflight")
        status = _git(self.repo, "status", "--porcelain").stdout
        control_observation = self._status_git_observation()["control"]
        todo_fingerprint, todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        ticket_store = None
        ticket_error = ""
        try:
            ticket_store = self._ticket_store()
        except DevlegateError as error:
            ticket_error = str(error)
        project_context_error = ""
        try:
            load_project_context(self.repo)
        except ProjectContextError as error:
            project_context_error = str(error)
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        remote_result = _git(self.repo, "rev-parse", remote_ref, check=False)
        remote_head = remote_result.stdout.strip()
        generation_differs = (
            str(self._state.get("handled_remote_head", "")) != remote_head
            or str(self._state.get("handled_control_head", ""))
            != (
                _git(
                    self.control_worktree, "rev-parse", "HEAD", check=False
                ).stdout.strip()
                if self.control_worktree.is_dir()
                else ""
            )
            or str(self._state.get("handled_todo_fingerprint", "")) != todo_fingerprint
        )
        checks = [
            (
                "OPENCODE_MODEL",
                bool(self.opencode_model),
                "set OPENCODE_MODEL in the effective configuration",
            ),
            ("repository root", self.repo.is_dir(), str(self.repo)),
            (
                "product branch",
                bool(self.current_branch)
                and self.current_branch == self.remote_branch,
                f"current={self.current_branch or '<detached>'}, "
                f"configured={self.remote_branch or '<unset>'}",
            ),
            (
                "configured remote",
                _git(
                    self.repo, "remote", "get-url", self.remote_name, check=False
                ).returncode
                == 0,
                self.remote_name,
            ),
            (
                "poll interval",
                self.poll_interval.isdecimal(),
                "POLL_INTERVAL must be an integer",
            ),
            ("OpenCode executable", shutil.which(self.opencode_bin) is not None, ""),
            (
                "state directory",
                not self.state_dir.is_symlink()
                and (not self.state_dir.exists() or self.state_dir.is_dir()),
                str(self.state_dir),
            ),
            (
                "working tree clean",
                not bool(status),
                "",
            ),
            ("ticket schema", not ticket_error, ticket_error),
            ("dependency graph", not ticket_error, ticket_error),
            (
                "project context",
                not project_context_error,
                project_context_error,
            ),
            (
                "control worktree",
                control_observation is not None,
                f"{self.control_worktree} (run 'devlegate control init')",
            ),
            (
                "control branch",
                control_observation is not None
                and control_observation.branch == self.control_branch,
                self.control_branch,
            ),
            (
                "control remote",
                control_observation is not None
                and control_observation.remote_head is not None,
                f"{self.remote_name}/{self.control_branch}",
            ),
            *[
                (
                    f"workflow directory ({state})",
                    (self.control_worktree / configured_path).is_dir(),
                    str(self.control_worktree / configured_path),
                )
                for state, configured_path in self._workflow_paths().items()
            ],
            (
                "control working tree clean",
                control_observation is not None
                and control_observation.working_tree_clean,
                "",
            ),
        ]
        failed = False
        for name, passed, detail in checks:
            label = "OK" if passed else "FAIL"
            suffix = f": {detail}" if detail else ""
            print(f"{label:4}  {name}{suffix}")
            failed |= not passed
        if status:
            print("Dirty working tree details:")
            for line in status.rstrip().splitlines():
                print(f"  {line}")
            for name, count in _status_summary(status).items():
                print(f"{name.replace('_', ' ')}: {count}")
        print(f"local HEAD: {local_head}")
        print(f"known remote HEAD: {remote_head or '<unknown>'}")
        if remote_head:
            counts = _git(
                self.repo,
                "rev-list",
                "--left-right",
                "--count",
                f"{local_head}...{remote_head}",
                check=False,
            ).stdout.strip()
            count_parts = counts.split()
            display_counts = (
                f"{count_parts[0]} {count_parts[1]}"
                if len(count_parts) == 2
                else "<unknown>"
            )
            print(f"ahead/behind: {display_counts}")
        print(f"todo files: {todo_count}")
        print(f"todo fingerprint: {todo_fingerprint}")
        if self.control_worktree.is_dir():
            print(
                "control HEAD: "
                + _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
            )
        if ticket_store is not None:
            counts = {
                state: sum(ticket.state == state for ticket in ticket_store.tickets)
                for state in ("backlog", "todo", "review", "accepted", "done")
            }
            print("Ticket storage:")
            for state in ("backlog", "todo", "review", "accepted", "done"):
                print(f"  {state}: {counts[state]}")
            print(f"Runnable tickets: {len(ticket_store.runnable)}")
            selected = ticket_store.selected()
            if selected is not None:
                print(f"Next runnable: {selected.id} - {selected.title}")
        elif ticket_error:
            print(f"Ticket storage: invalid: {ticket_error}")
        print(f"work generation differs from persisted: {generation_differs}")
        print(
            "remote information is from the existing remote-tracking ref; "
            "check does not fetch"
        )
        if failed:
            print("\nNot ready.")
            return 1
        print("\nReady.")
        return 0


# Compatibility import for callers of the pre-ServiceEngine runtime surface.
Devlegate = ServiceEngine
