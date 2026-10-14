# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure proofs used by product-lineage reconciliation.

The coordinator supplies observed commit parents. This module does not run Git
or mutate refs, so malformed or divergent observations fail closed.
"""

from __future__ import annotations

import dataclasses
from collections.abc import Mapping, Sequence


@dataclasses.dataclass(frozen=True)
class LineageProof:
    root: str
    commits: tuple[str, ...]
    valid: bool
    reason: str


def prove_linear_lineage(
    root: str, commits: Sequence[str], parents: Mapping[str, Sequence[str]]
) -> LineageProof:
    """Prove a non-empty, ordered, single-parent chain from ``root``."""
    ordered = tuple(commits)
    if not root or not ordered:
        return LineageProof(root, ordered, False, "lineage is empty")
    previous = root
    for commit in ordered:
        commit_parents = parents.get(commit)
        if commit_parents is None:
            return LineageProof(root, ordered, False, "commit parent is unavailable")
        if len(commit_parents) != 1 or commit_parents[0] != previous:
            return LineageProof(
                root, ordered, False, "lineage contains a merge, side branch, or gap"
            )
        previous = commit
    return LineageProof(root, ordered, True, "linear lineage proven")


def same_parent_replacement(
    original: str, target: str, parents: Mapping[str, Sequence[str]]
) -> bool:
    """Allow replacement only when both commits have the same sole parent."""
    if not original or not target or original == target:
        return False
    original_parents = parents.get(original)
    target_parents = parents.get(target)
    return (
        original_parents is not None
        and target_parents is not None
        and len(original_parents) == 1
        and len(target_parents) == 1
        and original_parents[0] == target_parents[0]
    )


def rewrite_is_authorized(
    *, published_head: str | None, lineage: LineageProof, rewrite_published: bool
) -> bool:
    """Require explicit authorization for changing an already published tip."""
    if not lineage.valid:
        return False
    return published_head is None or rewrite_published
