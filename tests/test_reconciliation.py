# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Reconciliation evidence identity proofs."""

import pytest

from devlegate.reconciliation import has_valid_evidence_identity


def _rewritten_reconciliation():
    return {
        "ticket_id": "LAB-158",
        "execution_id": "c68282b27e6244f0a7260233a3a9f7c0",
        "status": "resolved",
        "resolution": "update-base",
        "resolution_class": "update-base with published-lineage rewrite",
        "evidence_ref": (
            "refs/devlegate/reconciliation/LAB-158/"
            "c68282b27e6244f0a7260233a3a9f7c0-rewritten"
        ),
        "displaced_evidence_ref": (
            "refs/devlegate/reconciliation/LAB-158/"
            "c68282b27e6244f0a7260233a3a9f7c0"
        ),
    }


def test_rewritten_evidence_identity_accepts_resolved_update_base_provenance():
    assert has_valid_evidence_identity(_rewritten_reconciliation())


def test_rewritten_evidence_identity_accepts_legacy_resolved_provenance():
    reconciliation = _rewritten_reconciliation()
    reconciliation.pop("resolution_class")

    assert has_valid_evidence_identity(reconciliation)


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_ref", "refs/devlegate/reconciliation/LAB-158/other-rewritten"),
        ("displaced_evidence_ref", "refs/devlegate/reconciliation/LAB-157/exec"),
        ("displaced_evidence_ref", None),
        ("status", "pending"),
        ("resolution", "resume"),
        ("resolution_class", "unrecognized rewrite"),
    ],
)
def test_rewritten_evidence_identity_rejects_incoherent_provenance(field, value):
    reconciliation = _rewritten_reconciliation()
    reconciliation[field] = value

    assert not has_valid_evidence_identity(reconciliation)


def test_canonical_evidence_identity_remains_valid():
    assert has_valid_evidence_identity(
        {
            "ticket_id": "T-1",
            "execution_id": "done-execution",
            "evidence_ref": "refs/devlegate/reconciliation/T-1/done-execution",
        }
    )
