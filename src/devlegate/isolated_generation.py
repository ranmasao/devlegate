# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Exact new-generation binding and the existing before-effect journal contract.

These checks bind a recorded intent, not Git resource ownership. Callers must
still observe refs, registration, path, leases and worker absence before effects.
"""

from pathlib import Path

from .execution_workspace import ExecutionWorkspaceError, ExecutionWorkspaceIdentity

PREPARATION_STAGES = (
    "admitted", "source_pinning", "candidate_pinning", "candidate_pinned",
    "target_creating", "target_created", "worktree_attaching", "worktree_attached",
    "remote_publishing", "remote_published",
)
PUBLICATION_STAGES = PREPARATION_STAGES[3:]
PINNING_STAGES = PREPARATION_STAGES[:4]


def target_binding(identity: ExecutionWorkspaceIdentity) -> dict[str, object]:
    return {
        "branch": identity.branch, "path": str(identity.path),
        "ref": f"refs/heads/{identity.branch}", "expected_old_head": None,
    }


def bound_generation(
    root: Path, state: dict[str, object]
) -> ExecutionWorkspaceIdentity:
    """One recorded generation identity shared by preparation/worker/recovery."""
    rework = state.get("rework_provenance")
    ticket, execution = state.get("execution_ticket_id"), state.get("execution_id")
    if not (isinstance(rework, dict) and rework.get("mode") == "isolated"
            and isinstance(ticket, str) and isinstance(execution, str)):
        raise ExecutionWorkspaceError("isolated intent identity is incomplete")
    identity = ExecutionWorkspaceIdentity.for_ticket(root, ticket, generation=execution)
    if (rework.get("target") != target_binding(identity)
            or state.get("execution_branch") != identity.branch
            or state.get("execution_path") != str(identity.path)):
        raise ExecutionWorkspaceError("isolated intent binding is invalid")
    if rework.get("preparation_stage") not in PREPARATION_STAGES:
        raise ExecutionWorkspaceError("isolated intent preparation stage is invalid")
    return identity


def validate_transition(previous: object, following: object) -> None:
    """Only the same or next recorded stage is legal; never guess on restart."""
    if previous not in PREPARATION_STAGES or following not in PREPARATION_STAGES:
        raise ExecutionWorkspaceError("isolated preparation stage is invalid")
    old, new = PREPARATION_STAGES.index(previous), PREPARATION_STAGES.index(following)
    if new not in {old, old + 1}:
        raise ExecutionWorkspaceError(
            f"isolated preparation transition is invalid: {previous} -> {following}"
        )
