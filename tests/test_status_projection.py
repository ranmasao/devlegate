# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from itertools import product
from pathlib import Path

from devlegate.runtime import BlockedReason
from devlegate.status_projection import blocked_tickets
from devlegate.tickets import Ticket, TicketStore


def original_projection(
    store, dependency_blocked, eligible, bound, integration, barrier
):
    """The original runtime blocked-entry algorithm, used as a differential oracle."""
    eligible_ids = {ticket_id for ticket_id, _ in eligible}
    dependency_entries = {
        i: (title, blockers) for i, title, blockers in dependency_blocked
    }
    entries = []
    for ticket in store.tickets:
        if ticket.state != "todo" or ticket.id == bound:
            continue
        if ticket.id in dependency_entries:
            title, blockers = dependency_entries[ticket.id]
            reason = BlockedReason(
                "dependencies",
                tickets=tuple(
                    (dep, "integration-in-progress" if dep == integration else state)
                    for dep, state in blockers
                ),
            )
            if (
                integration is not None
                and len(blockers) == 1
                and blockers[0][0] == integration
            ):
                reason = BlockedReason("integration-in-progress", ticket_id=integration)
            entries.append((ticket.id, title, reason))
        elif ticket.id not in eligible_ids:
            assert barrier is not None
            entries.append((ticket.id, ticket.title, barrier))
    return tuple(entries)


def test_blocked_projection_matches_original_across_observations():
    def ticket(id, state, dependencies=()):
        return Ticket(id, state, id + " title", dependencies, "body", Path(id))

    stores = [
        TicketStore(()),
        TicketStore(
            (
                ticket("B", "todo", ("A",)),
                ticket("A", "accepted"),
                ticket("C", "todo"),
                ticket("D", "review"),
            )
        ),
        TicketStore(
            (
                ticket("B", "todo", ("A", "D")),
                ticket("A", "done"),
                ticket("C", "todo"),
                ticket("D", "review"),
            )
        ),
        TicketStore(
            (
                ticket("B", "todo", ("A", "D")),
                ticket("A", "accepted"),
                ticket("C", "todo"),
                ticket("D", "review"),
            )
        ),
    ]
    for store, integration, bound, runnable in product(
        stores, (None, "A"), (None, "B", "C"), (False, True)
    ):
        observed = tuple(
            (
                t.id,
                t.title,
                tuple(
                    (d, store.by_id[d].state)
                    for d in sorted(t.depends_on)
                    if store.by_id[d].state != "done"
                ),
            )
            for t in store.tickets
            if t.state == "todo"
            and any(store.by_id[d].state != "done" for d in t.depends_on)
        )
        eligible = tuple((t.id, t.title) for t in store.runnable) if runnable else ()
        barrier = BlockedReason("review", ticket_id="D")
        actual = blocked_tickets(
            store.tickets,
            observed,
            eligible,
            bound,
            integration,
            barrier,
            BlockedReason,
        )
        assert actual == original_projection(
            store, observed, eligible, bound, integration, barrier
        )
