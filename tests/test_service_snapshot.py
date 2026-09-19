# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import threading

import pytest
from test_control_plane import control_fixture, git, invoke

from devlegate.runtime import ServiceSnapshot
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerRunResult


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), state


def _persist_active_execution(
    engine, phase, stage, execution_id="execution-1", worker_identity=None
):
    engine._save_state(
        phase,
        local_head=engine._state.get("local_head", "unknown"),
        remote_head=engine._state.get("remote_head", "unknown"),
        changed_paths="",
        control_head=engine._state.get("control_head", "unknown"),
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=engine._state.get("local_head", "unknown"),
        execution_control_head=engine._state.get("control_head", "unknown"),
        execution_branch="devlegate/work/T-1",
        execution_path=str(engine.execution_worktree_root / "work" / "T-1"),
        execution_id=execution_id,
        execution_remote_head=None,
        execution_stage=stage,
        worker_identity=worker_identity,
    )


def test_service_snapshot_is_deeply_immutable_and_replaced(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    first = engine.service_snapshot()

    with pytest.raises(AttributeError):
        first.counts.append(("todo", 1))  # type: ignore[attr-defined]
    with pytest.raises(dataclasses.FrozenInstanceError):
        first.lifecycle = "worker"  # type: ignore[misc]

    engine._publish_service_snapshot(lifecycle="ready", blocked_reason="blocked")
    second = engine.service_snapshot()

    assert isinstance(first, ServiceSnapshot)
    assert first is not second
    assert first.lifecycle == "initialized"
    assert first.blocked_reason is None
    assert second.lifecycle == "ready"
    assert second.blocked_reason == "blocked"


def test_service_snapshot_read_performs_no_canonical_io(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    engine.status_view()
    expected = engine.service_snapshot()

    monkeypatch.setattr(engine._runtime_store, "observe", lambda: pytest.fail("SQLite"))
    monkeypatch.setattr(
        "devlegate.runtime._git",
        lambda *_args, **_kwargs: pytest.fail("Git"),
    )
    monkeypatch.setattr(engine, "_ticket_store", lambda: pytest.fail("workflow"))

    assert engine.service_snapshot() is expected


def test_service_snapshot_read_does_not_acquire_project_lock(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    with engine._lock():
        snapshot = engine.service_snapshot()
    assert snapshot.lifecycle == "initialized"


def test_long_worker_publishes_live_snapshot_before_blocking(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    worker_entered = threading.Event()
    worker_release = threading.Event()
    stop_event = threading.Event()
    result = []

    def worker(_workspace, _prompt):
        worker_entered.set()
        durable = engine._runtime_store.load()
        assert durable["phase"] == "agent_running"
        assert durable["execution_start_head"]
        assert worker_release.wait(10)
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event))
    )
    thread.start()
    assert worker_entered.wait(10)

    snapshot = engine.service_snapshot()
    assert snapshot.lifecycle == "worker"
    assert snapshot.phase == "agent_running"
    assert snapshot.worker_running is True
    assert snapshot.selected_ticket_id == "T-1"
    assert snapshot.execution_id

    worker_release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert result == [0]
    assert engine.service_snapshot().worker_running is False


def test_worker_running_clears_when_worker_raises(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)

    def worker(_workspace, _prompt):
        raise RuntimeError("worker exploded")

    monkeypatch.setattr(engine, "_run_worker", worker)
    with pytest.raises(RuntimeError, match="worker exploded"):
        engine.run_once()

    snapshot = engine.service_snapshot()
    assert snapshot.worker_running is False
    assert snapshot.phase == "agent_running"


def test_stranded_agent_running_is_not_reported_as_live(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    engine._save_state(
        "agent_running",
        local_head=engine._state.get("local_head", "unknown"),
        remote_head=engine._state.get("remote_head", "unknown"),
        changed_paths="",
        control_head=engine._state.get("control_head", "unknown"),
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=engine._state.get("local_head", "unknown"),
        execution_control_head=engine._state.get("control_head", "unknown"),
        execution_branch="devlegate/work/T-1",
        execution_path=str(engine.execution_worktree_root / "work" / "T-1"),
        execution_id="stranded-execution",
        execution_remote_head=None,
    )
    restarted = ServiceEngine(engine.env_file)

    snapshot = restarted.service_snapshot()
    assert snapshot.phase == "agent_running"
    assert snapshot.worker_running is False
    assert snapshot.execution_id == "stranded-execution"


@pytest.mark.parametrize(
    ("phase", "stage"),
    [
        ("agent_pending", "lifecycle"),
        ("agent_running", "worker-launch"),
        ("agent_running", "post-worker"),
    ],
)
def test_restarted_active_execution_has_no_current_service_evidence(
    tmp_path, monkeypatch, phase, stage
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    _persist_active_execution(engine, phase, stage)

    restarted = ServiceEngine(engine.env_file)

    assert restarted._owned_execution_id is None
    assert restarted.live_execution_evidence() is None


@pytest.mark.parametrize(
    ("phase", "stage"),
    [
        ("agent_pending", "lifecycle"),
        ("agent_running", "worker-launch"),
        ("agent_running", "post-worker"),
    ],
)
def test_current_service_execution_ownership_produces_evidence(
    tmp_path, monkeypatch, phase, stage
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    _persist_active_execution(engine, phase, stage)
    engine._owned_execution_id = "execution-1"

    assert engine.live_execution_evidence() == {
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "stage": stage,
        "ownership": "current-service",
    }


def test_execution_ownership_evidence_rejects_wrong_process_execution(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    _persist_active_execution(engine, "agent_running", "post-worker")
    engine._owned_execution_id = "different-execution"

    assert engine.live_execution_evidence() is None


def test_worker_evidence_still_requires_matching_live_identity(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    identity = {
        "execution_id": "execution-1",
        "pid": 1,
        "pgid": 1,
        "sid": 1,
        "boot_id": "test-boot",
        "start_time": 0,
    }
    _persist_active_execution(
        engine, "agent_running", "worker-running", worker_identity=identity
    )
    engine._owned_execution_id = "execution-1"
    engine._worker_execution_id = "execution-1"
    engine._worker_identity_handler = lambda _identity: None

    monkeypatch.setattr(
        "devlegate.runtime.observe_worker_identity", lambda _identity: "matching-live"
    )

    assert engine.live_execution_evidence() == {
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "stage": "worker-running",
        "ownership": "current-service",
        "identity_state": "matching-live",
    }

    monkeypatch.setattr(
        "devlegate.runtime.observe_worker_identity", lambda _identity: "not-live"
    )
    assert engine.live_execution_evidence() is None


def test_accepted_integration_takes_plan_and_status_precedence(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    control = next((engine.state_dir / "worktrees").glob("*/control"))
    accepted = control / "kanban/todo/T-1.md"
    accepted.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(
        control,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    checkpoint = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "idle",
        accepted_integration={
            "ticket_id": "T-1",
            "checkpoint": checkpoint,
            "control_head": control_head,
        },
    )

    snapshot = engine.status_view()

    assert snapshot.lifecycle_integration == ("T-1", "in-progress")
    assert snapshot.plan.action == "none"
    assert "accepted integration recovery in progress" in snapshot.plan.reason
    assert engine.service_snapshot().lifecycle == "blocked"
    assert engine.service_snapshot().blocked_reason == snapshot.plan.reason


def test_live_superseding_execution_hides_only_its_historical_failure(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    _persist_active_execution(engine, "agent_running", "worker-launch")
    metadata = {
        "execution_id": "old-execution",
        "product_head": "old-product",
        "remote_head": "old-remote",
        "control_head": "old-control",
        "todo_fingerprint": "old-todo",
        "reason": "old failure",
        "report_unavailable": True,
    }
    engine._save_state(
        "agent_running",
        failed_executions={"T-1": metadata, "T-99": metadata},
    )
    engine._owned_execution_id = "execution-1"

    active = engine.status_view()

    assert [failure.ticket_id for failure in active.failed_executions] == ["T-99"]
    assert "T-1" not in active.as_dict()["failed_executions"]

    engine._owned_execution_id = None
    ended = engine.status_view()

    assert {failure.ticket_id for failure in ended.failed_executions} == {"T-1", "T-99"}


def test_blocked_reason_is_published_and_cleared(tmp_path, monkeypatch):
    engine, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").write_text("invalid workflow\n")

    blocked = engine.status_view()
    snapshot = engine.service_snapshot()
    assert blocked.plan.action == "blocked"
    assert snapshot.lifecycle == "blocked"
    assert snapshot.blocked_reason
    assert snapshot.worker_running is False

    (control / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Control ticket"\n---\nwork\n'
    )
    engine.status_view()
    assert engine.service_snapshot().blocked_reason is None


def test_service_snapshot_keeps_last_known_coordinates(tmp_path, monkeypatch):
    engine, _state = make_engine(tmp_path, monkeypatch)
    engine.status_view()
    expected = engine.service_snapshot()

    monkeypatch.setattr(
        engine,
        "_status_git_observation",
        lambda: pytest.fail("unexpected fresh observation"),
    )
    observed = engine.service_snapshot()
    assert observed.product == expected.product
    assert observed.control == expected.control
