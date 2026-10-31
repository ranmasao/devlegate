# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Conservative read-only assessment of execution resource evidence.

An admission intent is not a resource binding. In particular, the nonce in an
intent is not installed in, or independently correlated with, Git resources.
Until resource transitions are journaled, callers must not use an intent to
authorize operations on those resources.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping


@dataclass(frozen=True)
class ExecutionResourceProof:
    ticket_id: str
    execution_id: str
    proven: bool
    reason: str


def inspect_execution_resource_proof(
    state: Mapping[str, object], ticket_id: str, execution_id: str
) -> ExecutionResourceProof:
    """Assess saved evidence without changing state or observing Git.

    No currently persisted record can establish continuity, including an intent
    whose path, branch and checkpoint happen to match a current observation.
    """
    intents = state.get("execution_resource_intents")
    if not isinstance(intents, dict):
        reason = "owner-published resource binding is missing or malformed"
    else:
        intent = intents.get(execution_id)
        if not isinstance(intent, dict):
            reason = "owner-published resource binding is missing"
        elif (
            intent.get("execution_id") != execution_id
            or intent.get("ticket_id") != ticket_id
        ):
            reason = "resource intent identity does not match the requested execution"
        else:
            reason = "resource intent does not prove creation or continuity"
    return ExecutionResourceProof(ticket_id, execution_id, False, reason)
