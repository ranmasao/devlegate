# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure recovery decisions shared by the runtime coordinator and tests.

This module deliberately has no Git, SQLite, worker, or ServiceEngine imports.
It describes what the persisted runtime state permits; the service owner still
performs every resulting mutation.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping


INTERRUPTION_KINDS = frozenset({"operator_abort", "service_shutdown", "process_loss"})
WORKER_STAGES = frozenset({"worker-launch", "worker-running", "post-worker"})
CHECKPOINT_STAGES = frozenset(
    {"checkpointing", "post-checkpoint", "publishing", "post-publication"}
)


@dataclasses.dataclass(frozen=True)
class RecoveryDecision:
    """A read-only decision. It never authorizes a side effect by itself."""

    action: str
    reason: str
    ticket_id: str | None = None


def interruption_kind(state: Mapping[str, object]) -> str:
    """Return the persisted interruption kind, defaulting to process loss."""
    value = state.get("execution_interruption_kind")
    if value is None:
        return "process_loss"
    if value not in INTERRUPTION_KINDS:
        raise ValueError("invalid execution interruption kind")
    return str(value)


def classify_stranded_execution(state: Mapping[str, object]) -> RecoveryDecision:
    """Classify a lost owner without inspecting or changing external state."""
    phase = state.get("phase")
    stage = state.get("execution_stage")
    if phase != "agent_running":
        return RecoveryDecision("not-stranded", "execution is not running")
    ticket_id = state.get("execution_ticket_id")
    ticket = ticket_id if isinstance(ticket_id, str) and ticket_id else None
    if stage == "worker-running":
        return RecoveryDecision(
            "prove-worker-absent", "worker ownership must be observed", ticket
        )
    if stage == "post-worker":
        return RecoveryDecision("normalize", "post-worker state can be normalized", ticket)
    return RecoveryDecision(
        "blocked",
        f"unsafe execution stage {stage or 'unknown'} requires explicit recovery",
        ticket,
    )


def classify_stage(stage: object) -> str:
    """Map a durable stage to its recovery transaction boundary."""
    if stage in CHECKPOINT_STAGES:
        return "checkpoint-publication"
    if stage == "lifecycle":
        return "lifecycle"
    if stage in WORKER_STAGES:
        return "worker"
    if stage in {None, "pre-checkpoint"}:
        return "pre-checkpoint"
    return "unknown"


def validate_execution_binding(
    state: Mapping[str, object], *, require_pre_worker: bool = False
) -> tuple[str, str, str, str, str]:
    """Validate the immutable identity fields before a recovery operation."""
    fields = (
        "execution_ticket_id",
        "execution_id",
        "execution_base_head",
        "execution_branch",
        "execution_path",
    )
    values = tuple(state.get(field) for field in fields)
    if not all(isinstance(value, str) and value for value in values):
        raise ValueError("persisted execution workspace binding is incomplete")
    if require_pre_worker and state.get("phase") != "agent_pending":
        raise ValueError("automatic workspace recovery is limited to pre-worker state")
    if state.get("worker_identity") is not None:
        raise ValueError("persisted worker ownership prevents workspace recovery")
    if state.get("pending_execution_report") is not None:
        raise ValueError("durable worker result prevents workspace recovery")
    return values  # type: ignore[return-value]
