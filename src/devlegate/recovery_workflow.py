# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Crash replay transactions for execution publication and lifecycle.

This module owns the durable stage transitions.  The service supplies only
owner-held observations and mutations through the protocol below; it does not
give this module a second lock, state store, or Git authority.
"""

from __future__ import annotations

import dataclasses
from typing import Any, Protocol

from devlegate.execution_result import ExecutionReport, ExecutionReportError
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)


class WorkflowRecoveryError(Exception):
    """A recovery transaction could not prove a safe continuation."""


class WorkflowRecoveryCapabilities(Protocol):
    repo: Any
    execution_worktree_root: Any

    def recovery_state(self) -> dict[str, object]: ...
    def recovery_report_matches_binding(self, report: ExecutionReport) -> None: ...
    def recovery_workspace(self) -> Any: ...
    def recovery_workspace_manager(
        self, ticket_id: str
    ) -> ExecutionWorkspaceManager: ...
    def recovery_product(self, base: str) -> dict[str, object]: ...
    def recovery_prove_product(self) -> None: ...
    def recovery_zero_delta(
        self, report: ExecutionReport, product: dict[str, object]
    ) -> bool: ...
    def recovery_zero_delta_payload(
        self, report: ExecutionReport, product: dict[str, object]
    ) -> dict[str, object]: ...
    def recovery_ticket_title(self, ticket_id: str) -> str: ...
    def recovery_save_state(self, state: str, **values: object) -> None: ...
    def recovery_publish_execution(
        self, workspace: Any, checkpoint: str, expected: str | None
    ) -> None: ...
    def recovery_execution_remote(self, branch: str) -> str | None: ...
    def recovery_report_from_store(
        self, ticket_id: str, execution_id: str
    ) -> ExecutionReport: ...
    def recovery_validate_control(self) -> None: ...
    def recovery_apply_lifecycle(self, report: ExecutionReport) -> str: ...
    def recovery_finalize_lifecycle(
        self, report: ExecutionReport, control_head: str
    ) -> None: ...
    def recovery_observe_product_generation(self) -> None: ...
    def recovery_integrate_accepted(
        self, ticket_id: str, code: Any, control: Any
    ) -> None: ...
    def recovery_sync_control(self) -> tuple[str, str | None]: ...
    def recovery_fetch_product(self) -> bool: ...
    def recovery_code_observation(self) -> Any: ...
    def recovery_control_observation(self) -> Any: ...
    def recovery_todo_fingerprint(self) -> str: ...
    def recovery_publish_ready(self) -> None: ...
    def recovery_log(self, message: str) -> None: ...


def recover_checkpoint_publication(owner: WorkflowRecoveryCapabilities) -> None:
    """Replay checkpoint and execution publication from its WAL stage."""
    state = owner.recovery_state()
    stage = state.get("execution_stage")
    pending = state.get("pending_execution_report")
    try:
        report = ExecutionReport.from_dict(pending)
    except ExecutionReportError as error:
        raise WorkflowRecoveryError(
            "pending execution report is invalid; recovery is ambiguous"
        ) from error
    owner.recovery_report_matches_binding(report)
    workspace = owner.recovery_workspace()
    start_head = state.get("execution_start_head")
    if not isinstance(start_head, str) or not start_head:
        raise WorkflowRecoveryError("execution start HEAD is unavailable")
    if stage == "checkpointing":
        if workspace.head == start_head and not workspace.dirty:
            product = owner.recovery_product(report.code_base_head)
            if owner.recovery_zero_delta(report, product):
                report = dataclasses.replace(report, workspace_head=start_head)
                owner.recovery_save_state(
                    "agent_running",
                    execution_stage="lifecycle",
                    pending_execution_report=report.as_dict(),
                    automatic_retry=owner.recovery_zero_delta_payload(report, product),
                )
                return
        owner.recovery_prove_product()
        if workspace.head == start_head:
            try:
                checkpoint = owner.recovery_workspace_manager(
                    report.ticket_id
                ).checkpoint(
                    workspace,
                    report.execution_id,
                    title=owner.recovery_ticket_title(report.ticket_id),
                    summary=(
                        report.result.claim.summary
                        if report.result.claim is not None
                        else report.result.reason
                    ),
                )
            except (ExecutionWorkspaceError, OSError) as error:
                raise WorkflowRecoveryError(
                    f"cannot recover execution checkpoint: {error}"
                ) from error
            checkpoint_head = checkpoint.after_head
        else:
            checkpoint_head = workspace.head
            if not owner.recovery_checkpoint_exact(
                workspace, checkpoint_head, start_head
            ):
                raise WorkflowRecoveryError(
                    "execution worktree contains ambiguous checkpoint history"
                )
        report = dataclasses.replace(report, workspace_head=checkpoint_head)
        workspace = owner.recovery_workspace()
        if workspace.head != checkpoint_head or workspace.dirty:
            raise WorkflowRecoveryError(
                "execution worktree changed or is dirty after checkpoint"
            )
        owner.recovery_save_state(
            "agent_running",
            execution_stage="post-checkpoint",
            pending_execution_report=report.as_dict(),
        )
        stage = "post-checkpoint"
    product = owner.recovery_product(report.code_base_head)
    classification = product["classification"]
    if classification == "unobservable":
        raise WorkflowRecoveryError(
            "product remote could not be observed; retrying when connectivity returns"
        )
    if classification == "unsafe-local" and not product.get("remote_head"):
        raise WorkflowRecoveryError(
            f"product generation is unsafe-local; {product['reason']}"
        )
    if owner.recovery_zero_delta(report, product):
        owner.recovery_save_state(
            "agent_running",
            execution_stage="lifecycle",
            pending_execution_report=report.as_dict(),
            automatic_retry=owner.recovery_zero_delta_payload(report, product),
        )
        return
    if classification != "stable":
        owner.recovery_persist_reconciliation(
            report, product, state.get("execution_remote_head")
        )
        raise WorkflowRecoveryError(
            "product generation changed; reconciliation required"
        )
    if stage in {"post-checkpoint", "publishing"}:
        if report.workspace_head is None or workspace.head != report.workspace_head:
            raise WorkflowRecoveryError(
                "execution worktree HEAD does not match the proven checkpoint"
            )
        if workspace.dirty:
            raise WorkflowRecoveryError(
                "execution worktree is dirty after the proven checkpoint"
            )
        remote = owner.recovery_execution_remote(report.execution_branch)
        expected = state.get("execution_remote_head")
        if expected is not None and not isinstance(expected, str):
            raise WorkflowRecoveryError("execution remote identity is invalid")
        if remote == expected:
            owner.recovery_publish_execution(workspace, report.workspace_head, expected)
            remote = owner.recovery_execution_remote(report.execution_branch)
        if remote != report.workspace_head:
            raise WorkflowRecoveryError(
                "execution remote does not match the proven checkpoint"
            )
        owner.recovery_save_state(
            "agent_running",
            execution_stage="post-publication",
            pending_execution_report=report.as_dict(),
        )
        stage = "post-publication"
    if stage == "post-publication":
        if report.workspace_head is None or workspace.head != report.workspace_head:
            raise WorkflowRecoveryError(
                "execution worktree HEAD changed after publication"
            )
        if (
            owner.recovery_execution_remote(report.execution_branch)
            != report.workspace_head
        ):
            raise WorkflowRecoveryError(
                "published execution checkpoint is no longer exact"
            )
        owner.recovery_save_state(
            "agent_running",
            execution_stage="lifecycle",
            pending_execution_report=report.as_dict(),
        )


def recover_lifecycle_report(owner: WorkflowRecoveryCapabilities) -> ExecutionReport:
    """Load and prove the report at the lifecycle WAL boundary."""
    state = owner.recovery_state()
    ticket_id = state.get("execution_ticket_id")
    execution_id = state.get("execution_id")
    if not isinstance(ticket_id, str) or not isinstance(execution_id, str):
        raise WorkflowRecoveryError("lifecycle recovery identity is incomplete")
    pending = state.get("pending_execution_report")
    if isinstance(pending, dict):
        try:
            report = ExecutionReport.from_dict(pending)
        except ExecutionReportError as error:
            raise WorkflowRecoveryError(
                f"pending lifecycle report is invalid: {error}"
            ) from error
    else:
        owner.recovery_validate_control()
        try:
            report = owner.recovery_report_from_store(ticket_id, execution_id)
        except ExecutionReportError as error:
            raise WorkflowRecoveryError(
                "lifecycle report evidence is unavailable; recovery is ambiguous"
            ) from error
    if not isinstance(state.get("local_head"), str) or not isinstance(
        state.get("remote_head"), str
    ):
        raise WorkflowRecoveryError("lifecycle product binding is incomplete")
    retry = state.get("automatic_retry")
    if (
        isinstance(retry, dict)
        and retry.get("status") == "pending"
        and report.workspace_head == report.code_base_head
    ):
        owner.recovery_report_matches_binding(report)
        if owner.recovery_execution_remote(report.execution_branch) is not None:
            raise WorkflowRecoveryError(
                "zero-delta retry execution branch is already published"
            )
    else:
        owner.recovery_observe_product_generation()
        owner.recovery_report_matches_state(report)
    return report


def recover_accepted_integration(owner: WorkflowRecoveryCapabilities) -> int:
    """Replay the accepted-integration transaction after a process crash."""
    pending = owner.recovery_state().get("accepted_integration")
    if not isinstance(pending, dict) or not isinstance(pending.get("ticket_id"), str):
        raise WorkflowRecoveryError("invalid accepted integration state")
    ticket_id = pending["ticket_id"]
    control_head, _ = owner.recovery_sync_control()
    if not owner.recovery_fetch_product():
        raise WorkflowRecoveryError(
            "cannot freshly observe product remote for accepted integration"
        )
    code = owner.recovery_code_observation()
    control = owner.recovery_control_observation()
    owner.recovery_integrate_accepted(ticket_id, code, control)
    owner.recovery_save_state(
        "idle",
        handled_remote_head=code.remote_head or "",
        handled_control_head=control_head,
        handled_todo_fingerprint=owner.recovery_todo_fingerprint(),
        accepted_integration=None,
    )
    owner.recovery_publish_ready()
    owner.recovery_log(f"accepted ticket {ticket_id} integrated")
    return 0
