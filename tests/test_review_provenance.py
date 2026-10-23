# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Persisted curator-review binding compatibility."""

import hashlib

import pytest

from devlegate.review_provenance import ReviewProvenanceError, restore_review_binding


def _binding():
    ticket_id = "TASK-088"
    execution_id = "a" * 32
    checkpoint = "b" * 40
    control = "c" * 40
    content = (
        "<!-- devlegate-review-v1\n"
        f"ticket: {ticket_id}\n"
        f"execution: {execution_id}\n"
        f"checkpoint: {checkpoint}\n"
        f"control: {control}\n"
        "disposition: rework\n"
        "-->\n"
        "Preserve the worker changes.\n"
    )
    return {
        "content": content,
        "path": f"reviews/{ticket_id}/{execution_id}.md",
        "publication": "d" * 40,
        "transition": ["e" * 40],
        "sha256": hashlib.sha256(content.encode()).hexdigest(),
    }, ticket_id, execution_id, checkpoint, control


def test_restore_review_binding_accepts_known_inert_legacy_markers():
    binding, ticket_id, execution_id, checkpoint, control = _binding()
    binding.update(legacy=False, legacy_boundary=None)

    restored = restore_review_binding(
        binding,
        ticket_id=ticket_id,
        execution_id=execution_id,
        checkpoint=checkpoint,
        control_head=control,
    )

    assert restored.content == binding["content"]


@pytest.mark.parametrize("key, value", [("legacy", True), ("legacy_boundary", "x")])
def test_restore_review_binding_rejects_non_inert_legacy_markers(key, value):
    binding, ticket_id, execution_id, checkpoint, control = _binding()
    binding[key] = value

    with pytest.raises(ReviewProvenanceError, match="compatibility fields"):
        restore_review_binding(
            binding,
            ticket_id=ticket_id,
            execution_id=execution_id,
            checkpoint=checkpoint,
            control_head=control,
        )
