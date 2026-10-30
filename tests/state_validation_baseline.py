# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Frozen validator from baseline e48a44b688d20a58eff22e376925dbfd45d44bd3."""

from devlegate.execution_result import ExecutionReport, ExecutionReportError
from devlegate.review_provenance import ReviewProvenanceError, restore_review_binding
from devlegate.runtime import DevlegateError
from devlegate.worker_supervisor import WorkerProcessIdentity


def _is_git_identity(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 40
        and all(character in "0123456789abcdef" for character in value.lower())
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


def baseline_validate_state_invariant(state: dict[str, object]) -> None:
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
    retirement = state.get("execution_ref_retirement")
    if retirement is not None and (
        not isinstance(retirement, dict)
        or not isinstance(retirement.get("branch"), str)
        or not retirement["branch"].startswith("devlegate/work/")
        or not _is_git_identity(retirement.get("expected_head"))
    ):
        raise DevlegateError("invalid execution ref retirement intent")
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
        product_transition = (
            accepted_integration.get("product_transition")
            if isinstance(accepted_integration, dict)
            else None
        )
        if (
            not isinstance(accepted_integration, dict)
            or set(accepted_integration)
            not in (
                {"ticket_id", "checkpoint", "control_head"},
                {
                    "ticket_id",
                    "checkpoint",
                    "control_head",
                    "control_predecessor",
                },
                {
                    "ticket_id",
                    "checkpoint",
                    "control_head",
                    "product_transition",
                },
                {
                    "ticket_id",
                    "checkpoint",
                    "control_head",
                    "control_predecessor",
                    "product_transition",
                },
            )
            or not (
                isinstance(accepted_integration.get("ticket_id"), str)
                and bool(accepted_integration["ticket_id"])
                and _is_git_identity(accepted_integration.get("checkpoint"))
                and _is_git_identity(accepted_integration.get("control_head"))
                and (
                    "control_predecessor" not in accepted_integration
                    or _is_git_identity(
                        accepted_integration.get("control_predecessor")
                    )
                )
            )
            or (
                product_transition is not None
                and (
                    not isinstance(product_transition, dict)
                    or set(product_transition)
                    not in (
                        {"status", "admitted_base", "predecessor"},
                        {
                            "status",
                            "admitted_base",
                            "predecessor",
                            "integration",
                        },
                    )
                    or product_transition.get("status")
                    not in {"merging", "merged", "published"}
                    or not _is_git_identity(product_transition.get("admitted_base"))
                    or not _is_git_identity(product_transition.get("predecessor"))
                    or (
                        product_transition.get("status") in {"merged", "published"}
                        and not _is_git_identity(
                            product_transition.get("integration")
                        )
                    )
                )
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
    rework = state.get("rework_provenance")
    if rework is not None:
        if not isinstance(rework, dict):
            raise DevlegateError("invalid state: rework provenance is malformed")
        ticket_id = state.get("execution_ticket_id")
        previous_execution = rework.get("previous_execution_id")
        previous_checkpoint = rework.get("previous_report_checkpoint")
        previous_control = rework.get("previous_control_head")
        if not all(
            isinstance(item, str)
            for item in (
                ticket_id,
                previous_execution,
                previous_checkpoint,
                previous_control,
            )
        ):
            raise DevlegateError(
                "invalid state: rework execution binding is incomplete"
            )
        try:
            restore_review_binding(
                rework.get("review"),
                ticket_id=ticket_id,
                execution_id=previous_execution,
                checkpoint=previous_checkpoint,
                control_head=previous_control,
            )
        except ReviewProvenanceError as error:
            raise DevlegateError(f"invalid state: {error}") from error

