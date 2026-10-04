# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Regression coverage for forced retry admission and recovery."""

from __future__ import annotations

import pytest
from git_support import control_publisher
from runtime_helpers import run_test_iteration
from test_control_plane import (
    control_fixture,
    git,
    invoke,
    persist_agent_running,
    state_payload,
)

from devlegate.cli import DevlegateError
from devlegate.runtime import OperatorCommand, ServiceEngine, _retry_request_fingerprint
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

    def worker(current_workspace, prompt, **_kwargs):
        prompts.append((current_workspace, prompt))
        execution_starts.append(engine._state.get("execution_start_head"))
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    assert len(prompts) == 1
    assert prompts[0][0].path == workspace.path
    assert "Continue the existing implementation" in prompts[0][1]
    assert "changed current work" in prompts[0][1]
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
