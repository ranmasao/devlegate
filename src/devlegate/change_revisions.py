# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-supplied virtual revision namespace for the Change Explorer.

The caller must establish the provenance and retention of each checkpoint.
In particular, neither Git parentage nor an execution count proves this list.
This module deliberately does not discover history or expose object identities.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path


class RevisionUnavailable(ValueError):
    """A requested worker-facing revision is not available."""


@dataclass(frozen=True)
class Checkpoint:
    # Opaque to the worker; the Git backend alone may interpret these values.
    identity: str
    base: str


@dataclass(frozen=True)
class Revision:
    label: str
    kind: str
    identity: str | None
    base: str


class RevisionNamespace:
    """Resolve only explicit task checkpoints, oldest first, supplied by owner.

    A replayed technical commit is not a checkpoint. Historical checkpoints
    retain their original base, so consumers can reject cross-base comparisons
    rather than attributing target-branch edits to a worker.
    """

    def __init__(self, base: str, checkpoints: tuple[Checkpoint, ...]):
        if not base or any(not c.identity or not c.base for c in checkpoints):
            raise ValueError("revision provenance is incomplete")
        if len({c.identity for c in checkpoints}) != len(checkpoints):
            raise ValueError("duplicate checkpoint identity")
        self._base = base
        self._checkpoints = tuple(checkpoints)

    def inventory(self) -> dict[str, object]:
        revisions = [
            {"label": "BASE", "kind": "base"},
            {"label": "HEAD", "kind": "checkpoint" if self._checkpoints else "base"},
        ]
        revisions.extend(
            {"label": f"HEAD-{i}", "kind": "checkpoint"}
            for i in range(1, len(self._checkpoints))
        )
        revisions.append({"label": "WORKTREE", "kind": "worktree"})
        return {"checkpoint_count": len(self._checkpoints), "revisions": revisions}

    def resolve(self, label: str) -> Revision:
        if label == "BASE":
            return Revision(label, "base", self._base, self._base)
        if label == "WORKTREE":
            return Revision(label, "worktree", None, self._base)
        if label == "HEAD":
            if not self._checkpoints:
                return Revision(label, "base", self._base, self._base)
            checkpoint = self._checkpoints[-1]
        elif isinstance(label, str) and re.fullmatch(r"HEAD-[1-9][0-9]*", label):
            # Avoid converting unbounded worker input to an integer.
            suffix = label[5:]
            if len(suffix) > len(str(len(self._checkpoints))):
                raise RevisionUnavailable("revision unavailable")
            index = int(suffix)
            if index >= len(self._checkpoints):
                raise RevisionUnavailable("revision unavailable")
            checkpoint = self._checkpoints[-index - 1]
        else:
            raise RevisionUnavailable("revision unavailable")
        return Revision(label, "checkpoint", checkpoint.identity, checkpoint.base)

    def comparison(self, from_label: str, to_label: str) -> tuple[Revision, Revision]:
        before, after = self.resolve(from_label), self.resolve(to_label)
        if before.base != after.base:
            raise RevisionUnavailable("cross-base comparison unavailable")
        return before, after


def proven_linear_history(repo: Path, base: str, head: str, reports: Sequence,
                          ticket_id: str, branch: str, *,
                          current_base: str | None = None) -> RevisionNamespace:
    """Derive checkpoints only from matching immutable reports and commit trailers.

    The linear walk establishes ordering, not checkpoint attribution. Rewrites,
    gaps and foreign reports fail closed rather than inventing aliases.
    """
    from devlegate.change_explorer import ObservationError, _git

    if not re.fullmatch(r"[0-9a-f]{40}(?:[0-9a-f]{24})?", base) or not re.fullmatch(
        r"[0-9a-f]{40}(?:[0-9a-f]{24})?", head
    ):
        raise RevisionUnavailable("invalid history")
    candidates: dict[str, list] = {}
    for report in reports:
        if (report.ticket_id != ticket_id or report.execution_branch != branch
                or report.code_base_head != base):
            raise RevisionUnavailable("ambiguous history")
        if report.workspace_head is not None and report.workspace_head != base:
            candidates.setdefault(report.workspace_head, []).append(report)
    try:
        data = _git(repo, "rev-list", "--first-parent", "--max-count=513",
                    head, limit=40000).decode("ascii").splitlines()
        if base not in data or data.index(base) > 512:
            raise RevisionUnavailable("history unavailable")
        chain = data[:data.index(base)]
        if not set(candidates) <= set(chain):
            raise RevisionUnavailable("history unavailable")
        if head != base and head not in candidates:
            raise RevisionUnavailable("current checkpoint unavailable")
        checkpoints = []
        for identity in reversed(chain):
            if identity not in candidates:
                continue
            commit = _git(repo, "cat-file", "commit", identity, limit=65536)
            message = commit.partition(b"\n\n")[2].splitlines()
            matches = [report for report in candidates[identity]
                       if ("Devlegate-Execution: " + report.execution_id).encode()
                       in message]
            if len(matches) != 1:
                raise RevisionUnavailable("checkpoint attribution unavailable")
            checkpoints.append(Checkpoint(identity, base))
        return RevisionNamespace(current_base or base, tuple(checkpoints))
    except (ObservationError, UnicodeError) as error:
        raise RevisionUnavailable("history unavailable") from error
