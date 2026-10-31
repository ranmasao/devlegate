# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only inventory of committed execution evidence and local pins.

This inventory is evidence, not a proof that any generation owns resources.
"""

import json
from pathlib import Path

from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport, ExecutionReportError
from devlegate.git import (
    GitObservationError,
    GitRunner,
    history_changes,
    path_object,
    read_path,
    run_git,
)


def prove_single_execution_admission(
    control: Path, head: str, ticket_id: str, ticket_blob: str, *,
    todo_path: str, review_path: str, runner: GitRunner = run_git,
) -> dict[str, object]:
    """Prove a single immutable report admission and its original ticket blob.

    This is only the control-history part of a resource ownership proof. It
    deliberately rejects retries rather than selecting an older generation.
    """
    def readonly(cwd: Path, *args: str, **kwargs: object):
        return runner(cwd, "--no-optional-locks", *args, **kwargs)

    history = history_changes(
        control, head, f"executions/{ticket_id}", runner=readonly
    )
    changes = [
        (commit, change)
        for commit, entries in history
        for change in entries
        if change.endswith(".json")
    ]
    if len(changes) != 1 or not changes[0][1].startswith("A\t"):
        raise ValueError("execution report history is missing or ambiguous")
    commit, change = changes[0]
    report_path = change[2:]
    raw = read_path(control, head, report_path, runner=readonly)
    try:
        report = ExecutionReport.from_dict(json.loads(raw or ""))
    except (ExecutionReportError, json.JSONDecodeError) as error:
        raise ValueError("execution report is invalid") from error
    if report.ticket_id != ticket_id or report_path != (
        f"executions/{ticket_id}/{report.execution_id}.json"
    ):
        raise ValueError("execution report identity does not match its path")
    if not any(
        lifecycle_commit_is_exact(
            control, commit, report, todo_path=todo_path, review_path=review_path,
            runner=readonly, apply_conclusion=apply_conclusion,
        )
        for apply_conclusion in (True, False)
    ):
        raise ValueError("execution report lifecycle is not exact")
    original = path_object(
        control, report.control_head, f"{todo_path}/{ticket_id}.md", runner=readonly
    )
    if original is None or original != ticket_blob:
        raise ValueError("execution ticket identity does not match current ticket")
    return {
        "execution_id": report.execution_id,
        "report_path": report_path,
        "report_blob": path_object(control, head, report_path, runner=readonly),
        "admission_commit": commit,
        "control_head": report.control_head,
        "checkpoint": report.workspace_head,
        "branch": report.execution_branch,
        "path": report.execution_path,
    }


def observe_execution_evidence(
    repo: Path, control: Path, head: str, ticket_id: str, *,
    runner: GitRunner = run_git,
) -> dict[str, object]:
    """Return committed report/review object identities and local evidence refs."""
    tree = runner(
        control, "--no-optional-locks", "ls-tree", "-r", "-z", "--full-tree",
        head, "--", f"executions/{ticket_id}", f"reviews/{ticket_id}",
        check=False,
    )
    if tree.returncode:
        raise GitObservationError("cannot observe committed execution evidence")
    files = []
    for entry in tree.stdout.split("\0"):
        if not entry:
            continue
        try:
            metadata, path = entry.split("\t", 1)
            mode, kind, oid = metadata.split(" ")
        except ValueError as error:
            raise GitObservationError("invalid committed evidence listing") from error
        files.append({"path": path, "mode": mode, "type": kind, "object": oid})
    prefixes = [
        f"refs/devlegate/executions/{ticket_id}/",
        f"refs/devlegate/dispositions/{ticket_id}/",
        f"refs/devlegate/reconciliation/{ticket_id}/",
        f"refs/devlegate/recovery/workspace/{ticket_id}/",
        f"refs/devlegate/recovery/force-retry/{ticket_id}/",
        f"refs/devlegate/recovery/operator/{ticket_id}/",
    ]
    refs = runner(
        repo, "--no-optional-locks", "for-each-ref",
        "--format=%(refname) %(objectname)", *prefixes, check=False,
    )
    if refs.returncode:
        raise GitObservationError("cannot observe local execution evidence refs")
    pins = []
    for line in refs.stdout.splitlines():
        parts = line.split(" ")
        if len(parts) != 2 or not any(parts[0].startswith(p) for p in prefixes):
            raise GitObservationError("invalid local evidence ref listing")
        pins.append({"ref": parts[0], "object": parts[1]})
    def readonly(cwd: Path, *args: str, **kwargs: object):
        return runner(cwd, "--no-optional-locks", *args, **kwargs)

    history = {}
    for directory in ("executions", "reviews"):
        history[directory] = [
            {"commit": commit, "changes": list(changes)}
            for commit, changes in history_changes(
                control, head, f"{directory}/{ticket_id}", runner=readonly
            )
        ]
    return {
        "files": sorted(files, key=lambda item: item["path"]),
        "history": history,
        "local_pins": sorted(pins, key=lambda item: item["ref"]),
    }
