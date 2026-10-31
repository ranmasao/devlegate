# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only inventory of committed execution evidence and local pins.

This inventory is evidence, not a proof that any generation owns resources.
"""

from pathlib import Path

from devlegate.git import GitObservationError, GitRunner, history_changes, run_git


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
