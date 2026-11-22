# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import os
import signal
import subprocess
import sys
from types import SimpleNamespace

import pytest

from devlegate.stalled_watchdog import poll_stalled
from devlegate.worker_activity import WorkerActivity
from devlegate.worker_supervisor import _run_opencode


@pytest.mark.parametrize("uncertain", [False, True])
def test_failed_intent_at_supervisor_boundary_is_failure_stop(monkeypatch, uncertain):
    monkeypatch.setattr(
        "devlegate.stalled_watchdog.observe_worker_identity",
        lambda identity: "matching-live",
    )

    class Containment:
        identity = "group"

        def spawn(self, command, **kwargs):
            return subprocess.Popen(command, **kwargs)

        def has_other_processes(self, pid):
            return False

        def request_graceful(self, pid):
            pass

        def force_terminate(self):
            pass

        def wait_empty(self, timeout):
            return True

        def destroy(self):
            pass

    class Owner:
        def __init__(self):
            self.state = {"phase": "agent_running", "execution_stage": "worker-running",
                          "execution_id": "attempt"}
            self._workers = SimpleNamespace(
                analysis_launch_evidence=lambda _: {"containment": "group"}
            )
            self.retired = False
            self.uncertain = False

        def recovery_state(self):
            return self.state

        def recovery_worker_identity(self, value, execution_id):
            return value

        def recovery_save_state(self, phase, **values):
            self.uncertain = uncertain
            raise OSError("injected state write failure")

        def recovery_retire_worker(self, identity, evidence):
            self.retired = True

    owner = Owner()
    activity = WorkerActivity(1, clock=lambda: 100)
    # Advance the deterministic clock past the recovery threshold.
    activity._clock = lambda: 200
    interruptions = []

    def identity_handler(identity):
        owner.state["worker_identity"] = identity

    # The live transaction validates the persisted identity's dictionary shape.
    def identity(value, execution_id):
        if isinstance(value, dict):
            return SimpleNamespace(**value)
        return value

    owner.recovery_worker_identity = identity

    def record(identity):
        identity_handler({"pid": identity.pid, "containment_id": "group"})

    result = _run_opencode(
        [sys.executable, "-c", "import time; time.sleep(60)"], "prompt",
        execution_id="attempt", worker_identity_handler=record,
        interruption_handler=interruptions.append, containment=Containment(),
        watchdog_poll=lambda process, containment: poll_stalled(
            owner, "attempt", activity, 10, process, containment
        ), show_worker_output=False,
    )
    assert not owner.retired
    assert owner.uncertain is uncertain
    assert result.interruption_kind == "operator_abort"
    assert interruptions == ["operator_abort"]
    assert result.transport_error == "watchdog decision failed; recovery not authorized"


@pytest.mark.parametrize("membership", [True, None])
def test_unproven_or_active_descendant_suppresses_decision(monkeypatch, membership):
    monkeypatch.setattr(
        "devlegate.stalled_watchdog.observe_worker_identity", lambda _: "matching-live"
    )
    identity = SimpleNamespace(pid=123, containment_id="group")
    owner = SimpleNamespace(
        recovery_state=lambda: {
            "phase": "agent_running", "execution_stage": "worker-running",
            "execution_id": "attempt", "worker_identity": {},
        },
        recovery_worker_identity=lambda value, execution_id: identity,
    )
    activity = WorkerActivity(1, clock=lambda: 0)
    activity._clock = lambda: 20
    process = SimpleNamespace(pid=123, poll=lambda: None)
    containment = SimpleNamespace(
        identity="group", has_other_processes=lambda pid: membership,
    )
    assert poll_stalled(owner, "attempt", activity, 10, process, containment) is None


def test_supervisor_retires_only_after_stalled_intent(monkeypatch):
    monkeypatch.setattr(
        "devlegate.stalled_watchdog.observe_worker_identity", lambda _: "matching-live"
    )

    class Containment:
        identity = "group"

        def spawn(self, command, **kwargs):
            return subprocess.Popen(command, **kwargs)

        def has_other_processes(self, pid):
            return False

    class Owner:
        def __init__(self):
            self.state = {"phase": "agent_running", "execution_stage": "worker-running",
                          "execution_id": "attempt"}
            self.events = []
            self._workers = SimpleNamespace(
                analysis_launch_evidence=lambda _: {"containment": "group"}
            )

        def recovery_state(self):
            return self.state

        def recovery_worker_identity(self, value, execution_id):
            return SimpleNamespace(**value) if isinstance(value, dict) else None

        def recovery_save_state(self, phase, **values):
            self.events.append(values["stalled_recovery"]["stage"])
            self.state.update(values)

        def recovery_retire_worker(self, identity, evidence):
            assert self.state["stalled_recovery"]["stage"] == "intent"
            self.events.append("retire")
            os.kill(identity.pid, signal.SIGKILL)

    owner = Owner()
    activity = WorkerActivity(1, clock=lambda: 0)
    activity._clock = lambda: 20
    result = _run_opencode(
        [sys.executable, "-c", "import time; time.sleep(60)"], "prompt",
        execution_id="attempt",
        worker_identity_handler=lambda identity: owner.state.update(
            worker_identity={"pid": identity.pid, "containment_id": "group"}
        ),
        containment=Containment(),
        watchdog_poll=lambda process, containment: poll_stalled(
            owner, "attempt", activity, 10, process, containment
        ), show_worker_output=False,
    )
    assert owner.events == ["intent", "retire", "retired"]
    assert result.interruption_kind == "worker_stalled"
    assert result.worker_group_retired
