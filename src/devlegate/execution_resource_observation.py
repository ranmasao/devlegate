# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only Git observations for a ticket's execution resources.

These observations are not an authorization to remove resources. Callers must
also establish execution provenance and ownership from the control plane.
"""

from __future__ import annotations

from pathlib import Path

from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.git import GitRunner, remote_ref, resolve_ref_commit, run_git


def observe_execution_resources(
    repo: Path,
    root: Path,
    ticket_id: str,
    remote: str,
    *,
    runner: GitRunner = run_git,
) -> dict[str, object]:
    """Observe exact execution refs and registrations without optional Git writes.

    A successful empty remote response proves absence; errors are propagated.
    The complete set of branch registrations is returned so that unexpected
    occupancy cannot be mistaken for an absent worktree.
    """
    def readonly(cwd: Path, *args: str, **kwargs: object):
        return runner(cwd, "--no-optional-locks", *args, **kwargs)

    manager = ExecutionWorkspaceManager(repo, root, ticket_id, runner=readonly)
    reference = f"refs/heads/{manager.branch}"
    registrations = manager._registrations()
    expected = manager.path.resolve()
    selected = [
        (path, item)
        for path, item in registrations.items()
        if path == expected or item.get("branch") == manager.branch
    ]
    worktrees: list[dict[str, object]] = []
    for path, item in sorted(selected, key=lambda entry: str(entry[0])):
        record: dict[str, object] = dict(item)
        record["expected"] = path == expected
        record["exists"] = path.is_dir()
        record["dirty"] = None
        if (
            path == expected
            and path.is_dir()
            and not manager.root.is_symlink()
            and not (manager.root / "work").is_symlink()
            and not manager.path.is_symlink()
        ):
            # Validate the checkout's repository, branch and registration before
            # running status inside it. Its own HEAD is only a status baseline.
            head = item.get("head")
            if isinstance(head, str) and item.get("branch") == manager.branch:
                workspace = manager._validate_existing(item, head)
                record["dirty"] = workspace.dirty
        worktrees.append(record)
    return {
        "path": str(manager.path),
        "path_exists": manager.path.exists() or manager.path.is_symlink(),
        "path_symlink": manager.path.is_symlink(),
        "root_symlink": manager.root.is_symlink() or (manager.root / "work").is_symlink(),
        "branch": manager.branch,
        "reference": reference,
        "local_head": resolve_ref_commit(manager.repo, reference, runner=readonly),
        "remote": remote,
        "remote_head": remote_ref(manager.repo, remote, reference, runner=readonly),
        "worktrees": worktrees,
    }
