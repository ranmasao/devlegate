# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure proof of an accepted integration's cleanup dependency."""

from dataclasses import dataclass

from devlegate.runtime_state_validation import _is_git_identity


@dataclass(frozen=True)
class IntegrationSnapshot:
    is_record: bool
    keys: frozenset[str]
    ticket_id: str | None
    checkpoint: str | None
    control_head: str | None
    control_predecessor: str | None


def snapshot_integration(value: object) -> IntegrationSnapshot | None:
    """Copy the relevant state into immutable values without validating it."""
    if value is None:
        return None
    if not isinstance(value, dict):
        return IntegrationSnapshot(False, frozenset(), None, None, None, None)

    def string(field: str) -> str | None:
        item = value.get(field)
        return item if isinstance(item, str) else None

    # Any non-string key is necessarily outside the allowed record shapes.
    keys = (
        frozenset(value) if all(isinstance(key, str) for key in value) else frozenset()
    )
    return IntegrationSnapshot(
        True, keys, string("ticket_id"), string("checkpoint"),
        string("control_head"), string("control_predecessor"),
    )


def accepted_integration_dependency_reason(
    ticket_id: str, checkpoint: str, pending: IntegrationSnapshot | None
) -> str | None:
    """Validate the complete record shape and decide its exact-SHA binding."""
    if pending is None:
        return None
    required = frozenset({"ticket_id", "checkpoint", "control_head"})
    if (
        not pending.is_record
        or not required.issubset(pending.keys)
        or not pending.keys.issubset(
            required | {"control_predecessor", "product_transition"}
        )
        or not pending.ticket_id
        or not _is_git_identity(pending.checkpoint)
        or not _is_git_identity(pending.control_head)
        or (
            "control_predecessor" in pending.keys
            and not _is_git_identity(pending.control_predecessor)
        )
    ):
        return "accepted integration dependency identity is invalid"
    if pending.ticket_id != ticket_id:
        return None
    if pending.checkpoint != checkpoint:
        return "accepted integration dependency contradicts accepted execution"
    return "accepted execution has a pending integration dependency"
