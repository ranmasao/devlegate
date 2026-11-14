# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Strict Devlegate ticket storage and dependency scheduling."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ._vendor.nanoyaml import NanoYAMLError, loads


class TicketError(ValueError):
    """Raised when managed ticket storage or its graph is invalid."""


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
WORKFLOW_STATES = ("backlog", "todo", "review", "accepted", "done", "abandoned")
WORKFLOW_TRANSITIONS = {
    "backlog": frozenset({"todo"}),
    "todo": frozenset({"review", "abandoned"}),
    "review": frozenset({"todo", "backlog", "accepted", "abandoned"}),
    "accepted": frozenset({"done", "abandoned"}),
    "done": frozenset(),
    "abandoned": frozenset(),
}
_METADATA_KEYS = {"type", "title", "depends_on"}
_ABANDONMENT_KEYS = _METADATA_KEYS | {"target", "start_head"}
_HEAD = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


def is_canonical_ticket_name(name: str) -> bool:
    """Return whether a managed-directory entry claims canonical ticket identity."""
    return name.endswith(".md") and bool(_ID.fullmatch(name[:-3]))


def validate_workflow_transition(ticket: Ticket, target: str) -> None:
    """Validate a canonical edge; its owner must also publish the required audit."""
    if target not in WORKFLOW_TRANSITIONS.get(ticket.state, frozenset()):
        raise TicketError(
            f"workflow prohibits {ticket.state} -> {target} for {ticket.id}; "
            "acceptance and terminal disposition have no reverse requeue edge"
        )


def requeue_transition(ticket: Ticket) -> None:
    """Preserving todo is a no-op; all moves use the canonical workflow edges."""
    if ticket.state != "todo":
        validate_workflow_transition(ticket, "todo")


@dataclass(frozen=True)
class Ticket:
    id: str
    state: str
    title: str
    depends_on: tuple[str, ...]
    body: str
    path: Path
    type: str = "devlegate.ticket"
    target: str | None = None
    start_head: str | None = None
    abandonment_reason: str | None = None


@dataclass(frozen=True)
class TicketStore:
    tickets: tuple[Ticket, ...]
    _index: Mapping[str, Ticket] = field(init=False, repr=False, compare=False)
    _runnable: tuple[Ticket, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {ticket.id: ticket for ticket in self.tickets}
        reserved = {
            ticket.target
            for ticket in self.tickets
            if ticket.type == "devlegate.abandonment"
            and ticket.state in {"todo", "review", "accepted"}
        }
        runnable = tuple(
            sorted(
                (
                    ticket
                    for ticket in self.tickets
                    if ticket.state == "todo"
                    and ticket.id not in reserved
                    and all(
                        index[dependency].state == "done"
                        for dependency in ticket.depends_on
                    )
                ),
                key=lambda ticket: (ticket.type != "devlegate.abandonment", ticket.id),
            )
        )
        object.__setattr__(self, "_index", MappingProxyType(index))
        object.__setattr__(self, "_runnable", runnable)

    @property
    def by_id(self) -> Mapping[str, Ticket]:
        return self._index

    @property
    def runnable(self) -> tuple[Ticket, ...]:
        return self._runnable

    def selected(self) -> Ticket | None:
        return self._runnable[0] if self._runnable else None


def _ticket_error(path: Path, reason: str) -> TicketError:
    return TicketError(f"invalid ticket {path}: {reason}")


def _split_ticket(path: Path, text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise _ticket_error(path, "missing opening frontmatter delimiter")
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], 1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing is None:
        raise _ticket_error(path, "missing closing frontmatter delimiter")
    metadata = "".join(lines[1:closing])
    body = "".join(lines[closing + 1 :])
    if not body.strip():
        raise _ticket_error(path, "ticket body is empty")
    return metadata, body


def _parse_ticket(path: Path, state: str) -> Ticket:
    return parse_ticket_text(path, state, path.read_text())


def parse_ticket_text(path: Path, state: str, text: str) -> Ticket:
    """Validate immutable ticket text without materializing a control checkout."""
    ticket_id = path.stem
    if not is_canonical_ticket_name(path.name):
        raise _ticket_error(path, "filename must be a valid ticket ID ending in .md")
    metadata_text, body = _split_ticket(path, text)
    try:
        metadata = loads(metadata_text)
    except (NanoYAMLError, TypeError) as error:
        raise _ticket_error(path, f"invalid NanoYAML metadata: {error}") from error
    kind = metadata.get("type")
    keys = _ABANDONMENT_KEYS if kind == "devlegate.abandonment" else _METADATA_KEYS
    if not set(metadata).issubset(keys):
        unknown = sorted(set(metadata) - keys)
        if unknown:
            raise _ticket_error(path, f"unknown metadata key {unknown[0]!r}")
    for key in ("type", "title"):
        if key not in metadata:
            raise _ticket_error(path, f'missing required metadata key "{key}"')
    if kind not in {"devlegate.ticket", "devlegate.abandonment"}:
        raise _ticket_error(
            path, 'metadata "type" must be "devlegate.ticket" '
            'or "devlegate.abandonment"'
        )
    title = metadata["title"]
    if not isinstance(title, str) or not title.strip():
        raise _ticket_error(path, 'metadata "title" must be a non-empty string')
    if len(title) > 160 or any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in title
    ):
        raise _ticket_error(
            path,
            'metadata "title" must be one readable line of at most 160 characters',
        )
    dependencies = metadata.get("depends_on", [])
    if "depends_on" in metadata and (
        not isinstance(dependencies, list) or not dependencies
    ):
        raise _ticket_error(path, 'metadata "depends_on" must be a non-empty sequence')
    if not all(isinstance(dependency, str) for dependency in dependencies):
        raise _ticket_error(path, 'metadata "depends_on" entries must be strings')
    if len(set(dependencies)) != len(dependencies):
        raise _ticket_error(path, "metadata contains duplicate dependency IDs")
    for dependency in dependencies:
        if not _ID.fullmatch(dependency):
            raise _ticket_error(path, f"invalid dependency ID {dependency!r}")
        if dependency == ticket_id:
            raise _ticket_error(path, "ticket cannot depend on itself")
    target = start_head = reason = None
    if kind == "devlegate.abandonment":
        target = metadata.get("target")
        start_head = metadata.get("start_head")
        if not isinstance(target, str) or not _ID.fullmatch(target):
            raise _ticket_error(path, 'metadata "target" must be exactly one ticket ID')
        if target == ticket_id:
            raise _ticket_error(path, "abandonment request cannot target itself")
        if not isinstance(start_head, str) or not _HEAD.fullmatch(start_head):
            raise _ticket_error(
                path, 'metadata "start_head" must be a full product HEAD'
            )
        reason = _required_section(path, body, "Abandonment reason")
        _required_section(path, body, "Report requirements")
    return Ticket(
        ticket_id, state, title.strip(), tuple(dependencies), body, path,
        kind, target, start_head, reason,
    )


def _required_section(path: Path, body: str, heading: str) -> str:
    matches = list(re.finditer(rf"(?m)^#{{1,6}} {heading}[ \t]*$", body))
    if len(matches) != 1:
        raise _ticket_error(path, f"body must contain exactly one {heading!r} section")
    tail = body[matches[0].end():]
    content = re.split(r"(?m)^#{1,6} ", tail, maxsplit=1)[0].strip()
    if not content or not re.search(r"[A-Za-z0-9]", content):
        raise _ticket_error(path, f"{heading} must contain concrete text")
    return content


def _validate_cycles(tickets: Mapping[str, Ticket]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()
    stack: list[str] = []

    def visit(ticket_id: str) -> None:
        if ticket_id in visited:
            return
        if ticket_id in visiting:
            start = stack.index(ticket_id)
            cycle = stack[start:] + [ticket_id]
            raise TicketError(f"dependency cycle: {' -> '.join(cycle)}")
        visiting.add(ticket_id)
        stack.append(ticket_id)
        for dependency in sorted(tickets[ticket_id].depends_on):
            visit(dependency)
        stack.pop()
        visiting.remove(ticket_id)
        visited.add(ticket_id)

    for ticket_id in sorted(tickets):
        visit(ticket_id)


def load_ticket_store(repo: Path, workflow_paths: Mapping[str, str]) -> TicketStore:
    """Load, validate, and graph-check all configured workflow directories."""
    tickets: list[Ticket] = []
    locations: dict[str, list[Path]] = {}
    if set(workflow_paths) != set(WORKFLOW_STATES):
        raise TicketError("workflow paths must configure exactly all six states")
    for state in WORKFLOW_STATES:
        directory = repo / workflow_paths[state]
        if directory.is_symlink() or not directory.is_dir():
            raise TicketError(f"managed workflow directory is unavailable: {directory}")
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not is_canonical_ticket_name(path.name):
                continue
            if path.is_symlink() or not path.is_file():
                raise TicketError(f"invalid managed ticket entry: {path}")
            ticket = _parse_ticket(path, state)
            locations.setdefault(ticket.id, []).append(path)
            tickets.append(ticket)
    for ticket_id, paths in sorted(locations.items()):
        if len(paths) > 1:
            formatted = "\n".join(f"  {path}" for path in paths)
            raise TicketError(f"duplicate ticket ID {ticket_id}:\n{formatted}")
    by_id = {ticket.id: ticket for ticket in tickets}
    for ticket in sorted(tickets, key=lambda item: item.id):
        for dependency in ticket.depends_on:
            if dependency not in by_id:
                raise TicketError(
                    f"missing dependency: {ticket.id} depends on {dependency}"
                )
    _validate_cycles(by_id)
    reservations: dict[str, str] = {}
    for ticket in tickets:
        if ticket.type != "devlegate.abandonment":
            continue
        target = by_id.get(ticket.target or "")
        if target is None:
            raise _ticket_error(
                ticket.path, f"missing abandonment target {ticket.target}"
            )
        if target.type != "devlegate.ticket":
            raise _ticket_error(ticket.path, "target must be an implementation ticket")
        if ticket.state in {"todo", "review", "accepted"}:
            if target.state in {"done", "abandoned"}:
                raise _ticket_error(
                    ticket.path, "abandonment target is already terminal"
                )
            if target.id in reservations:
                raise _ticket_error(
                    ticket.path,
                    f"conflicting abandonment request {reservations[target.id]} "
                    f"already reserves {target.id}",
                )
            reservations[target.id] = ticket.id
    return TicketStore(tuple(sorted(tickets, key=lambda item: item.id)))


__all__ = [
    "Ticket",
    "TicketError",
    "TicketStore",
    "WORKFLOW_STATES",
    "is_canonical_ticket_name",
    "load_ticket_store",
]
