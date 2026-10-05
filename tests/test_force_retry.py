# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: I001
"""Regression coverage for forced retry admission and recovery."""

from __future__ import annotations

import pytest
from git_support import control_publisher
from runtime_helpers import run_test_iteration
from _control_support import (
    control_fixture,
    git,
    invoke,
    persist_agent_running,
    state_payload,
)

from devlegate.cli import DevlegateError
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.runtime import (
    OperatorCommand,
    ServiceEngine,
    _retry_request_fingerprint,
)
from devlegate.worker_egress import WorkerRunResult


def _force_command(request_id="force-request"):
    return OperatorCommand(
        request_id,
        "retry",
        _retry_request_fingerprint("T-1", True),
        "T-1",
        force=True,
    )


def test_force_retry_dogfood_preserves_progress_and_uses_current_ticket(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    workspace, control = persist_agent_running(engine, state, execution_id="E1")
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=engine._state["worker_identity"],
    )
    old_control = git(control, "rev-parse", "HEAD").stdout.strip()
    assert git(control, "rev-parse", "HEAD").stdout.strip() == old_control
    (workspace.path / "worker-progress.txt").write_text("preserve me\n")

    monkeypatch.setattr(engine._workers, "observe", lambda _identity: "absent")
    publisher = control_publisher(tmp_path)
    (publisher / "kanban" / "todo" / "T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Changed ticket"\n'
        "---\nchanged current work\n"
    )
    git(publisher, "add", "kanban/todo/T-1.md")
    git(publisher, "commit", "-m", "change current ticket")
    git(publisher, "push", "origin", "HEAD:devlegate/control")
    git(control, "reset", "--hard", old_control)
    assert git(control, "rev-parse", "HEAD").stdout.strip() == old_control

    command = _force_command()
    with engine._lock():
        engine._validate_operator_admission(command)
        engine._record_operator_admission(command)

    transition = engine._state["force_retry_provenance"]
    current_control = transition["current_control_head"]
    assert transition["old_execution_id"] == "E1"
    assert transition["admitted_control_head"] == old_control
    assert current_control != old_control
    assert transition["evidence_ref"].startswith(
        "refs/devlegate/recovery/force-retry/T-1/E1"
    )
    checkpoint = transition["checkpoint"]
    assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == checkpoint
    assert (workspace.path / "worker-progress.txt").read_text() == "preserve me\n"
    assert engine.submit_retry("T-1", force=True, request_id="force-request") == {
        "accepted": True,
        "ticket_id": "T-1",
    }

    prompts = []
    execution_starts = []
    execution_ids = []

    def worker(current_workspace, prompt, **_kwargs):
        prompts.append((current_workspace, prompt))
        execution_starts.append(engine._state.get("execution_start_head"))
        execution_ids.append(engine._state.get("execution_id"))
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    assert len(prompts) == 1
    assert prompts[0][0].path == workspace.path
    assert "Continue the existing implementation" in prompts[0][1]
    assert "changed current work" in prompts[0][1]
    assert execution_ids and execution_ids[0] != "E1"
    assert engine._state["failed_executions"]["T-1"]["execution_id"] != "E1"
    assert execution_starts == [checkpoint]

    evidence = git(
        engine.repo,
        "show",
        "refs/devlegate/recovery/force-retry/T-1/E1:worker-progress.txt",
    ).stdout
    assert evidence == "preserve me\n"


@pytest.mark.parametrize("observation", ["matching-live", "indeterminate"])
def test_force_retry_rejects_non_absent_worker_without_mutation(
    tmp_path, monkeypatch, observation
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    persist_agent_running(engine, state, execution_id="E1")
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=engine._state["worker_identity"],
    )
    before = state_payload(state)
    monkeypatch.setattr(engine._workers, "observe", lambda _identity: observation)

    with pytest.raises(DevlegateError, match=observation):
        engine._validate_force_retry_admission("T-1")
    assert state_payload(state) == before


def _prepare_changed_descendant(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    workspace, control = persist_agent_running(engine, state, execution_id="E1")
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=engine._state["worker_identity"],
    )
    old_control = git(control, "rev-parse", "HEAD").stdout.strip()
    publisher = control_publisher(tmp_path)
    (publisher / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Changed ticket"\n'
        "---\nchanged current work\n"
    )
    git(publisher, "add", "kanban/todo/T-1.md")
    git(publisher, "commit", "-m", "change current ticket")
    git(publisher, "push", "origin", "HEAD:devlegate/control")
    monkeypatch.setattr(engine._workers, "observe", lambda _identity: "absent")
    return engine, workspace, control, old_control


def test_force_retry_rejects_unchanged_descendant_without_mutation(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    persist_agent_running(engine, state, execution_id="E1")
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=engine._state["worker_identity"],
    )
    publisher = control_publisher(tmp_path)
    (publisher / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Ticket"\n---\nwork\n'
    )
    git(publisher, "add", "kanban/todo/T-1.md")
    git(publisher, "commit", "-m", "advance control only")
    git(publisher, "push", "origin", "HEAD:devlegate/control")
    monkeypatch.setattr(engine._workers, "observe", lambda _identity: "absent")
    before = state_payload(state)
    with pytest.raises(DevlegateError, match="unchanged"):
        engine._validate_force_retry_admission("T-1")
    assert state_payload(state) == before


def test_force_retry_rejects_product_movement_without_mutation(tmp_path, monkeypatch):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    before = dict(engine._state)
    monkeypatch.setattr(
        engine,
        "_observe_product_generation",
        lambda _head: {"stable": False},
    )
    with pytest.raises(DevlegateError, match="product generation changed"):
        engine._validate_force_retry_admission("T-1")
    assert engine._state == before


def test_force_retry_rejects_divergent_control_without_mutation(tmp_path, monkeypatch):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    before = dict(engine._state)
    monkeypatch.setattr(
        engine,
        "_sync_control",
        lambda: ("f" * 40, "f" * 40),
    )
    with pytest.raises(DevlegateError, match="diverged"):
        engine._validate_force_retry_admission("T-1")
    assert engine._state == before


@pytest.mark.parametrize("field", ["execution_branch", "execution_path"])
def test_force_retry_rejects_unsafe_workspace_without_mutation(
    tmp_path, monkeypatch, field
):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    engine._state[field] = "/wrong" if field == "execution_path" else "wrong"
    before = dict(engine._state)
    with pytest.raises(DevlegateError, match="workspace topology"):
        engine._validate_force_retry_admission("T-1")
    assert engine._state == before


def test_force_retry_rejects_submodule_integrity_failure_without_mutation(
    tmp_path, monkeypatch
):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    before = dict(engine._state)

    def fail_submodules(_manager, _workspace):
        raise ExecutionWorkspaceError("submodule integrity is ambiguous")

    monkeypatch.setattr(
        "devlegate.runtime.ExecutionWorkspaceManager.verify_submodules",
        fail_submodules,
    )
    with pytest.raises(DevlegateError, match="submodule integrity"):
        engine._validate_force_retry_admission("T-1")
    assert engine._state == before


def test_force_retry_save_failure_leaves_old_execution_retryable(tmp_path, monkeypatch):
    engine, workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    original_save = engine._save_state

    def fail_save(*args, **kwargs):
        raise DevlegateError("injected state save failure")

    monkeypatch.setattr(engine, "_save_state", fail_save)
    with pytest.raises(DevlegateError, match="state save failure"):
        engine._record_operator_admission(_force_command())
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_id"] == "E1"
    assert git(workspace.path, "rev-parse", "HEAD").returncode == 0
    monkeypatch.setattr(engine, "_save_state", original_save)
    assert engine._state["execution_id"] == "E1"


def test_force_retry_reissues_after_precommit_save_failure(tmp_path, monkeypatch):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    command = _force_command("retry-again")
    engine._validate_operator_admission(command)
    original_save = engine._save_state
    calls = 0

    def fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise DevlegateError("injected state save failure")
        return original_save(*args, **kwargs)

    monkeypatch.setattr(engine, "_save_state", fail_once)
    with pytest.raises(DevlegateError, match="state save failure"):
        engine._record_operator_admission(command)
    assert engine._state["execution_id"] == "E1"
    engine._record_operator_admission(command)
    assert engine._state["phase"] == "idle"
    assert engine._state["force_retry_authorization"]["request_id"] == "retry-again"


def test_force_retry_replays_after_restart_and_creates_one_fresh_execution(
    tmp_path, monkeypatch
):
    engine, workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    engine._validate_operator_admission(_force_command())
    engine._record_operator_admission(_force_command())
    restarted = ServiceEngine(engine.env_file)
    prompts = []

    def worker(current_workspace, prompt, **_kwargs):
        prompts.append((current_workspace, prompt))
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(restarted._workers, "run", worker)
    assert run_test_iteration(restarted) == 1
    assert prompts[0][0].path == workspace.path
    assert "Continue the existing implementation" in prompts[0][1]
    assert restarted._state["execution_id"] != "E1"
    assert restarted._state["execution_id"]
    assert restarted._state["force_retry_authorization"] is None


def test_force_retry_duplicate_request_returns_same_receipt_without_second_checkpoint(
    tmp_path, monkeypatch
):
    engine, _workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    command = _force_command()
    engine._validate_operator_admission(command)
    engine._record_operator_admission(command)
    first = engine.submit_retry("T-1", force=True, request_id=command.request_id)
    state_after = state_payload(engine.state_dir)
    second = engine.submit_retry("T-1", force=True, request_id=command.request_id)
    assert first == second == {"accepted": True, "ticket_id": "T-1"}
    assert state_payload(engine.state_dir) == state_after


def test_force_retry_authorization_is_consumed_once_after_restart(
    tmp_path, monkeypatch
):
    engine, workspace, _control, _old_control = _prepare_changed_descendant(
        tmp_path, monkeypatch
    )
    command = _force_command("restart-once")
    engine._validate_operator_admission(command)
    engine._record_operator_admission(command)
    restarted = ServiceEngine(engine.env_file)
    seen = []

    def worker(current_workspace, _prompt, **kwargs):
        seen.append((kwargs["execution_id"], current_workspace.path))
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(restarted._workers, "run", worker)
    assert run_test_iteration(restarted) == 1
    first_execution = seen[0][0]
    assert first_execution != "E1"
    assert seen[0][1] == workspace.path
    assert restarted._state["force_retry_authorization"] is None

    again = ServiceEngine(restarted.env_file)
    monkeypatch.setattr(
        again._workers,
        "run",
        lambda *_args, **_kwargs: pytest.fail("forced authorization replayed"),
    )
    assert again._state["execution_id"] == first_execution
    assert again._state["phase"] == "idle"
