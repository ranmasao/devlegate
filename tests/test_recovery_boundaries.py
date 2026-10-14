# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from pathlib import Path

from devlegate.execution_workspace import WorkspaceInspection
from devlegate.reconciliation import (
    prove_linear_lineage,
    rewrite_is_authorized,
    same_parent_replacement,
)
from devlegate.reconciliation_transactions import (
    ReconciliationTransactionError,
    admit_update_base,
    authorize_workspace_rewrite,
    prove_workspace_lineage,
    publish_rewritten_lineage,
    run_update_base,
)
from devlegate.recovery import (
    classify_stage,
    classify_stranded_execution,
    interruption_kind,
    validate_execution_binding,
)
from devlegate.recovery_transactions import recover_stranded_execution
from devlegate.recovery_workspace import inspect_bound_workspace


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
    assert (
        rewrite_is_authorized(
            published_head="b", lineage=proof, rewrite_published=False
        )
        is False
    )
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


class _WorkspaceManager:
    branch = "devlegate/TASK-1"
    path = Path("/tmp/execution")

    def inspect(self, _base, _expected):
        return WorkspaceInspection("UNSAFE", "execution branch is missing")

    def _registrations(self):
        return {}


class _WorkspaceOwner:
    repo = Path("/tmp/repo")

    def recovery_workspace_manager(self, _ticket_id):
        return _WorkspaceManager()

    def recovery_git(self, _cwd, *_args, check=False):
        class Result:
            returncode = 1
            stdout = ""

        return Result()

    def recovery_reports(self, _ticket_id):
        return []


def test_workspace_inspection_allows_only_unattached_missing_branch_rebuild():
    state = {
        "phase": "agent_pending",
        "execution_ticket_id": "TASK-1",
        "execution_id": "exec-1",
        "execution_base_head": "base",
        "execution_branch": "devlegate/TASK-1",
        "execution_path": "/tmp/execution",
        "worker_identity": None,
        "pending_execution_report": None,
    }

    result = inspect_bound_workspace(_WorkspaceOwner(), state)

    assert result.classification == "RECOVERABLE"


class _LineageOwner:
    def __init__(self, *, descendant=True, evidence=True):
        self.descendant = descendant
        self.evidence = evidence
        self.executed = []

    def reconciliation_pending(self):
        return {
            "ticket_id": "TASK-1",
            "original_base": "base",
            "control_head": "control",
            "worker_checkpoint": "checkpoint",
            "evidence_ref": "refs/evidence",
            "execution_branch": "devlegate/TASK-1",
            "execution_path": "/tmp/execution",
            "execution_remote_head": None,
        }

    def reconciliation_target(self, value):
        return value

    def reconciliation_product(self, _base):
        return {
            "branch": "main",
            "dirty": False,
            "local_head": "target",
            "remote_head": "target",
        }

    def reconciliation_target_is_descendant(self, _base, _target):
        return self.descendant

    def reconciliation_same_parent_replacement(self, _base, _target):
        return False

    def reconciliation_evidence_matches(self, _reference, _checkpoint):
        return self.evidence

    def reconciliation_workspace_binding(self, _ticket_id, _value):
        return True

    def reconciliation_lock(self):
        class Lock:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return None

        return Lock()

    def reconciliation_recover_update_base(
        self, ticket_id, admission, authorized
    ):
        self.executed.append((ticket_id, admission, authorized))
        return 0


def test_lineage_admission_captures_provenance_before_mutation():
    admission = admit_update_base(_LineageOwner(), "TASK-1", "target")

    assert admission.original_base == "base"
    assert admission.checkpoint == "checkpoint"


def test_lineage_admission_rejects_unproven_history():
    try:
        admit_update_base(_LineageOwner(descendant=False), "TASK-1", "target")
    except ReconciliationTransactionError as error:
        assert "descendant" in str(error)
    else:
        raise AssertionError("divergent lineage must fail closed")


def test_update_base_transaction_admits_before_owner_effect_capability():
    owner = _LineageOwner()

    assert run_update_base(
        owner, "TASK-1", "target", rewrite_published=True, take_lock=True
    ) == 0

    ticket_id, admission, authorized = owner.executed[0]
    assert ticket_id == "TASK-1"
    assert admission.target == "target"
    assert authorized is True


def test_update_base_rejects_owner_without_effect_phase():
    class AdmissionOnly(_LineageOwner):
        reconciliation_recover_update_base = None

    try:
        run_update_base(AdmissionOnly(), "TASK-1", "target")
    except ReconciliationTransactionError as error:
        assert "effect phases" in str(error)
    else:
        raise AssertionError("missing effect phase must fail closed")


class _RewriteOwner:
    def __init__(self):
        self.events = []

    def reconciliation_pin_rewrite_evidence(self, reference, rewritten_head):
        self.events.append(("pin", reference, rewritten_head))

    def reconciliation_publish_rewrite(self, branch, rewritten_head, expected_remote):
        self.events.append(("publish", branch, rewritten_head, expected_remote))

    @staticmethod
    def reconciliation_git(_cwd, *args, check=False):
        command = list(args)
        if command[:2] == ["rev-list", "--reverse"]:
            return type("Result", (), {"returncode": 0, "stdout": "one\ntwo\n"})()
        commit = command[-1]
        parents = {"one": "one root\n", "two": "two one\n"}
        return type(
            "Result",
            (),
            {"returncode": 0, "stdout": parents.get(commit, "")},
        )()


def test_published_rewrite_pins_displaced_history_before_lease_push():
    owner = _RewriteOwner()

    publish_rewritten_lineage(
        owner,
        branch="devlegate/TASK-1",
        rewritten_head="rewritten",
        expected_remote="old-remote",
        evidence_ref="refs/devlegate/rewrite/TASK-1",
    )

    assert owner.events == [
        ("pin", "refs/devlegate/rewrite/TASK-1", "rewritten"),
        ("publish", "devlegate/TASK-1", "rewritten", "old-remote"),
    ]


def test_unpublished_rewrite_retains_evidence_without_remote_mutation():
    owner = _RewriteOwner()

    publish_rewritten_lineage(
        owner,
        branch="devlegate/TASK-1",
        rewritten_head="rewritten",
        expected_remote=None,
        evidence_ref="refs/devlegate/rewrite/TASK-1",
    )

    assert owner.events == [("pin", "refs/devlegate/rewrite/TASK-1", "rewritten")]


def test_rewrite_does_not_publish_when_evidence_pin_fails():
    class FailingOwner(_RewriteOwner):
        def reconciliation_pin_rewrite_evidence(self, reference, rewritten_head):
            self.events.append(("pin", reference, rewritten_head))
            raise RuntimeError("evidence pin failed")

    owner = FailingOwner()

    try:
        publish_rewritten_lineage(
            owner,
            branch="devlegate/TASK-1",
            rewritten_head="rewritten",
            expected_remote="old-remote",
            evidence_ref="refs/devlegate/rewrite/TASK-1",
        )
    except RuntimeError as error:
        assert str(error) == "evidence pin failed"
    else:
        raise AssertionError("evidence failure must stop publication")

    assert owner.events == [
        ("pin", "refs/devlegate/rewrite/TASK-1", "rewritten")
    ]


def test_rewrite_boundary_proves_chain_and_rejects_unapproved_published_tip():
    owner = _RewriteOwner()
    workspace = type("Workspace", (), {"path": Path("/tmp/execution")})()

    assert prove_workspace_lineage(owner, workspace, "root", "two") == (
        "one",
        "two",
    )
    try:
        authorize_workspace_rewrite(
            owner, workspace, "root", "two", "one", rewrite_published=False
        )
    except ReconciliationTransactionError as error:
        assert "authorization" in str(error)
    else:
        raise AssertionError("published lineage rewrite must require authorization")
