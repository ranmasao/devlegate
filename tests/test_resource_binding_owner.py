# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Admission and effect receipts under the real serialized service owner."""

import json

import pytest
from _control_support import (
    Devlegate,
    _multi_checkpoint_update_base_fixture,
    control_fixture,
    git,
    invoke,
)
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
    if receipt_written and operation != "worker":
        intent = owner._state["execution_resource_binding"]
        payload = json.loads(git(
            owner.repo, "show",
            f"devlegate/control:executions/{intent['ticket_id']}/{intent['execution_id']}.json",
        ).stdout)
        assert payload["resource_generation"] == intent["generation"]
        binding = restarted._execution_generation_binding()
        assert payload["workspace_head"] == binding._expected_head()


@pytest.mark.parametrize("token", [None, "f" * 64])
@pytest.mark.parametrize("stage", ["checkpointing", "post-checkpoint", "lifecycle"])
def test_recovery_refuses_missing_or_altered_report_generation(
    engine, monkeypatch, token, stage,
):
    owner, config = engine
    original = owner._save_state

    def crash(phase, **fields):
        original(phase, **fields)
        if fields.get("execution_stage") == stage:
            raise InjectedCrash()

    monkeypatch.setattr(owner, "_save_state", crash)
    with pytest.raises(InjectedCrash):
        run_test_iteration(owner)
    pending = dict(owner._state["pending_execution_report"])
    if token is None:
        pending.pop("resource_generation")
    else:
        pending["resource_generation"] = token
    with owner._lock():
        original("agent_running", pending_execution_report=pending)
    before = git(owner.repo, "rev-parse", "devlegate/control").stdout
    restarted = Devlegate(config)
    with pytest.raises(DevlegateError,
                       match="resource generation.*independent admission"):
        run_test_iteration(restarted)
    assert git(owner.repo, "rev-parse", "devlegate/control").stdout == before


def test_admission_crash_before_independent_intent_replays_only_absent_resources(
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
    def worker(_workspace, _prompt, **_kwargs):
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None,
        )

    monkeypatch.setattr(restarted._workers, "run", worker)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["execution_resource_binding"] == intent
    assert restarted.execution_resource_proof().local_state == "proved"


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


def test_post_worker_requires_independent_group_retirement(engine, monkeypatch):
    owner, config = engine
    original = owner._save_state

    def crash_after_retirement(phase, **fields):
        original(phase, **fields)
        if fields.get("execution_stage") == "post-worker":
            raise InjectedCrash()

    monkeypatch.setattr(owner, "_save_state", crash_after_retirement)
    with pytest.raises(InjectedCrash):
        run_test_iteration(owner)
    binding = owner._execution_generation_binding()
    binding.require_worker_retirement()
    retired = next(binding.directory.glob("worker-retired.*.json"))
    retired.unlink()
    restarted = Devlegate(config)
    monkeypatch.setattr(
        restarted._workers, "run",
        lambda *_args, **_kwargs: pytest.fail("unproven retirement launched worker"),
    )
    head = git(binding.manager.path, "rev-parse", "HEAD").stdout
    with pytest.raises(DevlegateError, match="group retirement is unproven"):
        run_test_iteration(restarted)
    assert git(binding.manager.path, "rev-parse", "HEAD").stdout == head


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


def test_native_conflict_abort_and_second_rewrite_keep_generation(
    tmp_path, monkeypatch
):
    owner, repo, _state, execution, _reconciliation, _first, second, _target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    generation = owner._state["execution_resource_binding"]["generation"]
    (repo / "first.txt").write_text("conflicting target\n")
    git(repo, "add", "first.txt")
    git(repo, "commit", "-m", "conflicting product")
    git(repo, "push", "origin", "HEAD:main")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="manual/advanced reconciliation"):
        owner.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == second
    assert not git(execution, "status", "--porcelain").stdout
    (repo / "first.txt").unlink()
    git(repo, "commit", "-am", "remove conflicting product file")
    git(repo, "push", "origin", "HEAD:main")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    restarted = Devlegate(owner.env_file)
    assert restarted.reconcile_update_base("T-1", target) == 0
    binding = restarted._execution_generation_binding()
    rewrites = [path for _, operation, path in binding._attempts()
                if operation == "rewrite"]
    assert len(rewrites) == 3  # conflict, acknowledged abort, second attempt
    assert binding.intent["generation"] == generation
    assert binding.require_local(clean=True).head == git(
        execution, "rev-parse", "HEAD"
    ).stdout.strip()
    assert git(execution, "rev-parse", "HEAD~2").stdout.strip() == target


def test_native_continue_after_receipted_conflict_and_restart(tmp_path, monkeypatch):
    owner, repo, _state, execution, _reconciliation, _first, _second, _target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    (repo / "first.txt").write_text("conflicting target\n")
    git(repo, "add", "first.txt")
    git(repo, "commit", "-m", "conflicting product")
    git(repo, "push", "origin", "HEAD:main")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    original = owner.reconciliation_git

    def interrupted(cwd, *args, **kwargs):
        result = original(cwd, *args, **kwargs)
        if args[0] == "rebase" and result.returncode:
            raise InjectedCrash()
        return result

    monkeypatch.setattr(owner, "reconciliation_git", interrupted)
    with pytest.raises(InjectedCrash):
        owner.reconcile_update_base("T-1", target)
    restarted = Devlegate(owner.env_file)
    (execution / "first.txt").write_text("first\n")
    git(execution, "add", "first.txt")
    git(execution, "config", "core.editor", "true")
    with restarted._lock():
        assert restarted.reconciliation_git(
            execution, "rebase", "--continue"
        ).returncode == 0
        workspace = restarted._execution_generation_binding().require_local(clean=True)
    assert workspace.head == git(execution, "rev-parse", "HEAD").stdout.strip()
    assert git(execution, "rev-parse", "HEAD~2").stdout.strip() == target
