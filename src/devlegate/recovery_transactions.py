# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-held recovery transactions.

The functions in this module contain recovery side-effect sequencing, but do
not own service state or scheduling.  The coordinator supplies a capability
for one serialized operation.  This keeps Git observations, evidence pins,
and durable state writes in the existing service owner while making the
transaction boundaries independently testable.
"""

from __future__ import annotations

from contextlib import nullcontext
from typing import Any, Protocol

from devlegate.git import (
    GitObservationError,
    create_ref_if_absent,
    reset_hard,
    resolve_commit,
    run_git,
)
from devlegate.recovery import classify_stranded_execution, interruption_kind


class ControlRecoveryCapabilities(Protocol):
    """Capabilities used by one authorized control-lineage transaction."""

    def recovery_lock(self) -> Any: ...

    def validate_control_reconcile_admission(
        self, from_head: str, to_head: str
    ) -> None: ...

    def validate_control_worktree(self) -> None: ...

    def reload_ticket_store(self) -> Any: ...

    def recovery_log(self, message: str) -> None: ...


def reconcile_control_lineage(
    owner: ControlRecoveryCapabilities,
    from_head: str,
    to_head: str,
    *,
    repo: Any,
    control_worktree: Any,
    take_lock: bool,
) -> int:
    """Adopt one freshly proved control head and retain displaced evidence.

    Admission is deliberately repeated immediately before the evidence pin and
    branch move.  The evidence ref uses a zero old value, so a replay either
    observes the original pin or fails closed rather than replacing evidence.
    """
    authority = owner.recovery_lock() if take_lock else nullcontext()
    with authority:
        owner.validate_control_reconcile_admission(from_head, to_head)
        evidence = f"refs/devlegate/recovery/control/{from_head}-{to_head}"
        # Repeat admission before the evidence pin as well as before the
        # worktree move.  No recovery evidence is created for a stale remote.
        owner.validate_control_reconcile_admission(from_head, to_head)
        try:
            existing = resolve_commit(repo, evidence, runner=run_git)
        except GitObservationError as error:
            raise ValueError(
                f"cannot observe control recovery evidence: {error}"
            ) from error
        if existing is None:
            # A failed create may be a concurrent writer.  The exact pin is
            # re-observed below, never replaced.
            create_ref_if_absent(repo, evidence, from_head, runner=run_git)
        try:
            pinned = resolve_commit(repo, evidence, runner=run_git)
        except GitObservationError as error:
            raise ValueError(
                f"cannot observe control recovery evidence: {error}"
            ) from error
        if pinned != from_head:
            raise ValueError(
                "control recovery evidence ref already points to another commit"
            )

        # The first admission can race with a remote update.  Re-prove the
        # exact source and target before changing the local control worktree.
        owner.validate_control_reconcile_admission(from_head, to_head)
        moved = reset_hard(control_worktree, to_head, runner=run_git)
        if moved.returncode:
            raise ValueError(f"control lineage adoption failed: {moved.stderr.strip()}")
        owner.validate_control_worktree()
        owner.reload_ticket_store()
        owner.recovery_log(
            f"control rewrite reconciliation accepted: {from_head} -> {to_head}"
        )
        owner.recovery_log(f"preserved displaced control head under {evidence}")
        return 0


class StrandedRecoveryCapabilities(Protocol):
    """Capabilities needed to normalize a worker after ownership is proven absent."""

    def recovery_save_state(self, state: str, **values: object) -> None: ...

    def recovery_worker_observation(self, identity: Any) -> str: ...

    def recovery_worker_identity(self, value: object, execution_id: object) -> Any: ...

    def normalize_stranded_execution(self, interruption: str) -> None: ...

    def recovery_publish_blocked(self, reason: str) -> None: ...


def recover_stranded_execution(
    owner: StrandedRecoveryCapabilities, state: dict[str, object]
) -> str | None:
    """Resolve only post-worker or proven-absent stranded executions.

    An operator abort remains an interruption mechanism.  It is recorded and
    never converted here into continuation intent or an automatic retry.
    """
    try:
        kind = interruption_kind(state)
    except ValueError as error:
        raise ValueError(str(error)) from error
    if state.get("execution_interruption_kind") is None:
        owner.recovery_save_state("agent_running", execution_interruption_kind=kind)
    decision = classify_stranded_execution(state)
    stage = state.get("execution_stage")
    identity = owner.recovery_worker_identity(
        state.get("worker_identity"), state.get("execution_id")
    )
    if decision.action == "prove-worker-absent":
        if identity is None:
            raise ValueError("previous worker ownership cannot be proven absent")
        observation = owner.recovery_worker_observation(identity)
        if observation == "absent":
            owner.normalize_stranded_execution(kind)
            return str(state["execution_ticket_id"])
        if observation == "matching-live":
            reason = (
                "previous Devlegate owner was lost; execution worker is still alive; "
                "automatic reconciliation is blocked"
            )
        else:
            reason = (
                "previous Devlegate owner was lost; worker ownership is indeterminate; "
                "reconciliation is blocked"
            )
        owner.recovery_publish_blocked(reason)
        raise ValueError(reason)
    if decision.action == "normalize":
        owner.normalize_stranded_execution(kind)
        return str(state["execution_ticket_id"])
    reason = (
        "previous Devlegate owner was lost during unsafe execution stage "
        f"{stage or 'unknown'}; reconciliation is blocked"
    )
    owner.recovery_publish_blocked(reason)
    raise ValueError(reason)
