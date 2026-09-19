# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import inspect

import pytest

from devlegate.cli import _execution_projection, _render_status_text
from devlegate.ipc_client import decode_status
from devlegate.output import render_grid, render_table
from devlegate.runtime import ExecutionPlan, GitObservation, StatusSnapshot


def test_status_renderer_requires_explicit_service_state():
    parameter = inspect.signature(_render_status_text).parameters["service_state"]

    assert parameter.default is inspect.Parameter.empty


def _execution_snapshot(phase, stage, *, ticket="LAB-1", execution_id="exec-1"):
    observation = GitObservation(
        "main", False, "a" * 40, "origin/main", "b" * 40, True, "fingerprint"
    )
    plan = ExecutionPlan(
        "run-worker", "test", ticket, "Title", "todo", True, observation, None
    )
    return StatusSnapshot(
        phase,
        ticket,
        True,
        observation,
        None,
        (('backlog', 0), ('todo', 0), ('review', 0), ('accepted', 0), ('done', 0)),
        (),
        (),
        (),
        (),
        None,
        plan,
        execution_stage=stage,
        execution_id=execution_id,
    )


@pytest.mark.parametrize(
    ("phase", "stage", "expected"),
    [
        ("idle", None, "idle"),
        ("agent_pending", "lifecycle", "preparing"),
        ("agent_running", "worker-launch", "starting"),
        ("agent_running", "post-worker", "finalizing"),
        ("agent_running", "worker-running", "recovery-required"),
    ],
)
def test_operator_execution_projection_is_conservative(phase, stage, expected):
    snapshot = _execution_snapshot(phase, stage)
    evidence = (
        {
            "ticket_id": "LAB-1",
            "execution_id": "exec-1",
            "stage": stage,
            "ownership": "current-service",
            **(
                {"identity_state": "matching-live"}
                if stage != "worker-running"
                else {}
            ),
        }
        if phase != "idle"
        else None
    )

    assert _execution_projection(snapshot, evidence)["state"] == expected


def test_operator_execution_projection_requires_exact_live_evidence():
    snapshot = _execution_snapshot("agent_running", "worker-running")
    evidence = {
        "ticket_id": "LAB-1",
        "execution_id": "exec-1",
        "stage": "worker-running",
        "ownership": "current-service",
        "identity_state": "matching-live",
    }

    assert _execution_projection(snapshot, evidence)["state"] == "running"
    assert _execution_projection(
        snapshot, {**evidence, "execution_id": "other"}
    )["state"] == "recovery-required"
    assert _execution_projection(
        snapshot, {**evidence, "ticket_id": "LAB-2"}
    )["state"] == "recovery-required"
    assert _execution_projection(
        snapshot, {**evidence, "ownership": "unknown"}
    )["state"] == "recovery-required"


def test_old_status_payload_without_live_evidence_fails_closed():
    snapshot = _execution_snapshot("agent_running", "worker-running")
    payload = snapshot.as_dict()
    payload["execution"].pop("stage")
    payload["execution"].pop("execution_id")

    decoded = decode_status(payload)

    assert _execution_projection(decoded, None)["state"] == "recovery-required"


def test_bound_title_does_not_depend_on_execution_plan():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_running", "worker-launch"),
        plan=dataclasses.replace(
            _execution_snapshot("agent_running", "worker-launch").plan,
            ticket_title="stale plan title",
        ),
        bound_ticket_title="canonical ticket title",
    )
    evidence = {
        "ticket_id": "LAB-1",
        "execution_id": "exec-1",
        "stage": "worker-launch",
        "ownership": "current-service",
    }

    assert _execution_projection(snapshot, evidence)["ticket_title"] == (
        "canonical ticket title"
    )


def test_idle_snapshot_suppresses_stale_execution_identity():
    snapshot = dataclasses.replace(
        _execution_snapshot("idle", "worker-running"), bound_ticket_id=None
    )

    payload = snapshot.as_dict()

    assert payload["execution"]["stage"] is None
    assert payload["execution"]["execution_id"] is None


def test_machine_eligible_excludes_bound_ticket():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_pending", "lifecycle"),
        runnable=(
            ("LAB-1", "bound"),
            ("LAB-2", "eligible"),
        ),
    )

    assert snapshot.as_dict()["tickets"]["eligible"] == [
        {"id": "LAB-2", "title": "eligible"}
    ]


def test_one_row_table_has_no_middle_separator():
    output = render_table("One", (("A", "B"),))

    assert output.splitlines() == [
        "One",
        "┌───┬───┐",
        "│ A │ B │",
        "└───┴───┘",
    ]


def test_multi_row_table_separates_rows():
    output = render_table("Many", (("A", "B"), ("C", "D")))

    assert output.splitlines() == [
        "Many",
        "┌───┬───┐",
        "│ A │ B │",
        "├───┼───┤",
        "│ C │ D │",
        "└───┴───┘",
    ]


def test_table_escapes_control_whitespace_in_cells():
    output = render_table("Safe", (("key\n", "value\r\t"),))

    assert "key\\n" in output
    assert "value\\r\\t" in output
    assert output.count("\n") == 3


def test_multi_column_grid_has_stable_row_separators():
    output = render_grid("Grid", ("Plane", "State"), (("Code", "clean"),))

    assert output.splitlines() == [
        "Grid",
        "┌───────┬───────┐",
        "│ Plane │ State │",
        "├───────┼───────┤",
        "│ Code  │ clean │",
        "└───────┴───────┘",
    ]
