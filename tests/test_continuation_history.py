# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real-object tests of content continuity, independent of resource ownership."""

import subprocess

import pytest

from devlegate.continuation_history import (
    ContinuationHistoryError,
    prove_continuation_history,
)
from devlegate.git import GitAncestryError, GitObservationError


@pytest.fixture
def history(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", *args], cwd=tmp_path, text=True, capture_output=True, check=True
        ).stdout.strip()

    git("init", "-b", "main")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    trees = []
    for value in ("base", "first", "second", "suffix", "different"):
        (tmp_path / "file").write_text(value)
        git("add", ".")
        trees.append(git("write-tree"))

    def commit(tree, parents=(), label="commit"):
        args = ["commit-tree", tree]
        for parent in parents:
            args.extend(("-p", parent))
        return git(*args, "-m", label)

    base = commit(trees[0])

    def chain(indices, *, start=base, label="chain"):
        commits = []
        for ordinal, index in enumerate(indices):
            start = commit(trees[index], (start,), f"{label}-{ordinal}")
            commits.append(start)
        return commits

    reviewed = chain((1, 2), label="reviewed")
    failed = chain((1, 2, 3), label="failed")
    assert all(left != right for left, right in zip(reviewed, failed))
    assert git("merge-base", reviewed[-1], failed[-1]) == base
    for left, right in zip(reviewed, failed):
        assert git("rev-parse", f"{left}^{{tree}}") == git(
            "rev-parse", f"{right}^{{tree}}"
        )
    return tmp_path, git, trees, commit, base, chain, reviewed, failed


def test_replayed_prefix_preserves_entire_failed_delta(history):
    repo, _, _, _, base, _, reviewed, failed = history
    proof = prove_continuation_history(repo, base, reviewed[-1], base, failed[-1])
    assert proof.mode == "replay-equivalent-prefix"
    assert proof.failed_commits == tuple(failed)
    assert proof.reviewed_commits == tuple(reviewed)


def test_direct_ancestry_keeps_existing_different_reviewed_base_rule(history):
    repo, _, _, _, base, _, reviewed, _ = history
    proof = prove_continuation_history(
        repo, reviewed[0], reviewed[0], base, reviewed[1]
    )
    assert proof.mode == "direct-ancestry"
    assert proof.failed_commits == tuple(reviewed)


@pytest.mark.parametrize(
    ("indices", "reason"),
    [
        ((4, 2, 3), "tree mismatch at step 1"),
        ((2, 1, 2), "tree mismatch at step 1"),
        ((2, 3), "tree mismatch at step 1"),
        ((1, 1, 2, 3), "tree mismatch at step 2"),
        ((1,), "history is shorter"),
        ((1, 2), "no positive continuation suffix"),
    ],
)
def test_replayed_prefix_refuses_reconvergence_order_and_length(
    history, indices, reason
):
    repo, _, _, _, base, chain, reviewed, _ = history
    failed = chain(indices, label="bad")
    with pytest.raises(ContinuationHistoryError, match=reason):
        prove_continuation_history(repo, base, reviewed[-1], base, failed[-1])


def test_replayed_prefix_refuses_equal_trees_from_unrelated_base(history):
    repo, _, trees, commit, base, chain, reviewed, _ = history
    other = commit(trees[0], label="unrelated identical base tree")
    failed = chain((1, 2, 3), start=other)
    with pytest.raises(ContinuationHistoryError, match="same exact reported base"):
        prove_continuation_history(repo, base, reviewed[-1], other, failed[-1])


@pytest.mark.parametrize("side", ["reviewed", "failed"])
def test_replayed_prefix_refuses_merge_in_either_source(history, side):
    repo, _, trees, commit, base, _, reviewed, failed = history
    merge = commit(trees[2], (reviewed[0], failed[0]))
    if side == "reviewed":
        reviewed = [merge]
    else:
        failed = [merge]
    with pytest.raises(ContinuationHistoryError, match="source history is not linear"):
        prove_continuation_history(repo, base, reviewed[-1], base, failed[-1])


@pytest.mark.parametrize("field", ["reviewed_head", "failed_head", "failed_base"])
def test_missing_commit_is_an_observation_failure(history, field):
    repo, _, _, _, base, _, reviewed, failed = history
    args = dict(reviewed_base=base, reviewed_head=reviewed[-1],
                failed_base=base, failed_head=failed[-1])
    args[field] = "f" * 40
    with pytest.raises(
        (GitObservationError, GitAncestryError, ContinuationHistoryError)
    ):
        prove_continuation_history(repo, **args)


def test_unreadable_tree_refuses_without_mutation(history):
    repo, git, trees, _, base, _, reviewed, failed = history
    before = git("for-each-ref")
    # Remove a loose tree only in this disposable fixture; commit objects remain.
    oid = trees[1]
    (repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    with pytest.raises((GitObservationError, ContinuationHistoryError)):
        prove_continuation_history(repo, base, reviewed[-1], base, failed[-1])
    assert git("for-each-ref") == before


@pytest.mark.parametrize("entry", ["mode", "gitlink"])
def test_full_root_tree_includes_modes_and_gitlinks(history, entry):
    repo, git, trees, commit, base, _, _, _ = history
    if entry == "mode":
        (repo / "file").chmod(0o644)
        git("add", "file")
        first_tree = git("write-tree")
        git("update-index", "--chmod=+x", "file")
        other_tree = git("write-tree")
    else:
        git("update-index", "--add", "--cacheinfo", f"160000,{base},dependency")
        first_tree = git("write-tree")
        other_commit = commit(trees[0], label="different submodule commit")
        git("update-index", "--cacheinfo", f"160000,{other_commit},dependency")
        other_tree = git("write-tree")
    assert first_tree != other_tree
    reviewed = commit(first_tree, (base,), "reviewed full tree")
    replay = commit(other_tree, (base,), "different full tree")
    suffix = commit(trees[3], (replay,), "later reconvergence")
    with pytest.raises(ContinuationHistoryError, match="tree mismatch at step 1"):
        prove_continuation_history(repo, base, reviewed, base, suffix)
