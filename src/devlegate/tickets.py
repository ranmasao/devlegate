"""Strict Devlegate ticket storage and dependency scheduling."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import Mapping

from nanoyaml import NanoYAMLError, loads


class TicketError(ValueError):
    """Raised when managed ticket storage or its graph is invalid."""


_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_WORKFLOW_STATES = ("backlog", "todo", "review", "accepted", "done")
_METADATA_KEYS = {"type", "title", "depends_on"}


def is_canonical_ticket_name(name: str) -> bool:
    """Return whether a managed-directory entry claims canonical ticket identity."""
    return name.endswith(".md") and bool(_ID.fullmatch(name[:-3]))


@dataclass(frozen=True)
class Ticket:
    id: str
    state: str
    title: str
    depends_on: tuple[str, ...]
    body: str
    path: Path


@dataclass(frozen=True)
class TicketStore:
    tickets: tuple[Ticket, ...]
    _index: Mapping[str, Ticket] = field(init=False, repr=False, compare=False)
    _runnable: tuple[Ticket, ...] = field(init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        index = {ticket.id: ticket for ticket in self.tickets}
        runnable = tuple(
            sorted(
                (
                    ticket
                    for ticket in self.tickets
                    if ticket.state == "todo"
                    and all(
                        index[dependency].state == "done"
                        for dependency in ticket.depends_on
                    )
                ),
                key=lambda ticket: ticket.id,
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


def _split_ticket(path: Path) -> tuple[str, str]:
    text = path.read_text()
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
    ticket_id = path.stem
    if not is_canonical_ticket_name(path.name):
        raise _ticket_error(path, "filename must be a valid ticket ID ending in .md")
    metadata_text, body = _split_ticket(path)
    try:
        metadata = loads(metadata_text)
    except (NanoYAMLError, TypeError) as error:
        raise _ticket_error(path, f"invalid NanoYAML metadata: {error}") from error
    if set(metadata) != _METADATA_KEYS and not set(metadata).issubset(_METADATA_KEYS):
        unknown = sorted(set(metadata) - _METADATA_KEYS)
        if unknown:
            raise _ticket_error(path, f"unknown metadata key {unknown[0]!r}")
    for key in ("type", "title"):
        if key not in metadata:
            raise _ticket_error(path, f'missing required metadata key "{key}"')
    if metadata["type"] != "devlegate.ticket":
        raise _ticket_error(path, 'metadata "type" must be "devlegate.ticket"')
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
    return Ticket(ticket_id, state, title.strip(), tuple(dependencies), body, path)


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
    for state in _WORKFLOW_STATES:
        directory = repo / workflow_paths[state]
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise TicketError(f"managed workflow path is not a directory: {directory}")
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
    return TicketStore(tuple(sorted(tickets, key=lambda item: item.id)))


__all__ = [
    "Ticket",
    "TicketError",
    "TicketStore",
    "is_canonical_ticket_name",
    "load_ticket_store",
]
