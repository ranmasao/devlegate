# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
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
import threading
from contextlib import contextmanager, nullcontext
from pathlib import Path

from devlegate import __version__
from devlegate.agent_protocol import (
    AgentProtocolError,
    RenderContext,
    initialize_project,
    render_project,
)
from devlegate.commit_messages import integration_message, lifecycle_message
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
from devlegate.operational_log import service_log
from devlegate.platform_support import HOSTED_RUNTIME_ERROR, hosted_runtime_supported
from devlegate.project_context import ProjectContextError, load_project_context
from devlegate.runtime_locator import (
    RuntimeLocator,
    RuntimeLocatorError,
    read_env,
    repository_root,
)
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore
from devlegate.tickets import (
    TicketError,
    TicketStore,
    is_canonical_ticket_name,
    load_ticket_store,
)
from devlegate.worker_prompt import (
    WorkDirective,
    WorkerPromptInput,
    build_worker_prompt,
)
from devlegate.worker_supervisor import (
    WorkerAdmissionClosed,
    WorkerProcessIdentity,
    WorkerSupervisor,
)


class DevlegateError(Exception):
    """A user-facing startup error."""


class WorkflowBlockedError(DevlegateError):
    """The repository cannot currently be interpreted safely for scheduling."""


class ShutdownInterrupted(Exception):
    """A requested shutdown interrupted a foreground Git child."""


@dataclasses.dataclass(frozen=True)
class IterationIntent:
    """Explicit operator intent supplied to one scheduler iteration."""

    retry_ticket_id: str | None = None


@dataclasses.dataclass(frozen=True)
class ExecutionAuthorization:
    """Iteration-local authorization for one retry or automatic resume."""

    ticket_id: str
    kind: str

    @property
    def is_automatic_resume(self) -> bool:
        return self.kind == "automatic_resume"

    @property
    def is_explicit_retry(self) -> bool:
        return self.kind == "explicit_retry"

    @property
    def is_zero_delta_retry(self) -> bool:
        return self.kind == "zero_delta_product_drift"


class SnapshotChanged(Exception):
    """The observed project changed during a status snapshot attempt."""


_UNSET = object()


def _is_git_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value.lower())
    )


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
class AdmissionBarrier:
    action: str
    kind: str
    reason: str
    ticket_id: str | None = None


@dataclasses.dataclass(frozen=True)
class BlockedReason:
    kind: str
    ticket_id: str | None = None
    tickets: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        if self.kind == "dependencies":
            return {
                "kind": self.kind,
                "tickets": [
                    {"id": ticket_id, "state": state}
                    for ticket_id, state in self.tickets
                ],
            }
        return {
            "kind": self.kind,
            **({"ticket_id": self.ticket_id} if self.ticket_id is not None else {}),
        }


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    phase: str
    bound_ticket_id: str | None
    persisted_body_present: bool
    code: GitObservation
    control: GitObservation | None
    counts: tuple[tuple[str, int], ...]
    runnable: tuple[tuple[str, str], ...] = dataclasses.field(compare=False)
    blocked: tuple[tuple[str, str, BlockedReason], ...]
    review: tuple[tuple[str, str], ...]
    accepted: tuple[tuple[str, str], ...]
    next_ticket: tuple[str, str] | None
    plan: ExecutionPlan
    failed_executions: tuple["FailedExecution", ...] = ()
    reconciliation: dict[str, object] | None = None
    execution_stage: str | None = None
    execution_id: str | None = None
    bound_ticket_title: str | None = None
    lifecycle_integration: tuple[str, str] | None = None
    eligible: tuple[tuple[str, str], ...] = ()

    def as_dict(self) -> dict[str, object]:
        return {
            "execution": {
                "phase": self.phase,
                "stage": self.execution_stage if self.bound_ticket_id else None,
                "execution_id": self.execution_id if self.bound_ticket_id else None,
                "bound_ticket": self.bound_ticket_id,
                "ticket_title": self.bound_ticket_title,
                "persisted_body": self.persisted_body_present,
            },
            "observation": {
                "code": self.code.as_dict(),
                "control": self.control.as_dict() if self.control else None,
            },
            "tickets": {
                "counts": dict(self.counts),
                "eligible": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.eligible
                ],
                "blocked": [
                    {
                        "id": ticket_id,
                        "title": title,
                        "reason": reason.as_dict(),
                    }
                    for ticket_id, title, reason in self.blocked
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
            "reconciliation": self.reconciliation,
            **(
                {
                    "lifecycle": {
                        "integration": {
                            "ticket_id": self.lifecycle_integration[0],
                            "state": self.lifecycle_integration[1],
                        }
                    }
                }
                if self.lifecycle_integration is not None
                else {}
            ),
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
            self.reason if self.retryable else (self.nonretryable_reason or "unknown")
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


@dataclasses.dataclass
class OperatorCommand:
    """One process-local operator mutation submitted to the service owner."""

    request_id: str
    method: str
    fingerprint: str
    ticket_id: str
    onto: str | None = None
    execution_id: str | None = None
    admission_event: threading.Event = dataclasses.field(
        default_factory=threading.Event
    )
    admission_result: dict[str, object] | None = None
    admission_error: DevlegateError | None = None
    cancelled: threading.Event = dataclasses.field(default_factory=threading.Event)


def _mutation_fingerprint(method: str, payload: dict[str, str]) -> str:
    payload = json.dumps(
        {"method": method, "payload": payload},
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode()).hexdigest()


def _retry_request_fingerprint(ticket_id: str) -> str:
    return _mutation_fingerprint("retry", {"ticket_id": ticket_id})


def _drop_request_fingerprint(ticket_id: str, execution_id: str) -> str:
    return _mutation_fingerprint(
        "drop", {"ticket_id": ticket_id, "execution_id": execution_id}
    )


def _reconcile_request_fingerprint(ticket_id: str, onto: str) -> str:
    return _mutation_fingerprint(
        "reconcile-update-base", {"ticket_id": ticket_id, "onto": onto}
    )


def _reconcile_resume_request_fingerprint(ticket_id: str) -> str:
    return _mutation_fingerprint("reconcile-resume", {"ticket_id": ticket_id})


def _control_reconcile_request_fingerprint(from_head: str, to_head: str) -> str:
    return _mutation_fingerprint(
        "reconcile-control", {"from": from_head, "to": to_head}
    )


def _worker_identity_from_value(
    value: object, execution_id: object | None = None
) -> WorkerProcessIdentity | None:
    if value is None:
        return None
    if not isinstance(value, dict) or set(value) != {
        "execution_id",
        "pid",
        "pgid",
        "sid",
        "boot_id",
        "start_time",
    }:
        raise DevlegateError("invalid worker identity")
    if execution_id is not None and value["execution_id"] != execution_id:
        raise DevlegateError("worker identity does not match execution identity")
    if (
        not isinstance(value["execution_id"], str)
        or not value["execution_id"]
        or not all(
            isinstance(value[field], int)
            and not isinstance(value[field], bool)
            and value[field] > 0
            for field in ("pid", "pgid", "sid")
        )
        or not isinstance(value["start_time"], int)
        or isinstance(value["start_time"], bool)
        or value["start_time"] < 0
        or not isinstance(value["boot_id"], str)
        or not value["boot_id"]
    ):
        raise DevlegateError("invalid worker identity")
    return WorkerProcessIdentity(
        value["execution_id"],
        value["pid"],
        value["pgid"],
        value["sid"],
        value["boot_id"],
        value["start_time"],
    )


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
    try:
        return read_env(path)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error


def _git(
    repo: Path, *args: str, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def _log(message: str) -> None:
    service_log(message)


OPERATOR_ADMISSION_TIMEOUT = 1.0


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
    """Own persistent workflow orchestration and mutable runtime operations."""

    def __init__(
        self,
        env_file: Path,
        *,
        read_only: bool = False,
        show_worker_output: bool = True,
        repository: Path | None = None,
    ) -> None:
        if not env_file.is_file():
            raise DevlegateError(
                f"configuration file not found: {env_file} "
                f"(copy devlegate's .env.example to $PWD/.env)"
            )
        if not read_only and not hosted_runtime_supported():
            raise DevlegateError(HOSTED_RUNTIME_ERROR)
        self.env_file = env_file.expanduser().resolve()
        config = _read_env(self.env_file)
        try:
            self.repo = (
                repository or repository_root(self.env_file.parent)
            ).resolve()
        except RuntimeLocatorError as error:
            raise DevlegateError(str(error)) from error

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
        self._locator = RuntimeLocator.from_config(self.env_file, self.repo, config)
        self.state_dir = self._locator.state_dir
        self._state_key = self._locator.state_key
        self._runtime_store = SQLiteRuntimeStore(self.state_dir, self._state_key)
        self._state_file = self._runtime_store.path
        self.control_worktree = (
            self.state_dir / "worktrees" / self._state_key / "control"
        )
        self.execution_worktree_root = self.state_dir / "worktrees" / self._state_key
        self._state: dict[str, object] = {"phase": "idle"}
        self._dirty_fingerprint: str | None = None
        self._workflow_blocker_fingerprint: str | None = None
        self._foreground_diagnostic_fingerprint: str | None = None
        self._iteration_diagnostic: str | None = None
        self._workflow_validation_succeeded = False
        self._stop_event: threading.Event | None = None
        self._operator_command_lock = threading.RLock()
        self._receipt_lock = threading.Lock()
        self._operator_command: OperatorCommand | None = None
        self._operator_active = False
        self._operator_active_command: OperatorCommand | None = None
        self._scheduler_active = False
        self._startup_admission_window = True
        self._service_wake = threading.Event()
        self._service_shutdown = threading.Event()
        self._foreground_abort_requested = False
        self._lifecycle_lock = threading.RLock()
        self._lifecycle_intent: str | None = None
        self._lifecycle_phase = "running"
        self._lifecycle_request_id: str | None = None
        self._service_ready = False
        self._workers = WorkerSupervisor(
            self.opencode_bin,
            self.opencode_model,
            self.opencode_agent,
            state_dir=self.state_dir,
            state_key=self._state_key,
            show_worker_output=show_worker_output,
        )
        self._owned_execution_id: str | None = None
        self._validate()
        self._state = self._load_state()
        self._snapshot_lock = threading.Lock()
        self._published_status_snapshot: StatusSnapshot | None = None
        self._published_live_execution: dict[str, object] | None = None
        self._published_retry_candidates: tuple[dict[str, str], ...] = ()
        self._published_drop_candidates: tuple[dict[str, str], ...] = ()
        self._host_owner_thread_id: int | None = None
        self._automatic_drop_recovery_execution: str | None = None
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

    @property
    def ipc_socket_path(self) -> Path:
        """Return the project-specific daemon IPC socket path."""
        return self._locator.socket_path

    def wake(self) -> None:
        """Wake the owner loop for an internal command or shutdown request."""
        self._service_wake.set()

    def request_lifecycle(self, intent: str, request_id: str) -> dict[str, object]:
        """Accept one process-local lifecycle intent and close worker admission."""
        if intent not in {"stop", "restart"}:
            raise DevlegateError(f"unsupported lifecycle intent: {intent}")
        with self._operator_command_lock:
            with self._lifecycle_lock:
                if self._lifecycle_intent is not None:
                    if self._lifecycle_intent != intent:
                        raise DevlegateError(
                            f"service {self._lifecycle_intent} is already accepted"
                        )
                    return self.lifecycle_status_payload()
                self._workers.begin_drain()
                self._lifecycle_intent = intent
                self._lifecycle_phase = "draining"
                self._lifecycle_request_id = request_id
        self.wake()
        return self.lifecycle_status_payload()

    def lifecycle_status_payload(self) -> dict[str, object]:
        with self._lifecycle_lock:
            intent = self._lifecycle_intent
            phase = self._lifecycle_phase
        return {
            "intent": intent or "none",
            "phase": phase,
            "ready": self._service_ready,
            "request_id": self._lifecycle_request_id,
            "workers": {"active": self._workers.active_count},
        }

    def mark_service_ready(self) -> None:
        with self._lifecycle_lock:
            self._service_ready = True

    def lifecycle_intent(self) -> str | None:
        with self._lifecycle_lock:
            return self._lifecycle_intent

    def _lifecycle_drain_pending(self) -> bool:
        with self._lifecycle_lock:
            return self._lifecycle_intent is not None

    def _lifecycle_exit_ready(self) -> bool:
        if not self._lifecycle_drain_pending() or self._workers.active_count:
            return False
        if self._owned_execution_id is None:
            return True
        if self._state.get("phase") != "agent_running":
            return True
        return self._state.get("execution_stage") in {
            "post-checkpoint",
            "publishing",
            "post-publication",
            "lifecycle",
        }

    def begin_hosted_owner(self) -> None:
        owner = threading.get_ident()
        if self._host_owner_thread_id not in {None, owner}:
            raise DevlegateError("hosted service already has an owner thread")
        self._host_owner_thread_id = owner

    def end_hosted_owner(self) -> None:
        if self._host_owner_thread_id == threading.get_ident():
            self._host_owner_thread_id = None

    def _assert_repository_owner(self) -> None:
        if (
            self._host_owner_thread_id is not None
            and self._host_owner_thread_id != threading.get_ident()
        ):
            raise DevlegateError(
                "hosted repository access is restricted to the owner thread"
            )

    def ensure_hosted_views(self) -> None:
        with self._snapshot_lock:
            ready = self._published_status_snapshot is not None
        if not ready:
            self.status_view()

    def published_status_payload(self) -> dict[str, object]:
        with self._snapshot_lock:
            snapshot = self._published_status_snapshot
            live_execution = self._published_live_execution
        if snapshot is None:
            raise DevlegateError("service status view is not ready")
        result = snapshot.as_dict()
        if live_execution is not None:
            result["live_execution"] = dict(live_execution)
        return result

    def published_plan_view(self) -> ExecutionPlan:
        with self._snapshot_lock:
            snapshot = self._published_status_snapshot
        if snapshot is None:
            raise DevlegateError("service plan view is not ready")
        return snapshot.plan

    def published_retry_candidates_view(self) -> tuple[dict[str, str], ...]:
        with self._snapshot_lock:
            if self._published_status_snapshot is None:
                raise DevlegateError("service retry candidates are not ready")
            return tuple(
                dict(candidate) for candidate in self._published_retry_candidates
            )

    def published_drop_candidates_view(self) -> tuple[dict[str, str], ...]:
        with self._snapshot_lock:
            return tuple(
                dict(candidate) for candidate in self._drop_candidate_from_state()
            )

    def retry_candidates_view(self) -> tuple[dict[str, str], ...]:
        """Return retry candidates from the current owner-published view."""
        return self.published_retry_candidates_view()

    def drop_candidates_view(self) -> tuple[dict[str, str], ...]:
        """Return drop candidates from the current owner-published view."""
        return self.published_drop_candidates_view()

    def submit_retry(self, ticket_id: str, *, request_id: str) -> dict[str, object]:
        """Submit one retry intent and wait only for owner-side admission."""
        return self._submit_operator_command(
            method="retry",
            ticket_id=ticket_id,
            request_id=request_id,
            fingerprint=_retry_request_fingerprint(ticket_id),
        )

    def submit_drop(
        self, ticket_id: str, execution_id: str, *, request_id: str
    ) -> dict[str, object]:
        """Submit one exact execution disposition to the service owner."""
        return self._submit_operator_command(
            method="drop",
            ticket_id=ticket_id,
            execution_id=execution_id,
            request_id=request_id,
            fingerprint=_drop_request_fingerprint(ticket_id, execution_id),
        )

    def submit_reconcile_update_base(
        self, ticket_id: str, onto: str, *, request_id: str
    ) -> dict[str, object]:
        """Submit one reconciliation intent for owner-side admission."""
        return self._submit_operator_command(
            method="reconcile-update-base",
            ticket_id=ticket_id,
            onto=onto,
            request_id=request_id,
            fingerprint=_reconcile_request_fingerprint(ticket_id, onto),
        )

    def submit_reconcile_resume(
        self, ticket_id: str, *, request_id: str
    ) -> dict[str, object]:
        """Submit same-base reconciliation for owner-side admission."""
        return self._submit_operator_command(
            method="reconcile-resume",
            ticket_id=ticket_id,
            request_id=request_id,
            fingerprint=_reconcile_resume_request_fingerprint(ticket_id),
        )

    def submit_reconcile_control(
        self, from_head: str, to_head: str, *, request_id: str
    ) -> dict[str, object]:
        """Submit an explicit externally rewritten control-lineage acknowledgement."""
        return self._submit_operator_command(
            method="reconcile-control",
            ticket_id=from_head,
            onto=to_head,
            request_id=request_id,
            fingerprint=_control_reconcile_request_fingerprint(from_head, to_head),
        )

    def _submit_operator_command(
        self,
        *,
        method: str,
        ticket_id: str,
        request_id: str,
        fingerprint: str,
        onto: str | None = None,
        execution_id: str | None = None,
    ) -> dict[str, object]:
        new_command = False
        with self._operator_command_lock:
            if self._service_shutdown.is_set() or (
                self._stop_event is not None and self._stop_event.is_set()
            ):
                raise DevlegateError(
                    "service is shutting down; runtime command was not admitted"
                )
            if self._lifecycle_drain_pending():
                raise DevlegateError(
                    "service is draining; runtime command was not admitted"
                )
            receipt = self._mutable_receipt(request_id)
            if receipt is not None:
                if (
                    receipt.get("method") != method
                    or receipt.get("fingerprint") != fingerprint
                    or receipt.get("accepted") is not True
                ):
                    raise DevlegateError(
                        f"request id collision: {method} request semantics differ"
                    )
                return self._operator_ack(method, ticket_id, onto, execution_id)
            existing = self._operator_command or self._operator_active_command
            if existing is not None:
                if existing.request_id == request_id:
                    if existing.method != method or existing.fingerprint != fingerprint:
                        raise DevlegateError(
                            f"request id collision: {method} request semantics differ"
                        )
                    command = existing
                else:
                    raise DevlegateError(
                        "service busy; mutable request was not admitted"
                    )
            else:
                if self._scheduler_active:
                    raise DevlegateError(
                        "service busy; mutable request was not admitted"
                    )
                command = OperatorCommand(
                    request_id, method, fingerprint, ticket_id, onto, execution_id
                )
                self._operator_command = command
                new_command = True
        if new_command:
            self.wake()
        if command.admission_event.is_set():
            return self._admission_result(command)
        if not command.admission_event.wait(OPERATOR_ADMISSION_TIMEOUT):
            with self._operator_command_lock:
                if self._operator_command is command:
                    self._operator_command = None
                    command.cancelled.set()
                    command.admission_error = DevlegateError(
                        "service admission coordination timed out; "
                        "request was not admitted"
                    )
                    command.admission_event.set()
                else:
                    command.cancelled.set()
            raise DevlegateError(
                "service admission coordination timed out; request was not admitted"
            )
        return self._admission_result(command)

    def _validate_retry_admission(self, ticket_id: str) -> None:
        """Validate retry admission on the owner thread."""
        if self.service_snapshot().worker_running:
            raise DevlegateError("service worker is already running")
        if self._state.get("phase") == "agent_running":
            execution_ticket = self._state.get("execution_ticket_id")
            if execution_ticket != ticket_id:
                raise DevlegateError(
                    f"ticket {ticket_id} does not match the persisted interrupted "
                    "execution"
                )
            identity = _worker_identity_from_value(
                self._state.get("worker_identity"),
                self._state.get("execution_id"),
            )
            if identity is not None and self._workers.observe(identity) != "absent":
                raise DevlegateError("service worker is already running")
            stage = self._state.get("execution_stage")
            if stage not in {
                None,
                "pre-checkpoint",
                "worker-running",
                "post-worker",
            }:
                raise DevlegateError("persisted execution is not currently retryable")
            return
        candidate_ids = {
            candidate.ticket_id for candidate in self._interactive_retry_candidates()
        }
        if ticket_id not in candidate_ids:
            raise DevlegateError(f"ticket {ticket_id} is not currently retryable")

    def _validate_drop_admission(
        self, ticket_id: str, execution_id: str | None
    ) -> None:
        if not isinstance(execution_id, str) or not execution_id:
            raise DevlegateError("drop requires an exact execution identity")
        if self._drop_already_completed(ticket_id, execution_id):
            return
        state = self._state
        if state.get("phase") != "agent_running":
            raise DevlegateError("execution is not currently droppable")
        if state.get("execution_ticket_id") != ticket_id:
            raise DevlegateError("requested ticket does not match the active execution")
        if state.get("execution_id") != execution_id:
            raise DevlegateError("requested execution is no longer active")
        if state.get("execution_stage") != "lifecycle":
            raise DevlegateError(
                "drop is currently limited to blocked lifecycle executions"
            )
        if state.get("worker_identity") is not None:
            raise DevlegateError("cannot drop an execution with worker ownership")
        if (
            self._has_pending_reconciliation()
            or self._has_pending_accepted_integration()
        ):
            raise DevlegateError(
                "drop is unsafe while reconciliation or accepted integration is pending"
            )
        self._validate_drop_evidence(
            ticket_id,
            execution_id,
            allow_missing_remote=(
                self._read_drop_disposition(ticket_id, execution_id) is not None
            ),
        )

    def _drop_candidate_from_state(self) -> tuple[dict[str, str], ...]:
        state = self._state
        ticket_id = state.get("execution_ticket_id")
        execution_id = state.get("execution_id")
        if (
            state.get("phase") != "agent_running"
            or state.get("execution_stage") != "lifecycle"
            or state.get("worker_identity") is not None
            or not isinstance(ticket_id, str)
            or not isinstance(execution_id, str)
        ):
            return ()
        pending = state.get("pending_execution_report")
        try:
            report = ExecutionReport.from_dict(pending)
            self._report_matches_execution_binding(report)
        except (ExecutionReportError, WorkflowBlockedError):
            return ()
        if report.workspace_head is None:
            return ()
        title = state.get("selected_ticket_title")
        return (
            {
                "id": ticket_id,
                "title": title if isinstance(title, str) and title else ticket_id,
                "reason": "control authority withdrawn; lifecycle replay refused",
                "kind": "lifecycle",
                "execution_id": execution_id,
                "stage": "lifecycle",
            },
        )

    def _validate_reconcile_admission(self, ticket_id: str, onto: str) -> None:
        if not onto:
            raise DevlegateError("requested reconciliation target is empty")
        reconciliation = self._state.get("reconciliation")
        if not isinstance(reconciliation, dict) or reconciliation.get("status") != (
            "pending"
        ):
            raise DevlegateError(f"ticket {ticket_id} has no pending reconciliation")
        if reconciliation.get("ticket_id") != ticket_id:
            raise DevlegateError(
                "requested ticket does not match pending reconciliation"
            )

    def _automatic_zero_delta_authorization(
        self,
        ticket_store: TicketStore,
        execution_plan: ExecutionPlan,
        local_head: str,
        remote_head: str,
    ) -> ExecutionAuthorization | None:
        retry = self._state.get("automatic_retry")
        if not isinstance(retry, dict) or retry.get("status") not in {
            "pending_admission",
            "admitted",
        }:
            return None
        ticket_id = retry.get("ticket_id")
        original_base = retry.get("original_base")
        if not isinstance(ticket_id, str) or not isinstance(original_base, str):
            raise WorkflowBlockedError("automatic zero-delta retry identity is invalid")
        ticket = ticket_store.by_id.get(ticket_id)
        if (
            ticket is None
            or ticket.state != "todo"
            or ticket not in ticket_store.runnable
        ):
            completed = {**retry, "status": "not_admitted"}
            self._save_state(
                "idle",
                automatic_retry=None,
                last_automatic_retry=completed,
            )
            _log(
                f"automatic zero-delta retry skipped; ticket {ticket_id} "
                "is no longer executable"
            )
            return None
        if execution_plan.ticket_id != ticket_id:
            return None
        if execution_plan.action != "run-worker" or execution_plan.bound:
            return None
        if local_head != remote_head or local_head == original_base:
            raise WorkflowBlockedError(
                "automatic zero-delta retry requires one current product generation"
            )
        ancestor = _git(
            self.repo,
            "merge-base",
            "--is-ancestor",
            original_base,
            local_head,
            check=False,
        )
        if ancestor.returncode:
            raise WorkflowBlockedError(
                "automatic zero-delta retry product history is not a descendant"
            )
        if retry.get("product_head") != local_head:
            retry = {**retry, "product_head": local_head}
            self._save_state("idle", automatic_retry=retry)
        return ExecutionAuthorization(ticket_id, "zero_delta_product_drift")

    def _discard_ineligible_zero_delta_retry(self, ticket_store: TicketStore) -> None:
        retry = self._state.get("automatic_retry")
        if not isinstance(retry, dict) or retry.get("status") != (
            "pending_admission"
        ):
            return
        ticket_id = retry.get("ticket_id")
        if not isinstance(ticket_id, str):
            raise WorkflowBlockedError("automatic zero-delta retry identity is invalid")
        ticket = ticket_store.by_id.get(ticket_id)
        if (
            ticket is not None
            and ticket.state == "todo"
            and ticket in ticket_store.runnable
        ):
            return
        self._save_state(
            "idle",
            automatic_retry=None,
            last_automatic_retry={**retry, "status": "not_admitted"},
        )
        _log(
            f"automatic zero-delta retry skipped; ticket {ticket_id} "
            "is no longer executable"
        )

    def _validate_reconcile_resume_admission(self, ticket_id: str) -> None:
        reconciliation = self._state.get("reconciliation")
        if not isinstance(reconciliation, dict) or reconciliation.get("status") not in {
            "pending",
            "resolving",
        }:
            raise DevlegateError(f"ticket {ticket_id} has no resumable reconciliation")
        if reconciliation.get("ticket_id") != ticket_id:
            raise DevlegateError(
                "requested ticket does not match pending reconciliation"
            )

    def _validate_control_reconcile_admission(
        self, from_head: str, to_head: str
    ) -> None:
        if self.service_snapshot().worker_running or self._state.get("phase") != "idle":
            raise DevlegateError(
                "control reconciliation requires an idle service with no live execution"
            )
        if self._state.get("accepted_integration") is not None:
            raise DevlegateError(
                "control reconciliation is unsafe while accepted integration is pending"
            )
        reconciliation = self._state.get("reconciliation")
        if isinstance(reconciliation, dict) and reconciliation.get("status") == (
            "pending"
        ):
            raise DevlegateError(
                "control reconciliation is unsafe while product-base reconciliation "
                "is pending"
            )
        self._validate_control_worktree()
        observation = self._git_observation(self.control_worktree, self.control_branch)
        if not observation.working_tree_clean:
            raise DevlegateError("control working tree is dirty")

        local = _git(self.control_worktree, "rev-parse", "--verify", "HEAD^{commit}")
        if local.stdout.strip() != from_head:
            raise DevlegateError(
                f"control local HEAD changed; expected {from_head}, "
                f"found {local.stdout.strip()}"
            )
        fetch = _git(
            self.control_worktree,
            "fetch",
            "--prune",
            self.remote_name,
            self.control_branch,
            check=False,
        )
        if fetch.returncode:
            raise DevlegateError(
                f"cannot freshly fetch control branch: {fetch.stderr.strip()}"
            )
        remote_ref = f"{self.remote_name}/{self.control_branch}"
        remote = _git(
            self.control_worktree,
            "rev-parse",
            "--verify",
            f"{remote_ref}^{{commit}}",
            check=False,
        )
        if remote.returncode:
            raise DevlegateError(f"control remote branch not found: {remote_ref}")
        observed_remote = remote.stdout.strip()
        if observed_remote != to_head:
            raise DevlegateError(
                f"control remote HEAD changed; expected {to_head}, "
                f"found {observed_remote}"
            )
        local_ancestor = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            from_head,
            to_head,
            check=False,
        )
        remote_ancestor = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            to_head,
            from_head,
            check=False,
        )
        if local_ancestor.returncode == 0 or remote_ancestor.returncode == 0:
            raise DevlegateError(
                "control reconciliation is unnecessary; histories are not divergent"
            )

    def _validate_operator_admission(self, command: OperatorCommand) -> None:
        if command.method == "retry":
            self._validate_retry_admission(command.ticket_id)
            return
        if command.method == "drop":
            self._validate_drop_admission(command.ticket_id, command.execution_id)
            return
        if command.method == "reconcile-update-base":
            if command.onto is None:
                raise DevlegateError(
                    "reconcile-update-base requires a product base target"
                )
            self._validate_reconcile_admission(command.ticket_id, command.onto)
            return
        if command.method == "reconcile-resume":
            self._validate_reconcile_resume_admission(command.ticket_id)
            return
        if command.method == "reconcile-control":
            if command.onto is None:
                raise DevlegateError(
                    "reconcile-control requires both control revisions"
                )
            self._validate_control_reconcile_admission(command.ticket_id, command.onto)
            return
        raise DevlegateError(f"unsupported operator command: {command.method}")

    @staticmethod
    def _operator_ack(
        method: str,
        ticket_id: str,
        onto: str | None,
        execution_id: str | None = None,
    ) -> dict[str, object]:
        if method == "retry":
            return {"accepted": True, "ticket_id": ticket_id}
        if method == "drop":
            if execution_id is None:
                raise DevlegateError("drop requires an exact execution identity")
            return {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
            }
        if method == "reconcile-control":
            if onto is None:
                raise DevlegateError(
                    "reconcile-control requires both control revisions"
                )
            return {"accepted": True, "from": ticket_id, "to": onto}
        if method == "reconcile-resume":
            return {"accepted": True, "ticket_id": ticket_id}
        if method == "reconcile-update-base":
            if onto is None:
                raise DevlegateError(
                    "reconcile-update-base requires a product base target"
                )
            return {"accepted": True, "ticket_id": ticket_id, "onto": onto}
        raise DevlegateError(f"unsupported operator command: {method}")

    @staticmethod
    def _admission_result(command: OperatorCommand) -> dict[str, object]:
        if command.admission_error is not None:
            raise command.admission_error
        if command.admission_result is None:
            raise DevlegateError("service did not produce an operator admission result")
        return command.admission_result

    def _mutable_receipt(self, request_id: str) -> dict[str, object] | None:
        with self._receipt_lock:
            receipts = self._state.get("mutable_receipts", {})
            if not isinstance(receipts, dict):
                raise DevlegateError("invalid mutable request receipt state")
            receipt = receipts.get(request_id)
            if receipt is None:
                return None
            if not isinstance(receipt, dict) or not set(receipt).issubset(
                {
                "method",
                "fingerprint",
                "ticket_id",
                "accepted",
                "execution_id",
                }
            ) or set(receipt) not in (
                {"method", "fingerprint", "ticket_id", "accepted"},
                {"method", "fingerprint", "ticket_id", "accepted", "execution_id"},
            ):
                raise DevlegateError("invalid mutable request receipt")
            if not all(
                isinstance(receipt[field], str) and receipt[field]
                for field in ("method", "fingerprint", "ticket_id")
            ) or not isinstance(receipt["accepted"], bool):
                raise DevlegateError("invalid mutable request receipt")
            execution_id = receipt.get("execution_id")
            if execution_id is not None and (
                not isinstance(execution_id, str) or not execution_id
            ):
                raise DevlegateError("invalid mutable request receipt execution")
            return dict(receipt)

    def _record_operator_admission(self, command: OperatorCommand) -> None:
        if command.method == "drop":
            self._drop_owned(command.ticket_id, command.execution_id)
        with self._receipt_lock:
            receipts = self._state.get("mutable_receipts", {})
            if not isinstance(receipts, dict):
                raise DevlegateError("invalid mutable request receipt state")
            updated = dict(receipts)
            receipt = {
                "method": command.method,
                "fingerprint": command.fingerprint,
                "ticket_id": command.ticket_id,
                "accepted": True,
            }
            execution_id = getattr(command, "execution_id", None)
            if execution_id is not None:
                receipt["execution_id"] = execution_id
            updated[command.request_id] = receipt
            reconciliation = None
            if command.method == "reconcile-resume":
                current = self._state.get("reconciliation")
                if not isinstance(current, dict):
                    raise DevlegateError("invalid reconciliation state")
                reconciliation = {
                    **current,
                    "status": "resolving",
                    "resolution": "resume",
                }
            self._save_state(
                str(self._state["phase"]),
                mutable_receipts=updated,
                **({"reconciliation": reconciliation} if reconciliation else {}),
            )

    def _admit_operator_command(self, command: OperatorCommand) -> None:
        try:
            with self._operator_command_lock:
                if command.cancelled.is_set():
                    raise DevlegateError(
                        "service busy; mutable request was not admitted"
                    )
                if self._lifecycle_drain_pending():
                    raise DevlegateError(
                        "service is draining; runtime command was not admitted"
                    )
                self._validate_operator_admission(command)
                if command.cancelled.is_set():
                    raise DevlegateError(
                        "service busy; mutable request was not admitted"
                    )
                self._record_operator_admission(command)
                command.admission_result = self._operator_ack(
                    command.method,
                    command.ticket_id,
                    command.onto,
                    getattr(command, "execution_id", None),
                )
        except DevlegateError as error:
            command.admission_error = error
            raise
        finally:
            command.admission_event.set()

    def _take_operator_command(self) -> OperatorCommand | None:
        with self._operator_command_lock:
            if (
                self._operator_command is None
                or self._operator_active
                or self._scheduler_active
            ):
                return None
            command = self._operator_command
            self._operator_command = None
            self._operator_active = True
            self._operator_active_command = command
            return command

    def _claim_owner_work(
        self, allow_operator: bool
    ) -> tuple[OperatorCommand | None, bool]:
        with self._operator_command_lock:
            if allow_operator and self._operator_command is not None:
                if self._operator_active or self._scheduler_active:
                    return None, False
                command = self._operator_command
                self._operator_command = None
                self._operator_active = True
                self._operator_active_command = command
                return command, False
            if self._operator_active or self._scheduler_active:
                return None, False
            self._scheduler_active = True
            return None, True

    def _release_operator_command(self) -> None:
        with self._operator_command_lock:
            self._operator_active = False
            self._operator_active_command = None

    def _release_scheduler_iteration(self) -> None:
        with self._operator_command_lock:
            self._scheduler_active = False

    def _reject_pending_operator_command_on_shutdown(self) -> None:
        with self._operator_command_lock:
            command = self._operator_command
            if command is None:
                return
            self._operator_command = None
            command.admission_error = DevlegateError(
                "service is shutting down; runtime command was not admitted"
            )
            command.admission_event.set()

    def _operator_command_pending(self) -> bool:
        with self._operator_command_lock:
            return bool(self._operator_command is not None)

    def service_snapshot(self) -> ServiceSnapshot:
        """Return the latest published snapshot without performing observation I/O."""
        with self._snapshot_lock:
            return self._published_snapshot

    def live_execution_evidence(self) -> dict[str, object] | None:
        """Return proof of this process-owned execution, if it is provable."""
        self._assert_repository_owner()
        state = self._state
        if state.get("phase") not in {"agent_pending", "agent_running"}:
            return None
        execution_id = state.get("execution_id")
        ticket_id = state.get("execution_ticket_id")
        selected_ticket_id = state.get("selected_ticket_id")
        if (
            not isinstance(execution_id, str)
            or not isinstance(ticket_id, str)
            or ticket_id != selected_ticket_id
            or self._owned_execution_id != execution_id
        ):
            return None
        evidence = {
            "ticket_id": ticket_id,
            "execution_id": execution_id,
            "stage": state.get("execution_stage"),
            "ownership": "current-service",
        }
        if state.get("execution_stage") != "worker-running":
            return evidence
        if not self._workers.owns(execution_id):
            return None
        identity = _worker_identity_from_value(
            state.get("worker_identity"), execution_id
        )
        if identity is None or self._workers.observe(identity) != "matching-live":
            return None
        return {**evidence, "identity_state": "matching-live"}

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
                    current.worker_running if worker_running is None else worker_running
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
        if snapshot.lifecycle_integration is not None:
            blocked_reason = snapshot.plan.reason
        worker_running = self.service_snapshot().worker_running
        lifecycle = (
            "worker" if worker_running else ("blocked" if blocked_reason else "ready")
        )
        if worker_running:
            blocked_reason = None
        retry_candidates = [
            {
                "id": failure.ticket_id,
                "title": failure.title,
                "reason": failure.reason,
                "kind": "failed",
            }
            for failure in snapshot.failed_executions
            if failure.retryable
        ]
        interrupted = self._interrupted_retry_candidate()
        if interrupted is not None and all(
            candidate["id"] != interrupted.ticket_id for candidate in retry_candidates
        ):
            retry_candidates.append(
                {
                    "id": interrupted.ticket_id,
                    "title": interrupted.title,
                    "reason": interrupted.reason,
                    "kind": interrupted.kind,
                }
            )
        live_execution = self.live_execution_evidence()
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
        with self._snapshot_lock:
            self._published_status_snapshot = snapshot
            self._published_live_execution = (
                {
                    key: value
                    for key, value in live_execution.items()
                    if key != "worker_identity"
                }
                if isinstance(live_execution, dict)
                else None
            )
            self._published_retry_candidates = tuple(retry_candidates)
            self._published_drop_candidates = self._drop_candidate_from_state()

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

    def _raise_on_shutdown_interruption(
        self, result: subprocess.CompletedProcess[str]
    ) -> None:
        """Keep an intentional signal interruption out of workflow diagnostics."""
        kind = getattr(self._stop_event, "kind", None)
        expected_signal = {
            "operator_abort": signal.SIGINT,
        }.get(kind)
        if (
            self._stop_requested()
            and expected_signal is not None
            and result.returncode == -expected_signal
        ):
            raise ShutdownInterrupted

    def _git_runtime(
        self, repo: Path, *args: str, check: bool = True
    ) -> subprocess.CompletedProcess[str]:
        """Run Git under the foreground shutdown policy for this runtime region."""
        self._assert_repository_owner()
        result = _git(repo, *args, check=False)
        if result.returncode and self._state.get("phase") == "merge_pending":
            try:
                self._raise_on_shutdown_interruption(result)
            except ShutdownInterrupted:
                result = _git(repo, *args, check=False)
        if result.returncode:
            self._raise_on_shutdown_interruption(result)
        if check:
            result.check_returncode()
        return result

    def _stop_before_admission(self) -> bool:
        """Return whether a new mutable operation must not be committed."""
        return self._stop_requested() or self._lifecycle_drain_pending()

    def _ticket_store(self) -> TicketStore:
        self._assert_repository_owner()
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
        registered = self._git_runtime(self.repo, "worktree", "list", "--porcelain")
        try:
            registered_paths = set(parse_worktree_porcelain(registered.stdout))
        except ExecutionWorkspaceError as error:
            raise WorkflowBlockedError(str(error)) from error
        if self.control_worktree.resolve() not in registered_paths:
            raise WorkflowBlockedError(
                "control path is not a registered Git worktree for this repository"
            )
        root = self._git_runtime(
            self.control_worktree, "rev-parse", "--show-toplevel", check=False
        )
        if (
            root.returncode
            or Path(root.stdout.strip()).resolve() != self.control_worktree.resolve()
        ):
            raise WorkflowBlockedError(
                "control worktree does not resolve to its configured checkout"
            )
        branch = self._git_runtime(
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
        try:
            return repository_root()
        except RuntimeLocatorError as error:
            raise DevlegateError(str(error)) from error

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
        status_result = _git(self.repo, "status", "--porcelain", check=False)
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

    def _attach_existing_control(self) -> dict[str, object]:
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
            return {
                "result": "already_attached",
                "control_worktree": str(self.control_worktree),
            }
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
        return {
            "result": "attached_existing_remote",
            "control_worktree": str(self.control_worktree),
        }

    def _bootstrap_control(self) -> dict[str, object]:
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
        return {
            "result": "initialized_new_control_plane",
            "control_worktree": str(self.control_worktree),
        }

    def control_init_result(self) -> dict[str, object]:
        """Attach an existing control branch or bootstrap a fresh control plane."""
        if self._control_remote_exists():
            return self._attach_existing_control()
        return self._bootstrap_control()

    def control_init(self) -> int:
        result = self.control_init_result()
        messages = {
            "already_attached": "control worktree already attached",
            "attached_existing_remote": "attached control worktree",
            "initialized_new_control_plane": "initialized control worktree",
        }
        print(f"{messages[result['result']]}: {result['control_worktree']}")
        return 0

    def _probe_state_access(self) -> None:
        self._runtime_store.probe()

    def _lock(self):
        lock_file = self._locator.lock_path
        lock_file.parent.mkdir(parents=True, exist_ok=True)
        handle = lock_file.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise DevlegateError(
                "another devlegate instance is already running for this checkout"
            ) from error
        return handle

    def _mutation_owner_live(self) -> bool:
        """Observe the project lock without opening it for ownership."""
        if sys.platform != "linux":
            return False
        lock_file = self._locator.lock_path
        try:
            identity = lock_file.stat()
            with open("/proc/locks", encoding="ascii") as locks:
                entries = locks.read().splitlines()
        except OSError:
            return False
        for entry in entries:
            fields = entry.split()
            if len(fields) < 6 or fields[1] != "FLOCK":
                continue
            lock_device, separator, lock_inode = fields[5].rpartition(":")
            try:
                lock_major, lock_minor = (
                    int(part, 16) for part in lock_device.split(":", 1)
                )
            except ValueError:
                continue
            if (
                separator
                and (lock_major, lock_minor)
                == (os.major(identity.st_dev), os.minor(identity.st_dev))
                and lock_inode == str(identity.st_ino)
            ):
                return True
        return False

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
        reconciliation = state.get("reconciliation")
        if reconciliation is not None:
            if not isinstance(reconciliation, dict) or reconciliation.get(
                "status"
            ) not in {"pending", "resolving", "resolved"}:
                raise DevlegateError("invalid reconciliation state")
            required_reconciliation = (
                "ticket_id",
                "execution_id",
                "original_base",
                "observed_product",
                "worker_checkpoint",
                "product_remote_head",
                "control_head",
                "execution_branch",
                "execution_path",
                "evidence_ref",
            )
            if not all(
                isinstance(reconciliation.get(field), str) and reconciliation[field]
                for field in required_reconciliation
            ):
                raise DevlegateError("invalid reconciliation identity")
            status = reconciliation["status"]
            if status == "resolving" and reconciliation.get("resolution") != "resume":
                raise DevlegateError("resolving reconciliation must be a resume")
            if "resolution" in reconciliation and reconciliation["resolution"] not in {
                None,
                "resume",
                "update-base",
            }:
                raise DevlegateError("invalid reconciliation resolution")
            if "reason" in reconciliation and not isinstance(
                reconciliation["reason"], str
            ):
                raise DevlegateError("invalid reconciliation reason")
            if "execution_remote_head" in reconciliation and not (
                reconciliation["execution_remote_head"] is None
                or _is_git_identity(reconciliation["execution_remote_head"])
            ):
                raise DevlegateError("invalid reconciliation execution remote identity")
            if "execution_report" in reconciliation:
                try:
                    report = ExecutionReport.from_dict(
                        reconciliation["execution_report"]
                    )
                except ExecutionReportError as error:
                    raise DevlegateError(
                        "invalid retained reconciliation report"
                    ) from error
                if not all(
                    actual == reconciliation[expected]
                    for actual, expected in (
                        (report.ticket_id, "ticket_id"),
                        (report.execution_id, "execution_id"),
                        (report.code_base_head, "original_base"),
                        (report.control_head, "control_head"),
                        (report.execution_branch, "execution_branch"),
                        (report.execution_path, "execution_path"),
                        (report.workspace_head, "worker_checkpoint"),
                    )
                ):
                    raise DevlegateError(
                        "retained reconciliation report binding is invalid"
                    )
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

        def validate_automatic_retry(
            value: object, *, current: bool
        ) -> None:
            if value is None:
                return
            if not isinstance(value, dict):
                raise DevlegateError("invalid automatic retry state")
            status = value.get("status")
            current_statuses = {"pending", "pending_admission", "admitted"}
            terminal_statuses = {"completed", "not_admitted"}
            valid_statuses = current_statuses if current else terminal_statuses
            if status not in valid_statuses:
                raise DevlegateError("invalid automatic retry status")
            required = {
                "status",
                "reason",
                "ticket_id",
                "stale_execution_id",
                "original_base",
                "worker_checkpoint",
                "product_head",
                "control_head",
            }
            if status in {"pending_admission", "admitted", "completed"}:
                required.add("fresh_execution_id")
            if status == "not_admitted" and "fresh_execution_id" in value:
                required.add("fresh_execution_id")
            if status == "completed":
                required.add("completed_conclusion")
            if not required.issubset(value):
                raise DevlegateError("automatic retry identity is incomplete")
            allowed = required
            if set(value) != allowed:
                raise DevlegateError("automatic retry fields are invalid")
            for field in (
                "reason",
                "ticket_id",
                "stale_execution_id",
                "worker_checkpoint",
            ):
                if not isinstance(value.get(field), str) or not value[field]:
                    raise DevlegateError("automatic retry identity is invalid")
            for field in ("original_base", "product_head", "control_head"):
                if not _is_git_identity(value.get(field)):
                    raise DevlegateError("automatic retry Git identity is invalid")
            if "fresh_execution_id" in required and (
                not isinstance(value.get("fresh_execution_id"), str)
                or not value["fresh_execution_id"]
            ):
                raise DevlegateError("automatic retry fresh execution is invalid")
            if "completed_conclusion" in required and (
                not isinstance(value.get("completed_conclusion"), str)
                or not value["completed_conclusion"]
            ):
                raise DevlegateError("automatic retry conclusion is invalid")

        validate_automatic_retry(state.get("automatic_retry"), current=True)
        validate_automatic_retry(
            state.get("last_automatic_retry"), current=False
        )
        identity = _worker_identity_from_value(
            state.get("worker_identity"), state.get("execution_id")
        )
        interruption_kind = state.get("execution_interruption_kind")
        if interruption_kind is not None and (
            not isinstance(interruption_kind, str)
            or interruption_kind
            not in {
                "operator_abort",
                "service_shutdown",
                "process_loss",
            }
        ):
            raise DevlegateError("invalid execution interruption kind")
        if phase == "idle" and identity is not None:
            raise DevlegateError("invalid idle state: worker identity is still active")
        accepted_integration = state.get("accepted_integration")
        if accepted_integration is not None:
            if phase != "idle":
                raise DevlegateError(
                    "accepted integration state is only valid while idle"
                )
            if (
                not isinstance(accepted_integration, dict)
                or set(accepted_integration)
                != {"ticket_id", "checkpoint", "control_head"}
                or not (
                    isinstance(accepted_integration.get("ticket_id"), str)
                    and bool(accepted_integration["ticket_id"])
                    and _is_git_identity(accepted_integration.get("checkpoint"))
                    and _is_git_identity(accepted_integration.get("control_head"))
                )
            ):
                raise DevlegateError("invalid accepted integration state")
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
        stage = state.get("execution_stage")
        if stage is not None and stage not in {
            "worker-launch",
            "worker-running",
            "post-worker",
            "pre-checkpoint",
            "checkpointing",
            "post-checkpoint",
            "publishing",
            "post-publication",
            "lifecycle",
        }:
            raise DevlegateError("invalid execution stage")
        if phase == "agent_running" and stage == "worker-running" and identity is None:
            raise DevlegateError("worker-running state requires worker identity")
        if (
            phase == "agent_running"
            and stage
            in {
                "worker-launch",
                "post-worker",
                "pre-checkpoint",
                "checkpointing",
                "post-checkpoint",
                "publishing",
                "post-publication",
                "lifecycle",
            }
            and identity is not None
        ):
            raise DevlegateError("later execution stage cannot retain worker identity")
        if phase == "agent_running" and stage in {
            "checkpointing",
            "post-checkpoint",
            "publishing",
            "post-publication",
        }:
            pending = state.get("pending_execution_report")
            try:
                report = ExecutionReport.from_dict(pending)
            except ExecutionReportError as error:
                raise DevlegateError(
                    f"invalid {stage} state: pending execution report is invalid"
                ) from error
            if stage == "checkpointing":
                if report.workspace_head is not None:
                    raise DevlegateError(
                        "invalid checkpointing state: checkpoint identity is premature"
                    )
            elif not report.workspace_head:
                raise DevlegateError(
                    f"invalid {stage} state: checkpoint identity is missing"
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
                field in state for field in (*execution_fields, "execution_remote_head")
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

    def _save_state(
        self, phase: str, *, clear_execution: bool = False, **fields: object
    ) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state: dict[str, object] = {**self._state, "phase": phase, **fields}
        if phase == "idle":
            state.pop("selected_ticket_id", None)
            state.pop("selected_ticket_body", None)
            state.pop("selected_ticket_title", None)
            state.pop("pending_execution_report", None)
            state.pop("execution_interruption_kind", None)
            state["worker_identity"] = None
        if clear_execution:
            for field in (
                "execution_ticket_id",
                "execution_base_head",
                "execution_control_head",
                "execution_branch",
                "execution_path",
                "execution_id",
                "execution_remote_head",
                "execution_start_head",
                "resume_required",
            ):
                state.pop(field, None)
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
        fetch = self._git_runtime(
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
        remote = self._git_runtime(
            self.control_worktree,
            "rev-parse",
            "--verify",
            remote_ref,
            check=False,
        )
        if remote.returncode:
            raise WorkflowBlockedError(f"control remote branch not found: {remote_ref}")
        remote_head = remote.stdout.strip()
        local_head = self._git_runtime(
            self.control_worktree, "rev-parse", "HEAD"
        ).stdout.strip()
        if local_head != remote_head:
            previous_head = local_head
            ancestor = self._git_runtime(
                self.control_worktree,
                "merge-base",
                "--is-ancestor",
                local_head,
                remote_head,
                check=False,
            )
            if ancestor.returncode:
                pending_integration = self._state.get("accepted_integration")
                if isinstance(pending_integration, dict):
                    classification, _commit = (
                        self._observe_accepted_control_integration(
                            str(pending_integration["ticket_id"]),
                            str(pending_integration["control_head"]),
                            local_head,
                            remote_head,
                        )
                    )
                    if classification == "local_exact_unpublished":
                        return local_head, remote_head
                    raise WorkflowBlockedError(
                        "accepted integration control lineage is ambiguous"
                    )
                raise WorkflowBlockedError(
                    "control branch cannot be fast-forwarded; histories diverged"
                )
            merge = self._git_runtime(
                self.control_worktree, "merge", "--ff-only", remote_ref, check=False
            )
            if merge.returncode:
                raise WorkflowBlockedError(
                    f"control fast-forward failed: {merge.stderr.strip()}"
                )
            local_head = self._git_runtime(
                self.control_worktree, "rev-parse", "HEAD"
            ).stdout.strip()
            _log(f"control updated: {previous_head} -> {local_head}")
        return local_head, remote_head

    def run_iteration(self, intent: IterationIntent | None = None) -> int:
        """Execute exactly one scheduler iteration under host authority."""
        intent = intent or IterationIntent()
        authorization = (
            ExecutionAuthorization(intent.retry_ticket_id, "explicit_retry")
            if intent.retry_ticket_id is not None
            else None
        )
        self._owned_execution_id = None
        try:
            return self._run_iteration_body(authorization)
        finally:
            self._owned_execution_id = None

    def _run_iteration_body(self, authorization: ExecutionAuthorization | None) -> int:
        self._publish_service_snapshot(lifecycle="processing")
        if self._stop_requested() and self._state.get("phase") != "merge_pending":
            return 0
        self._workflow_validation_succeeded = False
        branch = self._git_runtime(
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
        if (
            self._state.get("phase") == "idle"
            and self._state.get("accepted_integration") is not None
        ):
            return self._recover_accepted_integration()
        reconciliation = self._state.get("reconciliation")
        if (
            self._state.get("phase") == "idle"
            and isinstance(reconciliation, dict)
            and reconciliation.get("status") == "resolving"
        ):
            return self._reconcile_resume_owned(
                str(reconciliation.get("ticket_id", "")), _take_lock=False
            )
        if self._state.get("phase") == "agent_running":
            if self._stop_requested():
                return 0
            if self._state.get("execution_stage") in {
                "checkpointing",
                "post-checkpoint",
                "publishing",
                "post-publication",
            }:
                self._recover_checkpoint_publication()
                report = self._recover_lifecycle_report()
                return self._complete_execution_lifecycle(report)
            if (
                self._state.get("execution_stage") == "lifecycle"
                and self._state.get("worker_identity") is None
            ):
                dropped_ticket = self._state.get("execution_ticket_id")
                dropped_execution = self._state.get("execution_id")
                if isinstance(dropped_ticket, str) and isinstance(
                    dropped_execution, str
                ) and self._read_drop_disposition(
                    dropped_ticket, dropped_execution
                ) is not None:
                    recovery_key = f"{dropped_ticket}:{dropped_execution}"
                    if self._automatic_drop_recovery_execution != recovery_key:
                        _log(
                            f"recovering dropped execution {dropped_ticket} "
                            f"({dropped_execution[:12]})"
                        )
                        self._automatic_drop_recovery_execution = recovery_key
                    self._drop_owned(dropped_ticket, dropped_execution)
                    _log(
                        f"dropped execution cleanup complete: {dropped_ticket} "
                        f"({dropped_execution[:12]})"
                    )
                    self._automatic_drop_recovery_execution = None
                    return 0
                report = self._recover_lifecycle_report()
                return self._complete_execution_lifecycle(report)
            automatic_ticket = self._reconcile_stranded_execution()
            if automatic_ticket is None or self._stop_requested():
                return 0
            if authorization is not None:
                raise DevlegateError(
                    "explicit retry cannot also authorize automatic resume"
                )
            authorization = ExecutionAuthorization(automatic_ticket, "automatic_resume")
        status = self._git_runtime(self.repo, "status", "--porcelain").stdout
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
        local_head = self._git_runtime(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        state_phase = self._state.get("phase")
        pending_sync = state_phase in {"agent_pending", "merge_pending"}
        pending_agent_execution = state_phase == "agent_pending"
        had_remote_change = False
        local_ahead = False
        fetch = self._git_runtime(
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
            remote_result = self._git_runtime(
                self.repo, "rev-parse", "--verify", remote_ref, check=False
            )
            if remote_result.returncode:
                _log(
                    f"remote branch not found: {remote_ref}; "
                    f"git stderr: {remote_result.stderr.strip() or 'unknown git error'}"
                )
                return 1
            remote_head = self._git_runtime(
                self.repo, "rev-parse", remote_ref
            ).stdout.strip()
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
                        self._git_runtime(
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
                        self._git_runtime(
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
                    self._git_runtime(
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
                        self._git_runtime(
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
                        self._git_runtime(
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
                    changed_paths = self._git_runtime(
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
                    merge = self._git_runtime(
                        self.repo, "merge", "--ff-only", remote_ref, check=False
                    )
                    if merge.returncode:
                        self._log_sync_failure(local_head, remote_ref, merge.stderr)
                        return 1
                    local_head = self._git_runtime(
                        self.repo, "rev-parse", "HEAD"
                    ).stdout.strip()
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
                self._git_runtime(
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
                changed_paths = self._git_runtime(
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
                merge = self._git_runtime(
                    self.repo, "merge", "--ff-only", remote_ref, check=False
                )
                if merge.returncode:
                    self._log_sync_failure(local_head, remote_ref, merge.stderr)
                    return 1
                actual_head = self._git_runtime(
                    self.repo, "rev-parse", "HEAD"
                ).stdout.strip()
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
                self._git_runtime(
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
                changed_paths = self._git_runtime(
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
        self._discard_ineligible_zero_delta_retry(ticket_store)
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
                execution_plan.reason if execution_plan.action == "blocked" else None
            ),
        )
        automatic_authorization = self._automatic_zero_delta_authorization(
            ticket_store, execution_plan, local_head, remote_head
        )
        if automatic_authorization is not None:
            authorization = automatic_authorization
        if self._stop_before_admission():
            return 0
        if authorization is not None:
            authorized_ticket_id = authorization.ticket_id
            if execution_plan.action != "run-worker" or execution_plan.bound:
                raise DevlegateError(execution_plan.reason)
            retry_ticket = ticket_store.by_id.get(authorized_ticket_id)
            if (
                retry_ticket is None
                or retry_ticket.state != "todo"
                or retry_ticket not in ticket_store.runnable
            ):
                raise DevlegateError(
                    f"ticket {authorized_ticket_id} is no longer eligible for retry"
                )
            execution_plan = ExecutionPlan(
                "run-worker",
                (
                    "automatic resume of safely interrupted execution"
                    if authorization.is_automatic_resume
                    else (
                        "automatic retry after zero-delta product drift"
                        if authorization.is_zero_delta_retry
                        else "explicit retry of current failed execution"
                    )
                ),
                retry_ticket.id,
                retry_ticket.title,
                retry_ticket.state,
                False,
                execution_plan.code,
                execution_plan.control,
            )
        if execution_plan.action == "blocked":
            raise DevlegateError(execution_plan.reason)
        accepted_integration = self._state.get("accepted_integration")
        if accepted_integration is not None:
            if not isinstance(accepted_integration, dict):
                raise DevlegateError("invalid accepted integration state")
            pending_ticket = accepted_integration.get("ticket_id")
            if pending_ticket not in self._ticket_store().by_id:
                raise WorkflowBlockedError(
                    "accepted integration ticket is no longer observable"
                )
            pending_control_head = accepted_integration.get("control_head")
            if not isinstance(pending_control_head, str):
                raise DevlegateError("invalid accepted integration control identity")
            checkpoint = self._accepted_checkpoint(str(pending_ticket))
            if checkpoint != accepted_integration.get("checkpoint"):
                raise WorkflowBlockedError(
                    "accepted integration checkpoint changed during recovery"
                )
            if execution_plan.code is None or execution_plan.control is None:
                raise WorkflowBlockedError(
                    "accepted integration lacks fresh Git observations"
                )
            self.status_view()
            control_head = self._integrate_accepted(
                str(pending_ticket), execution_plan.code, execution_plan.control
            )
            todo_fingerprint, _todo_count = _todo_fingerprint(
                self.control_worktree, self.todo_path
            )
            self._save_state(
                "idle",
                handled_remote_head=remote_head,
                handled_control_head=control_head,
                handled_todo_fingerprint=todo_fingerprint,
                accepted_integration=None,
            )
            self._publish_service_snapshot(lifecycle="ready", worker_running=False)
            _log(f"accepted ticket {pending_ticket} integrated")
            return 0
        if execution_plan.action == "integrate":
            assert execution_plan.ticket_id is not None
            assert execution_plan.control is not None
            checkpoint = self._accepted_checkpoint(execution_plan.ticket_id)
            self._save_state(
                "idle",
                accepted_integration={
                    "ticket_id": execution_plan.ticket_id,
                    "checkpoint": checkpoint,
                    "control_head": execution_plan.control.local_head,
                },
            )
            self.status_view()
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
                accepted_integration=None,
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
        resume_required = False
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
            resume_required = (
                isinstance(self._state.get("resume_required"), dict)
                and self._state["resume_required"].get("status") == "required"
                and self._state["resume_required"].get("ticket_id")
                == selected_ticket.id
            )
            interrupted_failure = (
                isinstance(failed_for_ticket, dict)
                and failed_for_ticket.get("interrupted") is True
            )
            if (
                authorization is None
                and execution_plan.action == "run-worker"
                and not execution_plan.bound
                and interrupted_failure
            ):
                if (
                    failed_for_ticket.get("interruption_kind")
                    in {"service_shutdown", "process_loss"}
                    and generation_is_same
                ):
                    authorization = ExecutionAuthorization(
                        selected_ticket.id, "automatic_resume"
                    )
                else:
                    _log(
                        f"interrupted execution for {selected_ticket.id} is not "
                        "eligible for automatic resume; explicit retry is required"
                    )
                    return 0
            if authorization is not None:
                authorized_ticket_id = authorization.ticket_id
                if not authorization.is_zero_delta_retry:
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
                            f"ticket {authorized_ticket_id} failed execution is stale; "
                            "current project state must be reviewed before retry"
                        )
                    if authorization.is_automatic_resume and (
                        failed_for_ticket.get("interrupted") is not True
                        or failed_for_ticket.get("interruption_kind")
                        not in {"service_shutdown", "process_loss"}
                    ):
                        raise DevlegateError(
                            "automatic resume requires a safely interrupted execution"
                        )
            if (
                authorization is None
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
            if authorization is not None and authorization.is_explicit_retry:
                if selected_ticket.id != authorization.ticket_id:
                    raise DevlegateError(
                        f"ticket {authorization.ticket_id} is no longer the current "
                        "runnable ticket"
                    )
            if (
                authorization is not None
                and authorization.is_automatic_resume
                and (selected_ticket.id != authorization.ticket_id)
            ):
                raise DevlegateError(
                    "automatic resume ticket is no longer the current runnable ticket"
                )
            if (
                generation_is_same
                and not pending_agent_execution
                and authorization is None
                and not resume_required
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
                self._state["execution_base_head"] if existing_lineage else local_head
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
            automatic_retry = self._state.get("automatic_retry")
            automatic_fresh_id = (
                automatic_retry.get("fresh_execution_id")
                if authorization is not None
                and authorization.is_zero_delta_retry
                and isinstance(automatic_retry, dict)
                else None
            )
            execution_id = (
                self._state.get("execution_id")
                if pending_agent_execution
                else automatic_fresh_id or new_execution_id()
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
                selected_ticket_title=selected_ticket.title,
                execution_ticket_id=selected_ticket.id,
                execution_base_head=execution_base_head,
                execution_control_head=bound_execution_control,
                execution_branch=f"devlegate/work/{selected_ticket.id}",
                execution_path=str(
                    self.execution_worktree_root / "work" / selected_ticket.id
                ),
                execution_id=execution_id,
                execution_remote_head=execution_remote_head,
                worker_identity=None,
                resume_required=None,
                automatic_retry=(
                    {
                        **automatic_retry,
                        "status": "admitted",
                        "fresh_execution_id": execution_id,
                    }
                    if authorization is not None
                    and authorization.is_zero_delta_retry
                    and isinstance(automatic_retry, dict)
                    else automatic_retry
                ),
            )
            if self._stop_before_admission():
                return 0
            self._owned_execution_id = execution_id
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
        retrying_interrupted = False
        if authorization is not None and authorization.is_explicit_retry:
            failures = self._state.get("failed_executions", {})
            failure = (
                failures.get(selected_ticket.id) if isinstance(failures, dict) else None
            )
            retrying_interrupted = (
                isinstance(failure, dict) and failure.get("interrupted") is True
            )
        if (
            bound_execution
            or resume_required
            or (authorization is not None and authorization.is_automatic_resume)
            or (
                authorization is not None
                and authorization.is_explicit_retry
                and (workspace.head != workspace.base_head or retrying_interrupted)
            )
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
            execution_stage="worker-launch",
            execution_start_head=workspace.head,
            execution_ticket_id=selected_ticket.id,
            execution_base_head=workspace.base_head,
            execution_control_head=str(self._state["control_head"]),
            execution_branch=workspace.branch,
            execution_path=str(workspace.path),
            execution_id=execution_id,
            execution_remote_head=execution_remote_head,
            worker_identity=None,
        )
        self._owned_execution_id = execution_id
        self._publish_service_snapshot(lifecycle="worker", worker_running=True)
        worker_returned = False
        try:
            try:
                worker_run = self._workers.run(
                    workspace,
                    prompt,
                    execution_id=execution_id,
                    stop_request=self._stop_event,
                    identity_handler=lambda identity: self._save_state(
                        "agent_running",
                        execution_stage="worker-running",
                        worker_identity=identity.as_dict(),
                    ),
                    interruption_handler=lambda kind: self._save_state(
                        "agent_running", execution_interruption_kind=kind
                    ),
                )
            except WorkerAdmissionClosed:
                self._save_state(
                    "idle",
                    execution_stage=None,
                    pending_execution_report=None,
                    worker_identity=None,
                )
                return 0
            worker_returned = True
        finally:
            if worker_returned and worker_run.worker_group_retired:
                self._save_state(
                    "agent_running",
                    execution_stage="post-worker",
                    worker_identity=None,
                )
            self._publish_service_snapshot(lifecycle="processing", worker_running=False)
        if not worker_run.worker_group_retired:
            raise DevlegateError(
                "worker leader exited but execution process group is still alive"
            )
        try:
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
        if execution_result.result.conclusion == "failed":
            self._iteration_diagnostic = (
                f"worker failed: {execution_result.result.reason}"
            )
        self._save_state(
            "agent_running",
            execution_stage="checkpointing",
            pending_execution_report=execution_result.as_dict(),
        )
        try:
            checkpoint = manager.checkpoint(
                workspace,
                execution_id,
                title=selected_ticket.title,
                summary=(
                    execution_result.result.claim.summary
                    if execution_result.result.claim is not None
                    else execution_result.result.reason
                ),
            )
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(f"execution checkpoint failed: {error}") from error
        report = dataclasses.replace(
            execution_result,
            workspace_head=checkpoint.after_head,
        )
        self._save_state(
            "agent_running",
            execution_stage="post-checkpoint",
            pending_execution_report=report.as_dict(),
        )
        _log(
            "execution checkpoint "
            f"{'created' if checkpoint.commit_created else 'not needed'}: "
            f"{checkpoint.after_head[:12]}"
        )
        if self._lifecycle_drain_pending():
            self._publish_service_snapshot(
                lifecycle=(
                    "restarting"
                    if self.lifecycle_intent() == "restart"
                    else "stopping"
                ),
                worker_running=False,
            )
            return 0
        product = self._observe_product_generation(workspace.base_head)
        current_product_remote = product["remote_head"]
        if not product["stable"]:
            if self._is_zero_delta_stale_product_drift(
                workspace.base_head, checkpoint.after_head, product
            ):
                retry = self._zero_delta_retry_payload(report, product)
                self._save_state(
                    "agent_running",
                    execution_stage="lifecycle",
                    pending_execution_report=report.as_dict(),
                    automatic_retry=retry,
                )
                return self._complete_execution_lifecycle(report)
            reconciliation_product = (
                product["local_head"]
                if product["local_head"] != workspace.base_head
                else current_product_remote
            )
            if not reconciliation_product:
                reconciliation_product = workspace.base_head
            evidence_ref = self._reconciliation_evidence_ref(
                selected_ticket.id, execution_id
            )
            existing_evidence = _git(
                self.repo, "rev-parse", "--verify", evidence_ref, check=False
            )
            if existing_evidence.returncode == 0 and (
                existing_evidence.stdout.strip() != checkpoint.after_head
            ):
                raise DevlegateError("reconciliation evidence ref is inconsistent")
            pinned = _git(
                self.repo,
                "update-ref",
                evidence_ref,
                checkpoint.after_head,
                check=False,
            )
            if pinned.returncode:
                raise DevlegateError(
                    "cannot pin reconciliation worker checkpoint evidence"
                )
            reconciliation = {
                "status": "pending",
                "reason": product["reason"],
                "resolution": None,
                "ticket_id": selected_ticket.id,
                "execution_id": execution_id,
                "original_base": workspace.base_head,
                "observed_product": reconciliation_product,
                "worker_checkpoint": checkpoint.after_head,
                "execution_remote_head": execution_remote_head,
                "product_remote_head": current_product_remote,
                "control_head": str(self._state["execution_control_head"]),
                "execution_branch": workspace.branch,
                "execution_path": str(workspace.path),
                "evidence_ref": evidence_ref,
                "product_branch": product["branch"] or "",
                "product_local_head": product["local_head"],
                "product_dirty": product["dirty"],
                "product_observation": product["reason"],
                "product_target_eligible": product["target_eligible"],
                "execution_report": report.as_dict(),
            }
            self._save_state(
                "idle",
                handled_remote_head=current_product_remote,
                handled_control_head=str(self._state["control_head"]),
                reconciliation=reconciliation,
            )
            reason = (
                f"reconciliation required: ticket {selected_ticket.id}; "
                f"original base {workspace.base_head}; "
                f"observed product {reconciliation_product}; "
                f"product state {product['reason']}; "
                f"worker checkpoint {checkpoint.after_head}"
            )
            self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
            _log(reason)
            return 1
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
        self._save_state(
            "agent_running",
            execution_stage="lifecycle",
            pending_execution_report=report.as_dict(),
            execution_interruption_kind=worker_run.interruption_kind,
        )
        try:
            control_head = self._apply_execution_lifecycle(report)
        except (DevlegateError, OSError, ExecutionReportError) as error:
            raise DevlegateError(f"control-plane lifecycle failed: {error}") from error
        self._finalize_execution_lifecycle(
            report, control_head, local_head, remote_head
        )
        if worker_run.interruption_kind is not None:
            _log(
                "execution interrupted: "
                f"{worker_run.interruption_kind.replace('_', ' ')}; "
                f"lifecycle control HEAD {control_head[:12]}"
            )
        else:
            _log(
                f"execution {report.result.conclusion}; lifecycle control HEAD "
                f"{control_head[:12]}"
            )
        if worker_run.interruption_kind is not None:
            return 0
        return 1 if report.result.conclusion == "failed" else 0

    def _recover_accepted_integration(self) -> int:
        pending = self._state.get("accepted_integration")
        if not isinstance(pending, dict):
            raise DevlegateError("invalid accepted integration state")
        ticket_id = pending.get("ticket_id")
        if not isinstance(ticket_id, str) or not ticket_id:
            raise DevlegateError("invalid accepted integration ticket identity")
        control_head, _control_remote_head = self._sync_control()
        product_fetch = self._git_runtime(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        )
        if product_fetch.returncode:
            raise WorkflowBlockedError(
                "cannot freshly observe product remote for accepted integration"
            )
        code = self._git_observation(self.repo, self.remote_branch)
        control = self._git_observation(self.control_worktree, self.control_branch)
        self._integrate_accepted(ticket_id, code, control)
        todo_fingerprint, _todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        self._save_state(
            "idle",
            handled_remote_head=code.remote_head or "",
            handled_control_head=control_head,
            handled_todo_fingerprint=todo_fingerprint,
            accepted_integration=None,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)
        _log(f"accepted ticket {ticket_id} integrated")
        return 0

    def _complete_execution_lifecycle(self, report: ExecutionReport) -> int:
        automatic_retry = self._state.get("automatic_retry")
        if isinstance(automatic_retry, dict) and automatic_retry.get("status") == (
            "pending"
        ):
            return self._resolve_zero_delta_retry(report)
        product = self._observe_product_generation(report.code_base_head)
        if self._is_zero_delta_stale_product_drift(
            report.code_base_head, report.workspace_head, product
        ):
            retry = self._zero_delta_retry_payload(report, product)
            self._save_state(
                "agent_running",
                execution_stage="lifecycle",
                pending_execution_report=report.as_dict(),
                automatic_retry=retry,
            )
            return self._resolve_zero_delta_retry(report)
        control_head = self._apply_execution_lifecycle(report)
        self._finalize_execution_lifecycle(report, control_head)
        return 0 if report.result.conclusion != "failed" else 1

    def _is_zero_delta_stale_product_drift(
        self,
        admitted_base: str,
        checkpoint: str | None,
        product: dict[str, object],
    ) -> bool:
        if checkpoint != admitted_base or not product.get("target_eligible"):
            return False
        local_head = product.get("local_head")
        remote_head = product.get("remote_head")
        if (
            not isinstance(local_head, str)
            or not isinstance(remote_head, str)
            or remote_head == admitted_base
            or local_head not in {admitted_base, remote_head}
        ):
            return False
        if self._state.get("execution_remote_head") is not None:
            return False
        execution_branch = self._state.get("execution_branch")
        if not isinstance(execution_branch, str) or not execution_branch:
            return False
        if self._execution_remote_head(execution_branch) is not None:
            return False
        ancestor = _git(
            self.repo,
            "merge-base",
            "--is-ancestor",
            admitted_base,
            remote_head,
            check=False,
        )
        return ancestor.returncode == 0

    @staticmethod
    def _zero_delta_retry_payload(
        report: ExecutionReport, product: dict[str, object]
    ) -> dict[str, object]:
        product_head = product.get("remote_head")
        if not isinstance(product_head, str) or not product_head:
            raise WorkflowBlockedError("zero-delta retry lacks a product generation")
        return {
            "status": "pending",
            "reason": "automatic retry after zero-delta product drift",
            "ticket_id": report.ticket_id,
            "stale_execution_id": report.execution_id,
            "original_base": report.code_base_head,
            "worker_checkpoint": report.workspace_head,
            "product_head": product_head,
            "control_head": report.control_head,
        }

    def _retire_zero_delta_execution(self, report: ExecutionReport) -> None:
        checkpoint = report.workspace_head
        if checkpoint != report.code_base_head:
            raise WorkflowBlockedError(
                "zero-delta execution checkpoint is not its base"
            )
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, report.ticket_id
        )
        remote = self._execution_remote_head(report.execution_branch)
        if remote is not None:
            raise WorkflowBlockedError(
                "zero-delta execution has published execution history"
            )
        registrations = manager._registrations()
        registration = registrations.get(manager.path.resolve())
        if registration is not None:
            try:
                workspace = manager._validate_existing(registration, checkpoint)
                manager.retire(workspace, checkpoint)
            except (ExecutionWorkspaceError, OSError) as error:
                raise WorkflowBlockedError(
                    f"cannot retire zero-delta execution: {error}"
                ) from error
        elif manager.path.exists() or any(
            item.get("branch") == manager.branch for item in registrations.values()
        ):
            raise WorkflowBlockedError(
                "zero-delta execution workspace topology is ambiguous"
            )
        local_ref = f"refs/heads/{report.execution_branch}"
        local = _git(self.repo, "rev-parse", "--verify", local_ref, check=False)
        if local.returncode == 0 and local.stdout.strip() != checkpoint:
            raise WorkflowBlockedError(
                "zero-delta execution branch changed before retirement"
            )
        if local.returncode == 0:
            removed = _git(
                self.repo, "branch", "-D", report.execution_branch, check=False
            )
            if removed.returncode:
                raise WorkflowBlockedError(
                    removed.stderr.strip()
                    or "cannot retire zero-delta execution branch"
                )

    def _resolve_zero_delta_retry(self, report: ExecutionReport) -> int:
        retry = self._state.get("automatic_retry")
        if not isinstance(retry, dict) or retry.get("status") != "pending":
            raise WorkflowBlockedError("zero-delta retry state is missing")
        if any(
            retry.get(key) != value
            for key, value in (
                ("ticket_id", report.ticket_id),
                ("stale_execution_id", report.execution_id),
                ("original_base", report.code_base_head),
                ("worker_checkpoint", report.workspace_head),
            )
        ):
            raise WorkflowBlockedError("zero-delta retry identity is inconsistent")
        product = self._observe_product_generation(report.code_base_head)
        if not self._is_zero_delta_stale_product_drift(
            report.code_base_head, report.workspace_head, product
        ):
            raise WorkflowBlockedError(
                "zero-delta product drift is no longer safely provable"
            )
        control_head = self._persist_historical_execution_report(report)
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(report.ticket_id)
        executable = (
            ticket is not None
            and ticket.state == "todo"
            and ticket in ticket_store.runnable
        )
        self._retire_zero_delta_execution(report)
        product_head = str(product["remote_head"])
        if executable:
            fresh_execution_id = retry.get("fresh_execution_id")
            if not isinstance(fresh_execution_id, str) or not fresh_execution_id:
                fresh_execution_id = new_execution_id()
            next_retry = {
                **retry,
                "status": "pending_admission",
                "fresh_execution_id": fresh_execution_id,
                "product_head": product_head,
            }
            self._finalize_execution_lifecycle(
                report,
                control_head,
                product_head,
                product_head,
                clear_execution=True,
                automatic_retry=next_retry,
                apply_conclusion=False,
            )
            _log(
                f"zero-delta execution {report.execution_id} became stale on "
                f"{product_head[:12]}; fresh execution admission is pending"
            )
            return 0
        completed = {
            **retry,
            "status": "not_admitted",
            "product_head": product_head,
        }
        self._finalize_execution_lifecycle(
            report,
            control_head,
            product_head,
            product_head,
            clear_execution=True,
            automatic_retry=None,
            last_automatic_retry=completed,
            apply_conclusion=False,
        )
        _log(
            f"zero-delta execution {report.execution_id} retained without retry; "
            f"ticket {report.ticket_id} is no longer executable"
        )
        return 0

    def _finalize_execution_lifecycle(
        self,
        report: ExecutionReport,
        control_head: str,
        product_head: str | None = None,
        product_remote_head: str | None = None,
        *,
        clear_execution: bool = False,
        automatic_retry: object = _UNSET,
        last_automatic_retry: object = _UNSET,
        apply_conclusion: bool = True,
    ) -> None:
        interruption_kind = self._state.get("execution_interruption_kind")
        if not isinstance(interruption_kind, str):
            interruption_kind = None
        product_head = product_head or str(self._state.get("local_head", ""))
        product_remote_head = product_remote_head or str(
            self._state.get("remote_head", "")
        )
        todo_fingerprint, _todo_count = _todo_fingerprint(
            self.control_worktree, self.todo_path
        )
        failed_executions = self._state.get("failed_executions", {})
        if not isinstance(failed_executions, dict):
            failed_executions = {}
        if apply_conclusion and report.result.conclusion == "failed":
            failure = {
                "execution_id": report.execution_id,
                "product_head": product_head,
                "remote_head": product_remote_head,
                "control_head": control_head,
                "todo_fingerprint": todo_fingerprint,
                "reason": report.result.reason,
            }
            if interruption_kind in {
                "operator_abort",
                "service_shutdown",
            }:
                failure["interrupted"] = True
                failure["interruption_kind"] = interruption_kind
                failure["reason"] = "interrupted: " + interruption_kind.replace(
                    "_", " "
                )
                if interruption_kind == "operator_abort" and self._stop_event is None:
                    self._foreground_abort_requested = True
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
        elif apply_conclusion:
            failed_executions = {
                ticket_id: metadata
                for ticket_id, metadata in failed_executions.items()
                if ticket_id != report.ticket_id
            }
        fields: dict[str, object] = {
            "handled_remote_head": product_remote_head,
            "handled_control_head": control_head,
            "handled_todo_fingerprint": todo_fingerprint,
            "execution_control_head": control_head,
            "failed_executions": failed_executions,
        }
        if automatic_retry is _UNSET:
            current_retry = self._state.get("automatic_retry")
            if (
                isinstance(current_retry, dict)
                and current_retry.get("fresh_execution_id") == report.execution_id
            ):
                fields["automatic_retry"] = None
                fields["last_automatic_retry"] = {
                    **current_retry,
                    "status": "completed",
                    "completed_conclusion": report.result.conclusion,
                }
        else:
            fields["automatic_retry"] = automatic_retry
        if last_automatic_retry is not _UNSET:
            fields["last_automatic_retry"] = last_automatic_retry
        self._save_state(
            "idle",
            clear_execution=clear_execution,
            **fields,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)

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
                "report_unavailable": True,
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
        bound_base = self._state.get("execution_base_head") if same_ticket else None
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
        if expected_remote_head is not None:
            ancestor = _git(
                workspace.path,
                "merge-base",
                "--is-ancestor",
                expected_remote_head,
                checkpoint_head,
                check=False,
            )
            if ancestor.returncode:
                raise DevlegateError(
                    "execution predecessor is not an ancestor of checkpoint"
                )
        lease = f"refs/heads/{workspace.branch}:{expected_remote_head or ''}"
        pushed = _git(
            workspace.path,
            "push",
            f"--force-with-lease={lease}",
            self.remote_name,
            f"{checkpoint_head}:refs/heads/{workspace.branch}",
            check=False,
        )
        if pushed.returncode:
            current_remote_head = self._execution_remote_head(workspace.branch)
            if current_remote_head == checkpoint_head:
                return
            if current_remote_head == expected_remote_head:
                raise DevlegateError(
                    pushed.stderr.strip() or "execution checkpoint publication failed"
                )
            raise DevlegateError("execution branch remote changed during publication")

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

    def _accepted_execution_report(self, ticket_id: str) -> ExecutionReport:
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
            report
            for report in reports
            if report.execution_branch == expected_branch
            and isinstance(report.workspace_head, str)
            and report.workspace_head == remote_head
        ]
        if remote_head is None or len(matches) != 1:
            raise WorkflowBlockedError(
                f"accepted ticket {ticket_id} has ambiguous or missing completed "
                "execution evidence"
            )
        report = matches[0]
        checkpoint = report.workspace_head
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
        return report

    def _accepted_checkpoint(self, ticket_id: str) -> str:
        checkpoint = self._accepted_execution_report(ticket_id).workspace_head
        assert checkpoint is not None
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
        report = self._accepted_execution_report(ticket_id)
        checkpoint = report.workspace_head
        assert checkpoint is not None
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
                "product history diverged from accepted checkpoint; rebase or merge "
                "is not performed"
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
            raise WorkflowBlockedError("product checkout is not safely synchronized")
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
        pending = self._state.get("accepted_integration")
        expected_head = (
            pending.get("control_head")
            if isinstance(pending, dict)
            else control.local_head
        )
        if not isinstance(expected_head, str) or not _is_git_identity(expected_head):
            raise WorkflowBlockedError(
                "accepted integration control identity is invalid"
            )
        return self._complete_accepted(ticket_id, expected_head)

    def _complete_accepted(
        self, ticket_id: str, expected_head: str
    ) -> str:
        current = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        if not _is_git_identity(expected_head):
            raise WorkflowBlockedError(
                "accepted integration control identity is invalid"
            )
        store = load_ticket_store(self.control_worktree, self._workflow_paths())
        ticket = store.by_id.get(ticket_id)
        if ticket is None:
            raise WorkflowBlockedError("accepted ticket disappeared during integration")
        if ticket.state == "done":
            classification, commit = self._observe_accepted_control_integration(
                ticket_id, expected_head, current, None
            )
            if classification == "local_exact_unpublished" and commit is not None:
                push = _git(
                    self.control_worktree,
                    "push",
                    self.remote_name,
                    f"{commit}:refs/heads/{self.control_branch}",
                    check=False,
                )
                if push.returncode:
                    raise DevlegateError(
                        push.stderr.strip() or "cannot publish accepted integration"
                    )
                classification, commit = self._observe_accepted_control_integration(
                    ticket_id, expected_head, current, None
                )
            if classification not in {"remote_exact_published"}:
                raise WorkflowBlockedError(
                    "accepted integration control lineage is ambiguous"
                )
            assert commit is not None
            return current
        if ticket.state != "accepted":
            raise WorkflowBlockedError("accepted ticket changed during integration")
        report = self._accepted_execution_report(ticket_id)
        if current != expected_head:
            raise WorkflowBlockedError(
                "control HEAD changed during accepted integration"
            )
        target = self.control_worktree / self.done_path / f"{ticket_id}.md"
        ticket.path.rename(target)
        _git(self.control_worktree, "add", "-A")
        commit = _git(
            self.control_worktree,
            "commit",
            "-m",
            integration_message(
                ticket_id,
                ticket.title,
                report.execution_id,
                (
                    report.result.claim.summary
                    if report.result.claim is not None
                    else report.result.reason
                ),
                report.workspace_head or "",
            ),
            check=False,
        )
        if commit.returncode:
            raise DevlegateError(
                commit.stderr.strip() or "cannot create control transition"
            )
        commit_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        if not self._accepted_integration_commit_is_exact(
            commit_head, ticket_id, expected_head
        ):
            raise WorkflowBlockedError(
                "created accepted integration commit is not exact"
            )
        push = _git(
            self.control_worktree,
            "push",
            self.remote_name,
            f"{commit_head}:refs/heads/{self.control_branch}",
            check=False,
        )
        if push.returncode:
            raise DevlegateError(
                push.stderr.strip() or "cannot publish control transition"
            )
        return commit_head

    def _observe_accepted_control_integration(
        self,
        ticket_id: str,
        expected_parent: str,
        local_head: str,
        remote_head: str | None,
    ) -> tuple[str, str | None]:
        if not _is_git_identity(expected_parent) or not _is_git_identity(local_head):
            return "ambiguous", None
        if remote_head is None:
            remote = _git(
                self.control_worktree,
                "ls-remote",
                self.remote_name,
                f"refs/heads/{self.control_branch}",
                check=False,
            )
            if remote.returncode or not remote.stdout.split():
                return "ambiguous", None
            remote_head = remote.stdout.split()[0]
        if not _is_git_identity(remote_head):
            return "ambiguous", None
        if remote_head == expected_parent:
            if local_head == expected_parent:
                return "not_applied", None
            if self._accepted_integration_commit_is_exact(
                local_head, ticket_id, expected_parent
            ):
                return "local_exact_unpublished", local_head
            return "ambiguous", None
        lineage = _git(
            self.control_worktree,
            "rev-list",
            "--reverse",
            f"{expected_parent}..{remote_head}",
            check=False,
        )
        if lineage.returncode:
            return "ambiguous", None
        descendants = lineage.stdout.splitlines()
        if not descendants:
            return "ambiguous", None
        candidate = descendants[0]
        if self._accepted_integration_commit_is_exact(
            candidate, ticket_id, expected_parent
        ):
            return "remote_exact_published", candidate
        return "ambiguous", None

    def _accepted_integration_commit_is_exact(
        self, commit: str, ticket_id: str, expected_parent: str
    ) -> bool:
        parent = _git(self.control_worktree, "rev-parse", f"{commit}^", check=False)
        if parent.returncode or parent.stdout.strip() != expected_parent:
            return False
        names = _git(
            self.control_worktree,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            commit,
            check=False,
        )
        expected = {
            f"D\t{self.accepted_path}/{ticket_id}.md",
            f"A\t{self.done_path}/{ticket_id}.md",
        }
        if names.returncode or set(names.stdout.splitlines()) != expected:
            return False
        before = _git(
            self.control_worktree,
            "rev-parse",
            f"{parent.stdout.strip()}:{self.accepted_path}/{ticket_id}.md",
            check=False,
        )
        after = _git(
            self.control_worktree,
            "rev-parse",
            f"{commit}:{self.done_path}/{ticket_id}.md",
            check=False,
        )
        return (
            before.returncode == 0
            and after.returncode == 0
            and before.stdout.strip() == after.stdout.strip()
        )

    @staticmethod
    def _reconciliation_evidence_ref(ticket_id: str, execution_id: str) -> str:
        return f"refs/devlegate/reconciliation/{ticket_id}/{execution_id}"

    @staticmethod
    def _drop_evidence_ref(ticket_id: str, execution_id: str) -> str:
        return f"refs/devlegate/executions/{ticket_id}/{execution_id}"

    @staticmethod
    def _drop_disposition_ref(ticket_id: str, execution_id: str) -> str:
        return f"refs/devlegate/dispositions/{ticket_id}/{execution_id}"

    def _read_drop_disposition(
        self, ticket_id: str, execution_id: str
    ) -> dict[str, object] | None:
        ref = self._drop_disposition_ref(ticket_id, execution_id)
        resolved = _git(self.repo, "rev-parse", "--verify", ref, check=False)
        if resolved.returncode:
            return None
        object_id = resolved.stdout.strip()
        object_type = _git(
            self.repo, "cat-file", "-t", object_id, check=False
        )
        if object_type.returncode or object_type.stdout.strip() != "blob":
            raise DevlegateError("drop disposition ref does not point to a blob")
        content = _git(self.repo, "cat-file", "-p", object_id, check=False)
        if content.returncode:
            raise DevlegateError("drop disposition blob is unavailable")
        try:
            value = json.loads(content.stdout)
        except json.JSONDecodeError as error:
            raise DevlegateError("drop disposition blob is invalid") from error
        if not isinstance(value, dict):
            raise DevlegateError("drop disposition must be an object")
        return value

    def _drop_disposition_payload(
        self, report: ExecutionReport, checkpoint: str
    ) -> dict[str, object]:
        return self._drop_disposition_payload_for(
            report, checkpoint, disposition="dropped"
        )

    def _drop_disposition_payload_for(
        self,
        report: ExecutionReport,
        checkpoint: str,
        *,
        disposition: str,
    ) -> dict[str, object]:
        return {
            "schema": "devlegate.execution-disposition.v1",
            "execution_id": report.execution_id,
            "ticket_id": report.ticket_id,
            "control_head": report.control_head,
            "code_base_head": report.code_base_head,
            "execution_branch": report.execution_branch,
            "checkpoint": checkpoint,
            "worker_conclusion": report.result.conclusion,
            "execution_report": report.as_dict(),
            "disposition": disposition,
            "reason": "control authority withdrawn",
        }

    def _persist_drop_intent(self, report: ExecutionReport) -> None:
        checkpoint = report.workspace_head
        if not isinstance(checkpoint, str) or not checkpoint:
            raise DevlegateError("drop execution checkpoint identity is missing")
        payload = self._drop_disposition_payload_for(
            report, checkpoint, disposition="dropping"
        )
        existing = self._read_drop_disposition(report.ticket_id, report.execution_id)
        if existing is not None:
            if existing.get("disposition") == "dropped":
                return
            if existing != payload:
                raise DevlegateError("drop disposition intent is inconsistent")
            return
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        blob = _git(
            self.repo,
            "hash-object",
            "-w",
            "--stdin",
            input_text=encoded,
            check=False,
        )
        if blob.returncode or not blob.stdout.strip():
            raise DevlegateError("cannot persist drop disposition intent")
        updated = _git(
            self.repo,
            "update-ref",
            self._drop_disposition_ref(report.ticket_id, report.execution_id),
            blob.stdout.strip(),
            check=False,
        )
        if updated.returncode:
            reread = self._read_drop_disposition(
                report.ticket_id, report.execution_id
            )
            if reread != payload:
                raise DevlegateError("cannot publish drop disposition intent")

    def _pin_drop_evidence(
        self, report: ExecutionReport
    ) -> dict[str, object]:
        checkpoint = report.workspace_head
        if not isinstance(checkpoint, str) or not checkpoint:
            raise DevlegateError("drop execution checkpoint identity is missing")
        evidence_ref = self._drop_evidence_ref(report.ticket_id, report.execution_id)
        observed = _git(
            self.repo,
            "rev-parse",
            "--verify",
            f"{evidence_ref}^{{commit}}",
            check=False,
        )
        if observed.returncode == 0 and observed.stdout.strip() != checkpoint:
            raise DevlegateError("drop execution evidence ref is inconsistent")
        if observed.returncode:
            pinned = _git(
                self.repo,
                "update-ref",
                evidence_ref,
                checkpoint,
                check=False,
            )
            if pinned.returncode:
                reread = _git(
                    self.repo,
                    "rev-parse",
                    "--verify",
                    f"{evidence_ref}^{{commit}}",
                    check=False,
                )
                if reread.returncode or reread.stdout.strip() != checkpoint:
                    raise DevlegateError("cannot pin drop execution evidence")
        payload = self._drop_disposition_payload(report, checkpoint)
        existing = self._read_drop_disposition(report.ticket_id, report.execution_id)
        if existing is not None:
            if existing.get("disposition") == "dropped" and existing != payload:
                raise DevlegateError("drop disposition evidence is inconsistent")
            if existing.get("disposition") not in {"dropping", "dropped"}:
                raise DevlegateError("drop disposition evidence is inconsistent")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        blob = _git(
            self.repo,
            "hash-object",
            "-w",
            "--stdin",
            input_text=encoded,
            check=False,
        )
        if blob.returncode or not blob.stdout.strip():
            raise DevlegateError("cannot persist drop disposition evidence")
        disposition_ref = self._drop_disposition_ref(
            report.ticket_id, report.execution_id
        )
        updated = _git(
            self.repo,
            "update-ref",
            disposition_ref,
            blob.stdout.strip(),
            check=False,
        )
        if updated.returncode:
            reread = self._read_drop_disposition(
                report.ticket_id, report.execution_id
            )
            if reread != payload:
                raise DevlegateError("cannot publish drop disposition evidence")
        return payload

    def _drop_already_completed(self, ticket_id: str, execution_id: str) -> bool:
        payload = self._read_drop_disposition(ticket_id, execution_id)
        if payload is None:
            return False
        if (
            payload.get("disposition") not in {"dropping", "dropped"}
            or payload.get("ticket_id") != ticket_id
            or payload.get("execution_id") != execution_id
        ):
            raise DevlegateError("execution disposition is invalid")
        if payload.get("disposition") == "dropping":
            return False
        evidence = _git(
            self.repo,
            "rev-parse",
            "--verify",
            f"{self._drop_evidence_ref(ticket_id, execution_id)}^{{commit}}",
            check=False,
        )
        if evidence.returncode or evidence.stdout.strip() != payload.get("checkpoint"):
            raise DevlegateError("completed drop has inconsistent checkpoint evidence")
        return not (
            self._state.get("phase") in {"agent_pending", "agent_running"}
            and self._state.get("execution_id") == execution_id
        )

    def _validate_drop_evidence(
        self, ticket_id: str, execution_id: str, *, allow_missing_remote: bool = False
    ) -> tuple[ExecutionReport, ExecutionWorkspace]:
        pending = self._state.get("pending_execution_report")
        try:
            report = ExecutionReport.from_dict(pending)
        except ExecutionReportError as error:
            raise DevlegateError(
                "drop requires a valid pending execution report"
            ) from error
        if report.ticket_id != ticket_id or report.execution_id != execution_id:
            raise DevlegateError("pending report does not match requested execution")
        self._report_matches_execution_binding(report)
        if report.workspace_head is None:
            raise DevlegateError("drop requires a checkpoint identity")
        workspace = self._drop_workspace_from_state(report)
        if workspace.head != report.workspace_head:
            raise DevlegateError("execution worktree does not match its checkpoint")
        start_head = self._state.get("execution_start_head")
        if not isinstance(start_head, str) or not start_head:
            raise DevlegateError("drop execution start identity is incomplete")
        if workspace.path.exists():
            if not self._checkpoint_commit_is_exact(
                workspace, report.workspace_head, start_head
            ):
                raise DevlegateError("execution checkpoint evidence is ambiguous")
        elif not allow_missing_remote:
            raise DevlegateError("execution worktree is missing")
        else:
            evidence = _git(
                self.repo,
                "rev-parse",
                "--verify",
                f"{self._drop_evidence_ref(ticket_id, execution_id)}^{{commit}}",
                check=False,
            )
            if evidence.returncode or evidence.stdout.strip() != report.workspace_head:
                raise DevlegateError("retired execution checkpoint evidence is invalid")
        remote = self._execution_remote_head(report.execution_branch)
        if remote != report.workspace_head and not (
            allow_missing_remote and remote is None
        ):
            raise DevlegateError("execution remote checkpoint is not exact")
        return report, workspace

    def _drop_workspace_from_state(self, report: ExecutionReport) -> ExecutionWorkspace:
        ticket_id = self._state.get("execution_ticket_id")
        base_head = self._state.get("execution_base_head")
        branch = self._state.get("execution_branch")
        path = self._state.get("execution_path")
        if not all(
            isinstance(value, str) and value
            for value in (ticket_id, base_head, branch, path)
        ):
            raise DevlegateError("drop execution workspace binding is incomplete")
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        if branch != manager.branch or Path(path) != manager.path:
            raise DevlegateError("drop execution workspace binding is invalid")
        registrations = manager._registrations()
        registration = registrations.get(manager.path.resolve())
        if registration is not None:
            try:
                return manager._validate_existing(registration, base_head)
            except (ExecutionWorkspaceError, OSError) as error:
                raise DevlegateError(str(error)) from error
        branch_path = next(
            (
                registered
                for registered, item in registrations.items()
                if item.get("branch") == manager.branch
            ),
            None,
        )
        if branch_path is not None or manager.path.exists():
            raise DevlegateError("drop execution workspace topology is ambiguous")
        return ExecutionWorkspace(
            report.ticket_id,
            manager.branch,
            manager.path,
            report.workspace_head or "",
            base_head,
            False,
        )

    def _retire_drop_workspace(
        self, report: ExecutionReport, workspace: ExecutionWorkspace
    ) -> None:
        expected = report.workspace_head
        if expected is None:
            raise DevlegateError("drop execution checkpoint identity is missing")
        remote = self._execution_remote_head(report.execution_branch)
        if remote is not None and remote != expected:
            raise DevlegateError("execution remote changed before drop")
        if remote == expected:
            deleted = _git(
                self.repo,
                "push",
                f"--force-with-lease=refs/heads/{report.execution_branch}:{expected}",
                self.remote_name,
                f":refs/heads/{report.execution_branch}",
                check=False,
            )
            if deleted.returncode:
                remaining = self._execution_remote_head(report.execution_branch)
                if remaining is not None:
                    raise DevlegateError(
                        deleted.stderr.strip()
                        or "cannot retire execution remote branch"
                    )
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, report.ticket_id
        )
        try:
            manager.retire(workspace, expected)
        except ExecutionWorkspaceError as error:
            raise DevlegateError(
                f"cannot retire execution worktree: {error}"
            ) from error
        local_ref = f"refs/heads/{report.execution_branch}"
        local = _git(self.repo, "rev-parse", "--verify", local_ref, check=False)
        if local.returncode == 0 and local.stdout.strip() != expected:
            raise DevlegateError("local execution branch changed before drop")
        if local.returncode == 0:
            removed = _git(
                self.repo, "branch", "-D", report.execution_branch, check=False
            )
            if removed.returncode:
                raise DevlegateError(
                    removed.stderr.strip() or "cannot retire local execution branch"
                )

    def _drop_owned(self, ticket_id: str, execution_id: str | None) -> None:
        if not isinstance(execution_id, str) or not execution_id:
            raise DevlegateError("drop requires an exact execution identity")
        if self._drop_already_completed(ticket_id, execution_id):
            return
        existing = self._read_drop_disposition(ticket_id, execution_id)
        report, workspace = self._validate_drop_evidence(
            ticket_id,
            execution_id,
            allow_missing_remote=existing is not None,
        )
        self._persist_drop_intent(report)
        self._pin_drop_evidence(report)
        self._retire_drop_workspace(report, workspace)
        self._owned_execution_id = None
        self._save_state("idle", clear_execution=True)

    def _observe_product_generation(self, admitted_head: str) -> dict[str, object]:
        branch_result = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        branch = branch_result.stdout.strip() if branch_result.returncode == 0 else None
        head_result = _git(self.repo, "rev-parse", "HEAD", check=False)
        local_head = head_result.stdout.strip() if head_result.returncode == 0 else ""
        status_result = _git(self.repo, "status", "--porcelain", check=False)
        dirty = bool(status_result.stdout) if status_result.returncode == 0 else True
        fetch = _git(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        )
        remote_result = _git(
            self.repo,
            "rev-parse",
            "--verify",
            f"{self.remote_name}/{self.remote_branch}",
            check=False,
        )
        remote_head = (
            remote_result.stdout.strip()
            if fetch.returncode == 0 and remote_result.returncode == 0
            else ""
        )
        branch_ok = branch == self.current_branch
        observation = []
        if not branch_ok:
            observation.append("wrong product branch or detached checkout")
        if dirty:
            observation.append("product checkout is dirty")
        if not local_head or not remote_head:
            observation.append("product generation could not be observed")
        if local_head and local_head != admitted_head:
            observation.append("local product HEAD advanced")
        if remote_head and remote_head != admitted_head:
            observation.append("remote product HEAD advanced")
        target_eligible = branch_ok and not dirty and bool(local_head and remote_head)
        stable = (
            target_eligible
            and local_head == admitted_head
            and remote_head == admitted_head
        )
        return {
            "stable": stable,
            "branch": branch,
            "local_head": local_head,
            "remote_head": remote_head,
            "dirty": dirty,
            "target_eligible": target_eligible,
            "reason": "; ".join(observation) if observation else "stable",
        }

    def _report_matches_execution_state(self, report: ExecutionReport) -> None:
        self._report_matches_execution_binding(report)
        if not isinstance(report.workspace_head, str) or not report.workspace_head:
            raise WorkflowBlockedError("execution checkpoint identity is missing")
        observed = self._execution_remote_head(report.execution_branch)
        if observed != report.workspace_head:
            raise WorkflowBlockedError(
                "published execution checkpoint does not match the execution branch"
            )

    def _report_matches_execution_binding(self, report: ExecutionReport) -> None:
        state = self._state
        expected = {
            "execution_id": state.get("execution_id"),
            "ticket_id": state.get("execution_ticket_id"),
            "control_head": state.get("execution_control_head"),
            "execution_branch": state.get("execution_branch"),
            "execution_path": state.get("execution_path"),
            "code_base_head": state.get("execution_base_head"),
        }
        actual = {
            "execution_id": report.execution_id,
            "ticket_id": report.ticket_id,
            "control_head": report.control_head,
            "execution_branch": report.execution_branch,
            "execution_path": report.execution_path,
            "code_base_head": report.code_base_head,
        }
        if any(not isinstance(value, str) or not value for value in expected.values()):
            raise WorkflowBlockedError(
                "persisted lifecycle execution binding is incomplete"
            )
        if actual != expected:
            raise WorkflowBlockedError(
                "recovered execution report does not match its durable execution "
                "binding"
            )

    def _prove_product_generation(self) -> None:
        state = self._state
        local_head = state.get("local_head")
        remote_head = state.get("remote_head")
        if not isinstance(local_head, str) or not isinstance(remote_head, str):
            raise WorkflowBlockedError("execution product binding is incomplete")
        self._assert_product_checkout_unchanged(self.current_branch, local_head)
        product_fetch = _git(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        )
        product_ref = f"{self.remote_name}/{self.remote_branch}"
        observed_product = _git(
            self.repo, "rev-parse", "--verify", product_ref, check=False
        )
        if (
            product_fetch.returncode
            or observed_product.returncode
            or (observed_product.stdout.strip() != remote_head)
        ):
            raise WorkflowBlockedError(
                "product checkout or remote changed before execution recovery"
            )

    def _execution_workspace_for_recovery(self) -> ExecutionWorkspace:
        state = self._state
        ticket_id = state.get("execution_ticket_id")
        base_head = state.get("execution_base_head")
        branch = state.get("execution_branch")
        path = state.get("execution_path")
        if not all(
            isinstance(value, str) and value
            for value in (ticket_id, base_head, branch, path)
        ):
            raise WorkflowBlockedError(
                "execution workspace recovery binding is incomplete"
            )
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        if branch != manager.branch or Path(path) != manager.path:
            raise WorkflowBlockedError("execution workspace binding is invalid")
        try:
            manager._validate_base(base_head)
            manager._validate_branch(base_head)
            registrations = manager._registrations()
            workspace = manager._validate_existing(
                registrations.get(manager.path.resolve()), base_head
            )
            manager.verify_submodules(workspace)
        except (ExecutionWorkspaceError, OSError) as error:
            raise WorkflowBlockedError(
                f"cannot validate execution workspace recovery: {error}"
            ) from error
        return workspace

    def _checkpoint_commit_is_exact(
        self, workspace: ExecutionWorkspace, commit: str, start_head: str
    ) -> bool:
        parent = _git(
            workspace.path, "rev-parse", "--verify", f"{commit}^", check=False
        )
        if parent.returncode or parent.stdout.strip() != start_head:
            return False
        parents = _git(
            workspace.path,
            "rev-list",
            "--parents",
            "-n",
            "1",
            commit,
            check=False,
        )
        if parents.returncode or len(parents.stdout.split()) != 2:
            return False
        lineage = _git(
            workspace.path,
            "rev-list",
            "--reverse",
            f"{start_head}..{commit}",
            check=False,
        )
        if lineage.returncode or lineage.stdout.splitlines() != [commit]:
            return False
        return not workspace.dirty

    def _recover_checkpoint_publication(self) -> None:
        state = self._state
        stage = state.get("execution_stage")
        pending = state.get("pending_execution_report")
        try:
            report = ExecutionReport.from_dict(pending)
        except ExecutionReportError as error:
            raise WorkflowBlockedError(
                "pending execution report is invalid; recovery is ambiguous"
            ) from error
        self._report_matches_execution_binding(report)
        workspace = self._execution_workspace_for_recovery()
        start_head = state.get("execution_start_head")
        if not isinstance(start_head, str) or not start_head:
            raise WorkflowBlockedError("execution start HEAD is unavailable")
        checkpoint_head: str
        if stage == "checkpointing":
            if workspace.head == start_head and not workspace.dirty:
                product = self._observe_product_generation(report.code_base_head)
                if self._is_zero_delta_stale_product_drift(
                    report.code_base_head, start_head, product
                ):
                    report = dataclasses.replace(report, workspace_head=start_head)
                    retry = self._zero_delta_retry_payload(report, product)
                    self._save_state(
                        "agent_running",
                        execution_stage="lifecycle",
                        pending_execution_report=report.as_dict(),
                        automatic_retry=retry,
                    )
                    return
            self._prove_product_generation()
            if workspace.head == start_head:
                try:
                    checkpoint = ExecutionWorkspaceManager(
                        self.repo, self.execution_worktree_root, report.ticket_id
                    ).checkpoint(
                        workspace,
                        report.execution_id,
                        title=self._ticket_store().by_id[report.ticket_id].title,
                        summary=(
                            report.result.claim.summary
                            if report.result.claim is not None
                            else report.result.reason
                        ),
                    )
                except (ExecutionWorkspaceError, OSError) as error:
                    raise WorkflowBlockedError(
                        f"cannot recover execution checkpoint: {error}"
                    ) from error
                checkpoint_head = checkpoint.after_head
            else:
                checkpoint_head = workspace.head
                if not self._checkpoint_commit_is_exact(
                    workspace, checkpoint_head, start_head
                ):
                    raise WorkflowBlockedError(
                        "execution worktree contains ambiguous checkpoint history"
                    )
            report = dataclasses.replace(report, workspace_head=checkpoint_head)
            workspace = self._execution_workspace_for_recovery()
            if workspace.head != checkpoint_head or workspace.dirty:
                raise WorkflowBlockedError(
                    "execution worktree changed or is dirty after checkpoint"
                )
            self._save_state(
                "agent_running",
                execution_stage="post-checkpoint",
                pending_execution_report=report.as_dict(),
            )
            stage = "post-checkpoint"
        product = (
            self._observe_product_generation(report.code_base_head)
            if report.workspace_head == report.code_base_head
            else None
        )
        if (
            product is not None
            and self._is_zero_delta_stale_product_drift(
                report.code_base_head, report.workspace_head, product
            )
        ):
            retry = self._zero_delta_retry_payload(report, product)
            self._save_state(
                "agent_running",
                execution_stage="lifecycle",
                pending_execution_report=report.as_dict(),
                automatic_retry=retry,
            )
            return
        self._prove_product_generation()
        if stage in {"post-checkpoint", "publishing"}:
            if report.workspace_head is None or workspace.head != report.workspace_head:
                raise WorkflowBlockedError(
                    "execution worktree HEAD does not match the proven checkpoint"
                )
            if workspace.dirty:
                raise WorkflowBlockedError(
                    "execution worktree is dirty after the proven checkpoint"
                )
            remote = self._execution_remote_head(report.execution_branch)
            expected_remote = state.get("execution_remote_head")
            if expected_remote is not None and not isinstance(expected_remote, str):
                raise WorkflowBlockedError("execution remote identity is invalid")
            if remote == expected_remote:
                self._publish_execution_branch(
                    workspace, report.workspace_head, expected_remote
                )
                remote = self._execution_remote_head(report.execution_branch)
            if remote != report.workspace_head:
                raise WorkflowBlockedError(
                    "execution remote does not match the proven checkpoint"
                )
            self._save_state(
                "agent_running",
                execution_stage="post-publication",
                pending_execution_report=report.as_dict(),
            )
            stage = "post-publication"
        if stage == "post-publication":
            if report.workspace_head is None or workspace.head != report.workspace_head:
                raise WorkflowBlockedError(
                    "execution worktree HEAD changed after publication"
                )
            if (
                self._execution_remote_head(report.execution_branch)
                != report.workspace_head
            ):
                raise WorkflowBlockedError(
                    "published execution checkpoint is no longer exact"
                )
            self._save_state(
                "agent_running",
                execution_stage="lifecycle",
                pending_execution_report=report.as_dict(),
            )

    def _recover_lifecycle_report(self) -> ExecutionReport:
        state = self._state
        ticket_id = state.get("execution_ticket_id")
        execution_id = state.get("execution_id")
        if not isinstance(ticket_id, str) or not isinstance(execution_id, str):
            raise WorkflowBlockedError("lifecycle recovery identity is incomplete")
        pending = state.get("pending_execution_report")
        if isinstance(pending, dict):
            try:
                report = ExecutionReport.from_dict(pending)
            except ExecutionReportError as error:
                raise WorkflowBlockedError(
                    f"pending lifecycle report is invalid: {error}"
                ) from error
        else:
            self._validate_control_worktree()
            try:
                report = ExecutionReportStore(self.control_worktree).read(
                    ticket_id, execution_id
                )
            except ExecutionReportError as error:
                raise WorkflowBlockedError(
                    "lifecycle report evidence is unavailable; recovery is ambiguous"
                ) from error
        local_head = state.get("local_head")
        remote_head = state.get("remote_head")
        if not isinstance(local_head, str) or not isinstance(remote_head, str):
            raise WorkflowBlockedError("lifecycle product binding is incomplete")
        retry = self._state.get("automatic_retry")
        if (
            isinstance(retry, dict)
            and retry.get("status") == "pending"
            and report.workspace_head == report.code_base_head
        ):
            self._report_matches_execution_binding(report)
            if self._execution_remote_head(report.execution_branch) is not None:
                raise WorkflowBlockedError(
                    "zero-delta retry execution branch is already published"
                )
        else:
            self._prove_product_generation()
            self._report_matches_execution_state(report)
        return report

    def _control_report_at(
        self, commit: str, report: ExecutionReport
    ) -> ExecutionReport | None:
        path = f"executions/{report.ticket_id}/{report.execution_id}.json"
        shown = _git(self.control_worktree, "show", f"{commit}:{path}", check=False)
        if shown.returncode:
            return None
        try:
            recovered = ExecutionReport.from_dict(json.loads(shown.stdout))
        except (ExecutionReportError, json.JSONDecodeError):
            return None
        return recovered if recovered.as_dict() == report.as_dict() else None

    def _lifecycle_commit_is_exact(
        self,
        commit: str,
        report: ExecutionReport,
        *,
        apply_conclusion: bool = True,
    ) -> bool:
        parent = _git(self.control_worktree, "rev-parse", f"{commit}^", check=False)
        if parent.returncode:
            return False
        parent_head = parent.stdout.strip()
        lineage = _git(
            self.control_worktree,
            "rev-list",
            "--reverse",
            f"{parent_head}..{commit}",
            check=False,
        )
        if lineage.returncode or lineage.stdout.splitlines() != [commit]:
            return False
        ancestor = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            report.control_head,
            parent_head,
            check=False,
        )
        if ancestor.returncode:
            return False
        if self._control_report_at(commit, report) is None:
            return False
        names = _git(
            self.control_worktree,
            "diff-tree",
            "--no-commit-id",
            "--name-status",
            "--no-renames",
            "-r",
            commit,
            check=False,
        )
        report_path = f"executions/{report.ticket_id}/{report.execution_id}.json"
        expected = {f"A\t{report_path}"}
        if apply_conclusion and report.result.conclusion == "completed":
            expected.update(
                {
                    f"D\t{self.todo_path}/{report.ticket_id}.md",
                    f"A\t{self.review_path}/{report.ticket_id}.md",
                }
            )
        if names.returncode != 0 or set(names.stdout.splitlines()) != expected:
            return False
        report_path = f"executions/{report.ticket_id}/{report.execution_id}.json"
        report_bytes = _git(
            self.control_worktree, "show", f"{commit}:{report_path}", check=False
        )
        if report_bytes.returncode or report_bytes.stdout != report.to_json():
            return False
        if apply_conclusion and report.result.conclusion == "completed":
            parent_blob = _git(
                self.control_worktree,
                "rev-parse",
                f"{parent_head}:{self.todo_path}/{report.ticket_id}.md",
                check=False,
            )
            review_blob = _git(
                self.control_worktree,
                "rev-parse",
                f"{commit}:{self.review_path}/{report.ticket_id}.md",
                check=False,
            )
            if (
                parent_blob.returncode
                or review_blob.returncode
                or parent_blob.stdout.strip() != review_blob.stdout.strip()
            ):
                return False
        return True

    def _prove_unpublished_lifecycle_lineage(
        self,
        commit: str,
        remote_head: str,
        report: ExecutionReport,
        *,
        apply_conclusion: bool = True,
    ) -> None:
        parent = _git(self.control_worktree, "rev-parse", f"{commit}^", check=False)
        if parent.returncode:
            raise WorkflowBlockedError("unpublished lifecycle parent is unavailable")
        parent_head = parent.stdout.strip()
        parent_remote = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            parent_head,
            remote_head,
            check=False,
        )
        if parent_remote.returncode:
            raise WorkflowBlockedError(
                "unpublished lifecycle parent is not on the fresh remote lineage"
            )
        lineage = _git(
            self.control_worktree,
            "rev-list",
            "--reverse",
            f"{parent_head}..{commit}",
            check=False,
        )
        if lineage.returncode or lineage.stdout.splitlines() != [commit]:
            raise WorkflowBlockedError(
                "unpublished control history contains more than one commit"
            )
        if not self._lifecycle_commit_is_exact(
            commit, report, apply_conclusion=apply_conclusion
        ):
            raise WorkflowBlockedError(
                "unpublished control commit is not the exact lifecycle mutation"
            )

    def _remote_lifecycle_commit(
        self,
        remote_head: str,
        report: ExecutionReport,
        *,
        apply_conclusion: bool = True,
    ) -> str | None:
        path = f"executions/{report.ticket_id}/{report.execution_id}.json"
        history = _git(
            self.control_worktree,
            "log",
            "--format=%H",
            remote_head,
            "--",
            path,
            check=False,
        )
        if history.returncode:
            return None
        for commit in history.stdout.splitlines():
            if self._lifecycle_commit_is_exact(
                commit, report, apply_conclusion=apply_conclusion
            ):
                return commit
        return None

    def _prove_bound_ticket_descendant(
        self,
        candidate: str,
        control_head: str,
        ticket_id: str,
        selected_body: str,
    ) -> None:
        ancestor = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            control_head,
            candidate,
            check=False,
        )
        if ancestor.returncode:
            raise WorkflowBlockedError(
                "control history diverged from the execution admission generation"
            )
        original_path = f"{self.todo_path}/{ticket_id}.md"
        original = _git(
            self.control_worktree,
            "show",
            f"{control_head}:{original_path}",
            check=False,
        )
        current = _git(
            self.control_worktree, "show", f"{candidate}:{original_path}", check=False
        )
        if (
            original.returncode
            or current.returncode
            or original.stdout != current.stdout
        ):
            raise WorkflowBlockedError(
                "control descendant changed the active ticket; lifecycle replay refused"
            )
        original_lines = original.stdout.splitlines(keepends=True)
        delimiters = [
            index
            for index, line in enumerate(original_lines)
            if line.rstrip("\r\n") == "---"
        ]
        if len(delimiters) < 2:
            raise WorkflowBlockedError("active ticket admission evidence is invalid")
        original_body = "".join(original_lines[delimiters[1] + 1 :])
        if selected_body != original_body:
            raise WorkflowBlockedError(
                "persisted active ticket body does not match admission"
            )

    def _prove_control_descendant(
        self, candidate: str, report: ExecutionReport
    ) -> None:
        self._prove_bound_ticket_descendant(
            candidate,
            report.control_head,
            report.ticket_id,
            str(self._state.get("selected_ticket_body", "")),
        )

    def _apply_lifecycle_once(
        self, report: ExecutionReport, *, apply_conclusion: bool = True
    ) -> str:
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(report.ticket_id)
        if ticket is None or ticket.state != "todo":
            raise WorkflowBlockedError(
                "execution ticket is not in its expected todo state"
            )
        if apply_conclusion and report.result.conclusion == "completed":
            boundary = tuple(
                item
                for item in ticket_store.tickets
                if item.state in {"review", "accepted"} and item.id != ticket.id
            )
            if boundary:
                raise WorkflowBlockedError(
                    "review/accepted serial boundary conflicts with lifecycle replay"
                )
        report_store = ExecutionReportStore(self.control_worktree)
        report_store.write(report)
        if apply_conclusion and report.result.conclusion == "completed":
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
            lifecycle_message(
                report.ticket_id,
                ticket.title,
                report.execution_id,
                report.result.conclusion,
                (
                    report.result.claim.summary
                    if report.result.claim is not None
                    else report.result.reason
                ),
            ),
            check=False,
        )
        if committed.returncode:
            raise DevlegateError(
                committed.stderr.strip() or "cannot create control-plane commit"
            )
        return _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()

    def _apply_execution_lifecycle(
        self,
        report: ExecutionReport,
        *,
        allow_unpublished: bool = False,
        apply_conclusion: bool = True,
    ) -> str:
        if allow_unpublished:
            self._report_matches_execution_binding(report)
            if not isinstance(report.workspace_head, str) or not report.workspace_head:
                raise WorkflowBlockedError("execution checkpoint identity is missing")
        else:
            self._report_matches_execution_state(report)
        self._validate_control_worktree()
        for _attempt in range(3):
            status = _git(self.control_worktree, "status", "--porcelain").stdout
            if status:
                raise WorkflowBlockedError("control working tree is dirty")
            fetched = _git(
                self.control_worktree,
                "fetch",
                "--prune",
                self.remote_name,
                self.control_branch,
                check=False,
            )
            if fetched.returncode:
                raise WorkflowBlockedError(
                    "cannot freshly observe control remote before lifecycle: "
                    f"{fetched.stderr.strip() or 'unknown git error'}"
                )
            remote_ref = f"{self.remote_name}/{self.control_branch}"
            remote_result = _git(
                self.control_worktree, "rev-parse", "--verify", remote_ref, check=False
            )
            if remote_result.returncode:
                raise WorkflowBlockedError("control remote branch is unavailable")
            remote_head = remote_result.stdout.strip()
            local_head = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
            existing = self._remote_lifecycle_commit(
                remote_head, report, apply_conclusion=apply_conclusion
            )
            if existing is not None:
                parent = _git(
                    self.control_worktree, "rev-parse", f"{existing}^", check=False
                )
                if parent.returncode:
                    raise WorkflowBlockedError(
                        "published lifecycle parent is unavailable"
                    )
                lifecycle_parent = parent.stdout.strip()
                self._prove_control_descendant(lifecycle_parent, report)
                if local_head != remote_head:
                    relation = _git(
                        self.control_worktree,
                        "merge-base",
                        "--is-ancestor",
                        local_head,
                        remote_head,
                        check=False,
                    )
                    if relation.returncode:
                        raise WorkflowBlockedError(
                            "local control history diverges from published lifecycle"
                        )
                    else:
                        merge = _git(
                            self.control_worktree,
                            "merge",
                            "--ff-only",
                            remote_ref,
                            check=False,
                        )
                        if merge.returncode:
                            raise WorkflowBlockedError(
                                "cannot fast-forward control worktree"
                            )
                return _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
            if local_head != report.control_head:
                self._prove_unpublished_lifecycle_lineage(
                    local_head,
                    remote_head,
                    report,
                    apply_conclusion=apply_conclusion,
                )
                self._prove_control_descendant(remote_head, report)
                reset = _git(
                    self.control_worktree, "reset", "--hard", remote_head, check=False
                )
                if reset.returncode:
                    raise WorkflowBlockedError(
                        "cannot replay lifecycle on control descendant"
                    )
            elif remote_head != report.control_head:
                self._prove_control_descendant(remote_head, report)
                _log(
                    f"control advanced during execution: {report.control_head[:12]} "
                    f"-> {remote_head[:12]}; replaying compatible lifecycle"
                )
                merge = _git(
                    self.control_worktree, "merge", "--ff-only", remote_ref, check=False
                )
                if merge.returncode:
                    raise WorkflowBlockedError("cannot fast-forward control descendant")
            else:
                self._prove_control_descendant(report.control_head, report)
            lifecycle_head = self._apply_lifecycle_once(
                report, apply_conclusion=apply_conclusion
            )
            pushed = _git(
                self.control_worktree,
                "push",
                self.remote_name,
                f"HEAD:refs/heads/{self.control_branch}",
                check=False,
            )
            if pushed.returncode == 0:
                return lifecycle_head
            _log(
                f"lifecycle push raced with control update; retrying ({_attempt + 1}/3)"
            )
        raise WorkflowBlockedError(
            "control lifecycle reconciliation retry bound exceeded"
        )

    def _persist_historical_execution_report(self, report: ExecutionReport) -> str:
        return self._apply_execution_lifecycle(
            report, allow_unpublished=True, apply_conclusion=False
        )

    def serve(
        self,
        stop_event: threading.Event,
        lock_handle: object | None = None,
        *,
        once: bool = False,
    ) -> int:
        """Run the service until the host requests a stop."""
        owned_here = self._host_owner_thread_id is None
        if owned_here:
            self.begin_hosted_owner()
        try:
            return self._run_polling(
                once=once, stop_event=stop_event, lock_handle=lock_handle
            )
        finally:
            if owned_here:
                self.end_hosted_owner()

    def _has_recoverable_execution_stage(self) -> bool:
        return self._state.get("phase") == "agent_running" and self._state.get(
            "execution_stage"
        ) in {
            "checkpointing",
            "post-checkpoint",
            "publishing",
            "post-publication",
            "lifecycle",
        }

    def _has_blocked_execution_stage(self) -> bool:
        """Return whether recovery needs operator-visible fail-closed blocking."""
        return self._state.get("phase") == "agent_running" and self._state.get(
            "execution_stage"
        ) in {"worker-launch", "worker-running"}

    def _has_pending_reconciliation(self) -> bool:
        reconciliation = self._state.get("reconciliation")
        return isinstance(reconciliation, dict) and reconciliation.get("status") == (
            "pending"
        )

    def _has_pending_accepted_integration(self) -> bool:
        return isinstance(self._state.get("accepted_integration"), dict)

    def _dispatch_operator_command(
        self, command: OperatorCommand, stop_event: threading.Event | None
    ) -> int:
        if command.method == "retry":
            return self._retry_owned(command.ticket_id, stop_event)
        if command.method == "drop":
            return 0
        if command.method == "reconcile-resume":
            return self._reconcile_resume_owned(command.ticket_id)
        if command.method == "reconcile-update-base":
            if command.onto is None:
                raise DevlegateError(
                    "reconcile-update-base requires a product base target"
                )
            return self._reconcile_update_base_owned(command.ticket_id, command.onto)
        if command.method == "reconcile-control":
            if command.onto is None:
                raise DevlegateError(
                    "reconcile-control requires both control revisions"
                )
            return self._reconcile_control_owned(command.ticket_id, command.onto)
        raise DevlegateError(f"unsupported operator command: {command.method}")

    def _run_polling(
        self,
        *,
        once: bool,
        stop_event: threading.Event | None = None,
        lock_handle: object | None = None,
    ) -> int:
        self._foreground_abort_requested = False
        self._service_shutdown.clear()
        if stop_event is not None and not once:
            threading.Thread(
                target=self._wake_when_stopped,
                args=(stop_event,),
                name="devlegate-stop-wake",
                daemon=True,
            ).start()
        authority = (
            nullcontext(lock_handle) if lock_handle is not None else self._lock()
        )
        with self._stop_context(stop_event), authority:
            self._publish_service_snapshot(lifecycle="polling", worker_running=False)
            if not once and not (stop_event is not None and stop_event.is_set()):
                with self._snapshot_lock:
                    published = self._published_status_snapshot
                if published is None:
                    try:
                        self.status_view()
                    except DevlegateError:
                        pass
            while True:
                if self._lifecycle_exit_ready():
                    self._service_shutdown.set()
                    self._reject_pending_operator_command_on_shutdown()
                    self._publish_service_snapshot(
                        lifecycle=(
                            "restarting"
                            if self.lifecycle_intent() == "restart"
                            else "stopping"
                        ),
                        worker_running=False,
                    )
                    return 0
                if (
                    stop_event is not None
                    and stop_event.is_set()
                    and self._state.get("phase") != "merge_pending"
                ):
                    self._service_shutdown.set()
                    self._reject_pending_operator_command_on_shutdown()
                    self._publish_service_snapshot(lifecycle="ready")
                    return 0
                operator_command = None
                scheduler_active = False
                if self._startup_admission_window:
                    self._startup_admission_window = False
                    self._service_wake.wait(OPERATOR_ADMISSION_TIMEOUT)
                    self._service_wake.clear()
                    if self._lifecycle_exit_ready():
                        continue
                operator_command, scheduler_active = self._claim_owner_work(True)
                workflow_blocked = False
                self._iteration_diagnostic = None
                try:
                    if operator_command is not None:
                        self._admit_operator_command(operator_command)
                        status = self._dispatch_operator_command(
                            operator_command, stop_event
                        )
                    else:
                        status = self.run_iteration()
                except ShutdownInterrupted:
                    self._service_shutdown.set()
                    self._publish_service_snapshot(lifecycle="ready")
                    return 0
                except WorkflowBlockedError as error:
                    if operator_command is not None:
                        self._iteration_diagnostic = (
                            f"{operator_command.method} {operator_command.ticket_id} "
                            f"rejected: {error}"
                        )
                        status = 1
                    else:
                        workflow_blocked = True
                        message = str(error)
                        self._publish_service_snapshot(
                            lifecycle="blocked",
                            worker_running=False,
                            blocked_reason=message,
                        )
                        fingerprint = hashlib.sha256(message.encode()).hexdigest()
                        if self._workflow_blocker_fingerprint != fingerprint:
                            _log(f"workflow blocked: {message}")
                        self._workflow_blocker_fingerprint = fingerprint
                        status = 1
                except DevlegateError as error:
                    if operator_command is None:
                        if not once and (
                            self._has_pending_reconciliation()
                            or self._has_pending_accepted_integration()
                        ):
                            workflow_blocked = True
                            message = str(error)
                            self._publish_service_snapshot(
                                lifecycle="blocked",
                                worker_running=False,
                                blocked_reason=message,
                            )
                            fingerprint = hashlib.sha256(message.encode()).hexdigest()
                            if self._workflow_blocker_fingerprint != fingerprint:
                                _log(f"workflow blocked: {message}")
                            self._workflow_blocker_fingerprint = fingerprint
                            status = 1
                        elif not once and (
                            self._has_recoverable_execution_stage()
                            or self._has_blocked_execution_stage()
                        ):
                            if self._has_blocked_execution_stage():
                                workflow_blocked = True
                                self._publish_service_snapshot(
                                    lifecycle="blocked",
                                    worker_running=False,
                                    blocked_reason=str(error),
                                )
                            self._iteration_diagnostic = f"execution failed: {error}"
                            status = 1
                        else:
                            raise
                    else:
                        self._iteration_diagnostic = (
                            f"{operator_command.method} {operator_command.ticket_id} "
                            f"rejected: {error}"
                        )
                        status = 1
                except (OSError, subprocess.CalledProcessError) as error:
                    detail = getattr(error, "stderr", None) or str(error)
                    self._iteration_diagnostic = f"execution failed: {detail.strip()}"
                    status = 1
                else:
                    if (
                        self._workflow_blocker_fingerprint is not None
                        and self._workflow_validation_succeeded
                    ):
                        _log("workflow is valid again")
                        self._workflow_blocker_fingerprint = None
                finally:
                    if operator_command is not None:
                        self._release_operator_command()
                    elif scheduler_active:
                        self._release_scheduler_iteration()
                if operator_command is not None or scheduler_active:
                    try:
                        self.status_view()
                    except DevlegateError:
                        pass
                if status and not workflow_blocked:
                    diagnostic = self._iteration_diagnostic
                    if diagnostic is None:
                        diagnostic = f"execution failed: run returned status {status}"
                    fingerprint = hashlib.sha256(diagnostic.encode()).hexdigest()
                    if self._foreground_diagnostic_fingerprint != fingerprint:
                        _log(diagnostic)
                    self._foreground_diagnostic_fingerprint = fingerprint
                elif not workflow_blocked:
                    self._foreground_diagnostic_fingerprint = None
                if self._foreground_abort_requested:
                    return 130
                if once:
                    if not self.service_snapshot().worker_running:
                        self._publish_service_snapshot(
                            lifecycle="blocked" if workflow_blocked else "ready"
                        )
                    return status
                if stop_event is not None:
                    if self._wait_for_service_event(
                        stop_event, int(self.poll_interval)
                    ):
                        self._service_shutdown.set()
                        self._reject_pending_operator_command_on_shutdown()
                        self._publish_service_snapshot(lifecycle="ready")
                        return 0
                else:
                    self._service_wake.wait(int(self.poll_interval))
                    self._service_wake.clear()

    def _wake_when_stopped(self, stop_event: threading.Event) -> None:
        stop_event.wait()
        self.wake()

    def _wait_for_service_event(
        self, stop_event: threading.Event, timeout: int
    ) -> bool:
        """Wait for shutdown or an operator command without polling busy-work."""
        self._service_wake.clear()
        if self._lifecycle_exit_ready():
            return True
        if stop_event.is_set() or self._operator_command_pending():
            return stop_event.is_set()
        # Recheck after clearing the wake event so a command submitted during
        # the clear does not get stranded until the polling timeout.
        if self._operator_command_pending():
            return False
        self._service_wake.wait(timeout)
        return stop_event.is_set()

    def _status_snapshot_hook(self, _point: str) -> None:
        """Testing hook for deterministic snapshot-race simulations."""

    def _status_state_observation(self) -> tuple[dict[str, object], str]:
        self._assert_repository_owner()
        try:
            state, fingerprint = self._runtime_store.observe()
        except RuntimeStoreError as error:
            raise DevlegateError(str(error)) from error
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise DevlegateError(f"invalid state file: {self._state_file}")
        self._validate_state_invariant(state)
        return state, fingerprint

    def _git_observation(self, repo: Path, remote_branch: str) -> GitObservation:
        branch_result = self._git_runtime(
            repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        detached = branch_result.returncode != 0
        branch = branch_result.stdout.strip() if not detached else None
        local_head = self._git_runtime(repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{remote_branch}"
        remote_result = self._git_runtime(
            repo, "rev-parse", "--verify", remote_ref, check=False
        )
        status = self._git_runtime(repo, "status", "--porcelain").stdout
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
                    if not (
                        metadata.get("interrupted") is True
                        or metadata.get("report_unavailable") is True
                    ):
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
                    in {
                        "operator_abort",
                        "service_shutdown",
                        "process_loss",
                    }
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

    def _admission_barrier(
        self,
        state: dict[str, object],
        ticket_store: TicketStore,
        dirty: bool,
        branch_ok: bool,
        code: GitObservation,
    ) -> AdmissionBarrier | None:
        if not branch_ok:
            reason = "control worktree is unavailable or on the wrong branch"
            if code.detached:
                reason = "detached HEAD is not supported"
            return AdmissionBarrier("blocked", "repository", reason)
        reconciliation = state.get("reconciliation")
        if isinstance(reconciliation, dict) and reconciliation.get("status") in {
            "pending",
            "resolving",
        }:
            return AdmissionBarrier(
                "blocked",
                "reconciliation",
                (
                    "reconciliation recovery in progress: "
                    f"ticket {reconciliation['ticket_id']}; "
                    f"resolution {reconciliation.get('resolution', 'unknown')}"
                    if reconciliation.get("status") == "resolving"
                    else "reconciliation required: "
                    f"ticket {reconciliation['ticket_id']}; "
                    f"original base {reconciliation['original_base']}; "
                    f"observed product {reconciliation['observed_product']}; "
                    f"worker checkpoint {reconciliation['worker_checkpoint']}"
                ),
                reconciliation.get("ticket_id")
                if isinstance(reconciliation.get("ticket_id"), str)
                else None,
            )
        accepted_integration = state.get("accepted_integration")
        if isinstance(accepted_integration, dict):
            ticket_id = accepted_integration.get("ticket_id")
            ticket = (
                ticket_store.by_id.get(ticket_id)
                if isinstance(ticket_id, str)
                else None
            )
            if ticket is None:
                return AdmissionBarrier(
                    "blocked",
                    "accepted-integration",
                    "accepted integration ticket is no longer observable",
                )
            return AdmissionBarrier(
                "none",
                "accepted-integration",
                f"accepted integration recovery in progress: {ticket.id}",
                ticket.id,
            )
        if dirty:
            return AdmissionBarrier(
                "blocked", "repository", "code or control working tree is dirty"
            )
        boundary = tuple(
            ticket
            for ticket in ticket_store.tickets
            if ticket.state in {"review", "accepted"}
        )
        if len(boundary) > 1:
            return AdmissionBarrier(
                "blocked",
                "scheduler-barrier",
                "multiple tickets occupy the review/accepted serial boundary",
            )
        return None

    @staticmethod
    def _serial_admission_barrier(
        ticket_store: TicketStore,
    ) -> AdmissionBarrier | None:
        boundary = tuple(
            ticket
            for ticket in ticket_store.tickets
            if ticket.state in {"review", "accepted"}
        )
        if not boundary:
            return None
        ticket = boundary[0]
        if ticket.state == "review":
            return AdmissionBarrier(
                "none", "review", f"waiting for review: {ticket.id}", ticket.id
            )
        return AdmissionBarrier(
            "integrate",
            "accepted",
            f"accepted, ready for integration: {ticket.id}",
            ticket.id,
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
        pending_integration = state.get("accepted_integration")
        lifecycle_integration = (
            (pending_integration["ticket_id"], "in-progress")
            if isinstance(pending_integration, dict)
            and isinstance(pending_integration.get("ticket_id"), str)
            else None
        )
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
                lifecycle_integration=lifecycle_integration,
            )
        counts = tuple(
            (
                state_name,
                sum(ticket.state == state_name for ticket in ticket_store.tickets),
            )
            for state_name in ("backlog", "todo", "review", "accepted", "done")
        )
        runnable = tuple((ticket.id, ticket.title) for ticket in ticket_store.runnable)
        dependency_blocked = tuple(
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
        integration_ticket_id = (
            lifecycle_integration[0] if lifecycle_integration is not None else None
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
        dirty = not code.working_tree_clean or not (
            control and control.working_tree_clean
        )
        branch_ok = (
            not code.detached
            and code.branch == self.remote_branch
            and control is not None
            and not control.detached
            and control.branch == self.control_branch
        )
        plan = self._make_execution_plan(
            state,
            ticket_store,
            dirty,
            branch_ok,
            observation=git_observation,
        )
        admission_reason = (
            None if plan.action == "run-worker" and not plan.bound else plan.reason
        )
        failures = state.get("failed_executions", {})
        failed_executions = self._evaluate_failed_executions(
            failures, ticket_store, code, control, admission_reason
        )
        live_evidence = self.live_execution_evidence()
        if isinstance(live_evidence, dict):
            active_ticket_id = live_evidence.get("ticket_id")
            failed_executions = tuple(
                failure
                for failure in failed_executions
                if failure.ticket_id != active_ticket_id
            )
        next_ticket = (
            (
                ticket_store.by_id[plan.ticket_id].id,
                ticket_store.by_id[plan.ticket_id].title,
            )
            if plan.ticket_id is not None and plan.ticket_id in ticket_store.by_id
            else None
        )
        bound_ticket_id = (
            selected_id if bound_phase and isinstance(selected_id, str) else None
        )
        bound_ticket_title = (
            ticket_store.by_id[bound_ticket_id].title
            if bound_ticket_id is not None and bound_ticket_id in ticket_store.by_id
            else None
        )
        eligible = runnable if plan.action == "run-worker" and not plan.bound else ()
        eligible_ids = {ticket_id for ticket_id, _title in eligible}
        pre_barrier = self._admission_barrier(
            state, ticket_store, dirty, branch_ok, code
        )
        if pre_barrier is not None:
            global_barrier = pre_barrier
        elif bound_ticket_id is not None:
            global_barrier = AdmissionBarrier(
                "blocked",
                "active-execution",
                "active execution is in progress",
                bound_ticket_id,
            )
        elif bound_phase:
            global_barrier = AdmissionBarrier(
                "blocked",
                "scheduler-barrier",
                "persisted bound execution is not observable",
            )
        else:
            global_barrier = self._serial_admission_barrier(ticket_store)
        global_block_reason = (
            BlockedReason(global_barrier.kind, ticket_id=global_barrier.ticket_id)
            if global_barrier is not None
            else None
        )
        dependency_entries = {
            ticket_id: (title, blockers)
            for ticket_id, title, blockers in dependency_blocked
        }
        blocked_entries = []
        for ticket in ticket_store.tickets:
            if ticket.state != "todo" or ticket.id == bound_ticket_id:
                continue
            if ticket.id in dependency_entries:
                title, blockers = dependency_entries[ticket.id]
                blocked_reason = BlockedReason(
                    "dependencies",
                    tickets=tuple(
                        (
                            dependency,
                            (
                                "integration-in-progress"
                                if dependency == integration_ticket_id
                                else state
                            ),
                        )
                        for dependency, state in blockers
                    ),
                )
                if (
                    integration_ticket_id is not None
                    and len(blockers) == 1
                    and blockers[0][0] == integration_ticket_id
                ):
                    blocked_reason = BlockedReason(
                        "integration-in-progress",
                        ticket_id=integration_ticket_id,
                    )
                blocked_entries.append((ticket.id, title, blocked_reason))
            elif ticket.id not in eligible_ids:
                assert global_block_reason is not None
                blocked_entries.append((ticket.id, ticket.title, global_block_reason))
        return StatusSnapshot(
            phase=str(state["phase"]),
            bound_ticket_id=bound_ticket_id,
            persisted_body_present=bound_phase
            and isinstance(state.get("selected_ticket_body"), str),
            code=code,
            control=control,
            counts=counts,
            runnable=runnable,
            blocked=tuple(blocked_entries),
            review=review,
            accepted=accepted,
            next_ticket=next_ticket,
            plan=plan,
            failed_executions=failed_executions,
            reconciliation=(
                dict(state["reconciliation"])
                if isinstance(state.get("reconciliation"), dict)
                else None
            ),
            execution_stage=(
                state.get("execution_stage")
                if bound_ticket_id is not None
                and isinstance(state.get("execution_stage"), str)
                else None
            ),
            execution_id=(
                state.get("execution_id")
                if bound_ticket_id is not None
                and isinstance(state.get("execution_id"), str)
                else None
            ),
            bound_ticket_title=bound_ticket_title,
            lifecycle_integration=lifecycle_integration,
            eligible=eligible,
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
        identity = {"code": code, "control": control}
        barrier = self._admission_barrier(
            state,
            ticket_store,
            dirty,
            branch_ok,
            code,
        )
        if barrier is not None:
            ticket = (
                ticket_store.by_id.get(barrier.ticket_id)
                if barrier.ticket_id is not None
                else None
            )
            return ExecutionPlan(
                barrier.action,
                barrier.reason,
                ticket.id if ticket is not None else None,
                ticket.title if ticket is not None else None,
                ticket.state if ticket is not None else None,
                False,
                **identity,
            )
        bound_phase = str(state["phase"]) in {
            "agent_pending",
            "agent_running",
        }
        if bound_phase:
            if state["phase"] == "agent_running":
                stage = state.get("execution_stage")
                if stage == "worker-running":
                    identity_value = _worker_identity_from_value(
                        state.get("worker_identity"), state.get("execution_id")
                    )
                    if identity_value is not None:
                        observation = self._workers.observe(identity_value)
                        if observation == "matching-live":
                            reason = (
                                "persisted worker ownership is matching-live; "
                                "startup reconciliation is blocked"
                            )
                        elif observation == "indeterminate":
                            reason = (
                                "persisted worker ownership is indeterminate; "
                                "startup reconciliation is blocked"
                            )
                        else:
                            reason = (
                                "persisted worker ownership is absent; mutation-owner "
                                "reconciliation is required"
                            )
                        return ExecutionPlan(
                            "blocked",
                            reason,
                            **identity,
                        )
                elif stage == "post-worker":
                    return ExecutionPlan(
                        "blocked",
                        "persisted post-worker state awaits mutation-owner "
                        "reconciliation",
                        **identity,
                    )
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
        barrier = self._serial_admission_barrier(ticket_store)
        if barrier is not None:
            ticket = (
                ticket_store.by_id.get(barrier.ticket_id)
                if barrier.ticket_id is not None
                else None
            )
            return ExecutionPlan(
                barrier.action,
                barrier.reason,
                ticket.id if ticket is not None else None,
                ticket.title if ticket is not None else None,
                ticket.state if ticket is not None else None,
                False,
                **identity,
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
            with self._snapshot_lock:
                snapshot = self._published_status_snapshot
            if snapshot is None:
                raise DevlegateError(
                    "project state changed while status snapshot was being collected"
                )
            return snapshot
        self._publish_status_snapshot(snapshot)
        return snapshot

    def _retry_candidates(self) -> tuple[tuple[str, str, str], ...]:
        for _attempt in range(3):
            try:
                snapshot = self._collect_status_attempt(allow_workflow_blocked=False)
                break
            except SnapshotChanged:
                continue
        else:
            raise DevlegateError(
                "project state changed while retry candidates were being collected"
            )
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

    def _retry_interactive_candidate(
        self,
        candidate: RetryCandidate,
        stop_event: threading.Event | None = None,
    ) -> int:
        if stop_event is not None and stop_event.is_set():
            return 0
        with self._lock():
            if candidate.kind == "interrupted":
                self._recover_interrupted_execution(candidate.ticket_id)
            return self._retry_locked(candidate.ticket_id, stop_event)

    def _assert_previous_worker_is_safe(self) -> None:
        identity = _worker_identity_from_value(
            self._state.get("worker_identity"), self._state.get("execution_id")
        )
        if identity is None:
            raise DevlegateError(
                "previous worker ownership cannot be proven absent; duplicate "
                "launch refused"
            )
        observation = self._workers.observe(identity)
        if observation == "absent":
            return
        if observation == "matching-live":
            raise DevlegateError(
                "previous execution worker is still alive; duplicate launch refused"
            )
        raise DevlegateError(
            "previous worker ownership cannot be proven absent; duplicate launch "
            "refused"
        )

    def retry(
        self,
        ticket_id: str | None = None,
        stop_event: threading.Event | None = None,
    ) -> int:
        """Authorize exactly one fresh execution after current-state validation."""
        if stop_event is not None and stop_event.is_set():
            return 0
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
            return self._retry_interactive_candidate(candidates[index - 1], stop_event)
        with self._lock():
            return self._retry_owned(ticket_id, stop_event)

    def _retry_owned(
        self, ticket_id: str, stop_event: threading.Event | None = None
    ) -> int:
        """Execute retry semantics under an authority lock held by the caller."""
        if stop_event is not None and stop_event.is_set():
            return 0
        if self._state.get("phase") == "agent_running":
            if self._state.get("execution_stage") in {
                "worker-launch",
                "worker-running",
                "post-worker",
            }:
                self._reconcile_stranded_execution()
            else:
                self._recover_interrupted_execution(ticket_id)
        return self._retry_locked(ticket_id, stop_event)

    def _retry_locked(
        self, ticket_id: str, stop_event: threading.Event | None = None
    ) -> int:
        candidates = self._retry_candidates()
        candidate_ids = {candidate[0] for candidate in candidates}
        if ticket_id not in candidate_ids:
            raise DevlegateError(f"ticket {ticket_id} is not currently retryable")
        intent = IterationIntent(retry_ticket_id=ticket_id)
        if stop_event is None:
            return self.run_iteration(intent)
        with self._stop_context(stop_event):
            return self.run_iteration(intent)

    def reconcile_update_base(self, ticket_id: str, onto: str) -> int:
        """Transplant one preserved worker checkpoint onto an explicit base."""
        return self._reconcile_update_base_owned(ticket_id, onto, _take_lock=True)

    def reconcile_resume(self, ticket_id: str) -> int:
        """Recover retained execution progress without changing its product base."""
        return self._reconcile_resume_owned(ticket_id, _take_lock=True)

    def reconcile_control(self, from_head: str, to_head: str) -> int:
        """Adopt an explicitly authorized divergent control history."""
        return self._reconcile_control_owned(from_head, to_head, _take_lock=True)

    def _reconcile_control_owned(
        self, from_head: str, to_head: str, *, _take_lock: bool = False
    ) -> int:
        """Replace only the local control lineage after repeated exact proof."""
        authority = self._lock() if _take_lock else nullcontext()
        with authority:
            self._validate_control_reconcile_admission(from_head, to_head)
            # Re-observe immediately before preserving evidence or changing the branch.
            self._validate_control_reconcile_admission(from_head, to_head)
            evidence = f"refs/devlegate/recovery/control/{from_head}-{to_head}"
            existing = _git(self.repo, "rev-parse", "--verify", evidence, check=False)
            if existing.returncode == 0:
                if existing.stdout.strip() != from_head:
                    raise DevlegateError(
                        "control recovery evidence ref already points to another commit"
                    )
            else:
                created = _git(
                    self.repo,
                    "update-ref",
                    evidence,
                    from_head,
                    "0" * 40,
                    check=False,
                )
                if created.returncode:
                    raise DevlegateError(
                        "control recovery evidence ref could not be created"
                    )

            moved = _git(self.control_worktree, "reset", "--hard", to_head, check=False)
            if moved.returncode:
                raise DevlegateError(
                    f"control lineage adoption failed: {moved.stderr.strip()}"
                )
            self._validate_control_worktree()
            self._ticket_store()
            _log(f"control rewrite reconciliation accepted: {from_head} -> {to_head}")
            _log(f"preserved displaced control head under {evidence}")
            return 0

    def _reconcile_update_base_owned(
        self, ticket_id: str, onto: str, *, _take_lock: bool = False
    ) -> int:
        """Run reconciliation with authority held by the service owner."""
        authority = self._lock() if _take_lock else nullcontext()
        with authority:
            reconciliation = self._state.get("reconciliation")
            if not isinstance(reconciliation, dict) or reconciliation.get("status") != (
                "pending"
            ):
                raise DevlegateError(
                    f"ticket {ticket_id} has no pending reconciliation"
                )
            if reconciliation.get("ticket_id") != ticket_id:
                raise DevlegateError(
                    "requested ticket does not match pending reconciliation"
                )
            target_result = _git(
                self.repo, "rev-parse", "--verify", f"{onto}^{{commit}}", check=False
            )
            if target_result.returncode:
                raise DevlegateError("requested reconciliation target is not a commit")
            target = target_result.stdout.strip()
            if not target:
                raise DevlegateError("requested reconciliation target is empty")
            original_base = str(reconciliation["original_base"])
            product = self._observe_product_generation(original_base)
            if product["branch"] != self.current_branch:
                raise DevlegateError("product checkout is on the wrong branch")
            if product["dirty"]:
                raise DevlegateError("product checkout is dirty")
            current_product = str(product["local_head"])
            if current_product != target:
                raise DevlegateError(
                    "requested reconciliation target is not the current product HEAD"
                )
            if str(product["remote_head"]) != target:
                raise DevlegateError(
                    "requested reconciliation target is not the fresh product "
                    "remote HEAD"
                )
            ancestor = _git(
                self.repo,
                "merge-base",
                "--is-ancestor",
                original_base,
                target,
                check=False,
            )
            if ancestor.returncode:
                raise DevlegateError(
                    "requested reconciliation target is not a descendant of "
                    "the original base"
                )
            evidence = str(reconciliation["evidence_ref"])
            preserved = _git(self.repo, "rev-parse", "--verify", evidence, check=False)
            if (
                preserved.returncode
                or preserved.stdout.strip() != reconciliation["worker_checkpoint"]
            ):
                raise DevlegateError("preserved worker checkpoint evidence changed")
            manager = ExecutionWorkspaceManager(
                self.repo, self.execution_worktree_root, ticket_id
            )
            if (
                reconciliation["execution_branch"] != manager.branch
                or Path(str(reconciliation["execution_path"])) != manager.path
            ):
                raise DevlegateError("reconciliation workspace binding is invalid")
            workspace = self._execution_workspace_for_recovery()
            if workspace.head != reconciliation["worker_checkpoint"] or workspace.dirty:
                raise DevlegateError(
                    "execution workspace changed before reconciliation"
                )
            execution_remote = self._execution_remote_head(manager.branch)
            if execution_remote is not None:
                raise DevlegateError(
                    "published execution history cannot be rewritten without force push"
                )
            if not self._checkpoint_commit_is_exact(
                workspace,
                str(reconciliation["worker_checkpoint"]),
                original_base,
            ):
                raise DevlegateError(
                    "execution lineage is not the supported one-commit shape"
                )
            if str(reconciliation["worker_checkpoint"]) == original_base:
                raise DevlegateError(
                    "reconciliation requires a worker checkpoint commit"
                )
            try:
                rebased = _git(
                    workspace.path,
                    "rebase",
                    "--onto",
                    target,
                    original_base,
                    check=False,
                )
                if rebased.returncode:
                    _git(workspace.path, "rebase", "--abort", check=False)
                    restored = _git(
                        workspace.path, "rev-parse", "HEAD", check=False
                    ).stdout.strip()
                    clean = not _git(
                        workspace.path, "status", "--porcelain", check=False
                    ).stdout
                    if restored != reconciliation["worker_checkpoint"] or not clean:
                        raise DevlegateError(
                            "reconciliation conflict could not be safely aborted"
                        )
                    raise DevlegateError(
                        "reconciliation conflict; manual/advanced reconciliation "
                        "is required"
                    )
            except OSError as error:
                raise DevlegateError(f"reconciliation failed: {error}") from error
            updated = self._execution_workspace_for_recovery()
            if updated.dirty or updated.head == target:
                raise DevlegateError("reconciled execution lineage is invalid")
            parent = _git(updated.path, "rev-parse", "--verify", "HEAD^", check=False)
            if parent.returncode or parent.stdout.strip() != target:
                raise DevlegateError("reconciled execution base proof failed")
            resolved = {
                **reconciliation,
                "status": "resolved",
                "resolution": "update-base",
                "effective_base": target,
            }
            todo_fingerprint, _count = _todo_fingerprint(
                self.control_worktree, self.todo_path
            )
            self._save_state(
                "idle",
                execution_base_head=target,
                execution_start_head=target,
                execution_remote_head=None,
                handled_remote_head=target,
                handled_control_head=str(reconciliation["control_head"]),
                handled_todo_fingerprint=todo_fingerprint,
                reconciliation=resolved,
                resume_required={"ticket_id": ticket_id, "status": "required"},
            )
            _log(f"reconciliation resolved for {ticket_id}; resume required")
            return 0

    def _reconcile_resume_owned(
        self, ticket_id: str, *, _take_lock: bool = False
    ) -> int:
        authority = self._lock() if _take_lock else nullcontext()
        with authority:
            reconciliation = self._state.get("reconciliation")
            if not isinstance(reconciliation, dict) or reconciliation.get(
                "status"
            ) not in {"pending", "resolving"}:
                raise DevlegateError(
                    f"ticket {ticket_id} has no resumable reconciliation"
                )
            if reconciliation.get("ticket_id") != ticket_id:
                raise DevlegateError("requested ticket does not match reconciliation")
            original_base = str(reconciliation["original_base"])
            checkpoint = str(reconciliation["worker_checkpoint"])
            self._assert_product_checkout_unchanged(self.current_branch, original_base)
            product = self._observe_product_generation(original_base)
            if not product["stable"]:
                raise DevlegateError(
                    "same-base reconciliation requires a clean unchanged product "
                    "generation"
                )
            self._validate_control_worktree()
            recorded_control = str(reconciliation["control_head"])
            control_head = _git(
                self.control_worktree, "rev-parse", "HEAD"
            ).stdout.strip()
            if control_head != recorded_control:
                raise DevlegateError("control revision changed during reconciliation")
            ticket = self._ticket_store().by_id.get(ticket_id)
            if ticket is None or ticket.state not in {"todo", "review"}:
                raise DevlegateError("reconciliation ticket is no longer compatible")
            manager = ExecutionWorkspaceManager(
                self.repo, self.execution_worktree_root, ticket_id
            )
            if (
                reconciliation["execution_branch"] != manager.branch
                or Path(str(reconciliation["execution_path"])) != manager.path
            ):
                raise DevlegateError("reconciliation workspace binding is invalid")
            workspace = self._execution_workspace_for_recovery()
            if workspace.head != checkpoint or workspace.dirty:
                raise DevlegateError("execution workspace changed before resume")
            evidence = _git(
                self.repo,
                "rev-parse",
                "--verify",
                str(reconciliation["evidence_ref"]),
                check=False,
            )
            if evidence.returncode or evidence.stdout.strip() != checkpoint:
                raise DevlegateError("preserved worker checkpoint evidence changed")
            if "execution_remote_head" in reconciliation:
                expected_remote = reconciliation["execution_remote_head"]
            elif "execution_remote_head" in self._state:
                expected_remote = self._state["execution_remote_head"]
            else:
                raise DevlegateError(
                    "legacy reconciliation lacks execution predecessor"
                )
            if expected_remote is not None and not _is_git_identity(expected_remote):
                raise DevlegateError("execution predecessor identity is invalid")
            was_resolving = reconciliation["status"] == "resolving"
            remote = self._execution_remote_head(manager.branch)
            if not (
                (expected_remote is None and remote is None)
                or (isinstance(expected_remote, str) and remote == expected_remote)
                or (was_resolving and remote == checkpoint)
            ):
                raise DevlegateError(
                    "execution remote changed or has divergent lineage"
                )
            if isinstance(expected_remote, str):
                ancestor = _git(
                    self.repo,
                    "merge-base",
                    "--is-ancestor",
                    expected_remote,
                    checkpoint,
                    check=False,
                )
                if ancestor.returncode:
                    raise DevlegateError(
                        "execution predecessor is not an ancestor of checkpoint"
                    )
            resolving = {
                **reconciliation,
                "status": "resolving",
                "resolution": "resume",
            }
            self._save_state("idle", reconciliation=resolving)
            # Re-observe immediately after the durable write-ahead intent.
            product = self._observe_product_generation(original_base)
            if not product["stable"]:
                raise DevlegateError("product generation changed before publication")
            reobserved_remote = self._execution_remote_head(manager.branch)
            if reobserved_remote != remote:
                if not (was_resolving and reobserved_remote == checkpoint):
                    raise DevlegateError("execution remote changed before publication")
            pending_report = reconciliation.get("execution_report")
            if isinstance(pending_report, dict):
                report = ExecutionReport.from_dict(pending_report)
                self._save_state(
                    "agent_running",
                    local_head=original_base,
                    remote_head=original_base,
                    changed_paths="",
                    control_head=recorded_control,
                    selected_ticket_id=ticket_id,
                    selected_ticket_body=ticket.body,
                    execution_ticket_id=ticket_id,
                    execution_base_head=original_base,
                    execution_control_head=recorded_control,
                    execution_branch=manager.branch,
                    execution_path=str(manager.path),
                    execution_id=report.execution_id,
                    execution_remote_head=expected_remote,
                    execution_start_head=original_base,
                    execution_stage="post-checkpoint",
                    pending_execution_report=report.as_dict(),
                    worker_identity=None,
                )
                self._recover_checkpoint_publication()
                report = self._recover_lifecycle_report()
                lifecycle_head = self._apply_execution_lifecycle(report)
                self._finalize_execution_lifecycle(report, lifecycle_head)
                self._save_state(
                    "idle",
                    reconciliation={**resolving, "status": "resolved"},
                    execution_remote_head=checkpoint,
                )
                return 0 if report.result.conclusion != "failed" else 1
            if reobserved_remote != checkpoint:
                self._publish_execution_branch(
                    self._execution_workspace_for_recovery(),
                    checkpoint,
                    expected_remote,
                )
            if self._execution_remote_head(manager.branch) != checkpoint:
                raise DevlegateError(
                    "execution checkpoint publication could not be proven"
                )
            self._save_state(
                "idle",
                execution_base_head=original_base,
                execution_remote_head=checkpoint,
                reconciliation={**resolving, "status": "resolved"},
                resume_required={"ticket_id": ticket_id, "status": "required"},
            )
            _log(f"reconciliation resolved for {ticket_id}; resume required")
            return 0

    def _reconcile_stranded_execution(self) -> str | None:
        """Classify a lost owner and normalize only proven post-worker state."""
        state = self._state
        kind = state.get("execution_interruption_kind")
        if kind is None:
            kind = "process_loss"
            self._save_state("agent_running", execution_interruption_kind=kind)
        if kind not in {"operator_abort", "service_shutdown", "process_loss"}:
            raise DevlegateError("invalid execution interruption kind")
        stage = state.get("execution_stage")
        identity = _worker_identity_from_value(
            state.get("worker_identity"), state.get("execution_id")
        )
        if stage == "worker-running":
            assert identity is not None
            observation = self._workers.observe(identity)
            if observation == "matching-live":
                reason = (
                    "previous Devlegate owner was lost; execution worker is still "
                    "alive; automatic reconciliation is blocked"
                )
            elif observation == "indeterminate":
                reason = (
                    "previous Devlegate owner was lost; worker ownership is "
                    "indeterminate; reconciliation is blocked"
                )
            else:
                self._normalize_stranded_execution(kind)
                return (
                    str(state["execution_ticket_id"])
                    if kind in {"service_shutdown", "process_loss"}
                    else None
                )
            self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
            raise DevlegateError(reason)
        if stage == "post-worker":
            self._normalize_stranded_execution(kind)
            return (
                str(state["execution_ticket_id"])
                if kind in {"service_shutdown", "process_loss"}
                else None
            )
        reason = (
            f"previous Devlegate owner was lost during unsafe execution stage "
            f"{stage or 'unknown'}; reconciliation is blocked"
        )
        self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
        raise DevlegateError(reason)

    def _normalize_stranded_execution(self, interruption_kind: str) -> None:
        state = self._state
        required = (
            "local_head",
            "remote_head",
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
        self._assert_product_checkout_unchanged(
            self.current_branch, str(state["local_head"])
        )
        product_ref = f"{self.remote_name}/{self.remote_branch}"
        product_fetch = _git(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        )
        product_remote = _git(
            self.repo, "rev-parse", "--verify", product_ref, check=False
        )
        if (
            product_fetch.returncode
            or product_remote.returncode
            or product_remote.stdout.strip() != state["remote_head"]
        ):
            raise DevlegateError(
                "product generation changed before interrupted execution recovery"
            )
        self._validate_control_worktree()
        status = _git(self.control_worktree, "status", "--porcelain").stdout
        if status:
            raise DevlegateError("control working tree is dirty")
        control_fetch = _git(
            self.control_worktree,
            "fetch",
            "--prune",
            self.remote_name,
            self.control_branch,
            check=False,
        )
        control_ref = f"{self.remote_name}/{self.control_branch}"
        current_remote = _git(
            self.control_worktree, "rev-parse", "--verify", control_ref, check=False
        )
        if control_fetch.returncode or current_remote.returncode:
            raise DevlegateError("cannot freshly observe control remote")
        control_head = current_remote.stdout.strip()
        local_control = _git(self.control_worktree, "rev-parse", "HEAD").stdout.strip()
        local_is_ancestor = _git(
            self.control_worktree,
            "merge-base",
            "--is-ancestor",
            local_control,
            control_head,
            check=False,
        )
        if local_is_ancestor.returncode:
            raise DevlegateError("local control history diverged during recovery")
        self._prove_bound_ticket_descendant(
            control_head,
            str(state["execution_control_head"]),
            str(state["execution_ticket_id"]),
            str(state["selected_ticket_body"]),
        )
        if local_control != control_head:
            merged = _git(
                self.control_worktree, "merge", "--ff-only", control_ref, check=False
            )
            if merged.returncode:
                raise DevlegateError(
                    "cannot fast-forward compatible control descendant"
                )
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(str(state["execution_ticket_id"]))
        if (
            ticket is None
            or ticket.state != "todo"
            or ticket not in ticket_store.runnable
            or ticket.body != state["selected_ticket_body"]
        ):
            raise DevlegateError("persisted execution ticket is not safely recoverable")
        base_head = str(state["execution_base_head"])
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, str(state["execution_ticket_id"])
        )
        if (
            state["execution_branch"] != manager.branch
            or Path(str(state["execution_path"])) != manager.path
        ):
            raise DevlegateError("persisted execution workspace binding is invalid")
        try:
            manager._validate_base(base_head)
            manager._validate_branch(base_head)
            registrations = manager._registrations()
            workspace = manager._validate_existing(
                registrations.get(manager.path.resolve()), base_head
            )
            start_head = state.get("execution_start_head")
            if not isinstance(start_head, str) or not start_head:
                raise ExecutionWorkspaceError("execution start HEAD is unavailable")
            if workspace.head != start_head:
                raise ExecutionWorkspaceError(
                    "execution worktree HEAD does not match execution start HEAD"
                )
            manager.verify_submodules(workspace)
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(
                f"cannot safely normalize interrupted execution: {error}"
            ) from error
        failures = state.get("failed_executions", {})
        if not isinstance(failures, dict):
            failures = {}
        failures = {
            **failures,
            str(state["execution_ticket_id"]): {
                "execution_id": state["execution_id"],
                "product_head": state["local_head"],
                "remote_head": state["remote_head"],
                "control_head": control_head,
                "todo_fingerprint": _todo_fingerprint(
                    self.control_worktree, self.todo_path
                )[0],
                "reason": f"interrupted: {interruption_kind.replace('_', ' ')}",
                "interrupted": True,
                "interruption_kind": interruption_kind,
            },
        }
        self._save_state(
            "idle",
            handled_remote_head=state["remote_head"],
            handled_control_head=control_head,
            handled_todo_fingerprint=_todo_fingerprint(
                self.control_worktree, self.todo_path
            )[0],
            execution_control_head=state["execution_control_head"],
            interrupted_execution_id=state["execution_id"],
            failed_executions=failures,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)

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
        if state.get("execution_stage") != "pre-checkpoint":
            self._assert_previous_worker_is_safe()
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
        interruption_kind = state.get("execution_interruption_kind")
        if interruption_kind not in {
            "operator_abort",
            "service_shutdown",
            "process_loss",
        }:
            interruption_kind = "process_loss"
        failed_executions = {
            **failed_executions,
            ticket_id: {
                "execution_id": state["execution_id"],
                "product_head": state["local_head"],
                "remote_head": state["remote_head"],
                "control_head": state["control_head"],
                "todo_fingerprint": todo_fingerprint,
                "reason": f"interrupted: {interruption_kind.replace('_', ' ')}",
                "interrupted": True,
                "interruption_kind": interruption_kind,
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

    def init_project_result(self, conflicts: str = "abort") -> dict[str, object]:
        try:
            _total, changed = initialize_project(
                self.repo,
                self._agent_render_context(),
                conflicts=conflicts,
                state_dir=self.state_dir,
            )
        except AgentProtocolError as error:
            raise DevlegateError(str(error)) from error
        return {"result": "initialized", "rendered": changed}

    def init_project(self, conflicts: str = "abort") -> int:
        result = self.init_project_result(conflicts)
        print(
            "initialized Devlegate project protocol templates "
            f"and rendered {result['rendered']} agent protocol artifacts"
        )
        return 0

    def render_result(self, check: bool = False) -> dict[str, object]:
        try:
            total, changed = render_project(
                self.repo, self._agent_render_context(), check=check
            )
        except AgentProtocolError as error:
            raise DevlegateError(str(error)) from error
        return {
            "result": "current" if check else "rendered",
            "artifacts": total,
            "changed": changed,
            "check": check,
        }

    def render(self, check: bool = False) -> int:
        result = self.render_result(check)
        if check:
            print("generated agent protocol artifacts are current")
        else:
            print(
                f"rendered {result['artifacts']} agent protocol artifacts "
                f"({result['changed']} changed)"
            )
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
            with self._snapshot_lock:
                snapshot = self._published_status_snapshot
            if snapshot is None:
                raise DevlegateError(
                    "project state changed while execution plan was being collected"
                )
            return snapshot.plan
        blocked_reason = (
            snapshot.plan.reason if snapshot.plan.action == "blocked" else None
        )
        worker_running = self.service_snapshot().worker_running
        lifecycle = (
            "worker" if worker_running else ("blocked" if blocked_reason else "ready")
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

    def check_result(
        self, *, emit_output: bool = False
    ) -> tuple[int, dict[str, object]]:
        """Run read-only configuration and checkout diagnostics."""

        def write(*args: object) -> None:
            if emit_output:
                print(*args)

        write(f"Devlegate {__version__} preflight")
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
        phase = str(self._state.get("phase"))
        owner_live = self._mutation_owner_live()
        platform_supported = hosted_runtime_supported()
        checks = [
            (
                "Platform",
                platform_supported,
                "Linux hosted runtime supported"
                if platform_supported
                else HOSTED_RUNTIME_ERROR,
            ),
            (
                "OPENCODE_MODEL",
                bool(self.opencode_model),
                "set OPENCODE_MODEL in the effective configuration",
            ),
            ("repository root", self.repo.is_dir(), str(self.repo)),
            (
                "product branch",
                bool(self.current_branch) and self.current_branch == self.remote_branch,
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
            (
                "runtime state",
                phase == "idle" or owner_live,
                (
                    "idle"
                    if phase == "idle"
                    else (
                        f"phase={phase}, stage="
                        f"{self._state.get('execution_stage') or 'none'}; "
                        "mutation owner is active"
                        if owner_live
                        else (
                            f"phase={phase}, stage="
                            f"{self._state.get('execution_stage') or 'none'}; "
                            "mutation-owner reconciliation is required"
                        )
                    )
                ),
            ),
        ]
        failed = False
        check_results: list[dict[str, object]] = []
        for name, passed, detail in checks:
            label = "OK" if passed else "FAIL"
            suffix = f": {detail}" if detail else ""
            check_results.append({"name": name, "passed": passed, "detail": detail})
            write(f"{label:4}  {name}{suffix}")
            failed |= not passed
        if status:
            write("Dirty working tree details:")
            for line in status.rstrip().splitlines():
                write(f"  {line}")
            for name, count in _status_summary(status).items():
                write(f"{name.replace('_', ' ')}: {count}")
        write(f"local HEAD: {local_head}")
        write(f"known remote HEAD: {remote_head or '<unknown>'}")
        display_counts = None
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
            write(f"ahead/behind: {display_counts}")
        write(f"todo files: {todo_count}")
        write(f"todo fingerprint: {todo_fingerprint}")
        if self.control_worktree.is_dir():
            write(
                "control HEAD: "
                + (
                    control_observation.local_head
                    if control_observation is not None
                    else "<unknown>"
                )
            )
        ticket_counts = None
        selected = None
        if ticket_store is not None:
            ticket_counts = {
                state: sum(ticket.state == state for ticket in ticket_store.tickets)
                for state in ("backlog", "todo", "review", "accepted", "done")
            }
            write("Ticket storage:")
            for state in ("backlog", "todo", "review", "accepted", "done"):
                write(f"  {state}: {ticket_counts[state]}")
            write(f"Runnable tickets: {len(ticket_store.runnable)}")
            selected = ticket_store.selected()
            if selected is not None:
                write(f"Next runnable: {selected.id} - {selected.title}")
        elif ticket_error:
            write(f"Ticket storage: invalid: {ticket_error}")
        write(f"work generation differs from persisted: {generation_differs}")
        write(
            "remote information is from the existing remote-tracking ref; "
            "check does not fetch"
        )
        details = {
            "ready": not failed,
            "checks": check_results,
            "local_head": local_head,
            "known_remote_head": remote_head or None,
            "ahead_behind": display_counts,
            "todo_files": todo_count,
            "todo_fingerprint": todo_fingerprint,
            "work_generation_differs": generation_differs,
            "dirty_files": status.rstrip().splitlines(),
            "dirty_summary": _status_summary(status),
            "remote_note": (
                "remote information is from the existing remote-tracking ref; "
                "check does not fetch"
            ),
            "control_head": (
                control_observation.local_head
                if control_observation is not None
                else None
            ),
            "ticket_counts": ticket_counts,
            "runnable_tickets": (
                len(ticket_store.runnable) if ticket_store is not None else None
            ),
            "next_runnable": (
                {"id": selected.id, "title": selected.title}
                if selected is not None
                else None
            ),
        }
        write("\nNot ready." if failed else "\nReady.")
        return (1 if failed else 0), details

    def check(self) -> int:
        result, _details = self.check_result(emit_output=True)
        return result
