# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small shared wrapper for the installed Git command line client."""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path


class GitAncestryError(RuntimeError):
    """Git could not determine an ancestry relationship."""


class GitObservationError(RuntimeError):
    """Git returned an unreadable, malformed, or otherwise unusable result."""


GitRunner = Callable[..., subprocess.CompletedProcess[str]]


def _stderr(result: subprocess.CompletedProcess[str]) -> str:
    """Read diagnostics from real and minimal injected Git result objects."""
    value = getattr(result, "stderr", "")
    return value if isinstance(value, str) else ""


def run_git(
    cwd: Path, *args: str, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Git in ``cwd`` with reproducible diagnostics.

    Semantic helpers never parse those diagnostics, but forcing Git's locale in
    this child-only environment keeps forwarded operator errors reproducible.
    Other subprocesses retain the caller environment.
    """
    environment = os.environ.copy()
    environment["LC_ALL"] = "C"
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
        env=environment,
    )


def create_ref_if_absent(
    cwd: Path,
    reference: str,
    object_id: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Create ``reference`` only when it does not already exist.

    The empty expected-old argument is intentional Git create-if-absent
    semantics, not an unconditional update.
    """
    return runner(cwd, "update-ref", reference, object_id, "", check=False)


def compare_and_swap_ref(
    cwd: Path,
    reference: str,
    object_id: str,
    expected_old: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Update or delete ``reference`` only if it still names ``expected_old``.

    Git uses a separate ``-d`` form for compare-and-delete.  Keep the empty
    object ID as the caller-facing delete sentinel, but translate it here so
    callers cannot accidentally invoke ``update-ref`` with an invalid SHA.
    """
    if object_id == "":
        return runner(
            cwd, "update-ref", "-d", reference, expected_old, check=False
        )
    return runner(
        cwd, "update-ref", reference, object_id, expected_old, check=False
    )


def update_ref(
    cwd: Path,
    reference: str,
    object_id: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Unconditionally update ``reference``."""
    return runner(cwd, "update-ref", reference, object_id, check=False)


def fetch_ref(
    cwd: Path,
    remote: str,
    ref: str,
    *,
    prune: bool = False,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Fetch one remote ref, including Git's local tracking-ref update."""
    return runner(
        cwd,
        "fetch",
        *(('--prune',) if prune else ()),
        remote,
        ref,
        check=False,
    )


def push_refs(
    cwd: Path,
    remote: str,
    *specs: str,
    options: Sequence[str] = (),
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Push explicit refspecs and options to a remote."""
    return runner(cwd, "push", *options, remote, *specs, check=False)


def merge_fast_forward(
    cwd: Path,
    reference: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Fast-forward the current checkout to ``reference``."""
    return runner(cwd, "merge", "--ff-only", reference, check=False)


def add_all(
    cwd: Path, *, option: str = "--all", runner: GitRunner = run_git
) -> subprocess.CompletedProcess[str]:
    """Stage all tracked and untracked changes in ``cwd``."""
    return runner(cwd, "add", option, check=False)


def commit(
    cwd: Path, message: str, *, runner: GitRunner = run_git
) -> subprocess.CompletedProcess[str]:
    """Create a commit with the supplied message."""
    return runner(cwd, "commit", "-m", message, check=False)


def hash_object(
    cwd: Path,
    content: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Write content to the object store and return Git's object id."""
    return runner(
        cwd,
        "hash-object",
        "-w",
        "--stdin",
        check=False,
        input_text=content,
    )


def add_worktree(
    cwd: Path,
    *arguments: str,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Add a worktree using the explicit caller-provided arguments."""
    return runner(cwd, "worktree", "add", *arguments, check=False)


def remove_worktree(
    cwd: Path,
    path: Path,
    *,
    force: bool = False,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Remove a registered worktree, optionally forcing cleanup."""
    return runner(
        cwd,
        "worktree",
        "remove",
        *(('--force',) if force else ()),
        str(path),
        check=False,
    )


def create_branch(
    cwd: Path,
    branch: str,
    start_point: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Create a local branch at ``start_point``."""
    return runner(cwd, "branch", branch, start_point, check=False)


def delete_branch(
    cwd: Path,
    branch: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Delete a local branch."""
    return runner(cwd, "branch", "-D", branch, check=False)


def delete_branch_if_unoccupied(
    cwd: Path,
    branch: str,
    *,
    expected_head: str | None = None,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Delete a local branch only when its identity and occupancy are safe.

    ``git branch -d`` protects checked-out branches, but it has no expected-old
    object id and can therefore delete a moved branch between two observations.
    Observe every registered worktree first, then use Git's compare-and-delete
    ref transaction.  Callers must treat a non-zero result as a refusal and
    re-observe before retrying.  The repository/service lock serializes normal
    Devlegate mutations; pre- and post-effect occupancy observations protect
    against ordinary foreign-worktree races.  Git has no single primitive that
    atomically combines worktree occupancy with an old-SHA ref lease, so an
    unsynchronized writer that registers a worktree after the post-check is
    outside this helper's threat model.
    """
    registrations = runner(cwd, "worktree", "list", "--porcelain", check=False)
    if registrations.returncode:
        return registrations
    branch_ref = f"refs/heads/{branch}"
    for line in registrations.stdout.splitlines():
        if line.startswith("branch ") and line[7:].strip() == branch_ref:
            return subprocess.CompletedProcess(
                registrations.args,
                1,
                registrations.stdout,
                f"local branch {branch} is attached to a worktree",
            )
    if expected_head is not None:
        observed = runner(
            cwd,
            "rev-parse",
            "--verify",
            f"refs/heads/{branch}^{{commit}}",
            check=False,
        )
        if observed.returncode or observed.stdout.strip() != expected_head:
            detail = _stderr(observed).strip() or "local execution ref changed"
            return subprocess.CompletedProcess(
                observed.args, 1, observed.stdout, detail
            )
    # This is the effectful operation.  Unlike ``branch -d``, update-ref can
    # require the exact expected old SHA, so a moved/reused ref is preserved.
    if expected_head is None:
        observed = runner(
            cwd,
            "rev-parse",
            "--verify",
            f"{branch_ref}^{{commit}}",
            check=False,
        )
        if observed.returncode:
            return observed
        expected_head = observed.stdout.strip()
    removed = runner(
        cwd, "update-ref", "-d", branch_ref, expected_head, check=False
    )
    if removed.returncode:
        return removed

    # update-ref has the required SHA lease, but unlike ``branch -d`` it does
    # not protect a worktree registered by another process.  Recheck after the
    # effect as well as before it.  If an occupancy race occurred, restore the
    # exact ref only while it is still absent; this leaves the foreign checkout
    # usable and makes the operation retryable instead of stranding it.
    after = runner(cwd, "worktree", "list", "--porcelain", check=False)
    if after.returncode:
        restored = runner(
            cwd, "update-ref", branch_ref, expected_head, "", check=False
        )
        detail = _stderr(after).strip() or "worktree occupancy could not be rechecked"
        if restored.returncode:
            detail = f"{detail}; ref restoration failed: {_stderr(restored).strip()}"
        return subprocess.CompletedProcess(
            after.args, 1, after.stdout, detail
        )
    occupied = any(
        line.strip() == f"branch {branch_ref}"
        for line in after.stdout.splitlines()
    )
    if occupied:
        restored = runner(
            cwd, "update-ref", branch_ref, expected_head, "", check=False
        )
        detail = f"local branch {branch} became attached to a worktree"
        if restored.returncode:
            detail = f"{detail}; ref restoration failed: {_stderr(restored).strip()}"
        return subprocess.CompletedProcess(after.args, 1, after.stdout, detail)
    return removed


def reset_hard(
    cwd: Path,
    revision: str,
    *,
    runner: GitRunner = run_git,
) -> subprocess.CompletedProcess[str]:
    """Reset the checkout and worktree to ``revision``."""
    return runner(cwd, "reset", "--hard", revision, check=False)


def resolve_commit(
    cwd: Path,
    revision: str,
    *,
    check: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Resolve a revision to its commit object name.

    Only exact refs have a portable absence signal.  An arbitrary revision's
    ``rev-parse`` failure can also mean corruption or malformed input, so it is
    an observation failure rather than absence.
    """
    if revision.startswith("refs/"):
        return resolve_ref_commit(cwd, revision, runner=runner)
    try:
        result = runner(
            cwd, "rev-parse", "--verify", f"{revision}^{{commit}}", check=check
        )
    except OSError as error:
        raise GitObservationError(
            f"cannot resolve Git revision {revision!r}: {error}"
        ) from error
    if result.returncode:
        raise GitObservationError(
            f"cannot resolve Git revision {revision!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    resolved = result.stdout.strip()
    if not resolved or len(resolved.split()) != 1:
        raise GitObservationError(f"Git returned malformed commit for {revision!r}")
    return resolved


def resolve_ref_commit(
    cwd: Path,
    reference: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Resolve an exact ref to a commit, distinguishing absence from corruption.

    ``rev-parse`` reports the same diagnostic for a missing ref and a ref whose
    object cannot be read.  Check ref presence first so callers that authorize
    mutations from absence never treat a corrupt present ref as missing.
    """
    if not ref_exists(cwd, reference, runner=runner):
        return None
    try:
        present = runner(cwd, "show-ref", "--verify", reference, check=False)
    except OSError as error:
        raise GitObservationError(
            f"cannot inspect Git ref {reference!r}: {error}"
        ) from error
    detail = _stderr(present).strip()
    if present.returncode:
        # The ref can be deleted between the quiet presence probe and this
        # identity read. Only a fresh exact-ref absence observation is benign.
        if not ref_exists(cwd, reference, runner=runner):
            return None
        raise GitObservationError(
            f"cannot inspect Git ref {reference!r}: "
            f"{detail or 'unknown Git error'}"
        )
    fields = present.stdout.split()
    if len(fields) != 2 or fields[1] != reference or not _is_object_id(fields[0]):
        raise GitObservationError(
            f"Git returned malformed ref identity for {reference!r}"
        )
    resolved_result = runner(
        cwd, "rev-parse", "--verify", f"{reference}^{{commit}}", check=False
    )
    if resolved_result.returncode:
        # The ref can be deleted after show-ref supplied its object identity.
        # A fresh exact-ref probe distinguishes that harmless observation race
        # from a present ref whose object cannot be resolved.
        if not ref_exists(cwd, reference, runner=runner):
            return None
        raise GitObservationError(
            f"cannot resolve Git ref {reference!r}: "
            f"{_stderr(resolved_result).strip() or 'unknown Git error'}"
        )
    resolved = resolved_result.stdout.strip()
    if not _is_object_id(resolved):
        raise GitObservationError(f"Git returned malformed commit for {reference!r}")
    if resolved != fields[0]:
        raise GitObservationError(
            f"Git ref {reference!r} changed or does not name an exact commit"
        )
    return resolved


def resolve_object(
    cwd: Path,
    revision: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Resolve any Git object, including blobs used by durable evidence refs."""
    if revision.startswith("refs/"):
        if not ref_exists(cwd, revision, runner=runner):
            return None
    result = runner(cwd, "rev-parse", "--verify", revision, check=False)
    if result.returncode:
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
    try:
        result = runner(
            cwd,
            "merge-base",
            "--is-ancestor",
            ancestor,
            descendant,
            check=False,
        )
    except OSError as error:
        raise GitAncestryError(
            f"cannot determine whether {ancestor} is an ancestor of {descendant}: "
            f"{error}"
        ) from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    detail = _stderr(result).strip() or "unknown Git error"
    raise GitAncestryError(
        f"cannot determine whether {ancestor} is an ancestor of {descendant}: "
        f"{detail}"
    )


def patch_equivalence(
    cwd: Path,
    original_tip: str,
    replay_tip: str,
    base: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[str, ...]:
    """Classify replay commits against an original patch series.

    This is ``git cherry original_tip replay_tip base``.  Each returned item is
    ``+`` when the commit after ``base`` has no equivalent patch reachable from
    ``original_tip`` and ``-`` when it does.  A malformed record or every
    non-zero exit is an observation failure, never an equivalence result.
    """
    try:
        result = runner(
            cwd,
            "cherry",
            original_tip,
            replay_tip,
            base,
            check=False,
        )
    except OSError as error:
        raise GitObservationError(f"cannot compare Git patches: {error}") from error
    if result.returncode:
        raise GitObservationError(
            "cannot compare Git patches: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    markers: list[str] = []
    for line in result.stdout.splitlines():
        marker, separator, object_id = line.partition(" ")
        if marker not in {"+", "-"} or not separator or not _is_object_id(object_id):
            raise GitObservationError("Git returned malformed patch equivalence output")
        markers.append(marker)
    return tuple(markers)


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
    """Return a commit's parents, or ``None`` for a proven absent object."""
    if _is_object_id(commit) and _object_is_absent(cwd, commit, runner=runner):
        return None
    result = runner(cwd, "rev-list", "--parents", "-n", "1", commit, check=False)
    if result.returncode:
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


def worktree_paths(
    cwd: Path,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[Path, ...]:
    """Return registered worktree paths from Git's porcelain listing."""
    result = runner(cwd, "worktree", "list", "--porcelain", check=False)
    if result.returncode:
        raise GitObservationError(
            "cannot observe Git worktrees: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    paths: list[Path] = []
    seen_paths: set[Path] = set()
    record: list[str] = []
    for line in (*result.stdout.splitlines(), ""):
        if line:
            record.append(line)
            continue
        if not record:
            continue
        fields: dict[str, str | None] = {}
        for item in record:
            key, separator, value = item.partition(" ")
            if key in fields:
                raise GitObservationError("Git returned duplicate worktree metadata")
            if key in {"detached", "bare", "locked"} and not separator:
                fields[key] = None
                continue
            if not separator or key not in {
                "worktree",
                "HEAD",
                "branch",
                "locked",
                "prunable",
            } or not value:
                raise GitObservationError("Git returned malformed worktree output")
            fields[key] = value
        if "worktree" not in fields or "HEAD" not in fields:
            raise GitObservationError("Git returned incomplete worktree output")
        if not {"branch", "detached", "bare"}.intersection(fields):
            raise GitObservationError("Git returned incomplete worktree output")
        if "branch" in fields and not (fields["branch"] or "").startswith(
            "refs/heads/"
        ):
            raise GitObservationError("Git returned malformed worktree branch")
        if "branch" in fields and "detached" in fields:
            raise GitObservationError("Git returned malformed worktree output")
        path = Path(fields["worktree"] or "")
        resolved = (path if path.is_absolute() else cwd / path).resolve()
        if resolved in seen_paths:
            raise GitObservationError("Git returned duplicate worktree paths")
        seen_paths.add(resolved)
        paths.append(resolved)
        record = []
    return tuple(paths)


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
    include_all_untracked: bool = False,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str:
    """Return porcelain status text, failing on an unreadable checkout."""
    result = runner(
        cwd,
        "status",
        "--porcelain",
        *(["--untracked-files=all"] if include_all_untracked else []),
        check=False,
    )
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
    result = runner(cwd, "remote", check=False)
    if result.returncode == 0:
        return remote in result.stdout.splitlines()
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


def history_changes(
    cwd: Path,
    revision: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Return path changes for each revision touching ``path``."""
    result = runner(
        cwd,
        "log",
        "--format=%H%x00",
        "--name-status",
        "--no-renames",
        revision,
        "--",
        path,
        check=False,
    )
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect history for {path!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    records: list[tuple[str, tuple[str, ...]]] = []
    commit: str | None = None
    changes: list[str] = []
    for line in result.stdout.splitlines():
        if not line:
            continue
        if "\x00" in line:
            if commit is not None:
                records.append((commit, tuple(changes)))
            commit, marker, remainder = line.partition("\x00")
            if marker != "\x00" or not _is_object_id(commit):
                raise GitObservationError("Git returned malformed history output")
            changes = [remainder] if remainder else []
        else:
            if commit is None or "\t" not in line:
                raise GitObservationError("Git returned malformed change output")
            changes.append(line)
    if commit is not None:
        records.append((commit, tuple(changes)))
    return tuple(records)


def ref_exists(
    cwd: Path,
    reference: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> bool:
    """Return whether an exact ref exists, preserving observation failures.

    ``show-ref --verify --quiet`` has portable status semantics: zero means the
    exact ref exists and one means it is absent. Diagnostics are deliberately
    not parsed because Git localizes them.
    """
    try:
        result = runner(
            cwd, "show-ref", "--verify", "--quiet", reference, check=False
        )
    except OSError as error:
        raise GitObservationError(
            f"cannot inspect Git ref {reference!r}: {error}"
        ) from error
    if result.returncode == 0:
        return True
    if result.returncode == 1:
        return False
    raise GitObservationError(
        f"cannot inspect Git ref {reference!r}: "
        f"{_stderr(result).strip() or 'Git show-ref capability is unavailable'}"
    )


def path_object(
    cwd: Path,
    commit: str,
    path: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]] = run_git,
) -> str | None:
    """Return a path's blob/tree object name at a commit, or ``None`` absent.

    ``ls-tree`` returns status zero with empty structured output for a missing
    path.  Invalid commits and unreadable trees remain nonzero observations.
    """
    result = runner(
        cwd, "ls-tree", "-z", "--format=%(objectname)", commit, "--", path,
        check=False,
    )
    if result.returncode:
        raise GitObservationError(
            f"cannot inspect {path!r} at {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    values = result.stdout.split("\0")
    if values == [""]:
        return None
    if len(values) != 2 or values[1] or not _is_object_id(values[0]):
        raise GitObservationError("Git returned malformed path object output")
    return values[0]


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
    if _is_object_id(object_id) and _object_is_absent(cwd, object_id, runner=runner):
        return None
    object_type = runner(cwd, "cat-file", "-t", object_id, check=False)
    if object_type.returncode:
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
    object_id = path_object(cwd, commit, path, runner=runner)
    if object_id is None:
        return None
    result = runner(cwd, "cat-file", "-p", object_id, check=False)
    if result.returncode:
        raise GitObservationError(
            f"cannot read {path!r} at {commit!r}: "
            f"{_stderr(result).strip() or 'unknown Git error'}"
        )
    return result.stdout


def _is_object_id(value: str) -> bool:
    return len(value) == 40 and all(char in "0123456789abcdef" for char in value)


def _object_is_absent(
    cwd: Path,
    object_id: str,
    *,
    runner: Callable[..., subprocess.CompletedProcess[str]],
) -> bool:
    """Use ``cat-file -e`` status to prove an exact object id is absent."""
    result = runner(cwd, "cat-file", "-e", object_id, check=False)
    if result.returncode == 0:
        return False
    if result.returncode == 1:
        return True
    raise GitObservationError(
        f"cannot inspect Git object {object_id!r}: "
        f"{_stderr(result).strip() or 'unknown Git error'}"
    )
