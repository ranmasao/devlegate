# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import copy

import pytest

from devlegate.recovery_transactions import (
    persist_stalled_intent,
    retire_stalled_intent,
)


class Owner:
    def __init__(self):
        self.state = {
            "phase": "agent_running",
            "execution_stage": "worker-running",
            "execution_id": "attempt",
            "worker_identity": {"execution_id": "attempt", "containment_id": "group"},
        }
        self.events = []
        self.fail_save = False
        self.fail_retire = False

    def recovery_state(self):
        return copy.deepcopy(self.state)

    def recovery_save_state(self, phase, **values):
        if self.fail_save:
            raise OSError("state write failed")
        self.events.append(("save", copy.deepcopy(values)))
        self.state.update(phase=phase, **copy.deepcopy(values))

    def recovery_worker_identity(self, value, execution_id):
        return value if value.get("execution_id") == execution_id else None

    def recovery_retire_worker(self, identity, evidence):
        assert self.state["stalled_recovery"]["stage"] == "intent"
        self.events.append(("retire", identity, evidence))
        if self.fail_retire:
            raise OSError("retirement interrupted")


def test_stalled_intent_is_durable_before_retirement():
    owner = Owner()
    persist_stalled_intent(owner, launch_evidence={"containment": "group"})
    retire_stalled_intent(owner)
    assert [event[0] for event in owner.events] == ["save", "retire", "save"]
    assert owner.state["stalled_recovery"]["stage"] == "retired"
    assert owner.state["stalled_recovery"]["reason"] == "worker_stalled"


def test_failed_intent_write_never_authorizes_retirement():
    owner = Owner()
    owner.fail_save = True
    with pytest.raises(OSError):
        persist_stalled_intent(owner, launch_evidence={"containment": "group"})
    with pytest.raises(ValueError):
        retire_stalled_intent(owner)
    assert owner.events == []


def test_restart_replays_only_exact_persisted_binding():
    owner = Owner()
    persist_stalled_intent(owner, launch_evidence={"containment": "group"})
    owner.fail_retire = True
    with pytest.raises(OSError):
        retire_stalled_intent(owner)
    restarted = Owner()
    restarted.state = copy.deepcopy(owner.state)
    retire_stalled_intent(restarted)
    assert restarted.state["stalled_recovery"]["stage"] == "retired"
    with pytest.raises(ValueError):
        retire_stalled_intent(restarted)


def test_changed_execution_cannot_replay_old_intent():
    owner = Owner()
    persist_stalled_intent(owner, launch_evidence={"containment": "group"})
    owner.state["execution_id"] = "replacement"
    with pytest.raises(ValueError):
        retire_stalled_intent(owner)
    assert [event[0] for event in owner.events] == ["save"]


def test_retirement_save_failure_preserves_intent_for_reconciliation():
    owner = Owner()
    persist_stalled_intent(owner, launch_evidence={"containment": "group"})
    owner.fail_save = True
    with pytest.raises(OSError):
        retire_stalled_intent(owner)
    assert owner.state["stalled_recovery"]["stage"] == "intent"
    restarted = Owner()
    restarted.state = copy.deepcopy(owner.state)
    retire_stalled_intent(restarted)
    assert restarted.state["stalled_recovery"]["stage"] == "retired"
