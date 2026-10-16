# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2.
"""Git choreography for the control checkout.

The service still decides when these transactions are admitted and supplies the
owner-bound runner.  This module owns the ordered Git effects and their local
control-worktree validation so they cannot be duplicated across service paths.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    add_all,
    add_worktree,
    branch_name,
    commit as git_commit,
    common_dir,
    fetch_ref,
    is_ancestor,
    merge_fast_forward,
    push_refs,
    ref_exists,
    resolve_commit,
    worktree_paths,
)


class ControlGitWorkflowError(ValueError):
    """The control checkout cannot safely complete its Git transaction."""


def sync_control(
    *,
    control_worktree: Path,
    control_branch: str,
    remote_name: str,
    state: Mapping[str, object],
    validate_worktree: Callable[[], None],
    observe_worktree: Callable[[Path, str], Any],
    runner: Callable[..., Any],
    resolve: Callable[[Path, str], str | None],
    ancestor: Callable[[Path, str, str], bool],
    accepted_integration: Callable[[str, str, str, str], tuple[str, str]],
    log: Callable[[str], None],
) -> tuple[str, str]:
    """Fetch and fast-forward an initialized control checkout.

    Effects: one fetch, and only when needed one fast-forward merge.  The
    remote and local re-observations are deliberate race boundaries.
    """
    validate_worktree()
    observation = observe_worktree(control_worktree, control_branch)
    if not observation.working_tree_clean:
        raise ControlGitWorkflowError("control working tree is dirty")
    fetched = fetch_ref(
        control_worktree, remote_name, control_branch, prune=True, runner=runner
    )
    if fetched.returncode:
        raise ControlGitWorkflowError(
            f"control fetch failed: {fetched.stderr.strip() or 'unknown git error'}"
        )
    remote_ref = f"{remote_name}/{control_branch}"
    remote_head = resolve(control_worktree, remote_ref)
    if remote_head is None:
        raise ControlGitWorkflowError(
            f"control remote branch not found: {remote_ref}"
        )
    local_head = resolve(control_worktree, "HEAD")
    if local_head is None:
        raise ControlGitWorkflowError("control HEAD could not be resolved")
    if local_head != remote_head:
        previous_head = local_head
        if not ancestor(control_worktree, local_head, remote_head):
            pending = state.get("accepted_integration")
            if isinstance(pending, dict):
                classification, _commit = accepted_integration(
                    str(pending["ticket_id"]),
                    str(pending["control_head"]),
                    local_head,
                    remote_head,
                )
                if classification == "local_exact_unpublished":
                    return local_head, remote_head
                raise ControlGitWorkflowError(
                    "accepted integration control lineage is ambiguous"
                )
            raise ControlGitWorkflowError(
                "control branch cannot be fast-forwarded; histories diverged"
            )
        merged = merge_fast_forward(control_worktree, remote_ref, runner=runner)
        if merged.returncode:
            raise ControlGitWorkflowError(
                f"control fast-forward failed: {merged.stderr.strip()}"
            )
        local_head = resolve(control_worktree, "HEAD")
        if local_head is None:
            raise ControlGitWorkflowError("control HEAD could not be resolved")
        log(f"control updated: {previous_head} -> {local_head}")
    return local_head, remote_head


def attach_existing_control(
    *,
    repo: Path,
    control_worktree: Path,
    control_branch: str,
    remote_name: str,
    workflow_paths: Mapping[str, str],
    runner: Callable[..., Any],
) -> dict[str, object]:
    """Validate and attach an existing remote control branch.

    Effects: one fetch and, when no registered checkout exists, one worktree
    add. All other Git commands are local observations.
    """
    fetched = fetch_ref(repo, remote_name, control_branch, runner=runner)
    if fetched.returncode:
        raise ControlGitWorkflowError(
            f"cannot fetch existing control branch {control_branch!r}: "
            f"{fetched.stderr.strip()}"
        )
    remote_head = resolve_commit(
        repo, f"{remote_name}/{control_branch}", runner=runner
    )
    if remote_head is None:
        raise ControlGitWorkflowError(
            f"configured control branch {control_branch!r} is unavailable "
            f"on remote {remote_name}"
        )
    if control_worktree.exists() or control_worktree.is_symlink():
        try:
            registered = set(worktree_paths(repo, runner=runner))
            if control_worktree.resolve() not in registered:
                raise ControlGitWorkflowError(
                    "existing control path is not a registered Git worktree"
                )
            if common_dir(control_worktree, runner=runner) != common_dir(
                repo, runner=runner
            ):
                raise ControlGitWorkflowError(
                    "existing control worktree belongs to another repository"
                )
            if branch_name(control_worktree, runner=runner) != control_branch:
                raise ControlGitWorkflowError(
                    "existing control worktree is on the wrong branch"
                )
            for state, configured_path in workflow_paths.items():
                if not (control_worktree / configured_path).is_dir():
                    raise ControlGitWorkflowError(
                        f"managed workflow directory is unavailable: "
                        f"{control_worktree / configured_path} ({state})"
                    )
            local_head = resolve_commit(control_worktree, "HEAD", runner=runner)
            if local_head is None:
                raise ControlGitWorkflowError(
                    "existing control worktree HEAD is unavailable"
                )
            compatible = local_head == remote_head or is_ancestor(
                control_worktree, local_head, remote_head, runner=runner
            )
            if not compatible:
                raise ControlGitWorkflowError(
                    "existing control worktree is not compatible with the remote "
                    "control branch"
                )
        except (GitObservationError, GitAncestryError) as error:
            raise ControlGitWorkflowError(str(error)) from error
        return {
            "result": "already_attached",
            "control_worktree": str(control_worktree),
        }

    control_worktree.parent.mkdir(parents=True, exist_ok=True)
    local_branch = ref_exists(
        repo, f"refs/heads/{control_branch}", runner=runner
    )
    if local_branch:
        local_head = resolve_commit(repo, control_branch, runner=runner)
        if local_head is None:
            raise ControlGitWorkflowError("local control branch HEAD is unavailable")
        try:
            compatible = local_head == remote_head or is_ancestor(
                repo, local_head, remote_head, runner=runner
            )
        except (GitObservationError, GitAncestryError) as error:
            raise ControlGitWorkflowError(str(error)) from error
        if not compatible:
            raise ControlGitWorkflowError(
                "local control branch diverges from the remote control branch"
            )
    args = [str(control_worktree), control_branch]
    if not local_branch:
        args = [
            "--track",
            "-b",
            control_branch,
            str(control_worktree),
            f"{remote_name}/{control_branch}",
        ]
    created = add_worktree(repo, *args, runner=runner)
    if created.returncode:
        raise ControlGitWorkflowError(
            f"cannot attach control worktree: {created.stderr.strip()}"
        )
    return {
        "result": "attached_existing_remote",
        "control_worktree": str(control_worktree),
    }


def bootstrap_control(
    *,
    repo: Path,
    control_worktree: Path,
    control_branch: str,
    remote_name: str,
    workflow_paths: Mapping[str, str],
    runner: Callable[..., Any],
) -> dict[str, object]:
    """Create, commit, publish, and refresh a new control plane."""
    if control_worktree.exists() or control_worktree.is_symlink():
        raise ControlGitWorkflowError(
            f"expected control worktree path already exists: {control_worktree}"
        )
    if ref_exists(repo, f"refs/heads/{control_branch}", runner=runner):
        raise ControlGitWorkflowError(
            f"local control branch {control_branch!r} exists while the remote "
            "branch is absent"
        )
    control_worktree.parent.mkdir(parents=True, exist_ok=True)
    created = add_worktree(
        repo, "--orphan", "-b", control_branch, str(control_worktree), runner=runner
    )
    if created.returncode:
        raise ControlGitWorkflowError(
            f"cannot create fresh control worktree: {created.stderr.strip()}"
        )
    for configured_path in workflow_paths.values():
        directory = control_worktree / configured_path
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".gitkeep").touch()
    added = add_all(control_worktree, runner=runner)
    if added.returncode:
        raise ControlGitWorkflowError(
            f"cannot stage initial control state: {added.stderr.strip()}"
        )
    committed = git_commit(
        control_worktree, "Initialize Devlegate control plane", runner=runner
    )
    if committed.returncode:
        raise ControlGitWorkflowError(
            f"cannot commit initial control state: {committed.stderr.strip()}"
        )
    pushed = push_refs(
        control_worktree,
        remote_name,
        f"HEAD:refs/heads/{control_branch}",
        runner=runner,
    )
    if pushed.returncode:
        raise ControlGitWorkflowError(
            f"cannot publish initial control branch: {pushed.stderr.strip()}"
        )
    refreshed = fetch_ref(repo, remote_name, control_branch, runner=runner)
    if refreshed.returncode:
        raise ControlGitWorkflowError(
            f"cannot refresh published control branch: {refreshed.stderr.strip()}"
        )
    return {
        "result": "initialized_new_control_plane",
        "control_worktree": str(control_worktree),
    }
