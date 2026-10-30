# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure shaping of already observed status ticket values."""

_STATES = ("backlog", "todo", "review", "accepted", "done")


def ticket_counts(states: tuple[str, ...]) -> tuple[tuple[str, int], ...]:
    return tuple((name, states.count(name)) for name in _STATES)


def tickets_in_state(
    tickets: tuple[tuple[str, str, str], ...], state: str
) -> tuple[tuple[str, str], ...]:
    return tuple(
        (ticket_id, title) for ticket_id, title, value in tickets if value == state
    )
