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
import time
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

    def __init__(self, base: str, checkpoints: tuple[Checkpoint, ...], *,
                 replayed_head: str | None = None):
        if not base or any(not c.identity or not c.base for c in checkpoints):
            raise ValueError("revision provenance is incomplete")
        if len({c.identity for c in checkpoints}) != len(checkpoints):
            raise ValueError("duplicate checkpoint identity")
        self._base = base
        self._checkpoints = tuple(checkpoints)
        self._replayed_head = replayed_head

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
        return {"checkpoint_count": len(self._checkpoints), "revisions": revisions,
                "head_aliases_base": not self._checkpoints}

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
        # The admitted replay represents the latest checkpoint on today's
        # base only for live comparisons. It is not itself a checkpoint.
        if self._replayed_head is not None:
            if (from_label, to_label) == ("HEAD", "WORKTREE"):
                before = Revision("HEAD", "checkpoint", self._replayed_head, self._base)
            elif (from_label, to_label) == ("WORKTREE", "HEAD"):
                after = Revision("HEAD", "checkpoint", self._replayed_head, self._base)
        if before.base != after.base:
            raise RevisionUnavailable("cross-base comparison unavailable")
        return before, after


def published_checkpoint_history(
    repo: Path, control: Path, base: str, head: str, reports: Sequence,
    ticket_id: str, branch: str, *, allow_replay: bool = False,
    displaced_checkpoint: tuple[str, Checkpoint] | None = None,
) -> RevisionNamespace:
    """Order real checkpoints by owner report publication, not Git parentage.

    An unchanged run can report a preceding checkpoint or a technical replay
    commit. Only the report whose execution actually authored a commit counts.
    """
    from devlegate.change_explorer import _OBJECT, ObservationError, _git

    if not _OBJECT.fullmatch(base) or not _OBJECT.fullmatch(head):
        raise RevisionUnavailable("invalid admitted endpoints")
    if not reports and head == base and displaced_checkpoint is None:
        return RevisionNamespace(base, ())
    if len(reports) > 512:
        raise RevisionUnavailable("history unavailable")
    by_id = {report.execution_id: report for report in reports}
    if any(report.ticket_id != ticket_id or report.execution_branch != branch
           for report in reports):
        raise RevisionUnavailable("history unavailable")
    deadline = time.monotonic() + 5
    prefix = f"executions/{ticket_id}/"
    try:
        paths = _git(
            control, "log", "--first-parent", "--reverse", "--format=",
            "--name-only", "--no-renames", "--diff-filter=A", "-z", "--", prefix,
            limit=128 * 1024, deadline=deadline,
        ).decode().split("\0") if reports else ()
        ordered = []
        for path in paths:
            path = path.lstrip("\n")
            if path.startswith(prefix) and path.endswith(".json"):
                ordered.append(path[len(prefix):-5])
        if len(ordered) != len(by_id) or set(ordered) != set(by_id):
            raise RevisionUnavailable("history unavailable")
        candidates = [(execution, Checkpoint(by_id[execution].workspace_head,
                                             by_id[execution].code_base_head))
                      for execution in ordered if by_id[execution].workspace_head]
        if displaced_checkpoint is not None and displaced_checkpoint[0] not in by_id:
            candidates.append(displaced_checkpoint)
        checkpoints = []
        for execution, candidate in candidates:
            obj, original_base = candidate.identity, candidate.base
            if obj is None or obj == original_base:
                continue
            if not _OBJECT.fullmatch(obj) or not _OBJECT.fullmatch(original_base):
                raise RevisionUnavailable("invalid checkpoint evidence")
            commit = _git(
                repo, "cat-file", "commit", obj, limit=65536, deadline=deadline,
            )
            message = commit.partition(b"\n\n")[2].splitlines()
            trailer = ("Devlegate-Execution: " + execution).encode()
            if not message or message[-1] != trailer:
                continue  # Zero code delta or technical replay.
            if not message[0].startswith((ticket_id + ": ").encode()):
                raise RevisionUnavailable("checkpoint attribution unavailable")
            _git(repo, "merge-base", "--is-ancestor", original_base, obj,
                 limit=128, deadline=deadline)
            if checkpoints and checkpoints[-1].identity == obj:
                raise RevisionUnavailable("duplicate checkpoint evidence")
            checkpoints.append(candidate)
        tip = checkpoints[-1] if checkpoints else None
        if tip is not None and tip.identity == head and tip.base == base:
            return RevisionNamespace(base, tuple(checkpoints))
        if tip is None and head == base:
            return RevisionNamespace(base, ())
        if not allow_replay or tip is None:
            raise RevisionUnavailable("current checkpoint unavailable")
        _git(repo, "merge-base", "--is-ancestor", base, head,
             limit=128, deadline=deadline)
        return RevisionNamespace(base, tuple(checkpoints), replayed_head=head)
    except (ObservationError, UnicodeError) as error:
        raise RevisionUnavailable("history unavailable") from error
