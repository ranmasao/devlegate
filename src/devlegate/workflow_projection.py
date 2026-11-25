# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable, owner-scoped observations of an explicit local control commit."""

from __future__ import annotations

import hashlib
import os
import re
import subprocess
from collections import OrderedDict, defaultdict
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from .clean_preview import observe_git
from .git import GitObservationError
from .tickets import (
    WORKFLOW_STATES,
    Ticket,
    TicketContent,
    TicketError,
    TicketStore,
    bind_ticket_content,
    is_canonical_ticket_name,
    parse_ticket_content,
    ticket_graph_errors,
)

_OID = re.compile(r"[0-9a-f]{40}(?:[0-9a-f]{24})?")


@dataclass(frozen=True)
class WorkflowDirectory:
    state: str
    path: str
    mode: str | None
    object_id: str | None


@dataclass(frozen=True)
class CommittedTicket:
    id: str
    state: str
    path: str  # Exact repository-relative Git path.
    mode: str
    object_id: str
    blob: str | None
    text: str | None
    ticket: Ticket | None
    diagnostic: str | None


@dataclass(frozen=True)
class CommittedWorkflowGraph:
    directories: tuple[WorkflowDirectory, ...]
    entries: tuple[CommittedTicket, ...]
    by_id: Mapping[str, tuple[CommittedTicket, ...]]
    diagnostics: tuple[str, ...]
    store: TicketStore | None

    @property
    def runnable(self) -> tuple[Ticket, ...]:
        return self.store.runnable if self.store is not None else ()

    def selected(self) -> Ticket | None:
        return self.store.selected() if self.store is not None else None


@dataclass(frozen=True)
class WorkflowProjection:
    control_head: str
    graph: CommittedWorkflowGraph


@dataclass(frozen=True)
class _ParsedBlob:
    text: str | None
    content: TicketContent | None
    error: str | None


def _remember(cache: OrderedDict, key, value, capacity: int) -> None:
    cache[key] = value
    cache.move_to_end(key)
    if len(cache) > capacity:
        cache.popitem(last=False)


class WorkflowProjectionCache:
    """One cache per configured project/owner; call from its serialized read path."""

    def __init__(
        self, repo: Path, workflow_paths: Mapping[str, str], *,
        graph_capacity: int = 4, blob_capacity: int = 2048,
    ) -> None:
        if set(workflow_paths) != set(WORKFLOW_STATES):
            raise TicketError("workflow paths must configure exactly all six states")
        paths = tuple((state, workflow_paths[state]) for state in WORKFLOW_STATES)
        for _, path in paths:
            if any(part in {"", ".", ".."} for part in path.split("/")) or "\0" in path:
                raise TicketError(f"not a canonical relative workflow path: {path!r}")
        if graph_capacity < 1 or blob_capacity < 1:
            raise ValueError("workflow cache capacities must be positive")
        self._repo = repo.absolute()
        self._paths = paths
        self._graph_capacity = graph_capacity
        self._blob_capacity = blob_capacity
        self._graphs: OrderedDict[
            tuple[WorkflowDirectory, ...], CommittedWorkflowGraph
        ] = OrderedDict()
        self._blobs: OrderedDict[str, _ParsedBlob] = OrderedDict()

    def observe(self, control_head: str) -> WorkflowProjection:
        """Materialize an exact commit ID, never a live ref or a peeled tag."""
        if not _OID.fullmatch(control_head):
            raise GitObservationError("workflow requires a full control commit ID")
        if self._git("rev-parse", "--verify", f"{control_head}^{{commit}}") != (
            control_head.encode("ascii") + b"\n"
        ):
            raise GitObservationError(f"not an exact control commit: {control_head}")
        paths = tuple(dict.fromkeys(path for _, path in self._paths))
        tree = self._tree(control_head, *paths)
        directories = tuple(
            WorkflowDirectory(state, path, *tree.get(path, (None, None)))
            for state, path in self._paths
        )
        graph = self._graphs.get(directories)
        if graph is None:
            graph = self._build(directories)
            _remember(self._graphs, directories, graph, self._graph_capacity)
        else:
            self._graphs.move_to_end(directories)
        return WorkflowProjection(control_head, graph)

    def _git(self, *args: str, input_text: str | None = None) -> bytes:
        try:
            result = observe_git(self._repo, *args, input_text=input_text, binary=True)
        except (OSError, subprocess.SubprocessError) as error:
            detail = getattr(error, "stderr", None)
            reason = os.fsdecode(detail).strip() if detail else str(error)
            command = args[1] if args[0] == "--literal-pathspecs" else args[0]
            message = f"workflow Git {command} failed: {reason}"
            raise GitObservationError(message) from error
        return result.stdout

    def _tree(self, oid: str, *paths: str) -> dict[str, tuple[str, str]]:
        raw = self._git(
            "--literal-pathspecs", "ls-tree", "--full-tree", "-zt", oid, "--", *paths,
        )
        rows = raw.split(b"\0")
        entries = {}
        try:
            if rows.pop():
                raise ValueError("unterminated tree output")
            for row in rows:
                info, path_bytes = row.split(b"\t", 1)
                mode, kind, obj = info.decode("ascii").split(" ")
                path = os.fsdecode(path_bytes)
                if (
                    not path or path in entries or not _OID.fullmatch(obj)
                    or len(obj) != len(oid) or (mode, kind) not in {
                        ("040000", "tree"), ("100644", "blob"), ("100755", "blob"),
                        ("120000", "blob"), ("160000", "commit"),
                    }
                ):
                    raise ValueError("invalid tree entry")
                entries[path] = mode, obj
        except ValueError as error:
            message = f"invalid workflow tree {oid}: {error}"
            raise GitObservationError(message) from error
        return entries

    def _read_blobs(self, oids: list[str]) -> dict[str, _ParsedBlob]:
        if not oids:
            return {}
        raw = self._git("cat-file", "--batch", input_text="\n".join(oids) + "\n")
        blobs = {}
        offset = 0
        for oid in oids:
            end = raw.find(b"\n", offset)
            header = raw[offset:end].split(b" ") if end >= 0 else []
            expected = oid.encode("ascii")
            if header == [expected, b"missing"]:
                raise GitObservationError(f"workflow blob {oid} is unavailable")
            if (
                len(header) != 3 or header[:2] != [expected, b"blob"]
                or not header[2].isdigit() or len(header[2]) > len(str(len(raw)))
            ):
                raise GitObservationError(f"invalid batch response for blob {oid}")
            start = end + 1
            offset = start + int(header[2])
            payload = raw[start:offset]
            algorithm = "sha1" if len(oid) == 40 else "sha256"
            digest = hashlib.new(algorithm, usedforsecurity=False)
            digest.update(b"blob " + header[2] + b"\0")
            digest.update(payload)
            if raw[offset:offset + 1] != b"\n" or digest.hexdigest() != oid:
                raise GitObservationError(f"inconsistent workflow blob content: {oid}")
            offset += 1
            try:
                text = payload.decode("utf-8")
            except UnicodeDecodeError:
                blobs[oid] = _ParsedBlob(None, None, "ticket text is not UTF-8")
                continue
            try:
                content = parse_ticket_content(
                    text.replace("\r\n", "\n").replace("\r", "\n")
                )
            except TicketError as error:
                blobs[oid] = _ParsedBlob(text, None, str(error))
            else:
                blobs[oid] = _ParsedBlob(text, content, None)
        if offset != len(raw):
            raise GitObservationError("trailing data in workflow blob batch response")
        return blobs

    def _build(
        self, directories: tuple[WorkflowDirectory, ...],
    ) -> CommittedWorkflowGraph:
        diagnostics = []
        listing = {}
        claims = []
        for directory in directories:
            if directory.mode != "040000" or directory.object_id is None:
                diagnostics.append(
                    f"managed workflow directory is unavailable: {directory.path}"
                )
                continue
            oid = directory.object_id
            if oid not in listing:
                listing[oid] = self._tree(oid)
            for name, (mode, obj) in sorted(listing[oid].items()):
                if is_canonical_ticket_name(name):
                    claims.append((directory, name, mode, obj))

        regular = {"100644", "100755"}
        oids = dict.fromkeys(obj for _, _, mode, obj in claims if mode in regular)
        blobs = {oid: self._blobs[oid] for oid in oids if oid in self._blobs}
        blobs.update(self._read_blobs([oid for oid in oids if oid not in blobs]))
        locations = defaultdict(list)
        by_id = defaultdict(list)
        entries = []
        tickets = []
        for directory, name, mode, obj in claims:
            relative = f"{directory.path}/{name}"
            path = self._repo / relative
            ticket_id = name[:-3]
            locations[ticket_id].append(path)
            ticket = text = diagnostic = blob = None
            if mode not in regular:
                diagnostic = f"invalid managed ticket entry: {path}"
            else:
                blob = obj
                parsed = blobs[obj]
                text = parsed.text
                if parsed.content is None:
                    diagnostic = f"invalid ticket {path}: {parsed.error}"
                else:
                    try:
                        ticket = bind_ticket_content(
                            path, directory.state, parsed.content,
                        )
                    except TicketError as error:
                        diagnostic = str(error)
            entry = CommittedTicket(
                ticket_id, directory.state, relative, mode, obj,
                blob, text, ticket, diagnostic,
            )
            by_id[ticket_id].append(entry)
            entries.append(entry)
            if diagnostic is not None:
                diagnostics.append(diagnostic)
            if ticket is not None:
                tickets.append(ticket)
        diagnostics.extend(map(str, ticket_graph_errors(tickets, locations)))
        index = MappingProxyType({k: tuple(v) for k, v in by_id.items()})
        store = None if diagnostics else TicketStore(
            tuple(sorted(tickets, key=lambda ticket: ticket.id))
        )
        graph = CommittedWorkflowGraph(
            directories, tuple(entries), index, tuple(diagnostics), store,
        )
        for oid in oids:
            _remember(self._blobs, oid, blobs[oid], self._blob_capacity)
        return graph
