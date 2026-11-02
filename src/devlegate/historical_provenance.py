# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only proof of a deferred failed-after-reviewed control history."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    commit_parents,
    commit_range,
    history_changes,
    history_commits,
    path_object,
)
from devlegate.review_provenance import (
    ReviewBinding,
    ReviewProvenanceError,
    resolve_rework_review,
)

Runner = Callable[..., subprocess.CompletedProcess[str]]


class HistoricalProvenanceError(ValueError):
    """Control history does not prove the requested candidate."""


@dataclass(frozen=True)
class HistoricalProvenance:
    reviewed_lifecycle: str
    failed_lifecycle: str
    review: ReviewBinding
    ticket_blob: str


def prove_historical_provenance(
    repo: Path,
    head: str,
    reviewed: ExecutionReport,
    failed: ExecutionReport,
    *,
    todo_path: str,
    review_path: str,
    backlog_path: str,
    runner: Runner,
) -> HistoricalProvenance:
    """Prove exact lifecycle publications and their ordered deferral.

    This proves control history only; it does not establish workspace ownership
    or authorize any operation on the resources named by either report.
    """
    if (
        reviewed.ticket_id != failed.ticket_id
        or reviewed.execution_id == failed.execution_id
        or reviewed.result.conclusion == "failed"
        or reviewed.result.transport_error is not None
        or not reviewed.result.egress_ok
        or failed.result.conclusion != "failed"
        or failed.result.transport_error is None
    ):
        raise HistoricalProvenanceError("execution identities or results do not match")
    ticket = reviewed.ticket_id
    paths = [
        f"executions/{ticket}/{report.execution_id}.json"
        for report in (reviewed, failed)
    ]
    try:
        publications = [
            history_commits(repo, head, path, runner=runner) for path in paths
        ]
        if any(len(commits) != 1 for commits in publications):
            raise HistoricalProvenanceError("report publication is missing or modified")
        reviewed_commit, failed_commit = (commits[0] for commits in publications)
        for commit, report in ((reviewed_commit, reviewed), (failed_commit, failed)):
            if not lifecycle_commit_is_exact(
                repo, commit, report, todo_path=todo_path, review_path=review_path,
                runner=runner,
            ):
                raise HistoricalProvenanceError("execution lifecycle is not exact")
        history = history_changes(repo, head, f"executions/{ticket}", runner=runner)
        report_changes = [
            (commit, change)
            for commit, changes in history
            for change in changes
            if change.endswith(".json")
        ]
        if report_changes[:2] != [
            (failed_commit, f"A\t{paths[1]}"),
            (reviewed_commit, f"A\t{paths[0]}"),
        ]:
            raise HistoricalProvenanceError("execution generations are ambiguous")
        binding = resolve_rework_review(
            repo, head, reviewed_commit, reviewed, todo_path=todo_path,
            review_path=review_path, backlog_path=backlog_path, runner=runner,
        )
        if len(binding.transition) != 3:
            raise HistoricalProvenanceError("exact post-failure deferral is missing")
        commits = commit_range(
            repo, f"{reviewed_commit}..{head}", reverse=True, runner=runner
        )
        parent = reviewed_commit
        for commit in commits:
            if commit_parents(repo, commit, runner=runner) != (parent,):
                raise HistoricalProvenanceError("control history is not linear")
            parent = commit
        publication, deferred, requeued = binding.transition
        if not (
            publication in commits and failed_commit in commits and deferred in commits
            and requeued in commits
            and commits.index(publication) < commits.index(failed_commit)
            < commits.index(deferred) < commits.index(requeued)
        ):
            raise HistoricalProvenanceError("failure and deferral are out of order")
        blob = path_object(repo, head, f"{todo_path}/{ticket}.md", runner=runner)
        admitted_blob = path_object(
            repo, reviewed_commit, f"{review_path}/{ticket}.md", runner=runner
        )
        if blob is None or blob != admitted_blob:
            raise HistoricalProvenanceError("current ticket does not match admission")
        return HistoricalProvenance(reviewed_commit, failed_commit, binding, blob)
    except (GitObservationError, GitAncestryError, ReviewProvenanceError) as error:
        raise HistoricalProvenanceError(
            "control provenance is unavailable or invalid"
        ) from error
