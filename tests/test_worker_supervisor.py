# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import threading

import pytest

from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.worker_egress import OpenCodeRunResult
from devlegate.worker_supervisor import (
    WorkerAdmissionClosed,
    WorkerSupervisor,
    worker_environment,
)


def _report_event():
    return {
        "type": "tool_use",
        "part": {
            "type": "tool",
            "tool": "devlegate_report",
            "state": {
                "status": "completed",
                "input": {
                    "outcome": "completed",
                    "summary": "done",
                    "remaining": [],
                    "questions": [],
                },
            },
        },
    }


def test_worker_environment_removes_host_control_state():
    parent_environment = {
        "PROJECT_SETTING": "available",
        "NOTIFY_SOCKET": "/run/notify",
        "WATCHDOG_PID": "123",
        "WATCHDOG_USEC": "5000000",
        "DEVLEGATE_HOST_MODE": "external",
        "DEVLEGATE_REQUIRE_NOTIFY": "1",
        "DEVLEGATE_STARTUP_FD": "4",
        "DEVLEGATE_RESTART_AUTHORITY_FD": "5",
        "DEVLEGATE_RESTART_AUTHORITY_KEY": "key",
        "DEVLEGATE_RESTART_REQUEST": "request",
        "DEVLEGATE_RESTART_INSTANCE": "instance",
    }

    sanitized = worker_environment(parent_environment)

    assert sanitized == {"PROJECT_SETTING": "available"}
    assert parent_environment["NOTIFY_SOCKET"] == "/run/notify"


def test_sanitized_environment_does_not_enable_nested_hosting(monkeypatch):
    from devlegate import cli

    parent_environment = {
        "NOTIFY_SOCKET": "/run/notify",
        "DEVLEGATE_HOST_MODE": "external",
        "DEVLEGATE_REQUIRE_NOTIFY": "1",
    }
    monkeypatch.delenv("DEVLEGATE_HOST_MODE", raising=False)
    monkeypatch.delenv("DEVLEGATE_REQUIRE_NOTIFY", raising=False)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)
    for name, value in worker_environment(parent_environment).items():
        monkeypatch.setenv(name, value)

    assert cli._host_mode() is cli.HostingMode.DIRECT
    assert cli._systemd_readiness_report() is None


def test_supervisor_tracks_live_execution_ownership(tmp_path, monkeypatch):
    started = threading.Event()
    release = threading.Event()
    result = []

    def fake_run(_command, _prompt, *, event_handler=None, **_kwargs):
        event_handler(_report_event())
        started.set()
        assert release.wait(5)
        return OpenCodeRunResult(0)

    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    supervisor = WorkerSupervisor("opencode", "model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    thread = threading.Thread(
        target=lambda: result.append(
            supervisor.run(workspace, "prompt", execution_id="execution-1")
        )
    )
    thread.start()
    assert started.wait(5)
    assert supervisor.active_count == 1
    assert supervisor.owns("execution-1")
    assert not supervisor.owns("other-execution")
    release.set()
    thread.join(5)
    assert not thread.is_alive()
    assert result[0].egress_ok
    assert supervisor.active_count == 0


def test_supervisor_uses_workspace_and_prompt_as_worker_boundary(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, prompt, **kwargs):
        calls.append((command, prompt, kwargs))
        kwargs["event_handler"](_report_event())
        return OpenCodeRunResult(0)

    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    supervisor = WorkerSupervisor("opencode", "model", "agent")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    result = supervisor.run(workspace, "prompt", execution_id="execution-1")

    assert result.egress_ok
    assert calls[0][0] == [
        "opencode",
        "run",
        "--dir",
        str(tmp_path),
        "--format",
        "json",
        "--model",
        "model",
        "--agent",
        "agent",
    ]
    assert calls[0][1] == "prompt"
    assert calls[0][2]["cwd"] == tmp_path
    config = json.loads(calls[0][2]["env"]["OPENCODE_CONFIG_CONTENT"])
    assert config["permission"]["external_directory"] == {
        "*": "deny",
        f"{tmp_path}/**": "allow",
    }


def test_drain_and_worker_launch_have_one_admission_linearization_point(
    tmp_path, monkeypatch
):
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_run(_command, _prompt, *, event_handler=None, **_kwargs):
        calls.append(True)
        event_handler(_report_event())
        started.set()
        assert release.wait(5)
        return OpenCodeRunResult(0)

    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    supervisor = WorkerSupervisor("opencode", "model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    owner = threading.Thread(
        target=supervisor.run,
        args=(workspace, "prompt"),
        kwargs={"execution_id": "first"},
    )
    owner.start()
    assert started.wait(5)
    assert supervisor.begin_drain() is True
    assert supervisor.begin_drain() is False
    with pytest.raises(WorkerAdmissionClosed):
        supervisor.run(workspace, "prompt", execution_id="second")
    assert supervisor.active_count == 1
    release.set()
    owner.join(5)
    assert not owner.is_alive()
    assert calls == [True]
