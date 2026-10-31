# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import inspect
import json

import pytest

from devlegate.cli import _execution_projection, _render_check_text, _render_status_text
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
        (("backlog", 0), ("todo", 0), ("review", 0), ("accepted", 0), ("done", 0)),
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
                {"identity_state": "matching-live"} if stage != "worker-running" else {}
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

    assert "Active executions" in text
    assert "Execution" in text
    assert "exec-1" in text


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
    assert (
        _execution_projection(snapshot, {**evidence, "execution_id": "other"})["state"]
        == "recovery-required"
    )
    assert (
        _execution_projection(snapshot, {**evidence, "ticket_id": "LAB-2"})["state"]
        == "recovery-required"
    )
    assert (
        _execution_projection(snapshot, {**evidence, "ownership": "unknown"})["state"]
        == "recovery-required"
    )


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


def test_unverified_execution_remains_diagnosable_in_active_grid():
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_running", None), execution_id=None
    )

    output = _render_status_text(
        snapshot,
        "running",
        _execution_projection(snapshot, None, "running"),
    )

    assert "Active executions" in output
    assert "unverified" in output
    assert "agent_running" in output
    assert "Live ownership evidence is unavailable." in output


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


def test_active_execution_is_compact_and_wraps_long_title(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "88")
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
    assert "Active executions" in lines
    assert "Current:" not in output
    assert "LAB-1" in output
    assert "starting" in output
    assert "worker-launch" in output
    assert "exec-1" in output
    assert max(map(len, lines)) <= 88
    assert output.count("LAB-1") == 1


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

    assert "Active executions" in output
    assert "recovery-required" in output
    assert "agent_running" in output
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
    assert "Active executions" not in output


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
        blocked=(("LAB-2", "Blocked", BlockedReason("repository")),),
    )

    payload = snapshot.as_dict()
    assert payload["tickets"]["blocked"][0]["reason"] == {"kind": "repository"}
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

    assert "key" in output
    assert "value\\r\\t" in output
    assert output.count("\n") == 4


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


def test_grid_reflows_logical_rows_and_tracks_terminal_width(monkeypatch):
    from devlegate.output import _cell_width

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "40")
    rows = [("TASK-123", "wide text with several words and more words")]
    narrow = render_grid("Eligible", ("Ticket", "Title"), rows)
    assert narrow.count("├") == 1
    assert all(_cell_width(line) <= 40 for line in narrow.splitlines())
    assert "TASK-123" in narrow
    monkeypatch.setenv("COLUMNS", "100")
    wide = render_grid("Eligible", ("Ticket", "Title"), rows)
    assert len(wide.splitlines()) < len(narrow.splitlines())
    assert rows[0][1] in wide


def test_pipe_keeps_full_values_and_embedded_lines(monkeypatch):
    monkeypatch.setattr("sys.stdout.isatty", lambda: False)
    monkeypatch.setenv("COLUMNS", "10")
    value = "long words " * 10 + "/a/very/long/path"
    output = render_table("Next runnable", [("Title", value), ("ID", "a\nb")])
    assert value in output
    assert output.count("├") == 1
    assert "a" in output and "b" in output


def test_unicode_controls_and_opaque_overflow(monkeypatch):
    from devlegate.output import _cell_width

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "16")
    token = "0123456789abcdef0123456789abcdef"
    output = render_grid(
        "Grid", ("Ticket", "Title"), [(token, "e\u0301 \u754c\x1b[31m")]
    )
    assert token in output
    assert "\x1b" not in output
    lines = output.splitlines()[1:]
    assert len({_cell_width(line) for line in lines}) == 1


def test_joint_wrap_and_priority(monkeypatch):
    from devlegate.output import ColumnPolicy

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "36")
    value = "one two three four five six"
    output = render_grid(
        "Blocked",
        ("Title", "Reason"),
        [(value, value)],
        policies=[ColumnPolicy(True, 8, 5), ColumnPolicy(True, 8, 1)],
    )
    lines = output.splitlines()
    # Both columns fit in two physical lines; shrinking one to its minimum
    # instead requires at least three. The more protected first column wins ties.
    assert len(lines) == 7
    widths = [len(part) for part in lines[1][1:-1].split("┬")]
    assert widths[0] >= widths[1]


def test_many_column_grid_keeps_frame_and_values(monkeypatch):
    from devlegate.output import ColumnPolicy, _cell_width

    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "80")
    headers = [f"C{i}" for i in range(8)]
    row = ["one two three" for _ in headers]
    output = render_grid(
        "Grid", headers, [row], policies=[ColumnPolicy(True, 3)] * len(headers)
    )
    lines = output.splitlines()[1:]
    assert len({_cell_width(line) for line in lines}) == 1
    assert _cell_width(lines[0]) <= 80
    assert output.count("one") == 8
    assert output.count("three") == 8


@pytest.mark.parametrize("tty", [False, True])
@pytest.mark.parametrize(
    ("section", "row_count"),
    [
        ("Lifecycle", 1),
        ("Reconciliation", 3),
        ("Not ready.", 1),
        ("Repository diagnostics:", 6),
        ("Dirty working tree details:", 1),
        ("Dirty summary:", 1),
        ("Ticket storage:", 2),
        ("Next runnable:", 2),
        ("Control diagnostics:", 2),
    ],
)
def test_cli_key_value_sections_share_logical_row_layout(
    monkeypatch, tty, section, row_count
):
    from devlegate.output import _cell_width

    monkeypatch.setattr("sys.stdout.isatty", lambda: tty)
    monkeypatch.setenv("COLUMNS", "56")
    path = "/repository/opaque-path-with-no-spaces"
    title = "A long runnable title with many separate words for wrapping"
    snapshot = dataclasses.replace(
        _execution_snapshot("idle", None),
        lifecycle_integration=("LAB-126", "in-progress"),
        reconciliation={
            "status": "pending",
            "ticket_id": "LAB-1",
            "original_base": "a" * 40,
            "product_observation": "several words explaining the product observation",
        },
    )
    result = {
        "ready": False,
        "checks": [],
        "local_head": "a" * 40,
        "known_remote_head": "b" * 40,
        "ahead_behind": "0/0",
        "todo_files": 1,
        "todo_fingerprint": "fingerprint",
        "work_generation_differs": False,
        "dirty_files": [path],
        "dirty_summary": {"modified": 1},
        "ticket_counts": {"todo": 1},
        "runnable_tickets": 1,
        "next_runnable": {"id": "LAB-1", "title": title},
        "control_head": "c" * 40,
        "remote_note": "origin/long-opaque-ref",
    }
    text = _render_status_text(snapshot, "stopped", {"state": "idle"})
    text += "\n" + _render_check_text(result)
    lines = text.splitlines()
    start = lines.index(section) + 1
    end = next(i for i in range(start, len(lines)) if lines[i].startswith("└"))
    frame = lines[start : end + 1]
    assert sum(line.startswith("├") for line in frame) == row_count - 1
    assert len({_cell_width(line) for line in frame}) == 1
    if section == "Next runnable:":
        value_lines = [
            line.split("│")[2].strip() for line in frame if line.startswith("│")
        ]
        assert " ".join(value_lines[1:]).split() == title.split()
        assert (len(value_lines) > 2) == tty
    if section == "Dirty working tree details:":
        assert path in "\n".join(frame)
    if section == "Reconciliation":
        assert "a" * 40 in "\n".join(frame)


def test_unicode_width_policy_and_minimum_overflow(monkeypatch):
    from devlegate.output import _cell_width, _display

    assert _cell_width("e\u0301") == 1
    assert _cell_width("\u754c") == 2
    # The joiner is escaped before measurement, not treated as a grapheme.
    assert _display("\U0001f469\u200d\U0001f4bb") == "\U0001f469\\u200d\U0001f4bb"
    assert _display("\x1b[31m\t\r") == "\\x1b[31m\\t\\r"
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "8")
    identity = "refs/heads/a-very-long-identity"
    output = render_grid("Grid", ("ID", "Title"), [(identity, "short words")])
    frame = output.splitlines()[1:]
    assert identity in output
    assert _cell_width(frame[0]) > 8
    assert len({_cell_width(line) for line in frame}) == 1


@pytest.mark.parametrize("output_format", ["json", "yaml"])
def test_machine_status_round_trip_is_independent_of_table_layout(
    monkeypatch, output_format
):
    from devlegate._vendor import nanoyaml
    from devlegate.output import render_machine

    title = "Long title with words and /opaque/path/that/must/remain/intact"
    snapshot = dataclasses.replace(
        _execution_snapshot("agent_pending", "lifecycle"),
        bound_ticket_title=title,
        eligible=(("LAB-2", title),),
        blocked=(("LAB-3", title, BlockedReason("repository")),),
    )
    payload = snapshot.as_dict()
    monkeypatch.setattr("sys.stdout.isatty", lambda: True)
    monkeypatch.setenv("COLUMNS", "20")
    _render_status_text(snapshot, "running", _execution_projection(snapshot, None))
    serialized = render_machine(payload, output_format)
    if output_format == "json":
        decoded = json.loads(serialized)
    else:
        decoded = nanoyaml.loads(serialized)
    assert decoded == payload
    assert decoded["tickets"]["eligible"] == [{"id": "LAB-2", "title": title}]
    assert decoded["tickets"]["blocked"][0]["reason"] == {"kind": "repository"}
    assert decode_status(decoded).as_dict() == payload
