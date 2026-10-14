# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Fail-closed recovery of an admitted execution workspace.

This module owns the read/repair transaction for the pre-worker boundary.  It
does not own runtime state, Git authority, or worker scheduling.  Those remain
capabilities of the serialized ``ServiceEngine`` owner.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Protocol

from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
    WorkspaceInspection,
)
from devlegate.recovery import validate_execution_binding


class WorkspaceRecoveryCapabilities(Protocol):
    """Narrow owner operations required by workspace recovery."""

    repo: Path
    control_worktree: Path
    execution_worktree_root: Path

    def recovery_git(self, cwd: Path, *args: str, check: bool = False) -> Any: ...

    def recovery_workspace_manager(
        self, ticket_id: str
    ) -> ExecutionWorkspaceManager: ...

    def recovery_reports(self, ticket_id: str) -> list[Any]: ...


def inspect_bound_workspace(
    owner: WorkspaceRecoveryCapabilities, state: dict[str, object]
) -> WorkspaceInspection:
    """Classify a persisted pre-worker binding without changing the repository."""
    values = tuple(
        state.get(field)
        for field in (
            "execution_ticket_id",
            "execution_id",
            "execution_base_head",
            "execution_branch",
            "execution_path",
        )
    )
    if not all(isinstance(value, str) and value for value in values):
        return WorkspaceInspection(
            "UNSAFE", "persisted execution workspace binding is incomplete"
        )
    ticket_id, execution_id, base_head, branch, path = values
    try:
        validate_execution_binding(state, require_pre_worker=True)
        manager = owner.recovery_workspace_manager(ticket_id)
    except (ValueError, ExecutionWorkspaceError, OSError) as error:
        return WorkspaceInspection("UNSAFE", str(error))
    if branch != manager.branch or Path(path) != manager.path:
        return WorkspaceInspection(
            "UNSAFE", "persisted execution workspace binding is invalid"
        )
    expected_head = state.get("execution_start_head")
    topology = manager.inspect(
        base_head, expected_head if isinstance(expected_head, str) else None
    )
    if topology.classification == "REUSABLE":
        return topology

    branch_ref = f"refs/heads/{manager.branch}"
    observed = owner.recovery_git(owner.repo, "rev-parse", "--verify", branch_ref)
    if observed.returncode:
        if topology.reason != "execution branch is missing":
            return WorkspaceInspection("UNSAFE", topology.reason)
        try:
            registrations = manager._registrations()
        except (ExecutionWorkspaceError, OSError):
            return WorkspaceInspection(
                "UNSAFE", "cannot inspect execution worktree registrations"
            )
        attached = next(
            (
                item_path
                for item_path, item in registrations.items()
                if item.get("branch") == manager.branch
            ),
            None,
        )
        if attached is not None or manager.path.exists():
            return WorkspaceInspection(
                "UNSAFE",
                "execution branch is missing but workspace material remains attached",
            )
        return WorkspaceInspection(
            "RECOVERABLE", "execution branch is missing; exact workspace can be rebuilt"
        )

    old_head = observed.stdout.strip()
    try:
        reports = owner.recovery_reports(ticket_id)
    except (ExecutionWorkspaceError, OSError, ValueError) as error:
        return WorkspaceInspection("UNSAFE", f"cannot prove branch provenance: {error}")
    prior = [
        report
        for report in reports
        if report.execution_id != execution_id
        and report.execution_branch == manager.branch
        and report.workspace_head == old_head
    ]
    if len(prior) != 1:
        return WorkspaceInspection(
            "UNSAFE",
            "conflicting execution branch ownership cannot be proven; "
            "operator recovery required",
        )
    return WorkspaceInspection(
        "RECOVERABLE",
        "conflicting execution branch is exactly attributable to a prior "
        "execution generation",
    )


def repair_bound_workspace(
    owner: WorkspaceRecoveryCapabilities, state: dict[str, object]
) -> None:
    """Replace an older, exactly attributed branch generation using Git CAS."""
    if (
        state.get("phase") != "agent_pending"
        or state.get("worker_identity") is not None
    ):
        raise ValueError("bound execution ownership is not proven absent")
    try:
        ticket_id, execution_id, base_head, branch, path = validate_execution_binding(
            state, require_pre_worker=True
        )
    except ValueError as error:
        raise ValueError(str(error)) from error
    manager = owner.recovery_workspace_manager(ticket_id)
    if branch != manager.branch or Path(path) != manager.path:
        raise ValueError("persisted execution workspace binding is invalid")

    branch_ref = f"refs/heads/{manager.branch}"
    observed = owner.recovery_git(owner.repo, "rev-parse", "--verify", branch_ref)
    if observed.returncode:
        manager.prepare(base_head)
        return
    old_head = observed.stdout.strip()
    prior = [
        report
        for report in owner.recovery_reports(ticket_id)
        if report.execution_id != execution_id
        and report.execution_branch == manager.branch
        and report.workspace_head == old_head
    ]
    if len(prior) != 1:
        raise ValueError(
            "conflicting execution branch ownership cannot be proven; "
            "operator recovery required"
        )
    evidence_ref = (
        f"refs/devlegate/recovery/workspace/{ticket_id}/{prior[0].execution_id}"
    )
    existing = owner.recovery_git(owner.repo, "rev-parse", "--verify", evidence_ref)
    if existing.returncode == 0 and existing.stdout.strip() != old_head:
        raise ValueError("workspace recovery evidence ref has conflicting identity")
    if existing.returncode:
        pinned = owner.recovery_git(
            owner.repo, "update-ref", evidence_ref, old_head, ""
        )
        if pinned.returncode:
            reread = owner.recovery_git(
                owner.repo, "rev-parse", "--verify", evidence_ref
            )
            if reread.returncode or reread.stdout.strip() != old_head:
                raise ValueError("cannot preserve conflicting execution checkpoint")

    reobserved = owner.recovery_git(owner.repo, "rev-parse", "--verify", branch_ref)
    if reobserved.returncode or reobserved.stdout.strip() != old_head:
        raise ValueError("execution branch changed before automatic repair")
    registrations = manager._registrations()
    registration = registrations.get(manager.path.resolve())
    branch_path = next(
        (
            item_path
            for item_path, item in registrations.items()
            if item.get("branch") == manager.branch
        ),
        None,
    )
    if branch_path is not None and branch_path != manager.path.resolve():
        raise ValueError("execution branch moved to an unexpected worktree")
    if manager.path.exists() and registration is None:
        raise ValueError("execution worktree path is not safely registered")
    if registration is not None:
        workspace = manager._validate_existing(registration, old_head)
        if workspace.head != old_head or workspace.dirty:
            raise ValueError("conflicting execution worktree is not clean and exact")
        removed = owner.recovery_git(
            owner.repo, "worktree", "remove", "--force", str(manager.path)
        )
        if removed.returncode:
            raise ValueError("cannot remove conflicting execution worktree")
    reobserved = owner.recovery_git(owner.repo, "rev-parse", "--verify", branch_ref)
    if reobserved.returncode or reobserved.stdout.strip() != old_head:
        raise ValueError("execution branch changed during automatic repair")
    moved = owner.recovery_git(
        owner.repo, "update-ref", branch_ref, base_head, old_head
    )
    if moved.returncode:
        raise ValueError("execution branch changed before CAS repair")
    repaired = manager.prepare(base_head)
    final = manager.inspect(base_head, repaired.head)
    if final.classification != "REUSABLE":
        raise ValueError(
            f"repaired execution workspace is not reusable: {final.reason}"
        )
