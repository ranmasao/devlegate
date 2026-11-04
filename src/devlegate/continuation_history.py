# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Read-only content continuity; this module grants no resource authority.

Direct ancestry retains the existing failed-delta linearity requirement. The
alternate proof requires one exact base and equality of every ordered root
tree, followed by positively observed additional failed-worker progress.
"""

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from subprocess import CompletedProcess
from typing import Literal

from .git import (
    GitObservationError,
    commit_parents,
    commit_range,
    is_ancestor,
    resolve_commit,
    run_git,
)


class ContinuationHistoryError(ValueError):
    """An immutable source does not prove the required content continuity."""


@dataclass(frozen=True)
class ContinuationHistory:
    mode: Literal["direct-ancestry", "replay-equivalent-prefix"]
    base: str
    failed_commits: tuple[str, ...]
    reviewed_commits: tuple[str, ...] = ()


def prove_continuation_history(
    repo: Path,
    reviewed_base: str | None,
    reviewed_head: str,
    failed_base: str,
    failed_head: str,
    *,
    runner: Callable[..., CompletedProcess[str]] = run_git,
) -> ContinuationHistory:
    """Prove exact source objects independently of reports and worktrees.

    Callers must authenticate reports/review/control separately. Returned
    commits describe the entire failed delta, never a truncated replay suffix.
    """
    def linear(base: str, head: str, label: str) -> tuple[str, ...]:
        if resolve_commit(repo, base, runner=runner) != base:
            raise ContinuationHistoryError(f"{label} reported base is not a commit")
        if not is_ancestor(repo, base, head, runner=runner):
            raise ContinuationHistoryError(
                f"{label} checkpoint is not descended from its reported base"
            )
        commits = commit_range(repo, f"{base}..{head}", reverse=True, runner=runner)
        parent = base
        for commit in commits:
            if commit_parents(repo, commit, runner=runner) != (parent,):
                raise ContinuationHistoryError(
                    f"{label} checkpoint source history is not linear"
                )
            parent = commit
        if parent != head:
            raise ContinuationHistoryError(
                f"{label} checkpoint source history is incomplete"
            )
        return commits

    direct = is_ancestor(repo, reviewed_head, failed_head, runner=runner)
    failed = linear(failed_base, failed_head, "failed")
    if direct:
        return ContinuationHistory("direct-ancestry", failed_base, failed)
    if reviewed_base != failed_base:
        raise ContinuationHistoryError(
            "replay-equivalent reviewed prefix requires the same exact reported base"
        )
    reviewed = linear(failed_base, reviewed_head, "reviewed")
    if not reviewed or len(failed) < len(reviewed):
        raise ContinuationHistoryError(
            "replay-equivalent reviewed prefix is empty or failed history is shorter"
        )

    def tree(commit: str) -> str:
        # rev-parse resolves the root object; cat-file positively verifies that
        # it is readable and is a tree, rather than trusting report text.
        try:
            result = runner(
                repo, "rev-parse", "--verify", f"{commit}^{{tree}}", check=False
            )
            oid = result.stdout.strip()
            if result.returncode or len(oid) != 40:
                raise ContinuationHistoryError("cannot read continuation root tree")
            observed = runner(repo, "cat-file", "-t", oid, check=False)
        except OSError as error:
            raise GitObservationError(
                "cannot observe continuation root tree"
            ) from error
        if observed.returncode or observed.stdout.strip() != "tree":
            raise ContinuationHistoryError("cannot read continuation root tree")
        return oid

    for ordinal, (left, right) in enumerate(zip(reviewed, failed), 1):
        if tree(left) != tree(right):
            raise ContinuationHistoryError(
                f"replay-equivalent reviewed prefix tree mismatch at step {ordinal}"
            )
    if len(failed) == len(reviewed):
        raise ContinuationHistoryError(
            "replay-equivalent reviewed prefix has no positive continuation suffix"
        )
    return ContinuationHistory(
        "replay-equivalent-prefix", failed_base, failed, reviewed
    )
