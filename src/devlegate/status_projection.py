# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Shape blocked status entries from owner-acquired immutable observations.

Admission, freshness, ticket loading and dependency observation belong to the
runtime. This projection neither observes the repository nor selects work.
"""

from collections.abc import Callable

from devlegate.tickets import Ticket


def blocked_tickets[Reason](
    tickets: tuple[Ticket, ...],
    dependency_blocked: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...],
    eligible: tuple[tuple[str, str], ...],
    bound_ticket_id: str | None,
    integration_ticket_id: str | None,
    global_reason: Reason | None,
    reason_factory: Callable[..., Reason],
) -> tuple[tuple[str, str, Reason], ...]:
    eligible_ids = {ticket_id for ticket_id, _ in eligible}
    dependencies = {
        ticket_id: (title, blockers)
        for ticket_id, title, blockers in dependency_blocked
    }
    entries = []
    for ticket in tickets:
        if ticket.state != "todo" or ticket.id == bound_ticket_id:
            continue
        if ticket.id in dependencies:
            title, blockers = dependencies[ticket.id]
            reason = reason_factory(
                "dependencies",
                tickets=tuple(
                    (
                        dependency,
                        "integration-in-progress"
                        if dependency == integration_ticket_id
                        else state,
                    )
                    for dependency, state in blockers
                ),
            )
            if (
                integration_ticket_id is not None
                and len(blockers) == 1
                and blockers[0][0] == integration_ticket_id
            ):
                reason = reason_factory(
                    "integration-in-progress", ticket_id=integration_ticket_id
                )
            entries.append((ticket.id, title, reason))
        elif ticket.id not in eligible_ids:
            assert global_reason is not None
            entries.append((ticket.id, ticket.title, global_reason))
    return tuple(entries)
