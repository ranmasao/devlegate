# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json

import pytest

from devlegate.change_revisions import (
    Checkpoint,
    RevisionNamespace,
    RevisionUnavailable,
)


def test_empty_and_actual_checkpoints_are_bounded_to_task():
    empty = RevisionNamespace("secret-base", ())
    assert empty.resolve("HEAD").identity == "secret-base"
    with pytest.raises(RevisionUnavailable):
        empty.resolve("HEAD-1")
    namespace = RevisionNamespace("secret-base", (
        Checkpoint("secret-one", "secret-base"),
        Checkpoint("secret-two", "secret-base"),
    ))
    assert namespace.resolve("HEAD-1").identity == "secret-one"
    assert namespace.inventory()["checkpoint_count"] == 2
    assert "secret" not in json.dumps(namespace.inventory())
    for label in (
        "HEAD-0", "HEAD-2", "HEAD-01", "PREVIOUS", "main", "HEAD-" + "9" * 10000
    ):
        with pytest.raises(RevisionUnavailable):
            namespace.resolve(label)


def test_retained_checkpoint_from_old_base_cannot_be_silently_compared():
    namespace = RevisionNamespace("new-base", (
        Checkpoint("old-checkpoint", "old-base"),
        Checkpoint("new-checkpoint", "new-base"),
    ))
    assert namespace.resolve("HEAD-1").identity == "old-checkpoint"
    with pytest.raises(RevisionUnavailable, match="cross-base"):
        namespace.comparison("HEAD-1", "WORKTREE")
    assert namespace.comparison("BASE", "HEAD")[1].identity == "new-checkpoint"


def test_duplicate_or_incomplete_provenance_is_rejected():
    with pytest.raises(ValueError):
        RevisionNamespace(
            "base", (Checkpoint("same", "base"), Checkpoint("same", "base"))
        )
    with pytest.raises(ValueError):
        RevisionNamespace("base", (Checkpoint("", "base"),))
