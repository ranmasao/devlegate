# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import inspect

import pytest

from devlegate.cli import _execution_projection, _render_status_text
from devlegate.ipc_client import decode_status
from devlegate.output import render_grid, render_table
from devlegate.runtime import (
    BlockedReason,
    ExecutionPlan,
    GitObservation,
    StatusSnapshot,
)


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


def test_status_text_exposes_active_execution_selector():
    snapshot = _execution_snapshot("agent_running", "worker-launch")
    execution = _execution_projection(
        snapshot,
        {
            "ticket_id": "LAB-1",
            "execution_id": "exec-1",
            "stage": "worker-launch",
            "ownership": "current-service",
        },
    )

    text = _render_status_text(snapshot, "running", execution)

    assert "Execution: exec-1" in text


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


def test_running_service_without_live_evidence_is_unverified():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_running", None), execution_id=None
    )

    assert _execution_projection(snapshot, None, "running")["state"] == "unverified"


@pytest.mark.parametrize(
    "stage",
    ["worker-launch", "worker-running"],
)
def test_running_service_with_complete_execution_shape_requires_recovery_evidence(
    stage,
):
    snapshot = _execution_snapshot("agent_running", stage)

    assert _execution_projection(snapshot, None, "running")["state"] == (
        "recovery-required"
    )


def test_old_status_payload_without_live_evidence_is_unverified():
    snapshot = _execution_snapshot("agent_running", "worker-running")
    payload = snapshot.as_dict()
    payload["execution"].pop("stage")
    payload["execution"].pop("execution_id")

    decoded = decode_status(payload)

    assert _execution_projection(decoded, None, "running")["state"] == "unverified"


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


def test_current_execution_is_compact_and_wraps_long_title():
    title = "A very long current execution title " * 8
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_running", "worker-launch"),
        bound_ticket_title=title,
    )

    output = _render_status_text(
        snapshot,
        "running",
        _execution_projection(
            snapshot,
            {
                "ticket_id": "LAB-1",
                "execution_id": "exec-1",
                "stage": "worker-launch",
                "ownership": "current-service",
            },
            "running",
        ),
    )

    lines = output.splitlines()
    assert "Current: LAB-1  •  starting" in lines
    assert "Current execution" not in output
    assert max(map(len, lines)) <= 88
    assert sum(line.startswith("  ") for line in lines) > 1


def test_missing_title_and_recovery_coordinates_are_not_rendered_as_none():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_running", None),
        bound_ticket_title=None,
        execution_id=None,
    )

    output = _render_status_text(
        snapshot,
        "stopped",
        _execution_projection(snapshot, None, "stopped"),
    )

    assert "Current: LAB-1  •  recovery-required" in output
    assert "LAB-1 · None" not in output
    assert "stage=none" not in output.lower()
    assert "execution=none" not in output.lower()


def test_workflow_is_a_compact_summary_and_empty_eligible_is_omitted():
    snapshot = _execution_snapshot("idle", None)

    output = _render_status_text(
        snapshot, "stopped", _execution_projection(snapshot, None, "stopped")
    )

    assert (
        "Workflow  Backlog: 0  ·  Todo: 0  ·  Review: 0  ·  Accepted: 0  ·  Done: 0"
        in output
    )
    assert "Eligible" not in output


def test_non_empty_eligible_remains_visible():
    snapshot = dataclasses.replace(
        _execution_snapshot("idle", None), eligible=(("LAB-2", "Runnable"),)
    )

    output = _render_status_text(
        snapshot, "stopped", _execution_projection(snapshot, None, "stopped")
    )

    assert "Eligible" in output
    assert "LAB-2" in output


def test_machine_eligible_excludes_bound_ticket():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_pending", "lifecycle"),
        runnable=(
            ("LAB-1", "bound"),
            ("LAB-2", "eligible"),
        ),
        eligible=(("LAB-2", "eligible"),),
    )

    assert snapshot.as_dict()["tickets"]["eligible"] == [
        {"id": "LAB-2", "title": "eligible"}
    ]


def test_global_blocking_reason_has_no_fabricated_ticket_dependency():
    snapshot = dataclasses.replace(
        _execution_snapshot("idle", None),
        blocked=(
            ("LAB-2", "Blocked", BlockedReason("repository")),
        ),
    )

    payload = snapshot.as_dict()
    assert payload["tickets"]["blocked"][0]["reason"] == {
        "kind": "repository"
    }
    assert decode_status(payload).blocked == snapshot.blocked
    output = _render_status_text(
        snapshot, "stopped", _execution_projection(snapshot, None, "stopped")
    )
    assert "repository is not admissible" in output


def test_accepted_integration_is_visible_with_structured_blocking_reason():
    snapshot = dataclasses.replace(
        _execution_snapshot("idle", None),
        blocked=(
            (
                "LAB-127",
                "Dependent work",
                BlockedReason("integration-in-progress", ticket_id="LAB-126"),
            ),
        ),
        lifecycle_integration=("LAB-126", "in-progress"),
    )

    payload = snapshot.as_dict()
    output = _render_status_text(
        snapshot, "running", _execution_projection(snapshot, None)
    )

    assert payload["lifecycle"] == {
        "integration": {"ticket_id": "LAB-126", "state": "in-progress"}
    }
    assert payload["tickets"]["blocked"][0]["reason"] == {
        "kind": "integration-in-progress",
        "ticket_id": "LAB-126",
    }
    assert "Lifecycle" in output
    assert "in-progress LAB-126" in output
    assert "LAB-126 integration in progress" in output


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
