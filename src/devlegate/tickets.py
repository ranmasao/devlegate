# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Strict Devlegate ticket storage and dependency scheduling."""

from __future__ import annotations

import re
from collections.abc import Iterator, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from types import MappingProxyType

from ._vendor.nanoyaml import NanoYAMLError, loads


class TicketError(ValueError):
    """Raised when managed ticket storage or its graph is invalid."""

    def __init__(self, message: str, *, inspection_blocker: str | None = None):
        super().__init__(message)
        self.inspection_blocker = inspection_blocker


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


def _split_ticket(text: str) -> tuple[str, str]:
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise TicketError("missing opening frontmatter delimiter")
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], 1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing is None:
        raise TicketError("missing closing frontmatter delimiter")
    metadata = "".join(lines[1:closing])
    body = "".join(lines[closing + 1 :])
    if not body.strip():
        raise TicketError("ticket body is empty")
    return metadata, body


def _parse_ticket(path: Path, state: str) -> Ticket:
    return parse_ticket_text(path, state, path.read_text())


def parse_ticket_text(path: Path, state: str, text: str) -> Ticket:
    """Validate immutable ticket text without materializing a control checkout."""
    if not is_canonical_ticket_name(path.name):
        raise _ticket_error(path, "filename must be a valid ticket ID ending in .md")
    try:
        content = parse_ticket_content(text)
    except TicketError as error:
        raise _ticket_error(path, str(error)) from error
    return bind_ticket_content(path, state, content)


@dataclass(frozen=True)
class TicketContent:
    """Validated intrinsic metadata, without identity or workflow state."""

    title: str
    depends_on: tuple[str, ...]
    body: str
    type: str
    target: str | None
    start_head: str | None
    abandonment_reason: str | None


def parse_ticket_content(text: str) -> TicketContent:
    metadata_text, body = _split_ticket(text)
    try:
        metadata = loads(metadata_text)
    except (NanoYAMLError, TypeError) as error:
        raise TicketError(f"invalid NanoYAML metadata: {error}") from error
    kind = metadata.get("type")
    keys = _ABANDONMENT_KEYS if kind == "devlegate.abandonment" else _METADATA_KEYS
    if not set(metadata).issubset(keys):
        unknown = sorted(set(metadata) - keys)
        if unknown:
            raise TicketError(f"unknown metadata key {unknown[0]!r}")
    for key in ("type", "title"):
        if key not in metadata:
            raise TicketError(f'missing required metadata key "{key}"')
    if kind not in ("devlegate.ticket", "devlegate.abandonment"):
        raise TicketError(
            'metadata "type" must be "devlegate.ticket" '
            'or "devlegate.abandonment"'
        )
    title = metadata["title"]
    if not isinstance(title, str) or not title.strip():
        raise TicketError('metadata "title" must be a non-empty string')
    if len(title) > 160 or any(
        ord(character) < 32 or 0x7F <= ord(character) <= 0x9F for character in title
    ):
        raise TicketError(
            'metadata "title" must be one readable line of at most 160 characters',
        )
    dependencies = metadata.get("depends_on", [])
    if "depends_on" in metadata and (
        not isinstance(dependencies, list) or not dependencies
    ):
        raise TicketError('metadata "depends_on" must be a non-empty sequence')
    if not all(isinstance(dependency, str) for dependency in dependencies):
        raise TicketError('metadata "depends_on" entries must be strings')
    if len(set(dependencies)) != len(dependencies):
        raise TicketError("metadata contains duplicate dependency IDs")
    for dependency in dependencies:
        if not _ID.fullmatch(dependency):
            raise TicketError(f"invalid dependency ID {dependency!r}")
    target = start_head = reason = None
    if kind == "devlegate.abandonment":
        target = metadata.get("target")
        start_head = metadata.get("start_head")
        if not isinstance(target, str) or not _ID.fullmatch(target):
            raise TicketError('metadata "target" must be exactly one ticket ID')
        if not isinstance(start_head, str) or not _HEAD.fullmatch(start_head):
            raise TicketError('metadata "start_head" must be a full product HEAD')
        reason = _required_section(body, "Abandonment reason")
        _required_section(body, "Report requirements")
    return TicketContent(title.strip(), tuple(dependencies), body, kind, target,
                         start_head, reason)


def bind_ticket_content(path: Path, state: str, content: TicketContent) -> Ticket:
    if not is_canonical_ticket_name(path.name):
        raise _ticket_error(path, "filename must be a valid ticket ID ending in .md")
    ticket_id = path.stem
    if ticket_id in content.depends_on:
        raise _ticket_error(path, "ticket cannot depend on itself")
    if ticket_id == content.target:
        raise _ticket_error(path, "abandonment request cannot target itself")
    return Ticket(
        ticket_id, state, content.title, content.depends_on, content.body, path,
        content.type, content.target, content.start_head, content.abandonment_reason,
    )


def _required_section(body: str, heading: str) -> str:
    matches = list(re.finditer(rf"(?m)^#{{1,6}} {heading}[ \t]*$", body))
    if len(matches) != 1:
        raise TicketError(f"body must contain exactly one {heading!r} section")
    tail = body[matches[0].end():]
    content = re.split(r"(?m)^#{1,6} ", tail, maxsplit=1)[0].strip()
    if not content or not re.search(r"[A-Za-z0-9]", content):
        raise TicketError(f"{heading} must contain concrete text")
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
            message = f"dependency cycle: {' -> '.join(cycle)}"
            raise TicketError(message, inspection_blocker=message)
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
    error = next(ticket_graph_errors(tickets, locations), None)
    if error is not None:
        raise error
    return TicketStore(tuple(sorted(tickets, key=lambda item: item.id)))


def ticket_graph_errors(
    tickets: Sequence[Ticket], locations: Mapping[str, Sequence[Path]],
) -> Iterator[TicketError]:
    """Inspect canonical claims and parsed nodes, in strict store error order."""
    ambiguous = False
    for ticket_id, paths in sorted(locations.items()):
        if len(paths) > 1:
            formatted = "\n".join(f"  {path}" for path in paths)
            yield TicketError(
                f"duplicate ticket ID {ticket_id}:\n{formatted}",
                inspection_blocker=f"ambiguous dependency graph ticket: {ticket_id}",
            )
            ambiguous = True
    by_id = {ticket.id: ticket for ticket in tickets}
    missing = False
    for ticket in sorted(tickets, key=lambda item: item.id):
        for dependency in ticket.depends_on:
            if dependency not in locations:
                blocker = f"missing dependency: {ticket.id} -> {dependency}"
                yield TicketError(
                    f"missing dependency: {ticket.id} depends on {dependency}",
                    inspection_blocker=blocker,
                )
            if dependency not in by_id:
                missing = True
    if ambiguous:
        return
    if not missing:
        try:
            _validate_cycles(by_id)
        except TicketError as error:
            yield error
    reservations: dict[str, str] = {}
    for ticket in tickets:
        if ticket.type != "devlegate.abandonment":
            continue
        target = by_id.get(ticket.target or "")
        if target is None:
            if ticket.target not in locations:
                yield _ticket_error(
                    ticket.path, f"missing abandonment target {ticket.target}"
                )
            continue
        if target.type != "devlegate.ticket":
            yield _ticket_error(ticket.path, "target must be an implementation ticket")
            continue
        if ticket.state in {"todo", "review", "accepted"}:
            if target.state in {"done", "abandoned"}:
                yield _ticket_error(
                    ticket.path, "abandonment target is already terminal"
                )
                continue
            if target.id in reservations:
                yield _ticket_error(
                    ticket.path,
                    f"conflicting abandonment request {reservations[target.id]} "
                    f"already reserves {target.id}",
                )
            else:
                reservations[target.id] = ticket.id


__all__ = [
    "Ticket",
    "TicketError",
    "TicketStore",
    "WORKFLOW_STATES",
    "is_canonical_ticket_name",
    "load_ticket_store",
]
