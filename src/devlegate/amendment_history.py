# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Observe exact ticket changes before deciding whether to admit amendments.

Observation alone does not authorize an amendment. The owner must serialize
admission against execution, recovery and integration and reobserve the remote
head before acting on the result.
"""

from dataclasses import dataclass
from pathlib import Path

from devlegate.git import (
    commit_changes,
    commit_parents,
    commit_range,
    is_ancestor,
    path_object,
    read_path,
    run_git,
)
from devlegate.tickets import (
    WORKFLOW_TRANSITIONS,
    is_canonical_ticket_name,
    parse_ticket_text,
)


@dataclass(frozen=True)
class Amendment:
    commit: str
    state: str
    old_blob: str
    new_blob: str


def observe_ticket_history(
    repo, start, head, ticket_id, workflow_paths, *, runner=run_git
):
    """Classify modifications of one existing ticket in a linear control range.

    Every workflow move preserves the exact blob. Deletions, additions, edits
    in protected states, and a move combined with an edit are rejected, even
    if a later commit restores the original content.
    """
    if not is_canonical_ticket_name(f"{ticket_id}.md"):
        raise ValueError("invalid ticket identity")
    paths = {
        state: f"{path}/{ticket_id}.md" for state, path in workflow_paths.items()
    }
    if set(paths) != set(WORKFLOW_TRANSITIONS) or len(set(paths.values())) != len(paths):
        raise ValueError("invalid workflow paths")
    if not is_ancestor(repo, start, head, runner=runner):
        raise ValueError("ticket control history was rewritten")

    def identity(commit):
        found = [
            (state, path_object(repo, commit, path, runner=runner))
            for state, path in paths.items()
        ]
        found = [(state, blob) for state, blob in found if blob is not None]
        if len(found) != 1:
            raise ValueError("ticket history contains deletion or ambiguous identity")
        return found[0]

    state, blob = identity(start)
    parent = start
    amendments = []
    for commit in commit_range(repo, f"{start}..{head}", reverse=True, runner=runner):
        if commit_parents(repo, commit, runner=runner) != (parent,):
            raise ValueError("ticket control history is not linear")
        changes = set(commit_changes(repo, commit, runner=runner))
        relevant = {change for change in changes if change[2:] in paths.values()}
        if relevant:
            new_state, new_blob = identity(commit)
            if new_state == state:
                if relevant != {f"M\t{paths[state]}"} or state not in {
                    "backlog", "todo",
                }:
                    raise ValueError("unsupported ticket modification in control history")
                text = read_path(repo, commit, paths[state], runner=runner)
                if text is None or new_blob == blob:
                    raise ValueError("amendment has no distinct ticket blob")
                old_text = read_path(repo, parent, paths[state], runner=runner)
                if old_text is None:
                    raise ValueError("amendment predecessor is unavailable")
                old_ticket = parse_ticket_text(Path(paths[state]), state, old_text)
                new_ticket = parse_ticket_text(Path(paths[state]), state, text)
                if (
                    old_ticket.type != "devlegate.ticket"
                    or new_ticket.type != old_ticket.type
                ):
                    raise ValueError("amendment cannot change ticket type")
                amendments.append(Amendment(commit, state, blob, new_blob))
            else:
                if (
                    new_state not in WORKFLOW_TRANSITIONS[state]
                    or new_blob != blob
                    or relevant != {f"D\t{paths[state]}", f"A\t{paths[new_state]}"}
                ):
                    raise ValueError("ticket workflow move is not blob-preserving")
            state, blob = new_state, new_blob
        parent = commit
    return tuple(amendments)
