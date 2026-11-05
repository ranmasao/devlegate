# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Admission and effect receipts under the real serialized service owner."""

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime import DevlegateError
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from devlegate.worker_supervisor import WorkerProcessIdentity


class InjectedCrash(BaseException):
    pass


@pytest.fixture
def engine(tmp_path, monkeypatch):
    repo, config, _state = control_fixture(tmp_path)
    result = invoke(repo, "control", "init", config=config)
    assert result.returncode == 0, result.stderr
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("implementation\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None,
        )

    monkeypatch.setattr(owner._workers, "run", worker)
    return owner, config


def test_owner_admission_proof_and_restart_are_read_only(engine, monkeypatch):
    owner, config = engine
    observed = []
    original = ResourceBinding.record_intent

    def record(binding):
        assert owner._state["execution_resource_binding"] == binding.intent
        assert not binding.manager.path.exists()
        assert not binding.manager._branch_exists()
        observed.append(binding.intent["generation"])
        original(binding)

    monkeypatch.setattr(ResourceBinding, "record_intent", record)
    assert run_test_iteration(owner) == 0
    assert len(observed) == 1
    before = owner._runtime_store.observe()
    proof = owner.execution_resource_proof()
    assert proof.local_state == "proved"
    assert proof.upstream_exact and proof.state == "unproven"
    assert owner._runtime_store.observe() == before
    restarted = Devlegate(config)
    assert restarted.execution_resource_proof() == proof
    assert owner._runtime_store.observe() == before


@pytest.mark.parametrize("operation", ["worker", "checkpoint", "publication"])
@pytest.mark.parametrize("receipt_written", [False, True])
def test_effect_receipt_crashes_recover_exactly_or_block(
    engine, monkeypatch, operation, receipt_written
):
    owner, config = engine
    original = ResourceBinding.finish_operation

    def finish(binding, name, details):
        if name == operation:
            if receipt_written:
                original(binding, name, details)
            raise InjectedCrash()
        original(binding, name, details)

    monkeypatch.setattr(ResourceBinding, "finish_operation", finish)
    with pytest.raises(InjectedCrash):
        run_test_iteration(owner)
    monkeypatch.setattr(ResourceBinding, "finish_operation", original)
    restarted = Devlegate(config)
    launches = []
    monkeypatch.setattr(restarted._workers, "run", lambda *_a, **_k: launches.append(1))
    before_head = git(owner.repo, "rev-parse", "HEAD").stdout
    result = restarted.execution_resource_proof()
    if not receipt_written:
        assert not result.upstream_exact
        assert operation in result.reason or (
            operation == "worker" and "dirty" in result.reason
        )
        with pytest.raises((DevlegateError, ExecutionWorkspaceError, OSError)):
            run_test_iteration(restarted)
    else:
        # A durable receipt permits lifecycle recovery, never a second launch.
        try:
            run_test_iteration(restarted)
        except (DevlegateError, ExecutionWorkspaceError, OSError):
            # Worker-return recovery may still require an explicit disposition
            # because no completed report was durably recorded at this point.
            assert operation == "worker"
    assert not launches
    assert git(owner.repo, "rev-parse", "HEAD").stdout == before_head


def test_admission_crash_before_independent_intent_has_no_resource_effect(
    engine, monkeypatch
):
    owner, config = engine
    original = ResourceBinding.record_intent
    monkeypatch.setattr(
        ResourceBinding, "record_intent",
        lambda _binding: (_ for _ in ()).throw(InjectedCrash()),
    )
    with pytest.raises(InjectedCrash):
        run_test_iteration(owner)
    intent = owner._state["execution_resource_binding"]
    assert not owner.execution_worktree_root.joinpath("work", "T-1").exists()
    assert not git(owner.repo, "for-each-ref", intent["ref"]).stdout
    monkeypatch.setattr(ResourceBinding, "record_intent", original)
    restarted = Devlegate(config)
    proof = restarted.execution_resource_proof()
    assert proof.state == "unproven" and "missing evidence" in proof.reason
    with pytest.raises(DevlegateError):
        run_test_iteration(restarted)
    assert not git(owner.repo, "for-each-ref", intent["ref"]).stdout


def test_second_owner_cannot_create_generation_evidence(engine):
    owner, config = engine
    other = Devlegate(config)
    with owner._lock():
        with pytest.raises(DevlegateError):
            run_test_iteration(other)
    assert not (owner.execution_worktree_root / "bindings").exists()


def test_worker_handoff_receipt_precedes_runtime_identity(engine, monkeypatch):
    owner, _config = engine
    original = owner._save_state
    observed = []

    def save(phase, **fields):
        if fields.get("execution_stage") == "worker-running":
            receipt = owner._execution_generation_binding().operation_receipt("worker")
            assert receipt["identity"] == fields["worker_identity"]
            observed.append(receipt)
        return original(phase, **fields)

    def worker(_workspace, _prompt, *, execution_id, identity_handler, **_kwargs):
        identity_handler(WorkerProcessIdentity(
            execution_id, 1, 1, 1, "test-prior-boot", 0,
        ))
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None,
        )

    monkeypatch.setattr(owner, "_save_state", save)
    monkeypatch.setattr(owner._workers, "run", worker)
    assert run_test_iteration(owner) == 0
    assert len(observed) == 1


def test_admission_preserves_archived_branch_worktree_and_evidence(engine):
    owner, _config = engine
    archived = owner.execution_worktree_root / "work" / "TASK-134"
    archived.parent.mkdir(parents=True)
    branch = "devlegate/work/TASK-134"
    evidence = "refs/devlegate/evidence/archive/TASK-134"
    git(owner.repo, "worktree", "add", "-b", branch, archived)
    (archived / "immutable-report.json").write_text('{"archived": true}\n')
    (archived / "review.txt").write_text("preserved review evidence\n")
    git(archived, "add", ".")
    git(archived, "commit", "-m", "preserved archive fixture")
    head = git(archived, "rev-parse", "HEAD").stdout.strip()
    git(owner.repo, "update-ref", evidence, head)
    git(owner.repo, "push", "origin", f"{head}:{evidence}")

    def snapshot():
        return {
            path.relative_to(archived): (path.read_bytes(), path.stat().st_mtime_ns)
            for path in archived.rglob("*") if path.is_file()
        }

    before = snapshot()
    assert run_test_iteration(owner) == 0
    assert snapshot() == before
    assert git(owner.repo, "rev-parse", branch).stdout.strip() == head
    assert git(owner.repo, "rev-parse", evidence).stdout.strip() == head
    assert git(owner.repo, "ls-remote", "origin", evidence).stdout.split()[0] == head
