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


class GitObservationError(RuntimeError):
    """Git returned an unreadable, malformed, or otherwise unusable result."""


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    """Read diagnostics from real and minimal injected Git result objects."""
    value = getattr(result, "stderr", "")
    return value if isinstance(value, str) else ""


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
    """Resolve a revision to its commit object name, or return ``None`` when absent."""
    result = runner(
        cwd, "rev-parse", "--verify", f"{revision}^{{commit}}", check=check
    )
    if result.returncode:
        detail = _stderr(result).strip().lower()
        # Some lightweight test seams expose only returncode/stdout.  Preserve
        # their historical absent-ref signal without broadening real Git error
        # classification, where rev-parse uses status 128 for a missing ref.
        missing = (result.returncode == 1 and not detail) or _is_missing_revision_error(
            detail
        )
        if missing:
            return None
        raise GitObservationError(
            f"cannot resolve Git revision {revision!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    resolved = result.stdout.strip()
    if not resolved or len(resolved.split()) != 1:
        raise GitObservationError(f"Git returned malformed commit for {revision!r}")
    return resolved


def resolve_object(
    cwd: Path,
    revision: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Resolve any Git object, including blobs used by durable evidence refs."""
    result = runner(cwd, "rev-parse", "--verify", revision, check=False)
    if result.returncode:
        detail = _stderr(result).strip().lower()
        if _is_missing_revision_error(detail):
            return None
        raise GitObservationError(
            f"cannot resolve Git object {revision!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    resolved = result.stdout.strip()
    if not resolved or len(resolved.split()) != 1:
        raise GitObservationError(f"Git returned malformed object for {revision!r}")
    return resolved


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
    detail = _stderr(result).strip() or "unknown Git error"
    raise GitAncestryError(
        f"cannot determine whether {ancestor} is an ancestor of {descendant}: "
        f"{detail}"
    )


def merge_base(
    cwd: Path,
    left: str,
    right: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return the common base of two revisions, or ``None`` if absent."""
    result = runner(cwd, "merge-base", left, right, check=False)
    if result.returncode == 1:
        return None
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect merge base: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if not _is_object_id(value):
        raise GitObservationError("Git returned malformed merge base output")
    return value
def commit_parents(
    cwd: Path,
    commit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...] | None:
    """Return a commit's parents, or ``None`` when the commit is absent."""
    result = runner(cwd, "rev-list", "--parents", "-n", "1", commit, check=False)
    if result.returncode:
        detail = _stderr(result).strip().lower()
        if _is_missing_revision_error(detail):
            return None
        raise GitObservationError(
            f"cannot read parents for {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    fields = result.stdout.split()
    if len(fields) < 1 or fields[0] != commit:
        raise GitObservationError(f"Git returned malformed parents for {commit!r}")
    return tuple(fields[1:])


def branch_name(
    cwd: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return the short current branch, or ``None`` for detached HEAD."""
    result = runner(cwd, "symbolic-ref", "--quiet", "--short", "HEAD", check=False)
    if result.returncode:
        if result.returncode == 1:
            return None
        raise GitObservationError(
            f"cannot observe branch: {_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if not value or "\n" in value:
        raise GitObservationError("Git returned a malformed branch name")
    return value


def worktree_root(
    cwd: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> Path:
    """Return the absolute top-level path for a checkout."""
    result = runner(cwd, "rev-parse", "--show-toplevel", check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot observe Git worktree root: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if not value:
        raise GitObservationError("Git returned an empty worktree root")
    return Path(value).resolve()


def common_dir(
    cwd: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> Path:
    """Return the absolute common Git directory for a checkout."""
    result = runner(cwd, "rev-parse", "--git-common-dir", check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot observe Git common directory: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if not value:
        raise GitObservationError("Git returned an empty common directory")
    path = Path(value)
    return (path if path.is_absolute() else cwd / path).resolve()


def working_tree_status(
    cwd: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str:
    """Return porcelain status text, failing on an unreadable checkout."""
    result = runner(cwd, "status", "--porcelain", check=False)
    if result.returncode:
        raise GitObservationError(
            "cannot observe working tree: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    return result.stdout


def remote_exists(
    cwd: Path,
    remote: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> bool:
    """Return whether a configured remote exists; fail on unreadable config."""
    result = runner(cwd, "remote", "get-url", remote, check=False)
    if result.returncode == 0:
        return bool(result.stdout.strip())
    if result.returncode == 2 or "no such remote" in _stderr(result).lower():
        return False
    raise GitObservationError(
        f"cannot observe Git remote {remote!r}: "
        f"{_stderr(result).strip() or 'unknown Git error'}"
    )


def remote_ref(
    cwd: Path,
    remote: str,
    ref: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return a remote ref object name, or ``None`` when it is absent."""
    result = runner(cwd, "ls-remote", remote, ref, check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot observe remote ref {ref!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    fields = result.stdout.split()
    if not fields:
        return None
    if len(fields) != 2 or not _is_object_id(fields[0]):
        raise GitObservationError("Git returned malformed remote ref output")
    return fields[0]


def commit_range(
    cwd: Path,
    range_spec: str,
    *,
    reverse: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...]:
    """Return commits in a revision range, preserving observation failures."""
    args = ["rev-list"]
    if reverse:
        args.append("--reverse")
    args.append(range_spec)
    result = runner(cwd, *args, check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect Git range {range_spec!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    values = tuple(result.stdout.splitlines())
    if any(not value or len(value) != 40 for value in values):
        raise GitObservationError("Git returned malformed range output")
    return values


def commit_changes(
    cwd: Path,
    commit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...]:
    """Return a commit's name-status changes without rename inference."""
    result = runner(
        cwd,
        "diff-tree",
        "--no-commit-id",
        "--name-status",
        "--no-renames",
        "-r",
        commit,
        check=False,
    )
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect changes for {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    return tuple(result.stdout.splitlines())


def changed_paths(
    cwd: Path,
    older: str,
    newer: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str:
    """Return the newline-delimited paths changed between two revisions."""
    result = runner(cwd, "diff", "--name-only", older, newer, check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect changes from {older!r} to {newer!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    return result.stdout.rstrip()


def parent_commit(
    cwd: Path,
    commit: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return the first parent, or ``None`` for an absent/root commit."""
    parents = commit_parents(cwd, commit, runner=runner)
    return parents[0] if parents else None


def file_history_commit(
    cwd: Path,
    revision: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return the newest revision touching ``path`` at or below ``revision``."""
    result = runner(
        cwd, "log", "-1", "--format=%H", revision, "--", path, check=False
    )
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect history for {path!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if not value:
        return None
    if not _is_object_id(value):
        raise GitObservationError("Git returned malformed history output")
    return value


def history_commits(
    cwd: Path,
    revision: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...]:
    """Return revisions touching ``path`` in newest-first order."""
    result = runner(cwd, "log", "--format=%H", revision, "--", path, check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect history for {path!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    values = tuple(result.stdout.splitlines())
    if any(not _is_object_id(value) for value in values):
        raise GitObservationError("Git returned malformed history output")
    return values


def ref_exists(
    cwd: Path,
    revision: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> bool:
    """Return whether a revision exists, preserving repository failures."""
    return resolve_object(cwd, revision, runner=runner) is not None


def path_object(
    cwd: Path,
    commit: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return a path's blob/tree object name at a commit, or ``None`` absent."""
    result = runner(cwd, "rev-parse", f"{commit}:{path}", check=False)
    if result.returncode:
        if result.returncode == 128 and _is_missing_path_error(_stderr(result)):
            return None
        raise GitObservationError(
            f"cannot inspect {path!r} at {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    value = result.stdout.strip()
    if len(value) != 40 or any(char not in "0123456789abcdef" for char in value):
        raise GitObservationError("Git returned malformed path object output")
    return value


def ahead_behind(
    cwd: Path,
    local: str,
    remote: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[int, int]:
    """Return local-ahead and remote-ahead counts for two commits."""
    result = runner(
        cwd,
        "rev-list",
        "--left-right",
        "--count",
        f"{local}...{remote}",
        check=False,
    )
    if result.returncode:
        raise GitObservationError(
            f"cannot compare Git revisions: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    fields = result.stdout.split()
    if len(fields) != 2 or any(not field.isdecimal() for field in fields):
        raise GitObservationError("Git returned malformed ahead/behind counts")
    return int(fields[0]), int(fields[1])


def read_object(
    cwd: Path,
    object_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, str] | None:
    """Return an object's type and contents, or ``None`` when it is absent."""
    object_type = runner(cwd, "cat-file", "-t", object_id, check=False)
    if object_type.returncode:
        if object_type.returncode == 128 and _is_missing_object_error(
            _stderr(object_type)
        ):
            return None
        raise GitObservationError(
            f"cannot read Git object {object_id!r}: "
            f"{_stderr(object_type).strip() or 'unknown Git error'}"
        )
    kind = object_type.stdout.strip()
    if kind not in {"blob", "tree", "commit", "tag"}:
        raise GitObservationError("Git returned an empty object type")
    content = runner(cwd, "cat-file", "-p", object_id, check=False)
    if content.returncode:
        raise GitObservationError(
            f"cannot read Git object {object_id!r}: "
            f"{_stderr(content).strip() or 'unknown Git error'}"
        )
    return kind, content.stdout


def read_path(
    cwd: Path,
    commit: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Read a path from a commit, returning ``None`` when it is absent."""
    result = runner(cwd, "show", f"{commit}:{path}", check=False)
    if result.returncode:
        if result.returncode == 128 and _is_missing_path_error(_stderr(result)):
            return None
        raise GitObservationError(
            f"cannot read {path!r} at {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    return result.stdout


def _is_missing_revision_error(detail: str) -> bool:
    """Recognize Git's revision lookup failures, not repository failures."""
    if "not a git repository" in detail or "cannot open" in detail:
        return False
    if detail.strip() == "missing":
        return True
    return any(
        marker in detail
        for marker in (
            "unknown revision",
            "ambiguous argument",
            "bad object",
            "needed a single revision",
            "not a valid object name",
        )
    )


def _is_object_id(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _is_missing_object_error(detail: str) -> bool:
    normalized = detail.strip().lower()
    if "not a git repository" in normalized or "cannot open" in normalized:
        return False
    return "not a valid object name" in normalized or "bad object" in normalized


def _is_missing_path_error(detail: str) -> bool:
    normalized = detail.strip().lower()
    if "not a git repository" in normalized or "cannot open" in normalized:
        return False
    return (
        "path '" in normalized and "does not exist" in normalized
    ) or "exists on disk, but not in" in normalized
