# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-only ticket reset admission, cancellation and replay.

Transport request IDs are deliberately not lifecycle identities. The durable
operation is found by canonical ticket and control lineage on every invocation.
"""

import copy
import re
import secrets
import subprocess
from pathlib import Path

from devlegate.abandonment_publication import encoded, make_transaction
from devlegate.clean_preview import _blob, _control_identity, _tree, canonical_digest
from devlegate.execution_result import ExecutionReportStore
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import is_ancestor, path_object, remote_ref, resolve_ref_commit
from devlegate.resource_binding import ResourceBinding, UnprovenResource, _identity
from devlegate.tickets import (
    is_canonical_ticket_name,
    parse_ticket_text,
    requeue_transition,
)
from devlegate.worker_supervisor import WorkerSupervisionError

SCHEMA = "devlegate.ticket-reset.v1"
STAGES = {"stopping-worker", "retiring-old-generation", "requeue", "queued", "started"}


def validate(state):
    try:
        _validate(state)
    except (KeyError, TypeError, AttributeError) as error:
        raise ValueError("invalid durable ticket reset state") from error


def _validate(state):
    boundaries = state.get("reset_boundaries", {})
    if not isinstance(boundaries, dict) or any(
        not isinstance(ticket_id, str)
        or not is_canonical_ticket_name(f"{ticket_id}.md")
        or not isinstance(executions, list)
        or any(not isinstance(item, str) or not item for item in executions)
        for ticket_id, executions in boundaries.items()
    ):
        raise ValueError("invalid durable reset continuation boundaries")
    intent = state.get("ticket_reset")
    if intent is None:
        return
    if not isinstance(intent, dict) or not {
        "schema", "operation_id", "ticket_id", "status", "control", "blob",
        "source", "source_state", "prior_execution", "prior_reports", "binding",
        "retirement", "transaction", "blocker", "admission_revision",
        "admission_sha256",
        "fresh_execution_id", "fresh_generation", "fresh_configuration",
    } == set(intent):
        raise ValueError("invalid durable ticket reset fields")
    if intent["schema"] != SCHEMA or intent["status"] not in STAGES:
        raise ValueError("invalid durable ticket reset stage")
    token = intent["operation_id"]
    if not isinstance(token, str) or len(token) != 32 or any(
        c not in "0123456789abcdef" for c in token
    ):
        raise ValueError("invalid durable ticket reset identity")
    if not isinstance(intent["prior_reports"], list) or any(
        not isinstance(item, str) or not re.fullmatch(r"[A-Za-z0-9_-]+", item)
        for item in intent["prior_reports"]
    ):
        raise ValueError("invalid durable ticket reset report boundary")
    for key in ("ticket_id", "source", "source_state", "admission_revision"):
        if not isinstance(intent[key], str) or not intent[key]:
            raise ValueError("invalid durable ticket reset admission")
    if not is_canonical_ticket_name(f"{intent['ticket_id']}.md") or not re.fullmatch(
        r"[0-9a-f]{40}", intent["blob"],
    ):
        raise ValueError("invalid durable ticket reset ticket identity")
    if intent["admission_sha256"] != canonical_digest(_immutable(intent)):
        raise ValueError("durable ticket reset admission identity changed")
    if not isinstance(intent["retirement"], dict) or (
        intent["blocker"] is not None and not isinstance(intent["blocker"], str)
    ):
        raise ValueError("invalid durable ticket reset progress")
    fresh = intent["fresh_execution_id"]
    if fresh is None:
        if intent["fresh_generation"] is not None or (
            intent["fresh_configuration"] is not None or intent["status"] == "started"
        ):
            raise ValueError("reset fresh execution identity is incomplete")
    elif not isinstance(fresh, str) or not re.fullmatch(r"[0-9a-f]{32}", fresh) or (
        not isinstance(intent["fresh_generation"], str)
        or not re.fullmatch(r"[0-9a-f]{64}", intent["fresh_generation"])
        or not isinstance(intent["fresh_configuration"], dict)
        or set(intent["fresh_configuration"]) != {
            "opencode_bin", "opencode_model", "opencode_agent",
        }
        or any(not isinstance(v, str) for v in intent["fresh_configuration"].values())
    ):
        raise ValueError("invalid reset fresh execution binding")
    if intent["status"] in {"requeue", "queued", "started"} and (
        intent["retirement"].get("status") != "completed"
    ):
        raise ValueError("ticket reset has no proven resource retirement")


def _immutable(intent):
    return {key: intent[key] for key in (
        "schema", "operation_id", "ticket_id", "control", "blob", "source",
        "source_state", "prior_execution", "prior_reports", "binding",
        "admission_revision",
    )}


def _control(owner, head):
    control = _control_identity(owner, owner.control_worktree, head)
    control["filesystem"] = {
        "worktree": _identity(owner.control_worktree),
        "admin": _identity(Path(control["admin"])),
        "common": _identity(Path(control["common"])),
    }
    return control


def response(intent):
    return {
        "accepted": True, "ticket_id": intent["ticket_id"],
        "operation_id": intent["operation_id"],
        "status": "blocked" if intent["blocker"] else intent["status"],
        "stage": intent["status"], "reason": intent["blocker"],
        "fresh_execution_id": intent["fresh_execution_id"],
    }


def active(owner):
    intent = owner._state.get("ticket_reset")
    return isinstance(intent, dict) and intent["status"] not in {"queued", "started"}


def _save(owner, intent, **fields):
    persisted, revision = owner._runtime_store.observe()
    if persisted != owner._state:
        raise ValueError("reset runtime authority changed; restart required")
    owner._save_state(
        str(owner._state["phase"]), ticket_reset={**intent, **fields},
        expected_store_revision=revision,
    )


def admit(owner, ticket_id):
    owner._assert_repository_owner()
    if not isinstance(ticket_id, str) or not is_canonical_ticket_name(
        f"{ticket_id}.md"
    ):
        raise ValueError("reset requires one canonical ticket ID")
    if owner._state_write_uncertain:
        raise ValueError("reset runtime write outcome uncertain; restart required")
    previous = owner._state.get("ticket_reset")
    if previous is not None and previous["ticket_id"] == ticket_id:
        if previous["status"] != "started":
            return response(previous)
        if owner._state.get("phase") == "agent_running" and (
            owner._state.get("execution_id") == previous["fresh_execution_id"]
            and owner.reset_worker_configuration() == previous["fresh_configuration"]
        ):
            # A lost acknowledgement must not cancel the fresh worker that this
            # very operation just established, or widen its disposal scope.
            return response(previous)
    elif active(owner):
        raise ValueError("another ticket reset is in progress")
    head, _remote_head = owner._sync_control()
    control = _control(owner, head)
    if owner._status_runtime(owner.control_worktree):
        raise ValueError("reset blocked: control worktree is dirty")
    workflow = owner._workflow_paths()
    tree = _tree(owner.control_worktree, head, *workflow.values())
    matches = [(state, f"{path}/{ticket_id}.md") for state, path in workflow.items()
               if f"{path}/{ticket_id}.md" in tree]
    if len(matches) != 1:
        raise ValueError("unknown or ambiguous canonical ticket identity")
    state, source = matches[0]
    ticket = parse_ticket_text(
        owner.control_worktree / source, state,
        _blob(owner.control_worktree, tree[source]),
    )
    requeue_transition(ticket)
    if owner.service_snapshot().worker_running and (
        owner._state.get("execution_ticket_id") != ticket_id
    ):
        raise ValueError("reset blocked: another ticket's worker owns the service")
    for key in ("accepted_integration", "reconciliation", "abandonment_admission",
                "abandonment_publication", "clean_reset_intent"):
        if owner._state.get(key) is not None:
            raise ValueError(f"reset blocked by pending owner transaction: {key}")
    reports = ExecutionReportStore(owner.control_worktree).list(ticket_id)
    current = owner._state.get("execution_ticket_id") == ticket_id
    if ticket.state == "todo" and not reports and not current:
        manager = _manager(owner, ticket_id)
        if _namespace_absent(owner, manager):
            owner.wake()
            return {"accepted": True, "ticket_id": ticket_id,
                    "status": "already-fresh", "reason": None}
    binding = copy.deepcopy(owner._state.get("execution_resource_binding")) if (
        current
    ) else None
    if binding is None and not current:
        candidates = []
        manager = _manager(owner, ticket_id)
        for report in reports:
            try:
                candidate = ResourceBinding.for_execution(manager, report.execution_id)
                candidate.require_local(clean=False, read_only=True)
            except (ExecutionWorkspaceError, ValueError, OSError, RuntimeError):
                continue
            candidates.append(candidate.intent)
        if len(candidates) == 1:
            binding = copy.deepcopy(candidates[0])
    if binding is not None:
        _prove_lineage(owner, binding, head, ticket_id)
    if current and owner._state["phase"] not in {
        "idle", "agent_pending", "agent_running",
    }:
        raise ValueError("reset blocked by pending execution publication")
    if current and owner._state["phase"] != "idle" and owner._state.get(
        "execution_stage"
    ) not in {
        None, "worker-launch", "worker-running", "post-worker", "pre-checkpoint",
    }:
        raise ValueError("reset blocked by pending execution checkpoint/publication")
    persisted, revision = owner._runtime_store.observe()
    if persisted != owner._state:
        raise ValueError("reset runtime authority changed; restart required")
    intent = {
        "schema": SCHEMA, "operation_id": secrets.token_hex(16),
        "ticket_id": ticket_id, "status": "stopping-worker",
        "control": {"head": head, "resource": control}, "blob": tree[source],
        "source": source, "source_state": state,
        "prior_execution": copy.deepcopy({
            key: value for key, value in owner._state.items()
            if key.startswith("execution_") or key in {
                "worker_identity", "phase", "selected_ticket_body",
            }
        }) if current else None,
        "prior_reports": [r.execution_id for r in reports], "binding": binding,
        "retirement": {}, "transaction": None, "blocker": None,
        "admission_revision": revision,
        "fresh_execution_id": None, "fresh_generation": None,
        "fresh_configuration": None,
    }
    intent["admission_sha256"] = canonical_digest(_immutable(intent))
    identity = owner._state.get("worker_identity") if current else None
    if isinstance(identity, dict) and identity.get("containment_id"):
        launch = owner._workers.containment_provider.launch_evidence(
            identity["execution_id"],
        )
        if launch["containment"] != identity["containment_id"]:
            raise ValueError("reset worker launch-root identity differs")
        intent["prior_execution"]["reset_launch_evidence"] = launch
        intent["admission_sha256"] = canonical_digest(_immutable(intent))
    owner._save_state(
        str(owner._state["phase"]), ticket_reset=intent,
        expected_store_revision=revision,
    )
    return response(intent)


def poll_worker(owner):
    """Run on the worker's owner thread, never on an IPC thread."""
    with owner._operator_command_lock:
        command = owner._operator_command
        if command is None or command.method != "reset":
            return
        owner._operator_command = None
    try:
        owner._admit_operator_command(command)
    except Exception:
        # A rejected request (including one for a different ticket) must not
        # interrupt the worker whose owner happened to process it.
        if command.admission_error is None:
            raise


class ResetStop:
    """Compose the service stop policy with the admitted ticket cancellation."""

    def __init__(self, owner, ticket_id):
        self.owner, self.ticket_id = owner, ticket_id

    def __getattr__(self, name):
        return getattr(self.owner._stop_event, name)

    @property
    def kind(self):
        if self.owner._state_write_uncertain:
            return "ticket_reset"
        intent = self.owner._state.get("ticket_reset")
        if active(self.owner) and intent["ticket_id"] == self.ticket_id:
            return "ticket_reset"
        return getattr(self.owner._stop_event, "kind", None)


def _manager(owner, ticket_id):
    return ExecutionWorkspaceManager(
        owner.repo, owner.execution_worktree_root, ticket_id, runner=owner._git_runtime,
    )


def _prove_lineage(owner, binding, control_head, ticket_id):
    if binding["ticket_id"] != ticket_id or not is_ancestor(
        owner.control_worktree, binding["control"], control_head,
        runner=owner._git_runtime,
    ):
        raise ValueError("reset resource belongs to a foreign control lineage")
    if path_object(
        owner.control_worktree, binding["control"], f"{owner.todo_path}/{ticket_id}.md",
        runner=owner._git_runtime,
    ) != binding["ticket_blob"]:
        raise ValueError("reset generation has no exact canonical ticket admission")


def _binding(owner, intent):
    manager = _manager(owner, intent["ticket_id"])
    # Complete absence proves a nonconflicting namespace without authorizing
    # any deletion or adopting an unbound historical generation.
    if _namespace_absent(owner, manager):
        return None
    if intent["binding"] is not None:
        binding = ResourceBinding(manager, intent["binding"])
        binding._validate_intent()
        return binding
    raise ValueError("reset resources have no exact independent generation binding")


def _namespace_absent(owner, manager):
    """Never infer mutable remote absence from a local snapshot or IO failure."""
    registrations = manager._registrations()
    ref = f"refs/heads/{manager.branch}"
    if manager.path.exists() or manager.path.is_symlink() or any(
        p == manager.path.resolve() or r.get("branch") == manager.branch
        for p, r in registrations.items()
    ) or resolve_ref_commit(owner.repo, ref, runner=owner._git_runtime) is not None or (
        remote_ref(owner.repo, owner.remote_name, ref, runner=owner._git_runtime)
        is not None
    ):
        return False
    return True


def advance(owner):
    """Advance durable stages; any uncertain effect holds the scheduler closed."""
    from devlegate.runtime_state_validation import DevlegateError

    owner._assert_repository_owner()
    intent = owner._state["ticket_reset"]
    try:
        if intent["status"] == "stopping-worker":
            prior = intent["prior_execution"]
            if prior is not None:
                execution_id = prior.get("execution_id")
                if owner._workers.owns(execution_id):
                    return
                from devlegate.runtime_state_validation import (
                    _worker_identity_from_value,
                )

                identity = _worker_identity_from_value(
                    owner._state.get("worker_identity"), execution_id,
                )
                if identity is not None:
                    proved = False
                    if intent["binding"] is not None:
                        binding = ResourceBinding(
                            _manager(owner, intent["ticket_id"]), intent["binding"],
                        )
                        binding._validate_intent()
                        try:
                            binding.require_worker_retirement()
                            proved = True
                        except UnprovenResource:
                            pass
                    if not proved:
                        owner._workers.retire(
                            identity, evidence=prior.get("reset_launch_evidence"),
                        )
                elif owner._state.get("execution_stage") in {
                    "worker-launch", "worker-running",
                }:
                    raise ValueError("worker retirement cannot be proved")
                if intent["binding"] is not None and prior.get("phase") != "idle":
                    binding = ResourceBinding(_manager(owner, intent["ticket_id"]),
                                              intent["binding"])
                    binding._validate_intent()
                    if prior.get("phase") == "agent_pending" and not any(
                        operation == "worker"
                        for _sequence, operation, _path in binding._attempts()
                    ):
                        # Launch is WAL-ordered after this operation. Its absence
                        # proves this pre-worker attempt never owned a process.
                        binding.begin_operation("worker", {"reset_not_launched": True})
                        binding.finish_operation("worker", {"reset_not_launched": True})
                    if binding.operation_pending("worker"):
                        binding.finish_operation("worker", {"reset": True,
                                                            "group_retired": True})
                    receipt = binding.operation_receipt("worker")
                    retirement = binding.directory / (
                        f"worker-retired.{receipt['sequence']:08d}.json"
                    )
                    if not retirement.exists():
                        binding.record_worker_retirement()
                    binding.require_worker_retirement()
            _save(owner, intent, status="retiring-old-generation", blocker=None)
            intent = owner._state["ticket_reset"]
        if intent["status"] == "retiring-old-generation":
            binding = _binding(owner, intent)
            intent = owner._state["ticket_reset"]
            if binding is not None:
                from devlegate.clean_reset import retire_ticket_generation

                retire_ticket_generation(owner, binding)
            else:
                _save(owner, intent, retirement={"status": "completed"})
            intent = owner._state["ticket_reset"]
            _save(owner, intent, status="requeue", blocker=None)
            intent = owner._state["ticket_reset"]
        if intent["status"] == "requeue":
            head = intent["control"]["head"]
            if intent["transaction"] is None:
                observed = owner._resolve_commit_runtime(owner.control_worktree, "HEAD")
                if observed != head:
                    raise ValueError("reset control lineage changed before requeue")
                audit_path = (
                    f"resets/{intent['ticket_id']}/{intent['operation_id']}.json"
                )
                changes: dict[str, str | None] = {
                    audit_path: encoded({
                        "schema": SCHEMA, "ticket_id": intent["ticket_id"],
                        "operation_id": intent["operation_id"],
                        "ticket_blob": intent["blob"],
                        "prior_reports": intent["prior_reports"],
                        "cancelled_execution": (
                            intent["prior_execution"].get("execution_id")
                            if intent["prior_execution"] else None
                        ),
                        "disposition": "reset; prior attempt is not a continuation",
                        "resource_generation": (
                            intent["binding"]["generation"]
                            if intent["binding"] else None
                        ),
                    }),
                }
                if intent["source_state"] != "todo":
                    changes[intent["source"]] = None
                    changes[f"{owner.todo_path}/{intent['ticket_id']}.md"] = _blob(
                        owner.control_worktree, intent["blob"],
                    )
                transaction = make_transaction(
                    owner.control_worktree, head, changes,
                    f"Reset {intent['ticket_id']} ({intent['operation_id']})",
                    owner._git_runtime,
                    blob_sources=(
                        {f"{owner.todo_path}/{intent['ticket_id']}.md":
                         intent["source"]}
                        if intent["source_state"] != "todo" else None
                    ),
                )
                if path_object(
                    owner.control_worktree, transaction["commit"],
                    f"{owner.todo_path}/{intent['ticket_id']}.md",
                    runner=owner._git_runtime,
                ) != intent["blob"]:
                    raise ValueError("reset requeue changed the exact ticket blob")
                _save(owner, intent, transaction=transaction)
                intent = owner._state["ticket_reset"]
            owner._publish_abandonment_transaction(intent["transaction"])
            current_control, _upstream = owner._sync_control()
            if path_object(
                owner.control_worktree, current_control,
                f"{owner.todo_path}/{intent['ticket_id']}.md",
                runner=owner._git_runtime,
            ) != intent["blob"]:
                raise ValueError(
                    "reset requeue was superseded by a canonical ticket change",
                )
            failures = dict(owner._state.get("failed_executions", {}))
            failures.pop(intent["ticket_id"], None)
            fields: dict = {
                "ticket_reset": {**intent, "status": "queued", "blocker": None},
                "failed_executions": failures, "handled_todo_fingerprint": "",
            }
            for key in (
                "automatic_retry", "force_retry_authorization", "resume_required",
                "manual_retry_required", "rework_provenance",
            ):
                value = owner._state.get(key)
                if isinstance(value, dict) and value.get("ticket_id") == (
                    intent["ticket_id"]
                ):
                    fields[key] = None
            current = owner._state.get("execution_ticket_id") == intent["ticket_id"]
            if current:
                fields["rework_provenance"] = None
                fields["automatic_retry"] = None
                fields["force_retry_authorization"] = None
                fields["resume_required"] = None
            persisted, revision = owner._runtime_store.observe()
            if persisted != owner._state:
                raise ValueError(
                    "reset runtime authority changed before queue activation",
                )
            owner._save_state(
                "idle" if current else str(owner._state["phase"]),
                clear_execution=current, clear_pre_worker_generation=current,
                expected_store_revision=revision,
                **fields,
                reset_boundaries={
                    **owner._state.get("reset_boundaries", {}),
                    intent["ticket_id"]: intent["prior_reports"],
                },
            )
            owner.wake()
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError,
            ExecutionWorkspaceError, WorkerSupervisionError, DevlegateError) as error:
        intent = owner._state["ticket_reset"]
        if not owner._state_write_uncertain:
            _save(owner, intent, blocker=str(error))
        raise


def report_is_discarded(owner, report):
    boundary = owner._state.get("reset_boundaries", {}).get(report.ticket_id, [])
    if report.execution_id in boundary:
        return True
    intent = owner._state.get("ticket_reset")
    return isinstance(intent, dict) and report.ticket_id == intent["ticket_id"] and (
        report.execution_id in intent["prior_reports"]
    )


def require_control(owner):
    """Reobserve exact admission lineage before each resource effect."""
    intent = owner._state["ticket_reset"]
    head = owner._resolve_commit_runtime(owner.control_worktree, "HEAD")
    if head != intent["control"]["head"] or _control(owner, head) != (
        intent["control"]["resource"]
    ):
        raise ValueError("reset control lineage or checkout identity changed")
    if owner._status_runtime(owner.control_worktree):
        raise ValueError("reset control worktree changed")
