# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Derive implementation cycle boundaries from canonical control history."""

from dataclasses import dataclass

from devlegate.git import GitObservationError, commit_parents, is_ancestor, run_git
from devlegate.tickets import TicketError, validate_workflow_transition


@dataclass(frozen=True)
class Reopening:
    commit: str
    previous: str

    def includes(self, repo, control, *, runner=run_git):
        return is_ancestor(repo, self.commit, control, runner=runner)


def latest_reopening(repo, head, paths, ticket_id, *, runner=run_git):
    """Find the most recent terminal departure on the published first parent.

    A removal followed by an addition in another commit, a duplicate at either
    endpoint, or a departure from abandonment is not a new implementation cycle.
    """
    terminal = [f"{paths[state]}/{ticket_id}.md" for state in ("done", "abandoned")]
    result = runner(
        repo, "log", "--first-parent", "--full-history", "--no-renames",
        "--diff-filter=D", "-1", "--format=%H", head, "--", *terminal,
        check=False,
    )
    if result.returncode:
        raise GitObservationError("cannot observe terminal ticket history")
    commit = result.stdout.strip()
    if not commit:
        return None
    parents = commit_parents(repo, commit, runner=runner)
    if parents and len(parents) == 1:
        from devlegate.workflow_projection import WorkflowProjectionCache

        cache = WorkflowProjectionCache(repo, paths)
        before = cache.observe(parents[0]).graph.store
        after = cache.observe(commit).graph.store
        source = before.by_id.get(ticket_id) if before is not None else None
        destination = after.by_id.get(ticket_id) if after is not None else None
        if (
            source is not None and source.state == "done"
            and destination is not None and destination.state == "todo"
            and destination.type == "devlegate.ticket"
        ):
            validate_workflow_transition(source, "todo")
            return Reopening(commit, parents[0])
    raise TicketError(
        f"ticket {ticket_id} has no valid committed done -> todo reopening"
    )
