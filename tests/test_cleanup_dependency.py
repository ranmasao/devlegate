# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest

from devlegate.cleanup_dependency import (
    accepted_integration_dependency_reason,
    snapshot_integration,
)
from devlegate.runtime import ServiceEngine, WorkflowBlockedError

SHA = "a" * 40
OTHER = "b" * 40
INVALID = "accepted integration dependency identity is invalid"
CONTRADICTS = "accepted integration dependency contradicts accepted execution"
BOUND = "accepted execution has a pending integration dependency"


@pytest.mark.parametrize(
    ("pending", "reason"),
    [
        (None, None),
        ({"ticket_id": "T-2", "checkpoint": OTHER, "control_head": SHA}, None),
        ({"ticket_id": "T-1", "checkpoint": SHA, "control_head": SHA}, BOUND),
        ({"ticket_id": "T-1", "checkpoint": OTHER, "control_head": SHA}, CONTRADICTS),
        ({"ticket_id": "T-2", "checkpoint": "bad", "control_head": SHA}, INVALID),
        ([], INVALID),
        ({"ticket_id": "T-1", "checkpoint": SHA}, INVALID),
        (
            {"ticket_id": "T-1", "checkpoint": SHA, "control_head": SHA, "extra": 1},
            INVALID,
        ),
        ({"ticket_id": [], "checkpoint": SHA, "control_head": SHA}, INVALID),
        ({"ticket_id": "", "checkpoint": SHA, "control_head": SHA}, INVALID),
        ({"ticket_id": "T-1", "checkpoint": SHA, "control_head": 1}, INVALID),
        ({"ticket_id": "T-1", "checkpoint": SHA, "control_head": SHA,
          "control_predecessor": None}, INVALID),
        ({"ticket_id": "T-1", "checkpoint": SHA, "control_head": SHA,
          "control_predecessor": OTHER, "product_transition": []}, BOUND),
    ],
)
def test_integration_gate_preserves_original_decisions(pending, reason):
    report = SimpleNamespace(
        ticket_id="T-1", execution_id="exec-1", workspace_head=SHA,
        execution_branch="devlegate/work/T-1", execution_path="/work/T-1",
    )
    # The gate has only state available: any added state or Git I/O fails here.
    owner = SimpleNamespace(_state={"accepted_integration": pending})
    snapshot = snapshot_integration(pending)
    assert accepted_integration_dependency_reason("T-1", SHA, snapshot) == reason
    if reason is None:
        ServiceEngine._assert_cleanup_has_no_execution_dependency(owner, report)
    else:
        with pytest.raises(WorkflowBlockedError) as error:
            ServiceEngine._assert_cleanup_has_no_execution_dependency(owner, report)
        assert str(error.value) == reason


def test_snapshot_does_not_follow_mutable_state():
    pending = {"ticket_id": "T-1", "checkpoint": SHA, "control_head": SHA,
               "product_transition": {"nested": []}}
    snapshot = snapshot_integration(pending)
    pending["ticket_id"] = "T-2"
    pending["product_transition"]["nested"].append("changed")
    assert accepted_integration_dependency_reason("T-1", SHA, snapshot) == BOUND
    with pytest.raises(FrozenInstanceError):
        snapshot.ticket_id = "T-2"


@pytest.mark.parametrize(
    ("state", "reason"),
    [
        ({"worker_identity": []}, "worker dependency identity is invalid"),
        ({"phase": "agent_running"}, "active execution ticket identity is invalid"),
        ({"reconciliation": []}, "reconciliation dependency identity is invalid"),
        ({"automatic_retry": []}, "automatic retry dependency identity is invalid"),
    ],
)
def test_gate_checks_earlier_dependencies_before_integration(state, reason):
    state["accepted_integration"] = []
    owner = SimpleNamespace(_state=state)
    report = SimpleNamespace(
        ticket_id="T-1", execution_id="exec-1", workspace_head=SHA,
        execution_branch="devlegate/work/T-1", execution_path="/work/T-1",
    )
    with pytest.raises(WorkflowBlockedError) as error:
        ServiceEngine._assert_cleanup_has_no_execution_dependency(owner, report)
    assert str(error.value) == reason
