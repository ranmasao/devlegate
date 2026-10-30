# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from devlegate.status_projection import ticket_counts, tickets_in_state


def test_ticket_summary_preserves_order_and_known_state_counts():
    observed = (
        ("T-2", "Second", "review"),
        ("T-1", "First", "todo"),
        ("T-3", "Third", "review"),
        ("T-4", "Fourth", "accepted"),
    )
    assert ticket_counts(tuple(ticket[2] for ticket in observed)) == (
        ("backlog", 0), ("todo", 1), ("review", 2), ("accepted", 1), ("done", 0)
    )
    assert tickets_in_state(observed, "review") == (
        ("T-2", "Second"), ("T-3", "Third")
    )
    assert tickets_in_state(observed, "accepted") == (("T-4", "Fourth"),)
    assert tickets_in_state(observed, "done") == ()
