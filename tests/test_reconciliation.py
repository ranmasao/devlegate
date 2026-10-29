# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Reconciliation evidence identity proofs."""

import subprocess
import threading
from types import SimpleNamespace

import pytest

from devlegate import reconciliation_transactions as transactions
from devlegate import runtime
from devlegate.reconciliation import has_valid_evidence_identity
from devlegate.runtime import DevlegateError, ServiceEngine, ShutdownInterrupted


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


def test_rewrite_observation_failure_leaves_temporary_base_in_memory(monkeypatch):
    admission = SimpleNamespace(
        checkpoint="checkpoint", original_base="base", target="target",
        execution_remote_head=None, evidence_ref="evidence",
    )
    workspace = SimpleNamespace(head="checkpoint", dirty=False, path="workspace")
    durable = {}

    class Owner:
        _state = {
            "execution_base_head": "base", "reconciliation": {"execution_id": "exec"}
        }

        def reconciliation_workspace_manager(self, _ticket):
            return SimpleNamespace(branch="work")

        def _execution_workspace_for_recovery(self):
            if self._state["execution_base_head"] == "target":
                raise RuntimeError("observation failed")
            return workspace

        def _execution_remote_head(self, _branch):
            return None

        def _save_state(self, _phase, **fields):
            self._state = {**self._state, **fields}
            durable.update(self._state)

        def reconciliation_git(self, *_args):
            return SimpleNamespace(returncode=0)

    owner = Owner()
    monkeypatch.setattr(
        transactions, "prove_workspace_lineage", lambda *args: ["checkpoint"]
    )
    monkeypatch.setattr(transactions, "admit_update_base", lambda *args: admission)
    with pytest.raises(RuntimeError, match="observation failed"):
        transactions.start_fresh_rewrite(owner, "T-1", admission, False)
    assert durable["execution_base_head"] == "base"
    assert durable["reconciliation"]["rewrite_stage"] == "local-pending"
    assert owner._state["execution_base_head"] == "target"


def test_force_retry_evidence_observation_requires_owner(tmp_path, monkeypatch):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.execution_worktree_root = tmp_path
    engine._host_owner_thread_id = -1
    engine._stop_event = None
    engine._state = {
        "execution_id": "exec",
        "execution_control_head": "control",
        "execution_base_head": "base",
    }
    monkeypatch.setattr(engine, "_sync_control", lambda: ("control", "remote"))

    class WorkspaceManager:
        path = tmp_path

        def __init__(self, *args, **kwargs):
            pass

        def _registrations(self):
            return {tmp_path.resolve(): {}}

        def _validate_existing(self, *args):
            return object()

        def verify_submodules(self, workspace):
            pass

        def checkpoint(self, *args, **kwargs):
            return SimpleNamespace(after_head="checkpoint")

    monkeypatch.setattr(runtime, "ExecutionWorkspaceManager", WorkspaceManager)
    monkeypatch.setattr(runtime, "_git", lambda *a, **k: pytest.fail("Git spawned"))
    with pytest.raises(DevlegateError):
        engine._force_retry_transition("T-1", "request", {}, {})


@pytest.mark.parametrize("shutdown", [False, True])
def test_product_evidence_rejects_unowned_or_stopped_git(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    report = SimpleNamespace(
        ticket_id="T-1", execution_id="exec", code_base_head="base",
        workspace_head="checkpoint",
    )
    product = {"remote_head": "remote", "local_head": "local"}

    def unexpected_git(*args, **kwargs):
        pytest.fail("Git must not be invoked")

    monkeypatch.setattr("devlegate.runtime._git", unexpected_git)
    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._persist_product_reconciliation(report, product, "checkpoint")


@pytest.fixture
def product_evidence(tmp_path):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args], check=True,
            text=True, capture_output=True,
        ).stdout.strip()

    git("init")
    git("config", "user.name", "Test")
    git("config", "user.email", "test@example.com")
    git("commit", "--allow-empty", "-m", "base")
    base = git("rev-parse", "HEAD")
    git("commit", "--allow-empty", "-m", "checkpoint")
    checkpoint = git("rev-parse", "HEAD")
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine._host_owner_thread_id = None
    engine._stop_event = None
    engine._state = {"execution_control_head": "control", "control_head": "control"}
    report = SimpleNamespace(
        ticket_id="T-1", execution_id="exec", code_base_head=base,
        workspace_head=checkpoint, execution_branch="work", execution_path="work",
        as_dict=lambda: {"workspace_head": checkpoint},
    )
    product = {
        "remote_head": base, "local_head": checkpoint, "reason": "changed",
        "branch": "main", "dirty": False, "target_eligible": True,
    }
    ref = engine._reconciliation_evidence_ref("T-1", "exec")
    saved = []

    def save(phase, **kwargs):
        assert git("rev-parse", ref) == checkpoint
        saved.append((phase, kwargs))

    engine._save_state = save
    return engine, report, product, ref, base, checkpoint, saved, git


@pytest.mark.parametrize("existing", ["absent", "equal", "conflict"])
def test_product_evidence_existing_refs(product_evidence, existing):
    engine, report, product, ref, base, checkpoint, saved, git = product_evidence
    if existing != "absent":
        git("update-ref", ref, checkpoint if existing == "equal" else base)
    if existing == "conflict":
        with pytest.raises(runtime.WorkflowBlockedError, match="inconsistent"):
            engine._persist_product_reconciliation(report, product, checkpoint)
        assert git("rev-parse", ref) == base
        assert not saved
    else:
        observed = engine._persist_product_reconciliation(report, product, checkpoint)
        assert observed == checkpoint
        assert git("rev-parse", ref) == checkpoint
        assert saved[0][0] == "idle"
        assert saved[0][1]["reconciliation"]["evidence_ref"] == ref


@pytest.mark.parametrize("racer", ["equal", "conflict", "absent"])
def test_product_evidence_create_race_reobserves(product_evidence, monkeypatch, racer):
    engine, report, product, ref, base, checkpoint, saved, git = product_evidence
    create = runtime.create_ref_if_absent

    def race(repo, reference, oid, *, runner):
        if racer != "absent":
            git("update-ref", ref, checkpoint if racer == "equal" else base)
            return create(repo, reference, oid, runner=runner)
        return subprocess.CompletedProcess([], 1, "", "failure")

    monkeypatch.setattr(runtime, "create_ref_if_absent", race)
    if racer == "equal":
        engine._persist_product_reconciliation(report, product, checkpoint)
        assert saved
        assert git("rev-parse", ref) == checkpoint
    else:
        with pytest.raises(runtime.WorkflowBlockedError):
            engine._persist_product_reconciliation(report, product, checkpoint)
        assert not saved
        if racer == "conflict":
            assert git("rev-parse", ref) == base
        else:
            assert subprocess.run(
                ["git", "-C", str(engine.repo), "show-ref", "--verify", "--quiet", ref]
            ).returncode != 0


def test_product_evidence_interrupted_after_pin_restarts(product_evidence, monkeypatch):
    engine, report, product, ref, _, checkpoint, saved, git = product_evidence
    create = runtime.create_ref_if_absent

    def interrupted(*args, **kwargs):
        assert create(*args, **kwargs).returncode == 0
        raise ShutdownInterrupted

    monkeypatch.setattr(runtime, "create_ref_if_absent", interrupted)
    with pytest.raises(ShutdownInterrupted):
        engine._persist_product_reconciliation(report, product, checkpoint)
    assert git("rev-parse", ref) == checkpoint
    assert not saved
    monkeypatch.setattr(runtime, "create_ref_if_absent", create)
    engine.recovery_persist_reconciliation(report, product, checkpoint)
    assert saved[0][1]["reconciliation"]["worker_checkpoint"] == checkpoint


def test_product_evidence_normal_fallback_and_replay_requirement(product_evidence):
    engine, report, product, ref, base, checkpoint, saved, git = product_evidence
    product.update(remote_head=None, local_head=base)
    with pytest.raises(runtime.WorkflowBlockedError, match="remote identity"):
        engine.recovery_persist_reconciliation(report, product, checkpoint)
    assert not saved
    assert engine._persist_product_reconciliation(
        report, product, checkpoint, allow_base_fallback=True
    ) == base
    assert git("rev-parse", ref) == checkpoint


@pytest.mark.parametrize("shutdown", [False, True])
def test_checkpoint_push_rejects_unowned_or_stopped_git(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    workspace = SimpleNamespace(path=tmp_path, branch="work")
    monkeypatch.setattr(engine, "_resolve_commit_runtime", lambda *_: "checkpoint")
    monkeypatch.setattr(engine, "_execution_remote_head", lambda *_: None)
    monkeypatch.setattr(
        "devlegate.runtime._git", lambda *args, **kwargs: pytest.fail("Git spawned")
    )

    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._publish_execution_branch(workspace, "checkpoint", None)


def test_stopped_execution_cannot_publish_another_branch(tmp_path, monkeypatch):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine._host_owner_thread_id = None
    engine._stop_event = threading.Event()
    engine._stop_event.set()
    engine._state = {"phase": "agent_running", "execution_branch": "admitted"}
    workspace = SimpleNamespace(path=tmp_path, branch="other")
    monkeypatch.setattr(runtime, "_git", lambda *a, **k: pytest.fail("Git spawned"))

    with pytest.raises(ShutdownInterrupted):
        engine._publish_execution_branch(workspace, "checkpoint", None)


def test_checkpoint_ambiguous_push_reobserves_remote(tmp_path, monkeypatch):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None
    engine._stop_event = None
    workspace = SimpleNamespace(path=tmp_path, branch="work")
    monkeypatch.setattr(engine, "_resolve_commit_runtime", lambda *_: "checkpoint")
    observations = iter([None, "checkpoint"])
    monkeypatch.setattr(engine, "_execution_remote_head", lambda *_: next(observations))
    calls = []

    def failed_push(repo, *args, check=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "connection lost")

    monkeypatch.setattr("devlegate.runtime._git", failed_push)
    engine._publish_execution_branch(workspace, "checkpoint", None)
    assert len(calls) == 1
    assert calls[0][0] == "push"


def test_admitted_checkpoint_stop_during_ambiguous_push_reobserves(
    tmp_path, monkeypatch
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None
    engine._stop_event = threading.Event()
    engine._state = {"phase": "agent_running", "execution_branch": "work"}
    workspace = SimpleNamespace(path=tmp_path, branch="work")
    monkeypatch.setattr(engine, "_resolve_commit_runtime", lambda *_: "checkpoint")
    observations = iter([None, "checkpoint"])
    monkeypatch.setattr(engine, "_execution_remote_head", lambda *_: next(observations))
    calls = []

    def interrupted_push(repo, *args, check=False):
        calls.append(args)
        engine._stop_event.set()
        return subprocess.CompletedProcess(args, 1, "", "connection lost")

    monkeypatch.setattr(runtime, "_git", interrupted_push)
    engine._publish_execution_branch(workspace, "checkpoint", None)
    assert len(calls) == 1
    assert calls[0][0] == "push"
    assert next(observations, "exhausted") == "exhausted"


@pytest.mark.parametrize("shutdown", [False, True])
def test_rework_pin_refuses_git_before_first_observation(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    monkeypatch.setattr(
        runtime, "_git", lambda *args, **kwargs: pytest.fail("Git spawned")
    )
    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._pin_rework_evidence("T-1", "exec", "checkpoint")


def test_rework_pin_replays_after_stop_following_ref_creation(tmp_path, monkeypatch):
    def git(*args):
        return subprocess.run(
            ["git", "-C", str(tmp_path), *args],
            check=True, capture_output=True, text=True,
        ).stdout.strip()

    git("init")
    git("-c", "user.name=Test", "-c", "user.email=test@example.com",
        "commit", "--allow-empty", "-m", "base")
    checkpoint = git("rev-parse", "HEAD")
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine._host_owner_thread_id = None
    engine._stop_event = threading.Event()
    original = runtime._git

    def stop_after_create(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if args[0] == "update-ref":
            engine._stop_event.set()
        return result

    monkeypatch.setattr(runtime, "_git", stop_after_create)
    with pytest.raises(ShutdownInterrupted):
        engine._pin_rework_evidence("T-1", "exec", checkpoint)
    evidence = engine._workspace_recovery_ref("T-1", "exec")
    assert git("rev-parse", evidence) == checkpoint
    engine._stop_event.clear()
    assert engine._pin_rework_evidence("T-1", "exec", checkpoint) == evidence


@pytest.mark.parametrize("shutdown", [False, True])
def test_checkpoint_initial_observation_rejects_unowned_or_stopped_git(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    monkeypatch.setattr(
        runtime, "_git", lambda *args, **kwargs: pytest.fail("Git spawned")
    )
    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._publish_execution_branch(
            SimpleNamespace(path=tmp_path, branch="work"), "checkpoint", None
        )
