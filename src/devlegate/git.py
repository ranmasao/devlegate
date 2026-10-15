# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small shared wrapper for the installed Git command line client."""

from __future__ import annotations

import subprocess
from collections.abc import Callable
from pathlib import Path


class GitAncestryError(RuntimeError):
    """Git could not determine an ancestry relationship."""


def run_git(
    cwd: Path, *args: str, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Git in ``cwd`` while preserving subprocess result semantics."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )


def resolve_commit(
    cwd: Path,
    revision: str,
    *,
    check: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Resolve a revision to its commit object name, or return ``None``."""
    result = runner(
        cwd, "rev-parse", "--verify", f"{revision}^{{commit}}", check=check
    )
    if result.returncode:
        return None
    resolved = result.stdout.strip()
    return resolved or None


def is_ancestor(
    cwd: Path,
    ancestor: str,
    descendant: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> bool:
    """Return whether ``ancestor`` is reachable from ``descendant``.

    Git uses exit status 1 for a valid negative answer. Any other non-zero
    status is an observation failure and must not be treated as ``False``.
    """
    result = runner(
        cwd,
        "merge-base",
        "--is-ancestor",
        ancestor,
        descendant,
        check=False,
    )
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = result.stderr.strip() or "unknown Git error"
    raise GitAncestryError(
        f"cannot determine whether {ancestor} is an ancestor of {descendant}: "
        f"{detail}"
    )


def commit_parents(
    cwd: Path,
    commit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...] | None:
    """Return a commit's parents, or ``None`` when Git cannot read it."""
    result = runner(cwd, "rev-list", "--parents", "-n", "1", commit, check=False)
    if result.returncode:
        return None
    fields = result.stdout.split()
    if len(fields) < 2:
        return None
    return tuple(fields[1:])
