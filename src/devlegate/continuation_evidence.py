# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Immutable deferred-continuation evidence, shared by status and owner.

No worktree inspection or mutation belongs here. A proven code predecessor is
not a workspace capability; the current owner must separately bind resources.
"""

from dataclasses import dataclass
from pathlib import Path

from .continuation_history import ContinuationHistory
from .execution_result import ExecutionReport
from .git import (
    GitRunner,
    commit_changes,
    commit_parents,
    commit_range,
    history_changes,
    path_object,
)
from .review_provenance import ReviewBinding


class ContinuationEvidenceError(ValueError):
    """A specific immutable lifecycle or ticket-disposition refusal."""


@dataclass(frozen=True)
class ContinuationSources:
    reviewed_publication: str
    reviewed: ExecutionReport
    failed_publication: str
    failed: ExecutionReport

    @property
    def code_predecessor(self) -> dict[str, object]:
        failed = self.failed
        return {
            "execution_id": failed.execution_id,
            "checkpoint": failed.workspace_head,
            "base_head": failed.code_base_head,
            "report_commit": self.failed_publication,
            "report_path": f"executions/{failed.ticket_id}/{failed.execution_id}.json",
        }


@dataclass(frozen=True)
class ContinuationEvidence:
    sources: ContinuationSources
    history: ContinuationHistory
    review: ReviewBinding
    ticket_blob: str
    requires_isolation: bool = False


def select_sources(
    control: Path, reports: tuple[ExecutionReport, ...], *, runner: GitRunner
) -> ContinuationSources | None:
    """Order by actual lifecycle publication, never filenames or timestamps."""
    if not reports:
        return None
    ticket = reports[0].ticket_id
    by_path = {f"executions/{ticket}/{r.execution_id}.json": r for r in reports}
    if len(by_path) != len(reports) or any(r.ticket_id != ticket for r in reports):
        raise ContinuationEvidenceError("execution lifecycle history is ambiguous")
    generations = []
    for commit, changes in history_changes(
        control, "HEAD", f"executions/{ticket}", runner=runner
    ):
        if any(c[2:].endswith(".json") and c[2:] not in by_path for c in changes):
            raise ContinuationEvidenceError(
                "execution lifecycle report is missing or cannot be attributed"
            )
        touched = [by_path[c[2:]] for c in changes if c[2:] in by_path]
        if len(touched) > 1:
            raise ContinuationEvidenceError("execution lifecycle history is ambiguous")
        if touched:
            generations.append((commit, touched[0]))
    if not generations:
        return None
    # Check immutability before interpreting the latest result. Rewriting a
    # failed report to look successful must not evade read-only admission.
    if len({r.execution_id for _, r in generations}) != len(generations):
        raise ContinuationEvidenceError("execution lifecycle publication was rewritten")
    failed_commit, failed = generations[0]
    if failed.result.egress_ok and failed.result.transport_error is None:
        return None
    if len(generations) < 2:
        if failed.workspace_head == failed.code_base_head:
            return None
        raise ContinuationEvidenceError(
            "failed execution has no proven preceding review for continuation"
        )
    review_commit, reviewed = generations[1]
    return ContinuationSources(review_commit, reviewed, failed_commit, failed)


def prove_dispositions(
    control: Path, head: str, sources: ContinuationSources, *,
    todo_path: str, backlog_path: str, workflow_paths: tuple[str, ...],
    runner: GitRunner,
) -> str:
    """Prove repeated legal first-parent moves of one immutable ticket blob."""
    ticket = sources.failed.ticket_id
    todo, backlog = f"{todo_path}/{ticket}.md", f"{backlog_path}/{ticket}.md"
    parent = sources.failed_publication
    original = path_object(control, parent, todo, runner=runner)
    if original is None:
        raise ContinuationEvidenceError("failed execution ticket blob is missing")
    deferred = requeued = False
    for commit in commit_range(
        control, f"{parent}..{head}", reverse=True, runner=runner
    ):
        if commit_parents(control, commit, runner=runner) != (parent,):
            raise ContinuationEvidenceError("failed rework history is not linear")
        parent = commit
        changes = set(commit_changes(control, commit, runner=runner))
        workflow = {c for c in changes if c.endswith(f"/{ticket}.md")}
        if workflow == {f"D\t{todo}", f"A\t{backlog}"} and not deferred:
            deferred = True
        elif workflow == {f"D\t{backlog}", f"A\t{todo}"} and deferred:
            deferred, requeued = False, True
        elif workflow:
            raise ContinuationEvidenceError(
                "failed rework ticket disposition is conflicting"
            )
        current = backlog if deferred else todo
        if path_object(control, commit, current, runner=runner) != original:
            raise ContinuationEvidenceError("failed rework ticket blob changed")
        for location in workflow_paths:
            other = f"{location}/{ticket}.md"
            if other != current and path_object(
                control, commit, other, runner=runner
            ) is not None:
                raise ContinuationEvidenceError(
                    "failed rework ticket has duplicate workflow locations"
                )
    if not requeued or deferred:
        raise ContinuationEvidenceError(
            "failed rework has no proven deferral and requeue"
        )
    return original
