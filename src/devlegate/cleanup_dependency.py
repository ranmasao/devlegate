# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure comparisons for validated cleanup dependency identities."""


def accepted_integration_dependency_reason(
    ticket_id: str, checkpoint: str, pending_ticket: str, pending_checkpoint: str
) -> str | None:
    """Compare immutable, already validated identity values at exact SHA."""
    if pending_ticket != ticket_id:
        return None
    if pending_checkpoint != checkpoint:
        return "accepted integration dependency contradicts accepted execution"
    return "accepted execution has a pending integration dependency"
