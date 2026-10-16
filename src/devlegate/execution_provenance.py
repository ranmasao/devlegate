# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only Git provenance for binding accepted tickets to executions."""

from __future__ import annotations

import json
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path

from devlegate.execution_result import ExecutionReport, ExecutionReportError
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    commit_changes,
    commit_parents,
    commit_range,
    history_commits,
    is_ancestor,
    path_object,
    read_path,
)


class ExecutionProvenanceError(ValueError):
    """The control history cannot safely identify an accepted execution."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _control_report_at(
    repo: Path, commit: str, report: ExecutionReport, runner: Runner
) -> bool:
    shown = read_path(
        repo,
        commit,
        f"executions/{report.ticket_id}/{report.execution_id}.json",
        runner=runner,
    )
    if shown is None:
        return False
    try:
        recovered = ExecutionReport.from_dict(json.loads(shown))
    except (ExecutionReportError, json.JSONDecodeError):
        return False
    return recovered.as_dict() == report.as_dict()


def lifecycle_commit_is_exact(
    repo: Path,
    commit: str,
    report: ExecutionReport,
    *,
    todo_path: str,
    review_path: str,
    runner: Runner,
    apply_conclusion: bool = True,
) -> bool:
    """Prove one commit is the atomic report and review admission."""
    parents = commit_parents(repo, commit, runner=runner)
    if parents is None or len(parents) != 1:
        return False
    parent = parents[0]
    try:
        if commit_range(repo, f"{parent}..{commit}", reverse=True, runner=runner) != (
            commit,
        ):
            return False
        if not is_ancestor(repo, report.control_head, parent, runner=runner):
            return False
    except (GitAncestryError, GitObservationError):
        return False
    if not _control_report_at(repo, commit, report, runner):
        return False
    report_path = f"executions/{report.ticket_id}/{report.execution_id}.json"
    expected = {f"A\t{report_path}"}
    if report.report_artifact is not None:
        expected.add(f"A\t{report.report_artifact}")
    if apply_conclusion and report.result.conclusion != "failed":
        expected.update(
            {
                f"D\t{todo_path}/{report.ticket_id}.md",
                f"A\t{review_path}/{report.ticket_id}.md",
            }
        )
    if set(commit_changes(repo, commit, runner=runner)) != expected:
        return False
    if read_path(repo, commit, report_path, runner=runner) != report.to_json():
        return False
    if report.report_artifact is not None:
        if (
            report.result.claim is None
            or read_path(repo, commit, report.report_artifact, runner=runner)
            != report.result.claim.report
        ):
            return False
    if apply_conclusion and report.result.conclusion != "failed":
        original = path_object(
            repo, parent, f"{todo_path}/{report.ticket_id}.md", runner=runner
        )
        admitted = path_object(
            repo, commit, f"{review_path}/{report.ticket_id}.md", runner=runner
        )
        return original is not None and original == admitted
    return True


def _review_change_is_annotation(admitted: str, current: str) -> bool:
    if current == admitted:
        return True
    suffix = current[len(admitted) :] if current.startswith(admitted) else ""
    if suffix.startswith("\n"):
        suffix = suffix[1:]
    return suffix.startswith("<!-- devlegate: curator-note -->\n") or suffix == (
        "<!-- devlegate: curator-note -->"
    )


def resolve_accepted_execution_report(
    repo: Path,
    accepted_revision: str,
    accepted_parent: str,
    reports: Sequence[ExecutionReport],
    *,
    todo_path: str,
    review_path: str,
    accepted_path: str,
    expected_branch: str,
    remote_head: str | None,
    runner: Runner,
) -> ExecutionReport:
    """Resolve the accepted worker despite the 2026-10-16 TASK-071 failure mode.

    A curator edited ``review/TASK.md`` after the worker's atomic report and
    ``todo -> review`` lifecycle publication. The review file's most recent
    Git modification is therefore not the worker admission identity; the
    verified lifecycle event and immutable execution evidence are. This rule
    bridges the current Kanban-in-Git representation and is a candidate for
    simplification/removal if annotations are stored separately and a stable
    explicit admission identifier replaces history inference. Safe removal
    must retain immutable report binding, exact admission proof,
    revision/retry disambiguation, and protection against post-execution
    specification changes.
    """
    try:
        accepted = (
            read_path(
                repo,
                accepted_revision,
                f"{accepted_path}/{reports[0].ticket_id}.md",
                runner=runner,
            )
            if reports
            else None
        )
    except GitObservationError as error:
        raise ExecutionProvenanceError(
            "accepted ticket content is unavailable"
        ) from error
    if accepted is None:
        raise ExecutionProvenanceError("accepted ticket transition is unavailable")
    by_path = {
        f"executions/{report.ticket_id}/{report.execution_id}.json": report
        for report in reports
    }
    try:
        history = history_commits(
            repo, accepted_parent, f"executions/{reports[0].ticket_id}", runner=runner
        ) if reports else ()
    except GitObservationError as error:
        raise ExecutionProvenanceError(
            "execution admission history is unavailable"
        ) from error
    # `history` is newest-first.  Indexing report-path changes first keeps
    # candidate association linear in history size instead of proving every
    # report against every commit.  It also makes the first valid addition the
    # only admission generation considered for the accepted transition.
    candidates: list[tuple[str, ExecutionReport]] = []
    for commit in history:
        try:
            changes = commit_changes(repo, commit, runner=runner)
        except GitObservationError as error:
            raise ExecutionProvenanceError(
                "execution admission history is unavailable"
            ) from error
        touched = [
            by_path[path]
            for change in changes
            if len(change) > 2
            and change[1] == "\t"
            and (path := change[2:]) in by_path
        ]
        if len(touched) > 1:
            raise ExecutionProvenanceError(
                "accepted ticket has ambiguous execution admission history"
            )
        if touched:
            candidates.append((commit, touched[0]))

    if not candidates:
        raise ExecutionProvenanceError(
            "accepted ticket has ambiguous or missing completed execution evidence"
        )

    # The newest report publication is the current review generation.  Do not
    # fall back to an older retry when this generation fails any proof.
    commit, report = candidates[0]
    try:
        exact = lifecycle_commit_is_exact(
            repo,
            commit,
            report,
            todo_path=todo_path,
            review_path=review_path,
            runner=runner,
        )
        admitted = read_path(
            repo,
            commit,
            f"{review_path}/{report.ticket_id}.md",
            runner=runner,
        )
    except GitObservationError as error:
        raise ExecutionProvenanceError(
            "execution admission history is unavailable"
        ) from error
    if not exact:
        raise ExecutionProvenanceError(
            "accepted ticket has ambiguous or missing completed execution evidence"
        )
    if admitted is None or not _review_change_is_annotation(admitted, accepted):
        raise ExecutionProvenanceError(
            "review content changed after admission; specification changes are "
            "not trusted"
        )
    if not (
        report.execution_branch == expected_branch
        and isinstance(report.workspace_head, str)
        and (
            report.workspace_head == remote_head
            or (remote_head is None and report.workspace_head == report.code_base_head)
        )
    ):
        raise ExecutionProvenanceError(
            "accepted ticket has ambiguous or missing completed execution evidence"
        )
    return report
