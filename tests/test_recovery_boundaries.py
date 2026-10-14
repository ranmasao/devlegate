# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from devlegate.reconciliation import (
    prove_linear_lineage,
    rewrite_is_authorized,
    same_parent_replacement,
)
from devlegate.recovery import (
    classify_stage,
    classify_stranded_execution,
    interruption_kind,
    validate_execution_binding,
)
from devlegate.recovery_transactions import recover_stranded_execution


def test_recovery_classification_does_not_authorize_operator_stop():
    state = {
        "phase": "agent_running",
        "execution_stage": "worker-running",
        "execution_interruption_kind": "operator_abort",
        "execution_ticket_id": "TASK-1",
    }

    assert interruption_kind(state) == "operator_abort"
    assert classify_stranded_execution(state).action == "prove-worker-absent"
    assert classify_stage("post-worker") == "worker"


def test_recovery_binding_fails_closed_for_worker_or_result_ownership():
    state = {
        "phase": "agent_pending",
        "execution_ticket_id": "TASK-1",
        "execution_id": "exec-1",
        "execution_base_head": "base",
        "execution_branch": "devlegate/TASK-1",
        "execution_path": "/tmp/worktree",
        "worker_identity": None,
        "pending_execution_report": None,
    }

    assert validate_execution_binding(state, require_pre_worker=True)[0] == "TASK-1"
    state["worker_identity"] = {"pid": 1}
    try:
        validate_execution_binding(state, require_pre_worker=True)
    except ValueError as error:
        assert "worker ownership" in str(error)
    else:
        raise AssertionError("worker ownership must fail closed")


def test_lineage_proofs_reject_merges_and_gate_published_rewrites():
    parents = {"a": ("root",), "b": ("a",), "merge": ("a", "other")}
    proof = prove_linear_lineage("root", ["a", "b"], parents)
    assert proof.valid
    assert rewrite_is_authorized(
        published_head="b", lineage=proof, rewrite_published=False
    ) is False
    assert not prove_linear_lineage("root", ["merge"], parents).valid
    assert same_parent_replacement("a", "replacement", {"a": ("root",)}) is False


class _WorkerRecoveryOwner:
    def __init__(self, observation):
        self.observation = observation
        self.saved = []
        self.normalized = []
        self.blocked = []

    def recovery_save_state(self, state, **values):
        self.saved.append((state, values))

    def recovery_worker_identity(self, value, execution_id):
        return (value, execution_id)

    def recovery_worker_observation(self, identity):
        return self.observation

    def normalize_stranded_execution(self, interruption):
        self.normalized.append(interruption)

    def recovery_publish_blocked(self, reason):
        self.blocked.append(reason)


def test_stranded_transaction_normalizes_only_after_absence_proof():
    owner = _WorkerRecoveryOwner("absent")
    state = {
        "phase": "agent_running",
        "execution_stage": "worker-running",
        "execution_ticket_id": "TASK-1",
        "execution_id": "exec-1",
        "worker_identity": {"pid": 1},
    }

    assert recover_stranded_execution(owner, state) == "TASK-1"
    assert owner.normalized == ["process_loss"]
    assert owner.blocked == []


def test_stranded_transaction_blocks_live_worker_without_normalization():
    owner = _WorkerRecoveryOwner("matching-live")
    state = {
        "phase": "agent_running",
        "execution_stage": "worker-running",
        "execution_ticket_id": "TASK-1",
        "execution_id": "exec-1",
        "worker_identity": {"pid": 1},
    }

    try:
        recover_stranded_execution(owner, state)
    except ValueError as error:
        assert "still alive" in str(error)
    else:
        raise AssertionError("live worker must block recovery")
    assert owner.normalized == []
    assert owner.blocked
