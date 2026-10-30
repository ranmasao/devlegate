# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import pytest

from devlegate.cleanup_dependency import accepted_integration_dependency_reason


@pytest.mark.parametrize(
    ("pending_ticket", "pending_checkpoint", "reason"),
    [
        ("T-2", "b" * 40, None),
        ("T-1", "a" * 40, "accepted execution has a pending integration dependency"),
        (
            "T-1",
            "b" * 40,
            "accepted integration dependency contradicts accepted execution",
        ),
    ],
)
def test_accepted_integration_dependency_reason(
    pending_ticket, pending_checkpoint, reason
):
    assert accepted_integration_dependency_reason(
        "T-1", "a" * 40, pending_ticket, pending_checkpoint
    ) == reason
