# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from devlegate.execution_resource_proof import inspect_execution_resource_proof


def test_intent_is_not_proof_of_resource_ownership():
    intent = {
        "ticket_id": "TASK-1",
        "execution_id": "execution-1",
        "nonce": "nonce-1",
        "status": "intent",
        "branch": "devlegate/work/TASK-1",
        "path": "/work/TASK-1",
    }
    state = {"execution_resource_intents": {"execution-1": intent}}
    proof = inspect_execution_resource_proof(state, "TASK-1", "execution-1")
    assert not proof.proven
    assert "does not prove" in proof.reason
    assert state["execution_resource_intents"]["execution-1"] == intent


def test_missing_or_conflicting_evidence_is_not_proof():
    for state in (
        {},
        {"execution_resource_intents": []},
        {"execution_resource_intents": {"execution-1": {"ticket_id": "TASK-2"}}},
    ):
        assert not inspect_execution_resource_proof(state, "TASK-1", "execution-1").proven
