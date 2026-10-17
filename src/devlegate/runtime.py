# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Runtime and orchestration implementation for Devlegate."""

import dataclasses
import fcntl
import hashlib
import json
import os
import re
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
from devlegate.control_git_workflow import (
    ControlGitWorkflowError,
    attach_existing_control,
    bootstrap_control,
    sync_control,
)
from devlegate.execution_provenance import (
    ExecutionProvenanceError,
    lifecycle_commit_is_exact,
    resolve_accepted_execution_report,
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
    ExecutionWorkspaceConflict,
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
    WorkspaceInspection,
)
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    add_all,
    ahead_behind,
    branch_name,
    commit_changes,
    commit_parents,
    commit_range,
    common_dir,
    compare_and_swap_ref,
    create_ref_if_absent,
    delete_branch,
    delete_branch_if_unoccupied,
    fetch_ref,
    file_history_commit,
    hash_object,
    history_commits,
    is_ancestor,
    merge_fast_forward,
    parent_commit,
    path_object,
    push_refs,
    read_object,
    read_path,
    remote_exists,
    remote_ref,
    remove_worktree,
    reset_hard,
    resolve_commit,
    resolve_object,
    run_git,
    update_ref,
    working_tree_status,
    worktree_paths,
    worktree_root,
)
from devlegate.git import (
    changed_paths as git_changed_paths,
)
from devlegate.git import (
    commit as git_commit,
)
from devlegate.git import (
    merge_base as git_merge_base,
)
from devlegate.operational_log import service_log
from devlegate.platform_support import HOSTED_RUNTIME_ERROR, hosted_runtime_supported
from devlegate.project_context import ProjectContextError, load_project_context
from devlegate.reconciliation import same_parent_replacement
from devlegate.reconciliation_transactions import (
    ReconciliationTransactionError,
    run_update_base,
)
from devlegate.recovery import classify_stage
from devlegate.recovery_transactions import (
    reconcile_control_lineage,
    recover_stranded_execution,
)
from devlegate.recovery_workflow import (
    WorkflowRecoveryError,
    recover_accepted_integration,
    recover_checkpoint_publication,
    recover_lifecycle_report,
)
from devlegate.recovery_workspace import (
    inspect_bound_workspace,
    repair_bound_workspace,
)
from devlegate.runtime_locator import (
    RuntimeLocator,
    RuntimeLocatorError,
    read_env,
    repository_root,
)
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore
from devlegate.tickets import (
    Ticket,
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


class WorkspacePreparationBlockedError(DevlegateError):
    """A ticket workspace cannot be prepared without operator action."""


class ShutdownInterrupted(Exception):
    """A requested shutdown interrupted a foreground Git child."""


@dataclasses.dataclass(frozen=True)
class IterationIntent:
    """Explicit operator intent supplied to one scheduler iteration."""

    retry_ticket_id: str | None = None
    force_retry: bool = False


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

    @property
    def is_forced_retry(self) -> bool:
        return self.kind == "forced_retry"


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
    workspace_cleanup: dict[str, object] | None = None

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
            "workspace_cleanup": self.workspace_cleanup,
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
    manual_retry_required: bool = False

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
            "manual_retry_required": self.manual_retry_required,
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
    rewrite_published: bool = False
    force: bool = False
    mutable: bool = True
    resolution_class: str | None = None
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


def _retry_request_fingerprint(ticket_id: str, force: bool = False) -> str:
    if not force:
        return _mutation_fingerprint("retry", {"ticket_id": ticket_id})
    return _mutation_fingerprint(
        "retry", {"ticket_id": ticket_id, "force": "1" if force else "0"}
    )


def _drop_request_fingerprint(ticket_id: str, execution_id: str) -> str:
    return _mutation_fingerprint(
        "drop", {"ticket_id": ticket_id, "execution_id": execution_id}
    )


def _recover_request_fingerprint(
    ticket_id: str, execution_id: str, observed_head: str
) -> str:
    return _mutation_fingerprint(
        "recover",
        {
            "ticket_id": ticket_id,
            "execution_id": execution_id,
            "observed_head": observed_head,
        },
    )


def _reconcile_request_fingerprint(
    ticket_id: str, onto: str | None, rewrite_published: bool = False
) -> str:
    return _mutation_fingerprint(
        "reconcile-update-base",
        {
            "ticket_id": ticket_id,
            "onto": onto if onto is not None else "<current-product-head>",
            "rewrite_published": "1" if rewrite_published else "0",
        },
    )


def _reconcile_resume_request_fingerprint(ticket_id: str) -> str:
    return _mutation_fingerprint("reconcile-resume", {"ticket_id": ticket_id})


def _reconcile_auto_request_fingerprint(ticket_id: str, onto: str | None) -> str:
    return _mutation_fingerprint(
        "reconcile-auto",
        {"ticket_id": ticket_id, "onto": onto if onto is not None else "<auto>"},
    )


def _cleanup_workspaces_request_fingerprint(apply: bool, limit: int) -> str:
    return _mutation_fingerprint(
        "cleanup-workspaces",
        {"apply": "1" if apply else "0", "limit": str(limit)},
    )


def _control_reconcile_request_fingerprint(from_head: str, to_head: str) -> str:
    return _mutation_fingerprint(
        "reconcile-control", {"from": from_head, "to": to_head}
    )


def _worker_identity_from_value(
    value: object, execution_id: object | None = None
) -> WorkerProcessIdentity | None:
    if value is None:
        return None
    if (
        not isinstance(value, dict)
        or not set(value).issubset(
            {
                "execution_id",
                "pid",
                "pgid",
                "sid",
                "boot_id",
                "start_time",
                "containment_id",
            }
        )
        or set(value)
        < {
            "execution_id",
            "pid",
            "pgid",
            "sid",
            "boot_id",
            "start_time",
        }
    ):
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
        or (
            "containment_id" in value
            and value["containment_id"] is not None
            and not isinstance(value["containment_id"], str)
        )
    ):
        raise DevlegateError("invalid worker identity")
    return WorkerProcessIdentity(
        value["execution_id"],
        value["pid"],
        value["pgid"],
        value["sid"],
        value["boot_id"],
        value["start_time"],
        value.get("containment_id"),
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


_git = run_git


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


def _is_same_parent_replacement(repo: Path, original: str, target: str) -> bool:
    """Prove that two distinct commits are single-parent replacements."""
    parent_map: dict[str, tuple[str, ...]] = {}
    for commit in (original, target):
        try:
            parents = commit_parents(repo, commit, runner=_git)
        except GitObservationError:
            return False
        if parents is None:
            return False
        parent_map[commit] = parents
    return same_parent_replacement(original, target, parent_map)


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
            self.repo = (repository or repository_root(self.env_file.parent)).resolve()
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
        self._workspace_cleanup_observation_error: str | None = None
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
        self._lifecycle_force = False
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
        # Git observation helpers are used during startup validation.  Keep
        # their owner and interruption state valid before validation begins.
        self._host_owner_thread_id: int | None = None
        self._validate()
        self._state = self._load_state()
        self._snapshot_lock = threading.Lock()
        self._published_status_snapshot: StatusSnapshot | None = None
        self._published_live_execution: dict[str, object] | None = None
        self._published_retry_candidates: tuple[dict[str, str], ...] = ()
        self._published_drop_candidates: tuple[dict[str, str], ...] = ()
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

    def request_lifecycle(
        self, intent: str, request_id: str, *, force: bool = False
    ) -> dict[str, object]:
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
                    if force:
                        self._lifecycle_force = True
                        stop_event = self._stop_event
                        if stop_event is not None:
                            request_abort = getattr(stop_event, "request", None)
                            if callable(request_abort):
                                request_abort("operator_abort", source="operator_abort")
                            else:
                                stop_event.kind = "operator_abort"
                                stop_event.source = "operator_abort"
                                stop_event.set()
                    return self.lifecycle_status_payload()
                self._workers.begin_drain()
                self._lifecycle_intent = intent
                self._lifecycle_phase = "draining"
                self._lifecycle_request_id = request_id
                self._lifecycle_force = force
                if (
                    intent == "stop"
                    and force
                    and self._state.get("phase") in {"agent_pending", "agent_running"}
                    and all(
                        isinstance(self._state.get(field), str) and self._state[field]
                        for field in (
                            "execution_ticket_id",
                            "execution_id",
                            "execution_base_head",
                        )
                    )
                ):
                    # This is the only lifecycle path that creates a durable
                    # manual-retry barrier. The low-level abort itself is not
                    # sufficient to infer operator intent.
                    self._save_state(
                        str(self._state["phase"]),
                        manual_retry_required={
                            "ticket_id": self._state.get("execution_ticket_id"),
                            "execution_id": self._state.get("execution_id"),
                            "generation": self._state.get("execution_base_head"),
                            "request_id": request_id,
                        },
                    )
                stop_event = self._stop_event
                if force and stop_event is not None:
                    request_abort = getattr(stop_event, "request", None)
                    if callable(request_abort):
                        request_abort("operator_abort", source="operator_abort")
                    else:
                        stop_event.kind = "operator_abort"
                        stop_event.source = "operator_abort"
                        stop_event.set()
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
            "force": self._lifecycle_force,
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

    def submit_retry(
        self, ticket_id: str, *, force: bool = False, request_id: str
    ) -> dict[str, object]:
        """Submit one retry intent and wait only for owner-side admission."""
        return self._submit_operator_command(
            method="retry",
            ticket_id=ticket_id,
            request_id=request_id,
            fingerprint=_retry_request_fingerprint(ticket_id, force),
            force=force,
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

    def submit_recover(
        self, ticket_id: str, execution_id: str, observed_head: str, *, request_id: str
    ) -> dict[str, object]:
        """Submit an exact, operator-authorized pre-worker workspace recovery."""
        return self._submit_operator_command(
            method="recover",
            ticket_id=ticket_id,
            execution_id=execution_id,
            onto=observed_head,
            request_id=request_id,
            fingerprint=_recover_request_fingerprint(
                ticket_id, execution_id, observed_head
            ),
        )

    def submit_reconcile_update_base(
        self,
        ticket_id: str,
        onto: str | None,
        *,
        rewrite_published: bool = False,
        request_id: str,
    ) -> dict[str, object]:
        """Submit one reconciliation intent for owner-side admission."""
        return self._submit_operator_command(
            method="reconcile-update-base",
            ticket_id=ticket_id,
            onto=onto,
            rewrite_published=rewrite_published,
            request_id=request_id,
            fingerprint=_reconcile_request_fingerprint(
                ticket_id, onto, rewrite_published
            ),
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

    def submit_reconcile_auto(
        self, ticket_id: str, onto: str | None, *, request_id: str
    ) -> dict[str, object]:
        """Submit the intent-driven reconciliation operation."""
        return self._submit_operator_command(
            method="reconcile-auto",
            ticket_id=ticket_id,
            onto=onto,
            request_id=request_id,
            fingerprint=_reconcile_auto_request_fingerprint(ticket_id, onto),
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

    def submit_cleanup_workspaces(
        self, *, apply: bool, limit: int, request_id: str
    ) -> dict[str, object]:
        """Submit bounded workspace cleanup to the serialized service owner."""
        if limit < 1:
            raise DevlegateError("workspace cleanup limit must be positive")
        return self._submit_operator_command(
            method="cleanup-workspaces",
            ticket_id="__workspaces__",
            onto=str(limit),
            request_id=request_id,
            fingerprint=_cleanup_workspaces_request_fingerprint(apply, limit),
            force=apply,
            mutable=apply,
        )

    def workspace_cleanup_status(self, *, limit: int = 100) -> dict[str, object]:
        """Return the last owner-published cleanup inventory.

        Read-only IPC must not perform repository observation beside the owner
        thread. The owner publishes this projection with its normal status
        snapshot after each reconciliation pass.
        """
        del limit
        with self._snapshot_lock:
            snapshot = self._published_status_snapshot
            if snapshot is None:
                raise DevlegateError("service status view is not ready")
            cleanup = snapshot.workspace_cleanup
            if cleanup is None:
                return {
                    "status": "initializing",
                    "scan_complete": False,
                    "decisions": [],
                    "reason": "workspace inventory has not been observed yet",
                }
            return json.loads(json.dumps(cleanup))

    def _submit_operator_command(
        self,
        *,
        method: str,
        ticket_id: str,
        request_id: str,
        fingerprint: str,
        onto: str | None = None,
        execution_id: str | None = None,
        rewrite_published: bool = False,
        force: bool = False,
        mutable: bool = True,
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
            receipt = self._mutable_receipt(request_id) if mutable else None
            if receipt is not None:
                if (
                    receipt.get("method") != method
                    or receipt.get("fingerprint") != fingerprint
                    or receipt.get("accepted") is not True
                ):
                    raise DevlegateError(
                        f"request id collision: {method} request semantics differ"
                    )
                if method == "cleanup-workspaces" and isinstance(
                    receipt.get("result"), dict
                ):
                    return dict(receipt["result"])
                if method == "cleanup-workspaces":
                    # Admission was durable, but the owner may have been lost
                    # before the bounded sweep completed. Re-admit it so the
                    # operation is conservatively re-observed and replay-safe.
                    pass
                else:
                    return self._operator_ack(
                        method,
                        ticket_id,
                        receipt.get("onto", onto),
                        execution_id,
                        receipt.get("resolution_class"),
                    )
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
                    request_id=request_id,
                    method=method,
                    fingerprint=fingerprint,
                    ticket_id=ticket_id,
                    onto=onto,
                    execution_id=execution_id,
                    rewrite_published=rewrite_published,
                    force=force,
                    mutable=mutable,
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

    def _validate_retry_admission(self, ticket_id: str, force: bool = False) -> None:
        """Validate retry admission on the owner thread."""
        if force:
            self._validate_force_retry_admission(ticket_id)
            return
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

    def _validate_force_retry_admission(self, ticket_id: str) -> None:
        state = self._state
        if state.get("phase") != "agent_running" or state.get(
            "execution_stage"
        ) not in {
            "worker-launch",
            "worker-running",
        }:
            raise DevlegateError(
                "forced retry requires a stranded worker-running execution"
            )
        if state.get("execution_ticket_id") != ticket_id:
            raise DevlegateError("requested ticket does not match the active execution")
        execution_id = state.get("execution_id")
        if not isinstance(execution_id, str) or not execution_id:
            raise DevlegateError("forced retry requires an exact execution identity")
        identity = _worker_identity_from_value(
            state.get("worker_identity"), execution_id
        )
        if identity is None:
            raise DevlegateError("worker ownership is not proven absent")
        observation = self._workers.observe(identity)
        if observation != "absent":
            raise DevlegateError(
                f"worker ownership is {observation}; forced retry requires absent"
            )
        admitted_product = state.get("local_head")
        if not isinstance(admitted_product, str) or not admitted_product:
            raise DevlegateError(
                "forced retry product admission identity is incomplete"
            )
        product = self._observe_product_generation(admitted_product)
        if not product["stable"]:
            if product.get("classification") == "unobservable":
                raise DevlegateError(
                    "product remote could not be observed; "
                    "retry when connectivity returns"
                )
            raise DevlegateError(
                "product generation changed; use normal product reconciliation"
            )
        admitted_control = state.get("execution_control_head")
        if not isinstance(admitted_control, str) or not admitted_control:
            raise DevlegateError(
                "forced retry control admission identity is incomplete"
            )
        current_head, _remote_head = self._sync_control()
        try:
            control_is_ancestor = is_ancestor(
                self.control_worktree, admitted_control, current_head, runner=_git
            )
        except GitAncestryError as error:
            raise DevlegateError(
                "control history diverged; use explicit control reconciliation"
            ) from error
        if not control_is_ancestor:
            raise DevlegateError(
                "control history diverged; use explicit control reconciliation"
            )
        ticket_store = self._ticket_store()
        ticket = ticket_store.by_id.get(ticket_id)
        if (
            ticket is None
            or ticket.state != "todo"
            or ticket not in ticket_store.runnable
        ):
            raise DevlegateError(f"ticket {ticket_id} is not currently runnable")
        old_body = state.get("selected_ticket_body")
        if not isinstance(old_body, str) or ticket.body == old_body:
            raise DevlegateError(
                "current ticket body is unchanged; use ordinary retry/recovery"
            )
        branch = state.get("execution_branch")
        path = state.get("execution_path")
        base = state.get("execution_base_head")
        if not all(isinstance(value, str) and value for value in (branch, path, base)):
            raise DevlegateError("persisted execution workspace identity is incomplete")
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        if branch != manager.branch or Path(path) != manager.path:
            raise DevlegateError("retained execution workspace topology is invalid")
        inspection = manager.inspect(base)
        if inspection.classification != "REUSABLE" and inspection.reason != (
            "execution worktree is dirty"
        ):
            raise DevlegateError(
                f"retained execution workspace is not reusable: {inspection.reason}"
            )
        registrations = manager._registrations()
        registration = registrations.get(manager.path.resolve())
        if registration is None or not manager.path.is_dir():
            raise DevlegateError("retained execution workspace is not registered")
        try:
            workspace = manager._validate_existing(registration, base)
            manager.verify_submodules(workspace)
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(
                f"retained execution workspace integrity could not be proven: {error}"
            ) from error

    def _force_retry_transition(
        self,
        ticket_id: str,
        request_id: str,
        receipt: dict[str, object],
        receipts: dict[str, object],
    ) -> None:
        """Checkpoint and retire a proven stranded execution before retry."""
        state = self._state
        execution_id = str(state["execution_id"])
        old_control = str(state["execution_control_head"])
        current_control, _remote_head = self._sync_control()
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        registration = manager._registrations().get(manager.path.resolve())
        if registration is None:
            raise DevlegateError("retained execution workspace is not registered")
        try:
            workspace = manager._validate_existing(
                registration, str(state["execution_base_head"])
            )
            manager.verify_submodules(workspace)
            checkpoint = manager.checkpoint(
                workspace,
                execution_id,
                title="forced retry recovery",
                summary=(
                    "preserve stranded worker progress before retrying changed "
                    "ticket generation"
                ),
            )
        except (ExecutionWorkspaceError, OSError) as error:
            raise DevlegateError(
                f"forced retry workspace checkpoint failed: {error}"
            ) from error
        evidence_ref = f"refs/devlegate/recovery/force-retry/{ticket_id}/{execution_id}"
        existing = resolve_commit(self.repo, evidence_ref, runner=_git)
        if (
            existing is not None
            and existing != checkpoint.after_head
        ):
            raise DevlegateError("forced retry evidence ref has conflicting identity")
        if existing is None:
            pinned = create_ref_if_absent(
                self.repo, evidence_ref, checkpoint.after_head, runner=_git
            )
            if pinned.returncode:
                raise DevlegateError("cannot preserve forced retry evidence")
        todo_fingerprint, _ = _todo_fingerprint(self.control_worktree, self.todo_path)
        failed = self._state.get("failed_executions", {})
        if not isinstance(failed, dict):
            failed = {}
        failed = {
            **failed,
            ticket_id: {
                "execution_id": execution_id,
                "product_head": state["local_head"],
                "remote_head": state["remote_head"],
                "control_head": old_control,
                "current_control_head": current_control,
                "todo_fingerprint": todo_fingerprint,
                "interrupted": True,
                "interruption_kind": "forced_retry",
                "reason": (
                    "forced retry after lost worker and changed descendant ticket "
                    "generation"
                ),
                "checkpoint": checkpoint.after_head,
                "evidence_ref": evidence_ref,
            },
        }
        self._save_state(
            "idle",
            clear_execution=True,
            preserve_resume_required=True,
            execution_stage=None,
            failed_executions=failed,
            resume_required={
                "status": "required",
                "ticket_id": ticket_id,
                "checkpoint": checkpoint.after_head,
                "execution_id": execution_id,
                "evidence_ref": evidence_ref,
                "force_retry": True,
                "execution_base_head": state["execution_base_head"],
                "execution_remote_head": state.get("execution_remote_head"),
            },
            force_retry_authorization={
                "status": "admitted",
                "request_id": request_id,
                "ticket_id": ticket_id,
                "old_execution_id": execution_id,
                "control_head": current_control,
                "todo_fingerprint": todo_fingerprint,
                "checkpoint": checkpoint.after_head,
                "execution_base_head": state["execution_base_head"],
                "execution_remote_head": state.get("execution_remote_head"),
            },
            mutable_receipts={**receipts, request_id: receipt},
            force_retry_provenance={
                "old_execution_id": execution_id,
                "ticket_id": ticket_id,
                "admitted_control_head": old_control,
                "current_control_head": current_control,
                "product_head": state["local_head"],
                "checkpoint": checkpoint.after_head,
                "evidence_ref": evidence_ref,
                "reason": (
                    "forced retry after lost worker and changed descendant ticket "
                    "generation"
                ),
            },
            handled_control_head=current_control,
            # Leave the current ticket generation unhandled so the forced
            # authorization produces the fresh execution below.
            handled_todo_fingerprint="",
        )

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

    def _validate_recover_admission(
        self, ticket_id: str, execution_id: str, observed_head: str
    ) -> None:
        state = self._state
        if state.get("phase") != "agent_pending":
            raise DevlegateError("workspace recovery requires pre-worker state")
        if state.get("execution_ticket_id") != ticket_id:
            raise DevlegateError("requested ticket does not match the active execution")
        if state.get("execution_id") != execution_id:
            raise DevlegateError("requested execution is no longer active")
        if state.get("worker_identity") is not None:
            raise DevlegateError("cannot recover an execution with worker ownership")
        if state.get("pending_execution_report") is not None:
            raise DevlegateError("cannot recover an execution with a durable result")
        if not _is_git_identity(observed_head):
            raise DevlegateError("observed branch HEAD is not a Git identity")
        inspection = self._inspect_bound_execution_workspace(state)
        if inspection.classification != "UNSAFE":
            raise DevlegateError(
                "explicit recovery is allowed only for an UNSAFE workspace binding"
            )
        branch = state.get("execution_branch")
        if not isinstance(branch, str) or not branch:
            raise DevlegateError("persisted execution branch identity is incomplete")
        current = resolve_commit(self.repo, f"refs/heads/{branch}", runner=_git)
        evidence_ref = f"refs/devlegate/recovery/operator/{ticket_id}/{execution_id}"
        evidence = resolve_commit(self.repo, evidence_ref, runner=_git)
        replay = (
            evidence == observed_head
            and current == state.get("execution_base_head")
        )
        if current is None or (current != observed_head and not replay):
            raise DevlegateError("execution branch does not match the authorized HEAD")

    def _recover_unsafe_bound_execution_workspace(
        self, ticket_id: str, execution_id: str, observed_head: str
    ) -> None:
        state = self._state
        base_head = state.get("execution_base_head")
        branch = state.get("execution_branch")
        path = state.get("execution_path")
        if not all(
            isinstance(value, str) and value for value in (base_head, branch, path)
        ):
            raise DevlegateError("persisted execution workspace identity is incomplete")
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        if branch != manager.branch or Path(path) != manager.path:
            raise DevlegateError("persisted execution workspace binding is invalid")
        branch_ref = f"refs/heads/{branch}"
        evidence_ref = f"refs/devlegate/recovery/operator/{ticket_id}/{execution_id}"
        current_head = resolve_commit(self.repo, branch_ref, runner=_git)
        existing = resolve_commit(self.repo, evidence_ref, runner=_git)
        authorized_replay = (
            existing == observed_head
            and current_head == str(base_head)
        )
        if current_head is None or (
            current_head != observed_head and not authorized_replay
        ):
            raise DevlegateError("execution branch changed before explicit recovery")
        if existing is not None and existing != observed_head:
            raise DevlegateError("operator recovery evidence has conflicting identity")
        if existing is None:
            pinned = create_ref_if_absent(
                self.repo, evidence_ref, observed_head, runner=_git
            )
            if pinned.returncode:
                raise DevlegateError("cannot preserve the authorized branch HEAD")
        # Reconcile the conventional remote branch as a separate, leased effect.
        # A reused branch name is not evidence that its remote tip belongs to
        # this execution generation.
        remote_head = self._execution_remote_head(branch)
        if remote_head is not None and remote_head != str(base_head):
            remote_evidence = evidence_ref
            try:
                prior_reports = ExecutionReportStore(self.control_worktree).list(
                    ticket_id
                )
            except (ExecutionReportError, OSError, WorkflowBlockedError) as error:
                raise DevlegateError(
                    f"cannot prove prior remote branch ownership: {error}"
                ) from error
            prior = [
                report
                for report in prior_reports
                if report.execution_id != execution_id
                and report.execution_branch == branch
                and report.workspace_head == remote_head
            ]
            if len(prior) == 1:
                remote_evidence = self._workspace_recovery_ref(
                    ticket_id, prior[0].execution_id
                )
            remote_existing = resolve_commit(
                self.repo, remote_evidence, runner=_git
            )
            if (
                remote_existing is not None
                and remote_existing != remote_head
            ):
                raise DevlegateError(
                    "operator recovery evidence has conflicting identity"
                )
            if remote_existing is None:
                preserved = create_ref_if_absent(
                    self.repo, remote_evidence, remote_head, runner=_git
                )
                if preserved.returncode:
                    raise DevlegateError("cannot preserve the remote branch HEAD")
                published = push_refs(
                    self.repo,
                    self.remote_name,
                    f"{remote_head}:{remote_evidence}",
                    runner=_git,
                )
                if published.returncode:
                    raise DevlegateError("cannot publish remote recovery evidence")
            remote_again = self._execution_remote_head(branch)
            if remote_again != remote_head:
                raise DevlegateError(
                    "execution remote branch changed before recovery CAS"
                )
            retired = push_refs(
                self.repo,
                self.remote_name,
                f"{base_head}:refs/heads/{branch}",
                options=(f"--force-with-lease=refs/heads/{branch}:{remote_head}",),
                runner=_git,
            )
            if retired.returncode:
                raise DevlegateError(
                    "execution remote branch changed before recovery CAS"
                )
        registrations = manager._registrations()
        registration = registrations.get(manager.path.resolve())
        branch_path = next(
            (
                item_path
                for item_path, item in registrations.items()
                if item.get("branch") == branch
            ),
            None,
        )
        if branch_path is not None and branch_path != manager.path.resolve():
            raise DevlegateError(
                "execution branch is attached to an unexpected worktree"
            )
        if manager.path.exists() and registration is None:
            raise DevlegateError("execution worktree path is not safely registered")
        if registration is not None:
            # Validate the authorized conflicting checkout by its observed old
            # generation; the new admitted base is intentionally unrelated.
            expected_worktree_head = (
                str(base_head) if authorized_replay else observed_head
            )
            workspace = manager._validate_existing(registration, expected_worktree_head)
            if workspace.head != expected_worktree_head or workspace.dirty:
                raise DevlegateError("authorized execution worktree is not clean")
            removed = remove_worktree(
                self.repo, manager.path, force=True, runner=_git
            )
            if removed.returncode:
                raise DevlegateError("cannot remove authorized execution worktree")
        current_head = resolve_commit(self.repo, branch_ref, runner=_git)
        if current_head is None or (
            current_head != observed_head
            and not (authorized_replay and current_head == str(base_head))
        ):
            raise DevlegateError("execution branch changed during explicit recovery")
        if current_head == observed_head:
            moved = compare_and_swap_ref(
                self.repo, branch_ref, str(base_head), observed_head, runner=_git
            )
            if moved.returncode:
                raise DevlegateError(
                    "execution branch changed before explicit recovery CAS"
                )
        repaired = manager.prepare(str(base_head))
        final = manager.inspect(str(base_head), repaired.head)
        if final.classification != "REUSABLE":
            raise DevlegateError(
                f"recovered execution workspace is not reusable: {final.reason}"
            )
        current_remote_head = self._execution_remote_head(branch)
        if current_remote_head not in {None, str(base_head)}:
            raise DevlegateError(
                "execution remote branch changed during explicit recovery"
            )
        # A pre-worker generation owns no lifecycle stage or worker start head.
        # Explicit recovery starts a new coherent pending generation while
        # retaining the exact execution identity and binding.
        self._save_state(
            "agent_pending",
            clear_pre_worker_generation=True,
            clear_operator_recovery=True,
            # The leased reconciliation above deliberately leaves a current
            # generation predecessor at base_head.  Preserve that exact
            # observed value so normal checkpoint publication can lease from it.
            execution_remote_head=current_remote_head,
            worker_identity=None,
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

    def _validate_reconcile_admission(self, command: OperatorCommand) -> None:
        reconciliation = self._state.get("reconciliation")
        if not isinstance(reconciliation, dict) or reconciliation.get("status") != (
            "pending"
        ):
            raise DevlegateError(
                f"ticket {command.ticket_id} has no pending reconciliation"
            )
        if reconciliation.get("ticket_id") != command.ticket_id:
            raise DevlegateError(
                "requested ticket does not match pending reconciliation"
            )
        product = self._observe_product_generation(str(reconciliation["original_base"]))
        local_head = str(product["local_head"])
        remote_head = str(product["remote_head"])
        if not local_head or local_head != remote_head:
            raise DevlegateError("current product local and remote HEADs do not agree")
        if not product["target_eligible"]:
            raise DevlegateError(
                "current product generation is not eligible for reconciliation"
            )
        if command.onto is None:
            command.onto = local_head
        else:
            command.onto = self._canonical_reconcile_assertion(command.onto, local_head)

    def _canonical_reconcile_assertion(self, value: str, current_head: str) -> str:
        if not value:
            raise DevlegateError("requested reconciliation target is empty")
        if re.fullmatch(r"[0-9a-fA-F]+", value) and len(value) < 4:
            raise DevlegateError(
                "requested reconciliation target is a hexadecimal commit prefix; "
                "use at least 4 characters"
            )
        canonical = resolve_commit(self.repo, value, runner=_git)
        if canonical is None:
            raise DevlegateError(
                "requested reconciliation target is not a valid commit; "
                "use a full SHA or a unique hexadecimal prefix of at least 4 characters"
            )
        if canonical != current_head:
            raise DevlegateError(
                "requested reconciliation target is not the current product HEAD"
            )
        return canonical

    def _validate_reconcile_auto_admission(self, command: OperatorCommand) -> None:
        reconciliation = self._state.get("reconciliation")
        if (
            not isinstance(reconciliation, dict)
            or reconciliation.get("status") != "pending"
        ):
            raise DevlegateError(
                f"ticket {command.ticket_id} has no pending reconciliation"
            )
        if reconciliation.get("ticket_id") != command.ticket_id:
            raise DevlegateError(
                "requested ticket does not match pending reconciliation"
            )
        product = self._observe_product_generation(str(reconciliation["original_base"]))
        if not product["target_eligible"]:
            raise DevlegateError(
                "current product generation is not eligible for reconciliation"
            )
        local_head = str(product["local_head"])
        remote_head = str(product["remote_head"])
        if not local_head or local_head != remote_head:
            raise DevlegateError("current product local and remote HEADs do not agree")
        if command.onto is not None:
            command.onto = self._canonical_reconcile_assertion(command.onto, local_head)
        else:
            command.onto = None if product["stable"] else local_head
        if product["stable"]:
            command.resolution_class = "resume"
        elif reconciliation.get("execution_remote_head") is not None:
            command.resolution_class = "update-base with published-lineage rewrite"
        else:
            command.resolution_class = "update-base"

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
        try:
            product_is_descendant = is_ancestor(
                self.repo, original_base, local_head, runner=_git
            )
        except GitAncestryError as error:
            raise WorkflowBlockedError(
                "automatic zero-delta retry product history could not be observed"
            ) from error
        if not product_is_descendant:
            raise WorkflowBlockedError(
                "automatic zero-delta retry product history is not a descendant"
            )
        if retry.get("product_head") != local_head:
            retry = {**retry, "product_head": local_head}
            self._save_state("idle", automatic_retry=retry)
        return ExecutionAuthorization(ticket_id, "zero_delta_product_drift")

    def _discard_ineligible_zero_delta_retry(self, ticket_store: TicketStore) -> None:
        retry = self._state.get("automatic_retry")
        if not isinstance(retry, dict) or retry.get("status") != ("pending_admission"):
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

        local = resolve_commit(self.control_worktree, "HEAD", runner=_git)
        if local != from_head:
            raise DevlegateError(
                f"control local HEAD changed; expected {from_head}, "
                f"found {local}"
            )
        fetch = fetch_ref(
            self.control_worktree,
            self.remote_name,
            self.control_branch,
            prune=True,
            runner=_git,
        )
        if fetch.returncode:
            raise DevlegateError(
                f"cannot freshly fetch control branch: {fetch.stderr.strip()}"
            )
        remote_ref = f"{self.remote_name}/{self.control_branch}"
        observed_remote = resolve_commit(
            self.control_worktree, remote_ref, runner=_git
        )
        if observed_remote is None:
            raise DevlegateError(f"control remote branch not found: {remote_ref}")
        if observed_remote != to_head:
            raise DevlegateError(
                f"control remote HEAD changed; expected {to_head}, "
                f"found {observed_remote}"
            )
        try:
            histories_are_related = is_ancestor(
                self.control_worktree, from_head, to_head, runner=_git
            ) or is_ancestor(self.control_worktree, to_head, from_head, runner=_git)
        except GitAncestryError as error:
            raise DevlegateError(
                "control history could not be observed; reconciliation is unsafe"
            ) from error
        if histories_are_related:
            raise DevlegateError(
                "control reconciliation is unnecessary; histories are not divergent"
            )

    def _validate_operator_admission(self, command: OperatorCommand) -> None:
        if command.method == "cleanup-workspaces":
            if command.ticket_id != "__workspaces__" or command.onto is None:
                raise DevlegateError("workspace cleanup request identity is incomplete")
            try:
                limit = int(command.onto)
            except ValueError as error:
                raise DevlegateError("workspace cleanup limit is invalid") from error
            if limit < 1:
                raise DevlegateError("workspace cleanup limit must be positive")
            return
        if command.method == "retry":
            self._validate_retry_admission(command.ticket_id, command.force)
            return
        if command.method == "drop":
            self._validate_drop_admission(command.ticket_id, command.execution_id)
            return
        if command.method == "recover":
            if command.execution_id is None or command.onto is None:
                raise DevlegateError(
                    "recover requires an exact execution and observed branch HEAD"
                )
            self._validate_recover_admission(
                command.ticket_id, command.execution_id, command.onto
            )
            return
        if command.method == "reconcile-update-base":
            self._validate_reconcile_admission(command)
            return
        if command.method == "reconcile-auto":
            self._validate_reconcile_auto_admission(command)
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
        resolution_class: str | None = None,
    ) -> dict[str, object]:
        if method == "cleanup-workspaces":
            return {"accepted": True, "ticket_id": ticket_id}
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
        if method == "recover":
            if execution_id is None or onto is None:
                raise DevlegateError(
                    "recover requires an exact execution and observed branch HEAD"
                )
            return {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
                "observed_head": onto,
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
                raise DevlegateError("reconciliation target was not resolved")
            return {"accepted": True, "ticket_id": ticket_id, "onto": onto}
        if method == "reconcile-auto":
            return {
                "accepted": True,
                "ticket_id": ticket_id,
                **({"onto": onto} if onto is not None else {}),
                **(
                    {"resolution_class": resolution_class}
                    if resolution_class is not None
                    else {}
                ),
            }
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
            if (
                not isinstance(receipt, dict)
                or not set(receipt).issubset(
                    {
                        "method",
                        "fingerprint",
                        "ticket_id",
                        "accepted",
                        "execution_id",
                        "onto",
                        "resolution_class",
                        "result",
                        "cohort",
                    }
                )
                or not {"method", "fingerprint", "ticket_id", "accepted"}.issubset(
                    receipt
                )
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
            onto = receipt.get("onto")
            if onto is not None and (not isinstance(onto, str) or not onto):
                raise DevlegateError("invalid mutable request receipt target")
            resolution_class = receipt.get("resolution_class")
            if resolution_class is not None and (
                not isinstance(resolution_class, str) or not resolution_class
            ):
                raise DevlegateError("invalid mutable request receipt resolution")
            result = receipt.get("result")
            if result is not None and not isinstance(result, dict):
                raise DevlegateError("invalid mutable request receipt result")
            cohort = receipt.get("cohort")
            if cohort is not None and (
                not isinstance(cohort, list)
                or any(
                    not isinstance(item, dict)
                    or set(item) != {"ticket_id", "path", "branch", "head"}
                    or not all(
                        isinstance(item[field], str) and item[field]
                        for field in ("ticket_id", "path", "branch", "head")
                    )
                    for item in cohort
                )
            ):
                raise DevlegateError("invalid workspace cleanup cohort")
            return dict(receipt)

    def _record_operator_admission(self, command: OperatorCommand) -> None:
        if not command.mutable:
            return
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
            if command.method == "reconcile-update-base" or (
                command.method == "reconcile-auto" and command.onto is not None
            ):
                if command.onto is None:
                    raise DevlegateError("reconciliation target was not resolved")
                receipt["onto"] = command.onto
            if command.method == "reconcile-auto":
                if command.resolution_class is None:
                    raise DevlegateError("automatic reconciliation was not classified")
                receipt["resolution_class"] = command.resolution_class
            if command.method == "cleanup-workspaces":
                if command.onto is None:
                    raise DevlegateError("workspace cleanup limit is missing")
                previous = receipts.get(command.request_id)
                if isinstance(previous, dict) and isinstance(
                    previous.get("cohort"), list
                ):
                    receipt["cohort"] = previous["cohort"]
                else:
                    receipt["cohort"] = self._workspace_cleanup_cohort(
                        int(command.onto)
                    )
            if command.method == "retry" and command.force:
                self._force_retry_transition(
                    command.ticket_id, command.request_id, receipt, updated
                )
                return
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
            elif command.method == "reconcile-auto":
                current = self._state.get("reconciliation")
                if not isinstance(current, dict):
                    raise DevlegateError("invalid reconciliation state")
                reconciliation = {
                    **current,
                    "resolution": (
                        "resume"
                        if command.resolution_class == "resume"
                        else "update-base"
                    ),
                    "resolution_class": command.resolution_class,
                }
            manual_retry = self._state.get("manual_retry_required")
            clear_manual_retry = (
                command.method == "retry"
                and isinstance(manual_retry, dict)
                and manual_retry.get("ticket_id") == command.ticket_id
                and (
                    self._state.get("execution_id") == manual_retry.get("execution_id")
                    or not self._state.get("execution_id")
                )
                and (
                    self._state.get("execution_base_head")
                    == manual_retry.get("generation")
                    or not self._state.get("execution_base_head")
                )
            )
            self._save_state(
                str(self._state["phase"]),
                mutable_receipts=updated,
                **({"manual_retry_required": None} if clear_manual_retry else {}),
                **(
                    {
                        "operator_recovery": {
                            "ticket_id": command.ticket_id,
                            "execution_id": command.execution_id,
                            "observed_head": command.onto,
                        }
                    }
                    if command.method == "recover"
                    else {}
                ),
                **({"reconciliation": reconciliation} if reconciliation else {}),
            )

    def _workspace_cleanup_cohort(
        self, limit: int, *, rotate: bool = True
    ) -> list[dict[str, str]]:
        """Capture a bounded, independently observed execution-resource set."""
        self._workspace_cleanup_observation_error = None
        registrations = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, "T-1"
        )._registrations()
        work_root = (self.execution_worktree_root / "work").resolve()
        candidates: dict[str, dict[str, str]] = {}
        for path, registration in sorted(
            registrations.items(), key=lambda item: str(item[0])
        ):
            try:
                relative = path.relative_to(work_root)
            except ValueError:
                continue
            if len(relative.parts) != 1 or not is_canonical_ticket_name(
                f"{relative.name}.md"
            ):
                continue
            branch = registration.get("branch")
            head = registration.get("head")
            if (
                not isinstance(branch, str)
                or not branch.startswith("devlegate/work/")
                or not isinstance(head, str)
            ):
                continue
            candidates[relative.name] = {
                "ticket_id": relative.name,
                "path": str(path),
                "branch": branch,
                "head": head,
            }
        local = _git(
            self.repo,
            "for-each-ref",
            "--format=%(refname) %(objectname)",
            "refs/heads/devlegate/work",
            check=False,
        )
        if local.returncode:
            raise WorkflowBlockedError("local execution refs could not be observed")
        for line in local.stdout.splitlines():
            ref, _, head = line.partition(" ")
            prefix = "refs/heads/devlegate/work/"
            if not ref.startswith(prefix) or not _is_git_identity(head):
                continue
            ticket_id = ref[len(prefix) :]
            if not is_canonical_ticket_name(f"{ticket_id}.md"):
                continue
            candidates.setdefault(
                ticket_id,
                {
                    "ticket_id": ticket_id,
                    "path": str(self.execution_worktree_root / "work" / ticket_id),
                    "branch": f"devlegate/work/{ticket_id}",
                    "head": head,
                },
            )
        observed = _git(
            self.repo,
            "ls-remote",
            self.remote_name,
            "refs/heads/devlegate/work/*",
            check=False,
        )
        if observed.returncode:
            # A remote outage is a retryable observation, not proof of absence.
            self._workspace_cleanup_observation_error = (
                observed.stderr.strip()
                or "upstream execution refs could not be observed"
            )
            observed_lines: list[str] = []
        else:
            observed_lines = observed.stdout.splitlines()
        for line in observed_lines:
            fields = line.split()
            if len(fields) != 2 or not _is_git_identity(fields[0]):
                continue
            prefix = "refs/heads/devlegate/work/"
            if not fields[1].startswith(prefix):
                continue
            ticket_id = fields[1][len(prefix) :]
            if not is_canonical_ticket_name(f"{ticket_id}.md"):
                continue
            candidates.setdefault(
                ticket_id,
                {
                    "ticket_id": ticket_id,
                    "path": str(self.execution_worktree_root / "work" / ticket_id),
                    "branch": f"devlegate/work/{ticket_id}",
                    "head": fields[0],
                },
            )
        keys = sorted(candidates)
        if not keys:
            return []
        cursor = self._state.get("workspace_cleanup_cursor")
        if rotate and isinstance(cursor, str) and cursor in keys:
            start = (keys.index(cursor) + 1) % len(keys)
            keys = keys[start:] + keys[:start]
        return [candidates[key] for key in keys[:limit]]

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
                if command.method == "cleanup-workspaces":
                    assert command.onto is not None
                    result = self.cleanup_workspaces(
                        apply=command.force,
                        limit=int(command.onto),
                        targets=(
                            self._mutable_receipt(command.request_id) or {}
                        ).get("cohort"),
                    )
                    command.admission_result = result
                    if command.mutable:
                        self._state["workspace_cleanup"] = {
                            "status": "applied",
                            "limit": int(command.onto),
                            "decisions": result.get("decisions", []),
                        }
                        with self._receipt_lock:
                            receipts = self._state.get("mutable_receipts", {})
                            if not isinstance(receipts, dict):
                                raise DevlegateError(
                                    "invalid mutable request receipt state"
                                )
                            receipt = receipts.get(command.request_id)
                            if not isinstance(receipt, dict):
                                raise DevlegateError(
                                    "workspace cleanup admission receipt is missing"
                                )
                            updated = dict(receipts)
                            updated[command.request_id] = {
                                **receipt,
                                "result": result,
                            }
                        self._save_state(
                            str(self._state["phase"]),
                            mutable_receipts=updated,
                            workspace_cleanup=self._state["workspace_cleanup"],
                        )
                    return
                command.admission_result = self._operator_ack(
                    command.method,
                    command.ticket_id,
                    command.onto,
                    getattr(command, "execution_id", None),
                    command.resolution_class,
                )
        except DevlegateError as error:
            command.admission_error = error
            raise
        except (GitObservationError, GitAncestryError) as error:
            command.admission_error = DevlegateError(
                "service busy; mutable request was not admitted"
            )
            raise command.admission_error from error
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
        lifecycle_intent = self.lifecycle_intent()
        lifecycle = (
            "restarting"
            if lifecycle_intent == "restart"
            else "stopping"
            if lifecycle_intent == "stop"
            else (
                "worker"
                if worker_running
                else ("blocked" if blocked_reason else "ready")
            )
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

    def _refresh_published_status(self) -> None:
        """Refresh the owner-published view without weakening fail-closed reads."""
        try:
            self.status_view()
        except (DevlegateError, OSError, subprocess.CalledProcessError):
            # Keep the last coherent generation when observation cannot be proven.
            return

    def _record_worker_identity(self, identity: WorkerProcessIdentity) -> None:
        self._save_state(
            "agent_running",
            execution_stage="worker-running",
            worker_identity=identity.as_dict(),
        )
        self._refresh_published_status()

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
        if result.returncode:
            self._raise_on_shutdown_interruption(result)
        if check:
            result.check_returncode()
        return result

    def _merge_fast_forward_runtime(
        self, repo: Path, remote_ref: str, expected_old: str, expected_new: str
    ) -> subprocess.CompletedProcess[str]:
        """Recover one admitted product fast-forward at its owning workflow.

        The merge target is the immutable commit admitted in the WAL, never the
        mutable tracking ref.  Recovery is allowed only when the interrupted
        command left the expected checkout identity and clean state intact.
        """
        self._assert_repository_owner()
        expected_branch = self.current_branch
        if repo.resolve() != self.repo.resolve():
            raise DevlegateError(
                "product fast-forward was requested for another checkout"
            )

        def observe_safe_checkout() -> str:
            branch = self._branch_runtime(repo)
            if branch != expected_branch:
                raise DevlegateError(
                    "interrupted fast-forward changed the product checkout branch"
                )
            if self._worktree_root_runtime(repo) != repo.resolve():
                raise DevlegateError(
                    "interrupted fast-forward changed the product worktree identity"
                )
            if self._status_runtime(repo):
                raise DevlegateError(
                    "interrupted fast-forward left the product checkout ambiguous"
                )
            observed = self._resolve_commit_runtime(repo, "HEAD")
            if observed is None:
                raise WorkflowBlockedError("product HEAD could not be resolved")
            return observed

        # Resolve the tracking ref before the mutation so the admitted target is
        # proven current, then merge the object id rather than the mutable ref.
        resolved_target = self._resolve_commit_runtime(repo, remote_ref)
        if resolved_target != expected_new:
            raise DevlegateError(
                "fast-forward target changed before the admitted mutation"
            )
        try:
            result = merge_fast_forward(repo, expected_new, runner=self._git_runtime)
            if result.returncode == 0 and observe_safe_checkout() != expected_new:
                raise DevlegateError(
                    "fast-forward completed without reaching the admitted target"
                )
            return result
        except ShutdownInterrupted:
            observed = observe_safe_checkout()
            if observed == expected_new:
                return subprocess.CompletedProcess(
                    ["git", "merge", "--ff-only", expected_new], 0, "", ""
                )
            if observed != expected_old:
                raise DevlegateError(
                    "interrupted fast-forward changed HEAD to an unexpected revision"
                )
            if (
                self._state.get("phase") != "merge_pending"
                or self._state.get("local_head") != expected_old
                or self._state.get("remote_head") != expected_new
            ):
                raise
            if self._resolve_commit_runtime(repo, remote_ref) != expected_new:
                raise DevlegateError(
                    "fast-forward target changed before interrupted continuation"
                )
            # Only the product WAL coordinates authorize this bounded retry.
            result = merge_fast_forward(repo, expected_new, runner=self._git_runtime)
            if result.returncode == 0 and observe_safe_checkout() != expected_new:
                raise DevlegateError(
                    "fast-forward continuation did not reach the admitted target"
                )
            return result

    def _resolve_commit_runtime(self, repo: Path, revision: str) -> str | None:
        """Resolve one commit through the runtime Git interruption seam."""
        return resolve_commit(repo, revision, runner=self._git_runtime)

    def _branch_runtime(self, repo: Path) -> str | None:
        """Observe the branch through the runtime interruption seam."""
        return branch_name(repo, runner=self._git_runtime)

    def _status_runtime(self, repo: Path) -> str:
        """Observe porcelain status through the runtime interruption seam."""
        return working_tree_status(repo, runner=self._git_runtime)

    def _remote_exists_runtime(self, repo: Path, remote: str) -> bool:
        return remote_exists(repo, remote, runner=self._git_runtime)

    def _remote_exists_for_status(self) -> bool:
        """Keep status diagnostics fail-closed for unreadable Git config."""
        try:
            return remote_exists(self.repo, self.remote_name, runner=_git)
        except GitObservationError:
            return False

    def _ahead_behind_runtime(
        self, repo: Path, local: str, remote: str
    ) -> tuple[int, int]:
        return ahead_behind(repo, local, remote, runner=self._git_runtime)

    def _worktree_root_runtime(self, repo: Path) -> Path:
        return worktree_root(repo, runner=self._git_runtime)

    def _is_ancestor_runtime(
        self, repo: Path, ancestor: str, descendant: str
    ) -> bool:
        """Check ancestry without collapsing Git errors into a negative answer."""
        try:
            return is_ancestor(repo, ancestor, descendant, runner=self._git_runtime)
        except GitAncestryError as error:
            raise WorkflowBlockedError(str(error)) from error

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
        try:
            registered_paths = set(worktree_paths(self.repo, runner=self._git_runtime))
        except GitObservationError as error:
            raise WorkflowBlockedError(str(error)) from error
        if self.control_worktree.resolve() not in registered_paths:
            raise WorkflowBlockedError(
                "control path is not a registered Git worktree for this repository"
            )
        try:
            root = self._worktree_root_runtime(self.control_worktree)
        except GitObservationError as error:
            raise WorkflowBlockedError(str(error)) from error
        if root != self.control_worktree.resolve():
            raise WorkflowBlockedError(
                "control worktree does not resolve to its configured checkout"
            )
        try:
            branch = self._branch_runtime(self.control_worktree)
        except GitObservationError as error:
            raise WorkflowBlockedError(str(error)) from error
        if branch != self.control_branch:
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
        try:
            self.current_branch = self._branch_runtime(self.repo)
        except GitObservationError as error:
            raise DevlegateError(
                f"cannot observe control branch {self.control_branch!r}: {error}"
            ) from error
        if self.current_branch is None:
            if not self.read_only:
                raise DevlegateError("detached HEAD is not supported")
        self.remote_branch = self.remote_branch or self.current_branch or ""
        if not self.read_only and (
            self.current_branch is None or self.current_branch != self.remote_branch
        ):
            raise DevlegateError(
                f"current branch '{self.current_branch}' does not match "
                f"REMOTE_BRANCH '{self.remote_branch}'"
            )
        if not self.read_only and not self._remote_exists_runtime(
            self.repo, self.remote_name
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
        try:
            observed_branch = self._branch_runtime(self.repo)
            observed_head = self._resolve_commit_runtime(self.repo, "HEAD")
            status = self._status_runtime(self.repo)
        except GitObservationError as error:
            raise DevlegateError(
                "cannot verify product checkout after worker execution; "
                "execution isolation cannot be proven"
            ) from error
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
        try:
            return (
                remote_ref(
                    self.repo,
                    self.remote_name,
                    f"refs/heads/{self.control_branch}",
                    runner=self._git_runtime,
                )
                is not None
            )
        except GitObservationError as error:
            raise DevlegateError(
                f"cannot observe control branch {self.control_branch!r}: {error}"
            ) from error

    def control_init_result(self) -> dict[str, object]:
        """Attach an existing control branch or bootstrap a fresh control plane."""
        try:
            if self._control_remote_exists():
                return attach_existing_control(
                    repo=self.repo,
                    control_worktree=self.control_worktree,
                    control_branch=self.control_branch,
                    remote_name=self.remote_name,
                    workflow_paths=self._workflow_paths(),
                    runner=self._git_runtime,
                )
            return bootstrap_control(
                repo=self.repo,
                control_worktree=self.control_worktree,
                control_branch=self.control_branch,
                remote_name=self.remote_name,
                workflow_paths=self._workflow_paths(),
                runner=self._git_runtime,
            )
        except (
            ControlGitWorkflowError,
            GitObservationError,
            GitAncestryError,
        ) as error:
            raise DevlegateError(str(error)) from error

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
        cleanup = state.get("workspace_cleanup")
        if cleanup is not None:
            if not isinstance(cleanup, dict) or cleanup.get("status") not in {
                "initializing",
                "clean",
                "partial",
                "blocked",
                "reconciled",
                "applied",
                # A completed accepted integration records its per-ticket
                # retirement result before the next idle snapshot is saved.
                "retired",
            }:
                raise DevlegateError("invalid workspace cleanup status")
            if "scan_complete" in cleanup and not isinstance(
                cleanup.get("scan_complete"), bool
            ):
                raise DevlegateError("invalid workspace cleanup scan state")
            if "decisions" in cleanup and not isinstance(
                cleanup.get("decisions"), list
            ):
                raise DevlegateError("invalid workspace cleanup decisions")
        cleanup_event = state.get("workspace_cleanup_event")
        if cleanup_event is not None:
            if not isinstance(cleanup_event, dict) or cleanup_event.get(
                "status"
            ) not in {"retired", "blocked", "skipped"}:
                raise DevlegateError("invalid workspace cleanup event")
            if not isinstance(cleanup_event.get("ticket_id"), str):
                raise DevlegateError("invalid workspace cleanup event identity")
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
            if "resolution_class" in reconciliation and reconciliation[
                "resolution_class"
            ] not in {
                "resume",
                "update-base",
                "update-base with published-lineage rewrite",
            }:
                raise DevlegateError("invalid reconciliation resolution class")
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

        def validate_automatic_retry(value: object, *, current: bool) -> None:
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
        validate_automatic_retry(state.get("last_automatic_retry"), current=False)
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
        manual_retry = state.get("manual_retry_required")
        if manual_retry is not None and (
            not isinstance(manual_retry, dict)
            or set(manual_retry)
            != {
                "ticket_id",
                "execution_id",
                "generation",
                "request_id",
            }
            or any(
                not isinstance(manual_retry.get(field), str) or not manual_retry[field]
                for field in ("ticket_id", "execution_id", "generation", "request_id")
            )
        ):
            raise DevlegateError("invalid manual retry marker")
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
        self,
        phase: str,
        *,
        clear_execution: bool = False,
        preserve_resume_required: bool = False,
        clear_pre_worker_generation: bool = False,
        clear_operator_recovery: bool = False,
        **fields: object,
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
            fields_to_clear = (
                "execution_ticket_id",
                "execution_base_head",
                "execution_control_head",
                "execution_branch",
                "execution_path",
                "execution_id",
                "execution_remote_head",
                "execution_start_head",
                "resume_required",
            )
            for field in fields_to_clear:
                if field == "resume_required" and preserve_resume_required:
                    continue
                state.pop(field, None)
        if clear_pre_worker_generation:
            for field in (
                "execution_stage",
                "execution_start_head",
                "execution_interruption_kind",
                "resume_required",
            ):
                state.pop(field, None)
        if clear_operator_recovery:
            state.pop("operator_recovery", None)
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
        try:
            observed_remote = self._resolve_commit_runtime(self.repo, remote_ref)
            remote_head = observed_remote or "<unknown>"
        except GitObservationError:
            remote_head = "<unknown>"
        try:
            merge_base_value = git_merge_base(
                self.repo, local_head, remote_head, runner=_git
            )
            merge_base = merge_base_value or "<none>"
        except GitObservationError:
            merge_base = "<none>"
        try:
            ahead_count, behind_count = self._ahead_behind_runtime(
                self.repo, local_head, remote_head
            )
            ahead = str(ahead_count)
            behind = str(behind_count)
        except GitObservationError:
            ahead = behind = "unknown"
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
        try:
            return sync_control(
                control_worktree=self.control_worktree,
                control_branch=self.control_branch,
                remote_name=self.remote_name,
                state=self._state,
                validate_worktree=self._validate_control_worktree,
                observe_worktree=self._git_observation,
                runner=self._git_runtime,
                resolve=self._resolve_commit_runtime,
                ancestor=self._is_ancestor_runtime,
                accepted_integration=self._observe_accepted_control_integration,
                log=_log,
            )
        except ControlGitWorkflowError as error:
            raise WorkflowBlockedError(str(error)) from error

    def run_iteration(self, intent: IterationIntent | None = None) -> int:
        """Execute exactly one scheduler iteration under host authority."""
        intent = intent or IterationIntent()
        authorization = (
            ExecutionAuthorization(
                intent.retry_ticket_id,
                "forced_retry" if intent.force_retry else "explicit_retry",
            )
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
        persisted_force = self._state.get("force_retry_authorization")
        if (
            authorization is None
            and isinstance(persisted_force, dict)
            and persisted_force.get("status") == "admitted"
            and isinstance(persisted_force.get("ticket_id"), str)
        ):
            authorization = ExecutionAuthorization(
                persisted_force["ticket_id"], "forced_retry"
            )
        if self._stop_requested() and self._state.get("phase") != "merge_pending":
            return 0
        self._workflow_validation_succeeded = False
        recovery = self._state.get("operator_recovery")
        if self._state.get("phase") == "agent_pending" and isinstance(recovery, dict):
            ticket_id = recovery.get("ticket_id")
            execution_id = recovery.get("execution_id")
            observed_head = recovery.get("observed_head")
            if not all(
                isinstance(value, str) and value
                for value in (ticket_id, execution_id, observed_head)
            ):
                raise DevlegateError(
                    "persisted operator recovery identity is incomplete"
                )
            self._recover_unsafe_bound_execution_workspace(
                ticket_id, execution_id, observed_head
            )
            return 0
        branch = self._branch_runtime(self.repo)
        if branch is None:
            raise WorkflowBlockedError(
                f"current checkout is detached; expected branch '{self.current_branch}'"
            )
        actual_branch = branch
        if actual_branch != self.current_branch:
            raise WorkflowBlockedError(
                f"current checkout branch '{actual_branch}' does not match "
                f"expected branch '{self.current_branch}'"
            )
        if (
            self._state.get("phase") == "idle"
            and self._state.get("accepted_integration") is not None
        ):
            plan = self.status_view().plan
            if plan.action == "none":
                _log(f"no worker scheduled: {plan.reason}")
            return self._recover_accepted_integration()
        if self._state.get("phase") == "idle":
            self._reconcile_workspace_backlog()
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
            if classify_stage(self._state.get("execution_stage")) == (
                "checkpoint-publication"
            ):
                self._recover_checkpoint_publication()
                report = self._recover_lifecycle_report()
                return self._complete_execution_lifecycle(report)
            if (
                self._state.get("execution_stage") == "lifecycle"
                and self._state.get("worker_identity") is None
            ):
                dropped_ticket = self._state.get("execution_ticket_id")
                dropped_execution = self._state.get("execution_id")
                if (
                    isinstance(dropped_ticket, str)
                    and isinstance(dropped_execution, str)
                    and self._read_drop_disposition(dropped_ticket, dropped_execution)
                    is not None
                ):
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
        status = self._status_runtime(self.repo)
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
        local_head = self._resolve_commit_runtime(self.repo, "HEAD")
        if local_head is None:
            raise WorkflowBlockedError("local HEAD could not be resolved")
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        state_phase = self._state.get("phase")
        pending_sync = state_phase in {"agent_pending", "merge_pending"}
        pending_agent_execution = state_phase == "agent_pending"
        had_remote_change = False
        local_ahead = False
        fetch = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=self._git_runtime,
        )
        if fetch.returncode:
            _log(f"fetch failed: {fetch.stderr.strip() or 'unknown git error'}")
            return 1
        else:
            remote_head = self._resolve_commit_runtime(self.repo, remote_ref)
            if remote_head is None:
                _log(
                    f"remote branch not found: {remote_ref}; "
                    "git could not resolve the configured remote branch"
                )
                return 1
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
                    persisted_local_is_ancestor = self._is_ancestor_runtime(
                        self.repo, persisted_local_head, local_head
                    )
                    current_local_is_ancestor = self._is_ancestor_runtime(
                        self.repo, local_head, persisted_local_head
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
                target_is_ancestor = self._is_ancestor_runtime(
                    self.repo, target_head, remote_head
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
                    local_is_ancestor = self._is_ancestor_runtime(
                        self.repo, local_head, target_head
                    )
                    remote_is_ancestor = self._is_ancestor_runtime(
                        self.repo, target_head, local_head
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
                    changed_paths = git_changed_paths(
                        self.repo, local_head, target_head, runner=self._git_runtime
                    )
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
                    merge = self._merge_fast_forward_runtime(
                        self.repo, remote_ref, local_head, target_head
                    )
                    if merge.returncode:
                        self._log_sync_failure(local_head, remote_ref, merge.stderr)
                        return 1
                    local_head = self._resolve_commit_runtime(self.repo, "HEAD")
                    if local_head is None:
                        raise WorkflowBlockedError("product HEAD could not be resolved")
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
            elif self._is_ancestor_runtime(self.repo, local_head, remote_head):
                had_remote_change = True
                changed_paths = git_changed_paths(
                    self.repo, local_head, remote_head, runner=self._git_runtime
                )
                if self._stop_before_admission():
                    return 0
                self._save_state(
                    "merge_pending",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                    control_head=control_head,
                )
                merge = self._merge_fast_forward_runtime(
                    self.repo, remote_ref, local_head, remote_head
                )
                if merge.returncode:
                    self._log_sync_failure(local_head, remote_ref, merge.stderr)
                    return 1
                actual_head = self._resolve_commit_runtime(self.repo, "HEAD")
                if actual_head is None:
                    raise WorkflowBlockedError("product HEAD could not be resolved")
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
            elif self._is_ancestor_runtime(self.repo, remote_head, local_head):
                local_ahead = True
                changed_paths = git_changed_paths(
                    self.repo, remote_head, local_head, runner=self._git_runtime
                )
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
        if (
            pending_agent_execution
            and execution_plan.action == "blocked"
            and execution_plan.reason.startswith(
                "bound execution workspace is recoverable:"
            )
        ):
            self._repair_bound_execution_workspace()
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
        self._refresh_published_status()
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
            pending_state = self._ticket_store().by_id[str(pending_ticket)]
            if pending_state.state == "done":
                checkpoint = accepted_integration.get("checkpoint")
            else:
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
            self._refresh_published_status()
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
            self._refresh_published_status()
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
                dependency_blocked = self._dependency_blocked_tickets(ticket_store)
                if (
                    todo_count
                    and ticket_store.tickets
                    and (
                        execution_plan.reason != "no runnable tickets"
                        or dependency_blocked
                    )
                ):
                    if execution_plan.reason != "no runnable tickets":
                        _log(
                            "todo tickets are currently blocked by "
                            f"{execution_plan.reason}"
                        )
                    else:
                        _log(
                            "todo tickets are currently blocked by unfinished "
                            "dependencies"
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
            manual_retry = self._state.get("manual_retry_required")
            if isinstance(manual_retry, dict) and (
                manual_retry.get("ticket_id") != selected_ticket.id
                or not isinstance(failed_for_ticket, dict)
                or failed_for_ticket.get("execution_id")
                != manual_retry.get("execution_id")
                or failed_for_ticket.get("generation") != manual_retry.get("generation")
            ):
                self._save_state("idle", manual_retry_required=None)
                manual_retry = None
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
                    in {"operator_abort", "service_shutdown", "process_loss"}
                    and generation_is_same
                    and manual_retry is None
                ):
                    authorization = ExecutionAuthorization(
                        selected_ticket.id, "automatic_resume"
                    )
                else:
                    if isinstance(manual_retry, dict):
                        reason = "stop --force was admitted"
                    elif not generation_is_same:
                        reason = "the interrupted execution is stale"
                    else:
                        reason = (
                            "the interruption kind is not eligible for automatic "
                            "recovery"
                        )
                    _log(
                        f"interrupted execution for {selected_ticket.id} requires "
                        f"explicit retry because {reason}"
                    )
                    return 0
            if authorization is not None:
                authorized_ticket_id = authorization.ticket_id
                if not (
                    authorization.is_zero_delta_retry or authorization.is_forced_retry
                ):
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
                        not in {"operator_abort", "service_shutdown", "process_loss"}
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
                self._state["execution_base_head"]
                if existing_lineage
                else (
                    self._state.get("force_retry_authorization", {}).get(
                        "execution_base_head", local_head
                    )
                    if authorization is not None and authorization.is_forced_retry
                    else local_head
                )
            )
            execution_remote_head = (
                self._state.get("execution_remote_head")
                if pending_agent_execution
                else (
                    self._state.get("force_retry_authorization", {}).get(
                        "execution_remote_head"
                    )
                    if authorization is not None and authorization.is_forced_retry
                    else self._execution_remote_head(
                        f"devlegate/work/{selected_ticket.id}"
                    )
                )
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
                force_retry_authorization=None,
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
        except ExecutionWorkspaceConflict as error:
            raise WorkspacePreparationBlockedError(str(error)) from error
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
            or (authorization is not None and authorization.is_forced_retry)
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
        self._refresh_published_status()
        worker_returned = False
        try:
            try:
                worker_run = self._workers.run(
                    workspace,
                    prompt,
                    execution_id=execution_id,
                    stop_request=self._stop_event,
                    identity_handler=self._record_worker_identity,
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
            self._refresh_published_status()
        if not worker_run.worker_group_retired:
            raise DevlegateError(
                "execution cgroup remains populated; worker retirement is unproven"
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
        self._refresh_published_status()
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
        self._refresh_published_status()
        _log(
            "execution checkpoint "
            f"{'created' if checkpoint.commit_created else 'not needed'}: "
            f"{checkpoint.after_head[:12]}"
        )
        if self._lifecycle_drain_pending():
            self._publish_service_snapshot(
                lifecycle=(
                    "restarting" if self.lifecycle_intent() == "restart" else "stopping"
                ),
                worker_running=False,
            )
            self._refresh_published_status()
            return 0
        product = self._observe_product_generation(workspace.base_head)
        if product["classification"] == "unobservable":
            reason = (
                "product remote could not be observed; "
                "retrying when connectivity returns"
            )
            self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
            _log(reason)
            return 1
        if product["classification"] == "unsafe-local" and not product.get(
            "remote_head"
        ):
            reason = f"product generation is unsafe-local; {product['reason']}"
            self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)
            _log(reason)
            return 1
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
                self._refresh_published_status()
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
            existing_evidence = resolve_commit(self.repo, evidence_ref, runner=_git)
            if (
                existing_evidence is not None
                and existing_evidence != checkpoint.after_head
            ):
                raise DevlegateError("reconciliation evidence ref is inconsistent")
            pinned = update_ref(
                self.repo, evidence_ref, checkpoint.after_head, runner=_git
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
            self._refresh_published_status()
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
        self._refresh_published_status()
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
        self._refresh_published_status()
        self._save_state(
            "agent_running",
            execution_stage="lifecycle",
            pending_execution_report=report.as_dict(),
            execution_interruption_kind=worker_run.interruption_kind,
        )
        self._refresh_published_status()
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

    def _reconcile_workspace_backlog(self) -> None:
        """Run one low-priority, owner-serialized cleanup pass.

        Cleanup is deliberately best effort.  Its observations and refusals are
        durable diagnostics, while a Git or remote failure must not prevent the
        normal ticket scheduler from making progress.
        """
        try:
            result = self.cleanup_workspaces(apply=True, limit=1)
        except (
            ExecutionWorkspaceError,
            GitAncestryError,
            GitObservationError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            WorkflowBlockedError,
        ) as error:
            self._publish_workspace_cleanup_inventory()
            if not isinstance(self._state.get("workspace_cleanup"), dict):
                self._state["workspace_cleanup"] = {
                    "status": "blocked",
                    "scan_complete": False,
                    "decisions": [
                        {
                            "ticket_id": "__inventory__",
                            "status": "blocked",
                            "reason": str(error) or error.__class__.__name__,
                        }
                    ],
                }
                self._save_state(
                    str(self._state["phase"]),
                    workspace_cleanup=self._state["workspace_cleanup"],
                    workspace_cleanup_cursor=self._state.get(
                        "workspace_cleanup_cursor"
                    ),
                )
                self._refresh_published_status()
            _log(f"automatic workspace reconciliation deferred: {error}")
            return
        decisions = result.get("decisions", [])
        if decisions:
            selected = [
                item.get("ticket_id")
                for item in decisions
                if isinstance(item, dict)
                and isinstance(item.get("ticket_id"), str)
                and item.get("ticket_id") != "__remote__"
            ]
            if selected:
                self._state["workspace_cleanup_cursor"] = selected[-1]
        self._publish_workspace_cleanup_inventory(decisions)

    def _publish_workspace_cleanup_inventory(
        self, last_decisions: list[object] | None = None
    ) -> None:
        """Publish a complete owner-side view after a bounded mutation pass.

        The mutation pass is intentionally small for scheduler fairness.  The
        read-only inventory is separate, so its result cannot be mistaken for
        a global clean result merely because one candidate was processed.
        """
        previous = self._state.get("workspace_cleanup")
        try:
            targets = self._workspace_cleanup_cohort(10000, rotate=False)
            result = self.cleanup_workspaces(
                apply=False,
                limit=max(1, len(targets)),
                targets=targets,
            )
            decisions = result.get("decisions", [])
            blocked = [
                item
                for item in decisions
                if isinstance(item, dict)
                and (
                    item.get("status") == "blocked"
                    or (
                        item.get("status") == "skipped"
                        and item.get("reason")
                        not in {
                            "ticket is not durably done",
                            "ticket is not durably done; execution refs are absent",
                        }
                    )
                )
            ]
            remote_unobserved = any(
                isinstance(item, dict) and item.get("ticket_id") == "__remote__"
                for item in decisions
            )
            status = "clean" if not decisions else ("blocked" if blocked else "partial")
            cleanup: dict[str, object] = {
                "status": status,
                "scan_complete": not remote_unobserved and len(targets) < 10000,
                "inventory": {
                    "candidates": len(targets),
                    "remaining": len(decisions),
                },
                "blocked": len(blocked),
                "remote_observation": "blocked" if remote_unobserved else "observed",
                "decisions": decisions,
            }
            if last_decisions:
                cleanup["last_pass"] = last_decisions
        except (
            ExecutionWorkspaceError,
            GitAncestryError,
            GitObservationError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            WorkflowBlockedError,
        ) as error:
            cleanup = {
                "status": "blocked",
                "scan_complete": False,
                "inventory": {"candidates": 0, "remaining": 0},
                "decisions": [
                    {
                        "ticket_id": "__inventory__",
                        "status": "blocked",
                        "reason": str(error) or error.__class__.__name__,
                    }
                ],
            }
        if cleanup != previous:
            self._state["workspace_cleanup"] = cleanup
            self._save_state(
                str(self._state["phase"]),
                workspace_cleanup_cursor=self._state.get("workspace_cleanup_cursor"),
                workspace_cleanup=cleanup,
            )
            self._refresh_published_status()

    def _recover_accepted_integration(self) -> int:
        try:
            return recover_accepted_integration(self)
        except WorkflowRecoveryError as error:
            raise WorkflowBlockedError(str(error)) from error

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
        return self._is_ancestor_runtime(self.repo, admitted_base, remote_head)

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
        self._retire_execution_refs(report.execution_branch, checkpoint)

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
                "generation": self._state.get("execution_base_head"),
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
            marker = self._state.get("manual_retry_required")
            if (
                isinstance(marker, dict)
                and marker.get("execution_id") == report.execution_id
            ):
                failure["manual_retry_required"] = True
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
        marker = self._state.get("manual_retry_required")
        if (
            isinstance(marker, dict)
            and marker.get("execution_id") == report.execution_id
        ):
            fields["manual_retry_required"] = None
        self._save_state(
            "idle",
            clear_execution=clear_execution,
            **fields,
        )
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)
        self._refresh_published_status()

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
        self._refresh_published_status()

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
        current_control = self._resolve_commit_runtime(self.control_worktree, "HEAD")
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
        observed = self._resolve_commit_runtime(workspace.path, "HEAD")
        if observed != checkpoint_head:
            raise DevlegateError("execution HEAD changed before branch publication")
        observed_remote_head = self._execution_remote_head(workspace.branch)
        if observed_remote_head != expected_remote_head:
            raise DevlegateError(
                "execution branch remote changed during worker execution"
            )
        if expected_remote_head is not None:
            try:
                predecessor_is_ancestor = is_ancestor(
                    workspace.path,
                    expected_remote_head,
                    checkpoint_head,
                    runner=_git,
                )
            except GitAncestryError as error:
                raise DevlegateError(
                    "execution predecessor history could not be observed"
                ) from error
            if not predecessor_is_ancestor:
                raise DevlegateError(
                    "execution predecessor is not an ancestor of checkpoint"
                )
        lease = f"refs/heads/{workspace.branch}:{expected_remote_head or ''}"
        pushed = push_refs(
            workspace.path,
            self.remote_name,
            f"{checkpoint_head}:refs/heads/{workspace.branch}",
            options=(f"--force-with-lease={lease}",),
            runner=_git,
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
        try:
            return remote_ref(
                self.repo,
                self.remote_name,
                f"refs/heads/{branch}",
                runner=self._git_runtime,
            )
        except GitObservationError as error:
            raise DevlegateError(str(error)) from error

    def _accepted_execution_report(self, ticket_id: str) -> ExecutionReport:
        root = self.control_worktree / "executions" / ticket_id
        if not root.is_dir() or root.is_symlink():
            raise WorkflowBlockedError(
                f"accepted ticket {ticket_id} has no durable execution evidence"
            )
        try:
            reports = [
                report
                for report in ExecutionReportStore(self.control_worktree).list(
                    ticket_id
                )
                if report.result.conclusion != "failed"
            ]
        except ExecutionReportError as error:
            raise WorkflowBlockedError(
                f"malformed execution evidence for accepted ticket {ticket_id}"
            ) from error
        accepted_revision = file_history_commit(
            self.control_worktree,
            "HEAD",
            f"{self.accepted_path}/{ticket_id}.md",
            runner=_git,
        )
        if accepted_revision is None:
            raise WorkflowBlockedError("accepted ticket transition is unavailable")
        accepted_path = f"{self.accepted_path}/{ticket_id}.md"
        if (
            read_path(
                self.control_worktree, accepted_revision, accepted_path, runner=_git
            )
            is None
        ):
            previous = parent_commit(
                self.control_worktree, accepted_revision, runner=_git
            )
            if previous is None:
                raise WorkflowBlockedError(
                    "accepted ticket transition parent is unavailable"
                )
            accepted_revision = previous
        parent = parent_commit(self.control_worktree, accepted_revision, runner=_git)
        if parent is None:
            raise WorkflowBlockedError(
                "accepted ticket transition parent is unavailable"
            )
        expected_branch = f"devlegate/work/{ticket_id}"
        remote_head = self._execution_remote_head(expected_branch)
        if remote_head is None:
            # A local-only interrupted removal is still provable from its exact
            # branch SHA; absence of the upstream ref is not absence of evidence.
            remote_head = self._resolve_commit_runtime(self.repo, expected_branch)
        try:
            report = resolve_accepted_execution_report(
                self.control_worktree,
                accepted_revision,
                parent,
                reports,
                todo_path=self.todo_path,
                review_path=self.review_path,
                accepted_path=self.accepted_path,
                expected_branch=expected_branch,
                remote_head=remote_head,
                runner=self._git_runtime,
            )
        except ExecutionProvenanceError as error:
            raise WorkflowBlockedError(str(error)) from error
        checkpoint = report.workspace_head
        assert checkpoint is not None
        if not all(
            len(value) == 40 and all(c in "0123456789abcdef" for c in value.lower())
            for value in (
                checkpoint,
                report.code_base_head,
                report.control_head,
            )
        ):
            raise WorkflowBlockedError(
                "accepted execution evidence has invalid Git identity"
            )
        return report

    def _published_accepted_execution_report(
        self, ticket_id: str, pending: dict[str, object]
    ) -> ExecutionReport:
        """Resolve a completed integration without disposable refs.

        Cleanup can remove both worker refs before the owner records that the
        accepted-integration transaction is complete. In that replay window,
        the canonical done commit, its exact parent, and the immutable report
        are the durable identity; temporary refs must not be required.
        """
        checkpoint = pending.get("checkpoint")
        control_head = pending.get("control_head")
        if not isinstance(checkpoint, str) or not _is_git_identity(checkpoint):
            raise WorkflowBlockedError("accepted integration checkpoint is invalid")
        if not isinstance(control_head, str) or not _is_git_identity(control_head):
            raise WorkflowBlockedError(
                "accepted integration control identity is invalid"
            )
        integration = self._find_accepted_integration_commit(ticket_id)
        if integration is None:
            raise WorkflowBlockedError("published accepted integration is not proven")
        parents = commit_parents(self.control_worktree, integration, runner=_git)
        if parents is None or not parents or parents[0] != control_head:
            raise WorkflowBlockedError(
                "published accepted integration control lineage is not exact"
            )
        self._prove_published_product_checkpoint(checkpoint, integration=integration)
        root = self.control_worktree / "executions" / ticket_id
        if not root.is_dir() or root.is_symlink():
            raise WorkflowBlockedError(
                f"accepted ticket {ticket_id} has no durable execution evidence"
            )
        try:
            reports = [
                report
                for report in ExecutionReportStore(self.control_worktree).list(
                    ticket_id
                )
                if report.result.conclusion != "failed"
                and report.workspace_head == checkpoint
            ]
        except ExecutionReportError as error:
            raise WorkflowBlockedError(
                f"malformed execution evidence for accepted ticket {ticket_id}"
            ) from error
        matches = [
            report
            for report in reports
            if self._accepted_integration_commit_matches_report(
                integration, ticket_id, report
            )
        ]
        if len(matches) != 1:
            raise WorkflowBlockedError(
                "published accepted integration evidence is missing or ambiguous"
            )
        return matches[0]

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
        pending = self._state.get("accepted_integration")
        ticket = self._ticket_store().by_id.get(ticket_id)
        if isinstance(pending, dict) and ticket is not None and ticket.state == "done":
            report = self._published_accepted_execution_report(ticket_id, pending)
        else:
            report = self._accepted_execution_report(ticket_id)
        checkpoint = report.workspace_head
        assert checkpoint is not None
        product_ref = f"refs/heads/{self.remote_branch}"
        product_head = remote_ref(
            self.repo, self.remote_name, product_ref, runner=self._git_runtime
        )
        if product_head is None:
            raise WorkflowBlockedError("configured product branch is unavailable")

        if checkpoint == report.code_base_head:
            if product_head != report.code_base_head:
                raise WorkflowBlockedError(
                    "zero-delta accepted result is stale or product history changed"
                )
            self._retire_zero_delta_execution(report)
            return self._complete_accepted(ticket_id, control.local_head)

        try:
            if product_head == checkpoint:
                checkpoint_is_ancestor = True
                product_is_ancestor = False
            else:
                checkpoint_is_ancestor = is_ancestor(
                    self.repo, checkpoint, product_head, runner=_git
                )
                product_is_ancestor = (
                    False
                    if checkpoint_is_ancestor
                    else is_ancestor(
                        self.repo, product_head, checkpoint, runner=_git
                    )
                )
        except GitAncestryError as error:
            raise WorkflowBlockedError(
                "product history could not be observed during accepted publication"
            ) from error
        if checkpoint_is_ancestor:
            pass
        elif product_is_ancestor:
            pushed = push_refs(
                self.repo, self.remote_name, f"{checkpoint}:{product_ref}", runner=_git
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
        try:
            reobserved_head = remote_ref(
                self.repo, self.remote_name, product_ref, runner=self._git_runtime
            )
            published_checkpoint_is_ancestor = reobserved_head is not None and (
                reobserved_head == checkpoint
                or is_ancestor(
                    self.repo, checkpoint, reobserved_head, runner=self._git_runtime
                )
            )
        except (GitAncestryError, GitObservationError) as error:
            raise WorkflowBlockedError(
                "published product history could not be observed"
            ) from error
        if (
            reobserved_head is None
            or not published_checkpoint_is_ancestor
        ):
            raise WorkflowBlockedError(
                "published product checkpoint could not be proven"
            )
        current = self._resolve_commit_runtime(self.repo, "HEAD")
        status = self._status_runtime(self.repo)
        if status:
            raise WorkflowBlockedError("product checkout is not safely synchronized")
        if current != checkpoint:
            fetch = fetch_ref(
                self.repo, self.remote_name, self.remote_branch, runner=_git
            )
            if fetch.returncode:
                raise WorkflowBlockedError("cannot refresh published product branch")
            sync = merge_fast_forward(
                self.repo, f"{self.remote_name}/{self.remote_branch}", runner=_git
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
        control_head = self._complete_accepted(ticket_id, expected_head)
        cleanup = {
            "ticket_id": ticket_id,
            "status": "retired",
            "reason": "accepted integration proven",
        }
        try:
            self._retire_integrated_workspace(ticket_id, report, control_head)
        except (
            ExecutionWorkspaceError,
            ExecutionReportError,
            GitAncestryError,
            GitObservationError,
            OSError,
            subprocess.SubprocessError,
            ValueError,
            WorkflowBlockedError,
        ) as error:
            # Publication is already durable. Workspace retention is optional and
            # must never make an accepted integration pending again.
            cleanup = {
                "ticket_id": ticket_id,
                "status": "blocked",
                "reason": str(error) or error.__class__.__name__,
            }
            _log(
                f"workspace cleanup blocked for {ticket_id}: {cleanup['reason']}"
            )
        # Keep this per-ticket event separate from the aggregate inventory
        # consumed by `devlegate clean`; the next owner pass may still have a
        # large backlog after this integration.
        self._state["workspace_cleanup_event"] = cleanup
        return control_head

    def _retire_integrated_workspace(
        self, ticket_id: str, report: ExecutionReport, integration_head: str
    ) -> None:
        """Retire only the checkout proven by the accepted-to-done commit."""
        if report.ticket_id != ticket_id or report.workspace_head is None:
            raise WorkflowBlockedError(
                "accepted execution workspace provenance is incomplete"
            )
        parents = commit_parents(self.control_worktree, integration_head, runner=_git)
        if (
            parents is None
            or not parents
            or not self._accepted_integration_commit_is_exact(
                integration_head, ticket_id, parents[0]
            )
            or not self._accepted_integration_commit_matches_report(
                integration_head, ticket_id, report
            )
        ):
            raise WorkflowBlockedError("accepted integration provenance is not exact")
        ticket = self._ticket_store().by_id.get(ticket_id)
        if ticket is None or ticket.state != "done":
            raise WorkflowBlockedError("ticket is not durably done")
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        expected_path = Path(report.execution_path).absolute()
        if expected_path != manager.path:
            raise WorkflowBlockedError("accepted execution workspace path is not exact")
        if report.execution_branch != manager.branch:
            raise WorkflowBlockedError(
                "accepted execution workspace branch is not exact"
            )
        if self._state.get("worker_identity") is not None or self._state.get(
            "reconciliation"
        ) is not None or self._state.get("automatic_retry") is not None:
            raise WorkflowBlockedError("accepted execution still has live dependencies")
        pending = self._state.get("accepted_integration")
        if isinstance(pending, dict) and pending.get("ticket_id") != ticket_id:
            raise WorkflowBlockedError("another accepted integration is pending")
        registrations = manager._registrations()
        registration = registrations.get(manager.path.resolve())
        if registration is None:
            if manager.path.exists() or any(
                item.get("branch") == manager.branch for item in registrations.values()
            ):
                raise WorkflowBlockedError(
                    "accepted execution workspace registration is ambiguous"
                )
            _log(f"workspace already retired: {ticket_id}")
            self._retire_execution_refs(report.execution_branch, report.workspace_head)
            return
        workspace = manager._validate_existing(registration, report.workspace_head)
        manager.retire(workspace, report.workspace_head)
        self._retire_execution_refs(report.execution_branch, report.workspace_head)
        _log(f"retired workspace {ticket_id} at {report.workspace_head[:12]}")

    def _retire_execution_refs(self, branch: str, expected_head: str) -> None:
        """Release only exact, already-published disposable execution refs."""
        if not branch.startswith("devlegate/work/") or not _is_git_identity(
            expected_head
        ):
            raise WorkflowBlockedError("execution ref identity is not canonical")
        local_ref = f"refs/heads/{branch}"
        local_head = self._resolve_commit_runtime(self.repo, local_ref)
        manager = ExecutionWorkspaceManager(
            self.repo,
            self.execution_worktree_root,
            branch.removeprefix("devlegate/work/"),
        )
        try:
            registrations = manager._registrations()
        except (OSError, subprocess.SubprocessError) as error:
            raise WorkflowBlockedError(
                f"cannot verify worktree occupancy before retiring {branch}: {error}"
            ) from error
        occupied = [
            path
            for path, registration in registrations.items()
            if registration.get("branch") == branch
        ]
        if occupied:
            raise WorkflowBlockedError(
                f"execution ref {branch} is attached to worktree {occupied[0]}"
            )
        if local_head is not None:
            if local_head != expected_head:
                raise WorkflowBlockedError("local execution ref changed before removal")
            # Recheck the exact observed head before using Git's safe branch
            # deletion command.  Unlike raw update-ref deletion, this refuses
            # branches attached to any registered worktree.
            if self._resolve_commit_runtime(self.repo, local_ref) != expected_head:
                raise WorkflowBlockedError("local execution ref changed before removal")
            removed = delete_branch_if_unoccupied(
                self.repo, branch, runner=self._git_runtime
            )
            if removed.returncode:
                raise WorkflowBlockedError(
                    removed.stderr.strip() or "cannot retire local execution ref"
                )
            if self._resolve_commit_runtime(self.repo, local_ref) is not None:
                raise WorkflowBlockedError("local execution ref remains after removal")
        remote_head = remote_ref(
            self.repo,
            self.remote_name,
            f"refs/heads/{branch}",
            runner=self._git_runtime,
        )
        if remote_head is None:
            return
        if remote_head != expected_head:
            raise WorkflowBlockedError("remote execution ref changed before removal")
        removed = push_refs(
            self.repo,
            self.remote_name,
            f":refs/heads/{branch}",
            options=(f"--force-with-lease=refs/heads/{branch}:{expected_head}",),
            runner=self._git_runtime,
        )
        if removed.returncode:
            observed = remote_ref(
                self.repo,
                self.remote_name,
                f"refs/heads/{branch}",
                runner=self._git_runtime,
            )
            if observed is not None:
                raise WorkflowBlockedError(
                    removed.stderr.strip() or "cannot retire remote execution ref"
                )
        if remote_ref(
            self.repo,
            self.remote_name,
            f"refs/heads/{branch}",
            runner=self._git_runtime,
        ) is not None:
            raise WorkflowBlockedError("remote execution ref remains after removal")

    def cleanup_workspaces(
        self,
        *,
        apply: bool = False,
        limit: int = 100,
        targets: list[dict[str, str]] | None = None,
    ) -> dict[str, object]:
        """List, or apply, a bounded cleanup of proven done-ticket worktrees."""
        if limit < 1:
            raise ValueError("workspace cleanup limit must be positive")
        registrations = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, "T-1"
        )._registrations()
        decisions: list[dict[str, object]] = []
        if targets is None:
            targets = self._workspace_cleanup_cohort(limit)
        else:
            # An admitted apply carries its immutable cohort.  Never let a
            # malformed or stale receipt turn one bounded pass into an
            # unbounded sweep.
            targets = list(targets)
            if len(targets) > limit:
                raise WorkflowBlockedError(
                    "workspace cleanup cohort exceeds the admitted limit"
                )
        if self._workspace_cleanup_observation_error:
            decisions.append(
                {
                    "ticket_id": "__remote__",
                    "status": "blocked",
                    "reason": self._workspace_cleanup_observation_error,
                }
            )
        for target in targets:
            if not all(
                isinstance(target.get(field), str) and target[field]
                for field in ("ticket_id", "path", "branch", "head")
            ):
                decisions.append(
                    {
                        "ticket_id": str(target.get("ticket_id", "<unknown>")),
                        "status": "skipped",
                        "reason": "workspace cleanup target identity is invalid",
                    }
                )
                continue
            ticket_id = target["ticket_id"]
            path = Path(target["path"]).absolute()
            registration = registrations.get(path.resolve())
            if registration is None:
                if path.exists() or any(
                    item.get("branch") == target["branch"]
                    for item in registrations.values()
                ):
                    decisions.append(
                        {
                            "ticket_id": ticket_id,
                            "status": "skipped",
                            "reason": "workspace registration changed or is ambiguous",
                        }
                    )
                else:
                    try:
                        ticket = self._ticket_store().by_id.get(ticket_id)
                        if ticket is None or ticket.state != "done":
                            raise WorkflowBlockedError("ticket is not durably done")
                        report = self._accepted_execution_report(ticket_id)
                        if report.workspace_head != target["head"]:
                            raise WorkflowBlockedError(
                                "execution ref does not match accepted checkpoint"
                            )
                        integration = self._find_accepted_integration_commit(
                            ticket_id, report
                        )
                        if integration is None:
                            raise WorkflowBlockedError(
                                "accepted integration is not proven"
                            )
                        self._prove_published_product_checkpoint(
                            report.workspace_head, integration=integration
                        )
                        if apply:
                            self._retire_execution_refs(
                                report.execution_branch, report.workspace_head
                            )
                        decisions.append(
                            {
                                "ticket_id": ticket_id,
                                "status": "retired" if apply else "eligible",
                                "reason": "workspace absent; execution refs proven",
                            }
                        )
                    except (
                        ExecutionReportError,
                        GitAncestryError,
                        GitObservationError,
                        OSError,
                        subprocess.SubprocessError,
                        ValueError,
                        WorkflowBlockedError,
                    ) as error:
                        decisions.append(
                            {
                                "ticket_id": ticket_id,
                                "status": "skipped",
                                "reason": str(error),
                            }
                        )
                continue
            manager = ExecutionWorkspaceManager(
                self.repo, self.execution_worktree_root, ticket_id
            )
            try:
                ticket = self._ticket_store().by_id.get(ticket_id)
                if ticket is None or ticket.state != "done":
                    raise WorkflowBlockedError("ticket is not durably done")
                # Re-run the same accepted-transition evidence proof used by
                # normal integration. A report merely present on disk is not
                # sufficient historical provenance.
                report = self._accepted_execution_report(ticket_id)
                if not (
                    report.execution_branch == manager.branch
                    and Path(report.execution_path).absolute() == manager.path
                    and report.workspace_head == target["head"]
                    and registration.get("branch") == target["branch"]
                    and registration.get("head") == target["head"]
                ):
                    raise WorkflowBlockedError(
                        "workspace provenance does not match accepted evidence"
                    )
                integration = self._find_accepted_integration_commit(ticket_id, report)
                if integration is None:
                    raise WorkflowBlockedError("accepted integration is not proven")
                self._prove_published_product_checkpoint(
                    report.workspace_head, integration=integration
                )
                if self._state.get("worker_identity") is not None or any(
                    self._state.get(key) is not None
                    for key in (
                        "reconciliation",
                        "automatic_retry",
                        "accepted_integration",
                    )
                ):
                    raise WorkflowBlockedError(
                        "runtime still has execution dependencies"
                    )
                decision = {
                    "ticket_id": ticket_id,
                    "status": "eligible",
                    "reason": "accepted integration proven",
                }
                if apply:
                    manager.retire(
                        manager._validate_existing(registration, report.workspace_head),
                        report.workspace_head,
                    )
                    self._retire_execution_refs(
                        report.execution_branch, report.workspace_head
                    )
                    decision["status"] = "retired"
                decisions.append(decision)
            except (
                ExecutionWorkspaceError,
                ExecutionReportError,
                GitAncestryError,
                GitObservationError,
                OSError,
                subprocess.SubprocessError,
                ValueError,
                WorkflowBlockedError,
            ) as error:
                decisions.append(
                    {"ticket_id": ticket_id, "status": "skipped", "reason": str(error)}
                )
        return {"dry_run": not apply, "limit": limit, "decisions": decisions}

    def _prove_published_product_checkpoint(
        self, checkpoint: str | None, *, integration: str | None = None
    ) -> None:
        """Require the exact integrated checkpoint to be in published product."""
        if not isinstance(checkpoint, str) or not _is_git_identity(checkpoint):
            raise WorkflowBlockedError("accepted product checkpoint is invalid")
        if integration is not None:
            message = _git(
                self.control_worktree,
                "show",
                "-s",
                "--format=%B",
                integration,
                check=False,
            )
            if message.returncode:
                raise WorkflowBlockedError(
                    "canonical integration checkpoint could not be observed"
                )
            checkpoint_line = f"Devlegate-Checkpoint: {checkpoint}"
            lines = {line.strip() for line in message.stdout.splitlines()}
            if (
                any(line.startswith("Devlegate-") for line in lines)
                and checkpoint_line not in lines
            ):
                raise WorkflowBlockedError(
                    "canonical integration does not name the accepted checkpoint"
                )
        try:
            product_head = remote_ref(
                self.repo,
                self.remote_name,
                f"refs/heads/{self.remote_branch}",
                runner=self._git_runtime,
            )
            if product_head is None or not (
                product_head == checkpoint
                or is_ancestor(
                    self.repo, checkpoint, product_head, runner=self._git_runtime
                )
            ):
                raise WorkflowBlockedError(
                    "accepted product checkpoint is not published"
                )
        except (GitAncestryError, GitObservationError) as error:
            raise WorkflowBlockedError(
                "published product history could not be observed"
            ) from error

    def _find_accepted_integration_commit(
        self, ticket_id: str, report: ExecutionReport | None = None
    ) -> str | None:
        result = _git(
            self.control_worktree,
            "log",
            "--format=%H",
            "HEAD",
            "--",
            f"{self.done_path}/{ticket_id}.md",
            check=False,
        )
        if result.returncode:
            raise WorkflowBlockedError(
                "canonical control history could not be observed"
            )
        for value in result.stdout.splitlines():
            commit = value.strip()
            parents = commit_parents(self.control_worktree, commit, runner=_git)
            if (
                parents
                and self._accepted_integration_commit_is_exact(
                    commit, ticket_id, parents[0]
                )
                and (
                    report is None
                    or self._accepted_integration_commit_matches_report(
                        commit, ticket_id, report
                    )
                )
            ):
                return commit
        return None

    def _accepted_integration_commit_matches_report(
        self, commit: str, ticket_id: str, report: ExecutionReport
    ) -> bool:
        """Bind a done transition to the exact accepted execution generation."""
        if report.ticket_id != ticket_id or not _is_git_identity(report.control_head):
            return False
        parents = commit_parents(self.control_worktree, commit, runner=_git)
        if parents is None or not parents:
            return False
        try:
            if not is_ancestor(
                self.control_worktree,
                report.control_head,
                parents[0],
                runner=self._git_runtime,
            ):
                return False
        except (GitAncestryError, GitObservationError):
            raise WorkflowBlockedError(
                "accepted execution control lineage could not be observed"
            )
        shown = _git(
            self.control_worktree,
            "show",
            "-s",
            "--format=%B",
            commit,
            check=False,
        )
        if shown.returncode:
            raise WorkflowBlockedError(
                "canonical integration commit message could not be observed"
            )
        lines = {line.strip() for line in shown.stdout.splitlines()}
        modern = {
            f"Devlegate-Execution: {report.execution_id}",
            f"Devlegate-Checkpoint: {report.workspace_head}",
        }
        if modern.issubset(lines):
            return True
        # TASK-001 and other early integrations predate trailers.  Exact parent
        # identity plus the report's immutable checkpoint is the stronger
        # equivalent proof available for those commits; a merely matching ticket
        # name is never sufficient.
        return (
            not any(line.startswith("Devlegate-Execution:") for line in lines)
            and not any(line.startswith("Devlegate-Checkpoint:") for line in lines)
            and parents[0] == report.control_head
        )

    def _complete_accepted(self, ticket_id: str, expected_head: str) -> str:
        current = self._resolve_commit_runtime(self.control_worktree, "HEAD")
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
                push = push_refs(
                    self.control_worktree,
                    self.remote_name,
                    f"{commit}:refs/heads/{self.control_branch}",
                    runner=_git,
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
        add_all(self.control_worktree, option="-A", runner=_git)
        commit = git_commit(
            self.control_worktree,
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
            runner=_git,
        )
        if commit.returncode:
            raise DevlegateError(
                commit.stderr.strip() or "cannot create control transition"
            )
        commit_head = self._resolve_commit_runtime(self.control_worktree, "HEAD")
        if commit_head is None:
            raise WorkflowBlockedError("created control transition has no HEAD")
        if not self._accepted_integration_commit_is_exact(
            commit_head, ticket_id, expected_head
        ):
            raise WorkflowBlockedError(
                "created accepted integration commit is not exact"
            )
        push = push_refs(
            self.control_worktree,
            self.remote_name,
            f"{commit_head}:refs/heads/{self.control_branch}",
            runner=_git,
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
            remote_head = remote_ref(
                self.control_worktree,
                self.remote_name,
                f"refs/heads/{self.control_branch}",
                runner=self._git_runtime,
            )
            if remote_head is None:
                return "ambiguous", None
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
        try:
            descendants = commit_range(
                self.control_worktree,
                f"{expected_parent}..{remote_head}",
                reverse=True,
                runner=self._git_runtime,
            )
        except GitObservationError:
            return "ambiguous", None
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
        parents = commit_parents(self.control_worktree, commit, runner=_git)
        if parents is None or not parents or parents[0] != expected_parent:
            return False
        expected = {
            f"D\t{self.accepted_path}/{ticket_id}.md",
            f"A\t{self.done_path}/{ticket_id}.md",
        }
        try:
            names = commit_changes(
                self.control_worktree, commit, runner=self._git_runtime
            )
        except GitObservationError:
            return False
        if set(names) != expected:
            return False
        before = path_object(
            self.control_worktree,
            parents[0],
            f"{self.accepted_path}/{ticket_id}.md",
            runner=self._git_runtime,
        )
        after = path_object(
            self.control_worktree,
            commit,
            f"{self.done_path}/{ticket_id}.md",
            runner=self._git_runtime,
        )
        return before is not None and before == after

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
        object_id = resolve_object(self.repo, ref, runner=_git)
        if object_id is None:
            return None
        object = read_object(self.repo, object_id, runner=_git)
        if object is None or object[0] != "blob":
            raise DevlegateError("drop disposition ref does not point to a blob")
        try:
            value = json.loads(object[1])
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
        blob = hash_object(self.repo, encoded, runner=_git)
        if blob.returncode or not blob.stdout.strip():
            raise DevlegateError("cannot persist drop disposition intent")
        updated = update_ref(
            self.repo,
            self._drop_disposition_ref(report.ticket_id, report.execution_id),
            blob.stdout.strip(),
            runner=_git,
        )
        if updated.returncode:
            reread = self._read_drop_disposition(report.ticket_id, report.execution_id)
            if reread != payload:
                raise DevlegateError("cannot publish drop disposition intent")

    def _pin_drop_evidence(self, report: ExecutionReport) -> dict[str, object]:
        checkpoint = report.workspace_head
        if not isinstance(checkpoint, str) or not checkpoint:
            raise DevlegateError("drop execution checkpoint identity is missing")
        evidence_ref = self._drop_evidence_ref(report.ticket_id, report.execution_id)
        observed = resolve_commit(self.repo, evidence_ref, runner=_git)
        if observed is not None and observed != checkpoint:
            raise DevlegateError("drop execution evidence ref is inconsistent")
        if observed is None:
            pinned = update_ref(self.repo, evidence_ref, checkpoint, runner=_git)
            if pinned.returncode:
                reread = resolve_commit(self.repo, evidence_ref, runner=_git)
                if reread != checkpoint:
                    raise DevlegateError("cannot pin drop execution evidence")
        payload = self._drop_disposition_payload(report, checkpoint)
        existing = self._read_drop_disposition(report.ticket_id, report.execution_id)
        if existing is not None:
            if existing.get("disposition") == "dropped" and existing != payload:
                raise DevlegateError("drop disposition evidence is inconsistent")
            if existing.get("disposition") not in {"dropping", "dropped"}:
                raise DevlegateError("drop disposition evidence is inconsistent")
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n"
        blob = hash_object(self.repo, encoded, runner=_git)
        if blob.returncode or not blob.stdout.strip():
            raise DevlegateError("cannot persist drop disposition evidence")
        disposition_ref = self._drop_disposition_ref(
            report.ticket_id, report.execution_id
        )
        updated = update_ref(
            self.repo, disposition_ref, blob.stdout.strip(), runner=_git
        )
        if updated.returncode:
            reread = self._read_drop_disposition(report.ticket_id, report.execution_id)
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
        evidence = resolve_commit(
            self.repo,
            self._drop_evidence_ref(ticket_id, execution_id),
            runner=_git,
        )
        if evidence != payload.get("checkpoint"):
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
            evidence = resolve_commit(
                self.repo,
                self._drop_evidence_ref(ticket_id, execution_id),
                runner=_git,
            )
            if evidence != report.workspace_head:
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
            deleted = push_refs(
                self.repo,
                self.remote_name,
                f":refs/heads/{report.execution_branch}",
                options=(
                    f"--force-with-lease=refs/heads/{report.execution_branch}:{expected}",
                ),
                runner=_git,
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
        local_head = self._resolve_commit_runtime(self.repo, local_ref)
        if local_head is not None and local_head != expected:
            raise DevlegateError("local execution branch changed before drop")
        if local_head is not None:
            removed = delete_branch(self.repo, report.execution_branch, runner=_git)
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
        try:
            branch = self._branch_runtime(self.repo)
            local_head = self._resolve_commit_runtime(self.repo, "HEAD") or ""
            dirty = bool(self._status_runtime(self.repo))
        except GitObservationError:
            branch = None
            local_head = ""
            dirty = True
        fetch = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=_git,
        )
        remote_head = (
            self._resolve_commit_runtime(
                self.repo, f"{self.remote_name}/{self.remote_branch}"
            )
            if fetch.returncode == 0
            else None
        ) or ""
        branch_ok = branch == self.current_branch
        observation = []
        if not branch_ok:
            observation.append("wrong product branch or detached checkout")
        if dirty:
            observation.append("product checkout is dirty")
        remote_observable = bool(fetch.returncode == 0 and remote_head)
        if not remote_observable:
            observation.append("product remote could not be observed")
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
        if not branch_ok or dirty or (local_head and local_head != admitted_head):
            classification = "unsafe-local"
        elif not local_head or not remote_observable:
            classification = "unobservable"
        elif stable:
            classification = "stable"
        else:
            classification = "changed"
        return {
            "classification": classification,
            "stable": stable,
            "branch": branch,
            "local_head": local_head,
            "remote_head": remote_head,
            "dirty": dirty,
            "target_eligible": target_eligible,
            "reason": "; ".join(observation) if observation else "stable",
        }

    def _persist_product_reconciliation(
        self,
        report: ExecutionReport,
        product: dict[str, object],
        execution_remote_head: object,
    ) -> None:
        product_remote_head = product["remote_head"]
        if not isinstance(product_remote_head, str) or not product_remote_head:
            raise WorkflowBlockedError(
                "changed product lacks an observed remote identity"
            )
        observed_product = (
            product["local_head"]
            if product["local_head"] != report.code_base_head
            else product_remote_head
        )
        if not isinstance(observed_product, str) or not observed_product:
            raise WorkflowBlockedError("changed product lacks an observed identity")
        evidence_ref = self._reconciliation_evidence_ref(
            report.ticket_id, report.execution_id
        )
        existing_evidence = resolve_commit(self.repo, evidence_ref, runner=_git)
        if existing_evidence is not None and existing_evidence != report.workspace_head:
            raise WorkflowBlockedError("reconciliation evidence ref is inconsistent")
        pinned = update_ref(
            self.repo, evidence_ref, str(report.workspace_head), runner=_git
        )
        if pinned.returncode:
            raise WorkflowBlockedError(
                "cannot pin reconciliation worker checkpoint evidence"
            )
        reconciliation = {
            "status": "pending",
            "reason": str(product["reason"]),
            "resolution": None,
            "ticket_id": report.ticket_id,
            "execution_id": report.execution_id,
            "original_base": report.code_base_head,
            "observed_product": observed_product,
            "worker_checkpoint": report.workspace_head,
            "execution_remote_head": execution_remote_head,
            "product_remote_head": product_remote_head,
            "control_head": str(self._state["execution_control_head"]),
            "execution_branch": report.execution_branch,
            "execution_path": report.execution_path,
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
            handled_remote_head=product_remote_head,
            handled_control_head=str(self._state["control_head"]),
            reconciliation=reconciliation,
        )

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
        product_fetch = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=_git,
        )
        product_ref = f"{self.remote_name}/{self.remote_branch}"
        observed_product = resolve_commit(self.repo, product_ref, runner=_git)
        if product_fetch.returncode or observed_product != remote_head:
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

    def _execution_workspace_for_rewrite_recovery(
        self, manager: ExecutionWorkspaceManager
    ) -> ExecutionWorkspace:
        """Observe a staged rewrite without validating its persisted base yet."""
        expected_path = manager.path.resolve()
        registrations = manager._registrations()
        registration = registrations.get(expected_path)
        if registration is None or registration.get("branch") != manager.branch:
            raise WorkflowBlockedError(
                "cannot validate staged execution rewrite worktree binding"
            )
        try:
            common = common_dir(manager.path, runner=_git)
            root = worktree_root(manager.path, runner=_git)
            expected_common = common_dir(self.repo, runner=_git)
            observed_branch = branch_name(manager.path, runner=_git)
        except GitObservationError as error:
            raise WorkflowBlockedError(str(error)) from error
        if (
            common != expected_common
            or root != manager.path.resolve()
            or observed_branch != manager.branch
        ):
            raise WorkflowBlockedError(
                "staged execution rewrite worktree binding is invalid"
            )
        head_value = resolve_commit(manager.path, "HEAD", runner=_git)
        status_value = working_tree_status(manager.path, runner=_git)
        if (
            head_value is None
            or registration.get("head") != head_value
        ):
            raise WorkflowBlockedError(
                "cannot observe staged execution rewrite worktree"
            )
        return ExecutionWorkspace(
            ticket_id=manager.ticket_id,
            branch=manager.branch,
            path=manager.path,
            head=head_value,
            base_head=str(self._state.get("execution_base_head", "")),
            dirty=bool(status_value),
        )

    def _checkpoint_commit_is_exact(
        self, workspace: ExecutionWorkspace, commit: str, start_head: str
    ) -> bool:
        parent = parent_commit(workspace.path, commit, runner=self._git_runtime)
        if parent != start_head:
            return False
        parents = commit_parents(workspace.path, commit, runner=self._git_runtime)
        if parents is None or len(parents) != 1:
            return False
        try:
            lineage = commit_range(
                workspace.path,
                f"{start_head}..{commit}",
                reverse=True,
                runner=self._git_runtime,
            )
        except GitObservationError:
            return False
        if lineage != (commit,):
            return False
        return not workspace.dirty

    def _recover_checkpoint_publication(self) -> None:
        try:
            recover_checkpoint_publication(self)
        except WorkflowRecoveryError as error:
            raise WorkflowBlockedError(str(error)) from error

    def _recover_lifecycle_report(self) -> ExecutionReport:
        try:
            return recover_lifecycle_report(self)
        except WorkflowRecoveryError as error:
            raise WorkflowBlockedError(str(error)) from error

    def _lifecycle_commit_is_exact(
        self,
        commit: str,
        report: ExecutionReport,
        *,
        apply_conclusion: bool = True,
    ) -> bool:
        return lifecycle_commit_is_exact(
            self.control_worktree,
            commit,
            report,
            todo_path=self.todo_path,
            review_path=self.review_path,
            runner=self._git_runtime,
            apply_conclusion=apply_conclusion,
        )

    def _prove_unpublished_lifecycle_lineage(
        self,
        commit: str,
        remote_head: str,
        report: ExecutionReport,
        *,
        apply_conclusion: bool = True,
    ) -> None:
        parents = commit_parents(self.control_worktree, commit, runner=_git)
        if parents is None or len(parents) != 1:
            raise WorkflowBlockedError("unpublished lifecycle parent is unavailable")
        parent_head = parents[0]
        try:
            parent_on_remote = is_ancestor(
                self.control_worktree, parent_head, remote_head, runner=_git
            )
        except GitAncestryError as error:
            raise WorkflowBlockedError(str(error)) from error
        if not parent_on_remote:
            raise WorkflowBlockedError(
                "unpublished lifecycle parent is not on the fresh remote lineage"
            )
        lineage = commit_range(
            self.control_worktree,
            f"{parent_head}..{commit}",
            reverse=True,
            runner=self._git_runtime,
        )
        if lineage != (commit,):
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
        try:
            history = history_commits(
                self.control_worktree, remote_head, path, runner=self._git_runtime
            )
        except GitObservationError:
            return None
        for commit in history:
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
        try:
            candidate_descends = is_ancestor(
                self.control_worktree, control_head, candidate, runner=_git
            )
        except GitAncestryError as error:
            raise WorkflowBlockedError(str(error)) from error
        if not candidate_descends:
            raise WorkflowBlockedError(
                "control history diverged from the execution admission generation"
            )
        original_path = f"{self.todo_path}/{ticket_id}.md"
        original = read_path(
            self.control_worktree, control_head, original_path, runner=self._git_runtime
        )
        current = read_path(
            self.control_worktree, candidate, original_path, runner=self._git_runtime
        )
        if original is None or current is None or original != current:
            raise WorkflowBlockedError(
                "control descendant changed the active ticket; lifecycle replay refused"
            )
        original_lines = original.splitlines(keepends=True)
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
        if apply_conclusion and report.result.conclusion != "failed":
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
        if apply_conclusion and report.result.conclusion != "failed":
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
        add_all(self.control_worktree, option="-A", runner=_git)
        committed = git_commit(
            self.control_worktree,
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
            runner=_git,
        )
        if committed.returncode:
            raise DevlegateError(
                committed.stderr.strip() or "cannot create control-plane commit"
            )
        return self._resolve_commit_runtime(self.control_worktree, "HEAD") or ""

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
            status = self._status_runtime(self.control_worktree)
            if status:
                raise WorkflowBlockedError("control working tree is dirty")
            fetched = fetch_ref(
                self.control_worktree,
                self.remote_name,
                self.control_branch,
                prune=True,
                runner=_git,
            )
            if fetched.returncode:
                raise WorkflowBlockedError(
                    "cannot freshly observe control remote before lifecycle: "
                    f"{fetched.stderr.strip() or 'unknown git error'}"
                )
            remote_ref = f"{self.remote_name}/{self.control_branch}"
            remote_head = resolve_commit(
                self.control_worktree, remote_ref, runner=_git
            )
            if remote_head is None:
                raise WorkflowBlockedError("control remote branch is unavailable")
            local_head = resolve_commit(self.control_worktree, "HEAD", runner=_git)
            if local_head is None:
                raise WorkflowBlockedError("control HEAD is unavailable")
            existing = self._remote_lifecycle_commit(
                remote_head, report, apply_conclusion=apply_conclusion
            )
            if existing is not None:
                lifecycle_parent = parent_commit(
                    self.control_worktree, existing, runner=_git
                )
                if lifecycle_parent is None:
                    raise WorkflowBlockedError(
                        "published lifecycle parent is unavailable"
                    )
                self._prove_control_descendant(lifecycle_parent, report)
                if local_head != remote_head:
                    try:
                        local_control_is_ancestor = is_ancestor(
                            self.control_worktree,
                            local_head,
                            remote_head,
                            runner=_git,
                        )
                    except GitAncestryError as error:
                        raise WorkflowBlockedError(
                            "control history could not be observed during "
                            "lifecycle replay"
                        ) from error
                    if not local_control_is_ancestor:
                        raise WorkflowBlockedError(
                            "local control history diverges from published lifecycle"
                        )
                    else:
                        merge = merge_fast_forward(
                            self.control_worktree, remote_ref, runner=_git
                        )
                        if merge.returncode:
                            raise WorkflowBlockedError(
                                "cannot fast-forward control worktree"
                            )
                return resolve_commit(self.control_worktree, "HEAD", runner=_git) or ""
            if local_head != report.control_head:
                try:
                    admitted_local = is_ancestor(
                        self.control_worktree,
                        report.control_head,
                        local_head,
                        runner=_git,
                    )
                    local_remote = is_ancestor(
                        self.control_worktree, local_head, remote_head, runner=_git
                    )
                except GitAncestryError as error:
                    raise WorkflowBlockedError(
                        "control history could not be observed during lifecycle replay"
                    ) from error
                if admitted_local and local_remote:
                    # A locally observed commit can already be published when a
                    # previous replay failed after fast-forwarding the worktree.
                    # Do not mistake that published lineage for a local lifecycle
                    # mutation; prove the bound ticket at the candidate remote head
                    # and continue the idempotent replay from there.
                    self._prove_control_descendant(remote_head, report)
                else:
                    self._prove_unpublished_lifecycle_lineage(
                        local_head,
                        remote_head,
                        report,
                        apply_conclusion=apply_conclusion,
                    )
                    self._prove_control_descendant(remote_head, report)
                    reset = reset_hard(
                        self.control_worktree, remote_head, runner=_git
                    )
                    if reset.returncode:
                        raise WorkflowBlockedError(
                            "cannot replay lifecycle on control descendant"
                        )
                if local_remote and local_head != remote_head:
                    merge = merge_fast_forward(
                        self.control_worktree, remote_ref, runner=_git
                    )
                    if merge.returncode:
                        raise WorkflowBlockedError(
                            "cannot fast-forward control descendant"
                        )
            elif remote_head != report.control_head:
                self._prove_control_descendant(remote_head, report)
                _log(
                    f"control advanced during execution: {report.control_head[:12]} "
                    f"-> {remote_head[:12]}; replaying compatible lifecycle"
                )
                merge = merge_fast_forward(
                    self.control_worktree, remote_ref, runner=_git
                )
                if merge.returncode:
                    raise WorkflowBlockedError("cannot fast-forward control descendant")
            else:
                self._prove_control_descendant(report.control_head, report)
            lifecycle_head = self._apply_lifecycle_once(
                report, apply_conclusion=apply_conclusion
            )
            pushed = push_refs(
                self.control_worktree,
                self.remote_name,
                f"HEAD:refs/heads/{self.control_branch}",
                runner=_git,
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

    def _has_operator_recovery_required(self) -> bool:
        """Keep IPC recovery available for an unsafe pending workspace."""
        if self._state.get("phase") != "agent_pending":
            return False
        try:
            inspection = self._inspect_bound_execution_workspace(self._state)
            return inspection.classification == "UNSAFE"
        except (DevlegateError, OSError, ExecutionWorkspaceError):
            return True

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
            if command.force:
                return self._retry_owned(command.ticket_id, stop_event, True)
            return self._retry_owned(command.ticket_id, stop_event)
        if command.method == "drop":
            return 0
        if command.method == "recover":
            if command.execution_id is None or command.onto is None:
                raise DevlegateError("recover identity is incomplete")
            self._recover_unsafe_bound_execution_workspace(
                command.ticket_id, command.execution_id, command.onto
            )
            return 0
        if command.method == "reconcile-resume":
            return self._reconcile_resume_owned(command.ticket_id)
        if command.method == "reconcile-auto":
            reconciliation = self._state.get("reconciliation")
            resolution_class = (
                reconciliation.get("resolution_class")
                if isinstance(reconciliation, dict)
                else None
            )
            if resolution_class == "resume":
                return self._reconcile_resume_owned(command.ticket_id)
            if (
                resolution_class
                not in {
                    "update-base",
                    "update-base with published-lineage rewrite",
                }
                or command.onto is None
            ):
                raise DevlegateError(
                    "automatic reconciliation classification is invalid"
                )
            return self._reconcile_update_base_owned(
                command.ticket_id, command.onto, rewrite_published=True
            )
        if command.method == "reconcile-update-base":
            if command.onto is None:
                raise DevlegateError(
                    "reconcile-update-base requires a product base target"
                )
            return self._reconcile_update_base_owned(
                command.ticket_id,
                command.onto,
                rewrite_published=command.rewrite_published,
            )
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
                    if stop_event is None or not stop_event.is_set():
                        self._refresh_published_status()
                    return 0
                if (
                    stop_event is not None
                    and stop_event.is_set()
                    and self._state.get("phase") != "merge_pending"
                ):
                    self._service_shutdown.set()
                    self._reject_pending_operator_command_on_shutdown()
                    self._publish_service_snapshot(lifecycle="ready")
                    if stop_event is None or not stop_event.is_set():
                        self._refresh_published_status()
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
                        status = (
                            0
                            if operator_command.method == "cleanup-workspaces"
                            else self._dispatch_operator_command(
                                operator_command, stop_event
                            )
                        )
                    else:
                        status = self.run_iteration()
                except ShutdownInterrupted:
                    self._service_shutdown.set()
                    self._publish_service_snapshot(lifecycle="ready")
                    return 0
                except WorkspacePreparationBlockedError as error:
                    if operator_command is not None:
                        self._iteration_diagnostic = (
                            f"{operator_command.method} {operator_command.ticket_id} "
                            f"rejected: {error}"
                        )
                        status = 1
                    elif once:
                        self._iteration_diagnostic = f"execution failed: {error}"
                        print(
                            f"devlegate: {self._iteration_diagnostic}",
                            file=sys.stderr,
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
                            or self._has_operator_recovery_required()
                        ):
                            if (
                                self._has_blocked_execution_stage()
                                or self._has_operator_recovery_required()
                            ):
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
                        self._refresh_published_status()
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
        branch = self._branch_runtime(repo)
        detached = branch is None
        local_head = self._resolve_commit_runtime(repo, "HEAD")
        if local_head is None:
            raise GitObservationError("Git could not resolve HEAD")
        remote_ref = f"{self.remote_name}/{remote_branch}"
        remote_head = self._resolve_commit_runtime(repo, remote_ref)
        status = self._status_runtime(repo)
        return GitObservation(
            branch,
            detached,
            local_head,
            remote_ref,
            remote_head,
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
        try:
            git_before = self._status_git_observation()
        except GitObservationError as error:
            raise DevlegateError(f"cannot observe Git checkout: {error}") from error
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
        try:
            git_after = self._status_git_observation()
        except GitObservationError as error:
            raise DevlegateError(f"cannot observe Git checkout: {error}") from error
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

    @staticmethod
    def _dependency_blocked_tickets(
        ticket_store: TicketStore,
    ) -> tuple[Ticket, ...]:
        """Return todo tickets excluded by the ticket store's runnable predicate."""
        return tuple(
            ticket
            for ticket in ticket_store.tickets
            if ticket.state == "todo"
            and any(
                ticket_store.by_id[dependency].state != "done"
                for dependency in ticket.depends_on
            )
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
            for ticket in self._dependency_blocked_tickets(ticket_store)
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
            workspace_cleanup=(
                dict(state["workspace_cleanup"])
                if isinstance(state.get("workspace_cleanup"), dict)
                else None
            ),
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
            if state["phase"] == "agent_pending":
                inspection = self._inspect_bound_execution_workspace(state)
                if inspection.classification != "REUSABLE":
                    return ExecutionPlan(
                        "blocked",
                        "bound execution workspace is "
                        f"{inspection.classification.lower()}: {inspection.reason}",
                        ticket.id,
                        ticket.title,
                        ticket.state,
                        True,
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

    def _inspect_bound_execution_workspace(
        self, state: dict[str, object]
    ) -> WorkspaceInspection:
        return inspect_bound_workspace(self, state)

    @staticmethod
    def _workspace_recovery_ref(ticket_id: str, execution_id: str) -> str:
        return f"refs/devlegate/recovery/workspace/{ticket_id}/{execution_id}"

    def _repair_bound_execution_workspace(self) -> None:
        """Repair only a pre-worker branch proven to be an older generation."""
        try:
            repair_bound_workspace(self, self._state)
        except ValueError as error:
            raise DevlegateError(str(error)) from error

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
        self,
        ticket_id: str,
        stop_event: threading.Event | None = None,
        force: bool = False,
    ) -> int:
        """Execute retry semantics under an authority lock held by the caller."""
        if stop_event is not None and stop_event.is_set():
            return 0
        if self._state.get("phase") == "agent_running" and not force:
            if self._state.get("execution_stage") in {
                "worker-launch",
                "worker-running",
                "post-worker",
            }:
                self._reconcile_stranded_execution()
            else:
                self._recover_interrupted_execution(ticket_id)
        if force:
            return self._retry_locked(ticket_id, stop_event, True)
        return self._retry_locked(ticket_id, stop_event)

    def _retry_locked(
        self,
        ticket_id: str,
        stop_event: threading.Event | None = None,
        force: bool = False,
    ) -> int:
        forced = False
        if force:
            forced_authorization = self._state.get("force_retry_authorization")
            forced = (
                isinstance(forced_authorization, dict)
                and forced_authorization.get("status") == "admitted"
                and forced_authorization.get("ticket_id") == ticket_id
            )
        if not forced:
            candidates = self._retry_candidates()
            candidate_ids = {candidate[0] for candidate in candidates}
            if ticket_id not in candidate_ids:
                raise DevlegateError(f"ticket {ticket_id} is not currently retryable")
        intent = IterationIntent(retry_ticket_id=ticket_id, force_retry=force)
        try:
            if stop_event is None:
                return self.run_iteration(intent)
            with self._stop_context(stop_event):
                return self.run_iteration(intent)
        except ExecutionWorkspaceError as error:
            # Keep the direct retry API's public error contract without making
            # infrastructure failures recoverable in the hosted polling loop.
            raise DevlegateError(str(error)) from error

    def reconcile_update_base(
        self, ticket_id: str, onto: str, *, rewrite_published: bool = False
    ) -> int:
        """Transplant the preserved linear worker lineage onto an explicit base."""
        return self._reconcile_update_base_owned(
            ticket_id,
            onto,
            rewrite_published=rewrite_published,
            _take_lock=True,
        )

    def reconcile_resume(self, ticket_id: str) -> int:
        """Recover retained execution progress without changing its product base."""
        return self._reconcile_resume_owned(ticket_id, _take_lock=True)

    def reconcile_control(self, from_head: str, to_head: str) -> int:
        """Adopt an explicitly authorized divergent control history."""
        return self._reconcile_control_owned(from_head, to_head, _take_lock=True)

    # Narrow capabilities for owner-held recovery transactions.  These do not
    # expose the coordinator or permit an extracted module to schedule work.
    def recovery_lock(self):
        return self._lock()

    def reload_ticket_store(self):
        return self._ticket_store()

    @staticmethod
    def recovery_log(message: str) -> None:
        _log(message)

    def validate_control_reconcile_admission(
        self, from_head: str, to_head: str
    ) -> None:
        self._validate_control_reconcile_admission(from_head, to_head)

    def validate_control_worktree(self) -> None:
        self._validate_control_worktree()

    def recovery_save_state(self, state: str, **values: object) -> None:
        self._save_state(state, **values)

    def recovery_worker_observation(self, identity) -> str:
        return self._workers.observe(identity)

    @staticmethod
    def recovery_worker_identity(value: object, execution_id: object):
        return _worker_identity_from_value(value, execution_id)

    def recovery_publish_blocked(self, reason: str) -> None:
        self._publish_service_snapshot(lifecycle="blocked", blocked_reason=reason)

    # Recovery workflow capabilities.  These are intentionally small seams:
    # the coordinator remains the only owner of state, locks, Git, and WAL.
    def recovery_state(self) -> dict[str, object]:
        return self._state

    def recovery_report_matches_binding(self, report: ExecutionReport) -> None:
        self._report_matches_execution_binding(report)

    def recovery_report_matches_state(self, report: ExecutionReport) -> None:
        self._report_matches_execution_state(report)

    def recovery_workspace(self):
        return self._execution_workspace_for_recovery()

    def recovery_product(self, base: str) -> dict[str, object]:
        return self._observe_product_generation(base)

    def recovery_prove_product(self) -> None:
        self._prove_product_generation()

    def recovery_zero_delta(
        self, report: ExecutionReport, product: dict[str, object]
    ) -> bool:
        return self._is_zero_delta_stale_product_drift(
            report.code_base_head, report.workspace_head, product
        )

    def recovery_zero_delta_payload(
        self, report: ExecutionReport, product: dict[str, object]
    ) -> dict[str, object]:
        return self._zero_delta_retry_payload(report, product)

    def recovery_ticket_title(self, ticket_id: str) -> str:
        return self._ticket_store().by_id[ticket_id].title

    def recovery_publish_execution(
        self, workspace, checkpoint: str, expected: str | None
    ) -> None:
        self._publish_execution_branch(workspace, checkpoint, expected)

    def recovery_execution_remote(self, branch: str) -> str | None:
        return self._execution_remote_head(branch)

    def recovery_checkpoint_exact(self, workspace, checkpoint: str, start: str) -> bool:
        return self._checkpoint_commit_is_exact(workspace, checkpoint, start)

    def recovery_persist_reconciliation(
        self,
        report: ExecutionReport,
        product: dict[str, object],
        execution_remote: object,
    ) -> None:
        self._persist_product_reconciliation(report, product, execution_remote)

    def recovery_report_from_store(
        self, ticket_id: str, execution_id: str
    ) -> ExecutionReport:
        self._validate_control_worktree()
        return ExecutionReportStore(self.control_worktree).read(ticket_id, execution_id)

    def recovery_validate_control(self) -> None:
        self._validate_control_worktree()

    def recovery_apply_lifecycle(self, report: ExecutionReport) -> str:
        return self._apply_execution_lifecycle(report)

    def recovery_finalize_lifecycle(
        self, report: ExecutionReport, control_head: str
    ) -> None:
        self._finalize_execution_lifecycle(report, control_head)

    def recovery_observe_product_generation(self) -> None:
        self._prove_product_generation()

    def recovery_sync_control(self) -> tuple[str, str | None]:
        return self._sync_control()

    def recovery_fetch_product(self) -> bool:
        result = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=self._git_runtime,
        )
        return result.returncode == 0

    def recovery_code_observation(self):
        return self._git_observation(self.repo, self.remote_branch)

    def recovery_control_observation(self):
        return self._git_observation(self.control_worktree, self.control_branch)

    def recovery_integrate_accepted(self, ticket_id: str, code, control) -> None:
        self._integrate_accepted(ticket_id, code, control)

    def recovery_todo_fingerprint(self) -> str:
        return _todo_fingerprint(self.control_worktree, self.todo_path)[0]

    def recovery_publish_ready(self) -> None:
        self._publish_service_snapshot(lifecycle="ready", worker_running=False)
        self._refresh_published_status()

    def recovery_workspace_manager(self, ticket_id: str):
        """Provide the workspace capability without exposing the coordinator."""
        return ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )

    def recovery_reports(self, ticket_id: str) -> list[ExecutionReport]:
        self._validate_control_worktree()
        return ExecutionReportStore(self.control_worktree).list(ticket_id)

    def normalize_stranded_execution(self, interruption: str) -> None:
        self._normalize_stranded_execution(interruption)

    def _reconcile_control_owned(
        self, from_head: str, to_head: str, *, _take_lock: bool = False
    ) -> int:
        """Replace only the local control lineage after repeated exact proof."""
        try:
            return reconcile_control_lineage(
                self,
                from_head,
                to_head,
                repo=self.repo,
                control_worktree=self.control_worktree,
                take_lock=_take_lock,
            )
        except ValueError as error:
            raise DevlegateError(str(error)) from error

    # These observations form the capability boundary for the lineage
    # transaction.  The transaction module owns their ordering; the service
    # remains the only owner of Git, state, and workspace authority.
    def reconciliation_pending(self) -> dict[str, object]:
        reconciliation = self._state.get("reconciliation")
        if (
            not isinstance(reconciliation, dict)
            or reconciliation.get("status") != "pending"
        ):
            raise ReconciliationTransactionError(
                "reconciliation has no pending transaction"
            )
        return reconciliation

    def reconciliation_target(self, value: str) -> str:
        resolved = resolve_commit(
            self.repo, value, check=False, runner=_git
        )
        if resolved is None:
            raise ReconciliationTransactionError(
                "requested reconciliation target is not a commit"
            )
        return resolved

    def reconciliation_product(self, base: str) -> dict[str, object]:
        product = self._observe_product_generation(base)
        if product.get("branch") != self.current_branch:
            raise ReconciliationTransactionError(
                "product checkout is on the wrong branch"
            )
        return product

    def reconciliation_target_is_descendant(self, base: str, target: str) -> bool:
        try:
            return is_ancestor(self.repo, base, target, runner=_git)
        except GitAncestryError as error:
            raise ReconciliationTransactionError(str(error)) from error

    def reconciliation_same_parent_replacement(self, base: str, target: str) -> bool:
        return _is_same_parent_replacement(self.repo, base, target)

    def reconciliation_evidence_matches(self, reference: str, checkpoint: str) -> bool:
        return self._resolve_commit_runtime(self.repo, reference) == checkpoint

    def reconciliation_workspace_binding(
        self, ticket_id: str, value: dict[str, object]
    ) -> bool:
        manager = ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )
        return (
            value.get("execution_branch") == manager.branch
            and Path(str(value.get("execution_path"))) == manager.path
        )

    def reconciliation_lock(self):
        return self._lock()

    def reconciliation_workspace_manager(self, ticket_id: str):
        return ExecutionWorkspaceManager(
            self.repo, self.execution_worktree_root, ticket_id
        )

    def reconciliation_todo_fingerprint(self) -> str:
        fingerprint, _count = _todo_fingerprint(self.control_worktree, self.todo_path)
        return fingerprint

    def reconciliation_publish_rewrite(
        self, branch: str, rewritten_head: str, expected_remote: str
    ) -> None:
        reconciliation = self._state.get("reconciliation")
        if not isinstance(reconciliation, dict):
            raise DevlegateError("reconciliation workspace binding is unavailable")
        workspace_path = Path(str(reconciliation.get("execution_path")))
        pushed = push_refs(
            workspace_path,
            self.remote_name,
            f"{rewritten_head}:refs/heads/{branch}",
            options=(f"--force-with-lease=refs/heads/{branch}:{expected_remote}",),
            runner=_git,
        )
        observed = self._execution_remote_head(branch)
        if pushed.returncode and observed != rewritten_head:
            raise DevlegateError(
                pushed.stderr.strip() or "published execution rewrite failed"
            )
        if observed != rewritten_head:
            raise DevlegateError("execution branch remote changed during publication")

    def reconciliation_pin_rewrite_evidence(
        self, reference: str, rewritten_head: str
    ) -> None:
        pinned = compare_and_swap_ref(
            self.repo, reference, rewritten_head, "0" * 40, runner=_git
        )
        if pinned.returncode:
            existing = self._resolve_commit_runtime(self.repo, reference)
            if existing != rewritten_head:
                raise DevlegateError("rewritten execution evidence could not be pinned")

    def _reconcile_update_base_owned(
        self,
        ticket_id: str,
        onto: str,
        *,
        rewrite_published: bool = False,
        _take_lock: bool = False,
    ) -> int:
        """Run the owner-held reconciliation transaction."""
        try:
            return run_update_base(
                self,
                ticket_id,
                onto,
                rewrite_published=rewrite_published,
                take_lock=_take_lock,
            )
        except ReconciliationTransactionError as error:
            raise DevlegateError(str(error)) from error

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
                if product["classification"] == "unobservable":
                    raise DevlegateError(
                        "product remote could not be observed; "
                        "retry when connectivity returns"
                    )
                raise DevlegateError(
                    "same-base reconciliation requires a clean unchanged product "
                    "generation"
                )
            self._validate_control_worktree()
            recorded_control = str(reconciliation["control_head"])
            control_head = resolve_commit(self.control_worktree, "HEAD", runner=_git)
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
            evidence = resolve_commit(
                self.repo, str(reconciliation["evidence_ref"]), runner=_git
            )
            if evidence != checkpoint:
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
                try:
                    predecessor_is_ancestor = is_ancestor(
                        self.repo, expected_remote, checkpoint, runner=_git
                    )
                except GitAncestryError as error:
                    raise DevlegateError(
                        "execution predecessor history could not be observed"
                    ) from error
                if not predecessor_is_ancestor:
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
                if product["classification"] == "unobservable":
                    raise DevlegateError(
                        "product remote could not be observed; "
                        "retry when connectivity returns"
                    )
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
        try:
            return recover_stranded_execution(self, self._state)
        except ValueError as error:
            raise DevlegateError(str(error)) from error

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
        product_fetch = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=_git,
        )
        product_remote = resolve_commit(self.repo, product_ref, runner=_git)
        if product_fetch.returncode or product_remote != state["remote_head"]:
            raise DevlegateError(
                "product generation changed before interrupted execution recovery"
            )
        self._validate_control_worktree()
        status = self._status_runtime(self.control_worktree)
        if status:
            raise DevlegateError("control working tree is dirty")
        control_fetch = fetch_ref(
            self.control_worktree,
            self.remote_name,
            self.control_branch,
            prune=True,
            runner=_git,
        )
        control_ref = f"{self.remote_name}/{self.control_branch}"
        control_head = resolve_commit(
            self.control_worktree, control_ref, runner=_git
        )
        if control_fetch.returncode or control_head is None:
            raise DevlegateError("cannot freshly observe control remote")
        local_control = resolve_commit(self.control_worktree, "HEAD", runner=_git)
        try:
            control_is_ancestor = local_control is not None and is_ancestor(
                self.control_worktree, local_control, control_head, runner=_git
            )
        except GitAncestryError as error:
            raise DevlegateError(
                "local control history could not be observed during recovery"
            ) from error
        if not control_is_ancestor:
            raise DevlegateError("local control history diverged during recovery")
        self._prove_bound_ticket_descendant(
            control_head,
            str(state["execution_control_head"]),
            str(state["execution_ticket_id"]),
            str(state["selected_ticket_body"]),
        )
        if local_control != control_head:
            merged = merge_fast_forward(
                self.control_worktree, control_ref, runner=_git
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
                "generation": state["execution_base_head"],
                "product_head": state["local_head"],
                "remote_head": state["remote_head"],
                "control_head": control_head,
                "todo_fingerprint": _todo_fingerprint(
                    self.control_worktree, self.todo_path
                )[0],
                "reason": f"interrupted: {interruption_kind.replace('_', ' ')}",
                "interrupted": True,
                "interruption_kind": interruption_kind,
                "manual_retry_required": (
                    isinstance(state.get("manual_retry_required"), dict)
                    and state["manual_retry_required"].get("execution_id")
                    == state["execution_id"]
                ),
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
        self._refresh_published_status()

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
        current_local_head = self._resolve_commit_runtime(self.repo, "HEAD")
        if current_local_head is None:
            raise WorkflowBlockedError("product HEAD could not be resolved")
        if current_local_head != state["local_head"]:
            raise DevlegateError("product checkout changed since interrupted execution")
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        product_fetch = fetch_ref(
            self.repo,
            self.remote_name,
            self.remote_branch,
            prune=True,
            runner=_git,
        )
        if product_fetch.returncode:
            raise DevlegateError(
                "cannot freshly observe product remote before interrupted recovery: "
                f"{product_fetch.stderr.strip() or 'unknown git error'}"
            )
        current_remote = resolve_commit(self.repo, remote_ref, runner=_git)
        if current_remote != state["remote_head"]:
            raise DevlegateError(
                "product remote generation changed since interrupted execution"
            )
        self._validate_control_worktree()
        control_fetch = fetch_ref(
            self.control_worktree,
            self.remote_name,
            self.control_branch,
            prune=True,
            runner=_git,
        )
        if control_fetch.returncode:
            raise DevlegateError(
                "cannot freshly observe control remote before interrupted recovery: "
                f"{control_fetch.stderr.strip() or 'unknown git error'}"
            )
        control_remote_ref = f"{self.remote_name}/{self.control_branch}"
        current_control_remote = resolve_commit(
            self.control_worktree, control_remote_ref, runner=_git
        )
        if current_control_remote != state["control_head"]:
            raise DevlegateError(
                "control remote generation changed since interrupted execution"
            )
        current_control_head = resolve_commit(
            self.control_worktree, "HEAD", runner=_git
        )
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
        status = self._status_runtime(self.repo)
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
        local_head = self._resolve_commit_runtime(self.repo, "HEAD") or ""
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        remote_head = self._resolve_commit_runtime(self.repo, remote_ref) or ""
        generation_differs = (
            str(self._state.get("handled_remote_head", "")) != remote_head
            or str(self._state.get("handled_control_head", ""))
            != (
                self._resolve_commit_runtime(self.control_worktree, "HEAD") or ""
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
                self._remote_exists_for_status(),
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
            try:
                ahead, behind = self._ahead_behind_runtime(
                    self.repo, local_head, remote_head
                )
                display_counts = f"{ahead} {behind}"
            except GitObservationError:
                display_counts = "<unknown>"
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
