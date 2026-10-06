# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: E501

import subprocess

import pytest

import devlegate.worker_supervisor as worker_supervisor
from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.operational_log import open_execution_log
from devlegate.worker_egress import OpenCodeRunResult
from devlegate.worker_supervisor import WorkerSupervisor
from _worker_support import event, run_worker, FakeProcess


def test_worker_protocol_renders_events_and_stderr(capsys, monkeypatch):
    stdout = event("ordinary")
    _, result = run_worker(monkeypatch, stdout, b"stderr\n")
    output = capsys.readouterr()
    assert result.process_returncode == 0
    assert result.transport_ok
    assert "ordinary" in output.out
    assert "stderr" in output.err


def test_worker_output_is_sanitized_and_persisted_without_service_duplication(
    capsys, monkeypatch, tmp_path
):
    log_path = tmp_path / "state" / "logs" / "key" / "executions" / "execution-1.log"
    with open_execution_log(tmp_path / "state", "key", "execution-1") as log:
        _, result = run_worker(
            monkeypatch,
            event("ordinary\x1b[31m"),
            b"stderr\x1b\n",
            execution_log=log,
            show_worker_output=False,
        )
    output = capsys.readouterr()
    content = log_path.read_text()
    assert result.transport_ok
    assert output.out == ""
    assert output.err == ""
    assert "ordinary\\x1b[31m" in content
    assert "stderr\\x1b" in content
    assert "ordinary" not in output.out


def test_execution_logs_are_distinct_and_survive_worker_completion(
    monkeypatch, tmp_path
):
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: FakeProcess(event("worker output"))
    )
    supervisor = WorkerSupervisor(
        "opencode", "provider/model", "", state_dir=tmp_path / "state",
        state_key="state-key", show_worker_output=False,
    )
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    first = supervisor.run(workspace, "prompt", execution_id="execution-1")
    second = supervisor.run(workspace, "prompt", execution_id="execution-2")
    first_path = tmp_path / "state/logs/state-key/executions/execution-1.log"
    second_path = tmp_path / "state/logs/state-key/executions/execution-2.log"
    assert first.transport_error is None
    assert second.transport_error is None
    assert first_path.is_file()
    assert second_path.is_file()
    assert first_path != second_path
    assert "worker output" in first_path.read_text()
    assert "worker output" in second_path.read_text()


def test_execution_log_handoff_markers_follow_sink_and_worker_order(
    monkeypatch, tmp_path
):
    events: list[tuple[str, str]] = []
    log_path = tmp_path / "state" / "logs" / "key" / "executions" / "execution-1.log"
    class FakeLog:
        path = log_path
        def close(self):
            events.append(("close", str(self.path)))
    def open_log(*_args):
        events.append(("open", str(log_path)))
        return FakeLog()
    def log(message):
        events.append(("service", message))
    def run_opencode(*_args, **kwargs):
        events.append(("popen", "worker"))
        kwargs["worker_started_handler"]()
        events.append(("worker", kwargs["execution_id"]))
        return OpenCodeRunResult(0)
    monkeypatch.setattr(worker_supervisor, "open_execution_log", open_log)
    monkeypatch.setattr(worker_supervisor, "service_log", log)
    monkeypatch.setattr(worker_supervisor, "_run_opencode", run_opencode)
    supervisor = WorkerSupervisor("opencode", "provider/model", "", state_dir=tmp_path / "state", state_key="key")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    result = supervisor.run(workspace, "prompt", execution_id="execution-1")
    assert result.process_returncode == 0
    assert [event[0] for event in events] == ["open", "service", "popen", "worker", "service", "close"]
    assert "ticket=T-1" in events[1][1]
    assert "execution=execution-1" in events[1][1]
    assert f"log={log_path}" in events[1][1]
    assert events[1][1].startswith("execution starting: ")
    assert events[4][1].startswith("execution finished: ")
    assert f"log={log_path}" in events[4][1]


def test_execution_log_open_failure_has_no_handoff_or_worker_launch(
    monkeypatch, tmp_path
):
    events: list[str] = []
    def open_log(*_args):
        raise OSError("cannot open")
    monkeypatch.setattr(worker_supervisor, "open_execution_log", open_log)
    monkeypatch.setattr(worker_supervisor, "service_log", events.append)
    monkeypatch.setattr(worker_supervisor, "_run_opencode", lambda *_args, **_kwargs: pytest.fail("worker launched after log failure"))
    supervisor = WorkerSupervisor("opencode", "provider/model", "", state_dir=tmp_path / "state", state_key="key")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    result = supervisor.run(workspace, "prompt", execution_id="execution-1")
    assert result.process_returncode == -1
    assert "cannot open" in result.transport_error
    assert events == []


def test_popen_failure_has_start_but_no_completion_marker(monkeypatch, tmp_path):
    events: list[tuple[str, str]] = []
    log_path = tmp_path / "state" / "logs" / "key" / "executions" / "execution-1.log"
    class FakeLog:
        path = log_path
        def close(self):
            events.append(("close", str(self.path)))
    def open_log(*_args):
        events.append(("open", str(log_path)))
        return FakeLog()
    def fail_to_launch(*_args, **_kwargs):
        events.append(("popen", "worker"))
        raise OSError("cannot launch")
    monkeypatch.setattr(worker_supervisor, "open_execution_log", open_log)
    monkeypatch.setattr(worker_supervisor, "service_log", lambda message: events.append(("service", message)))
    monkeypatch.setattr(worker_supervisor.subprocess, "Popen", fail_to_launch)
    supervisor = WorkerSupervisor("opencode", "provider/model", "", state_dir=tmp_path / "state", state_key="key")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    result = supervisor.run(workspace, "prompt", execution_id="execution-1")
    assert result.process_returncode == -1
    assert "cannot launch" in result.transport_error
    assert [event[0] for event in events] == ["open", "service", "popen", "close"]
    assert events[1][1].startswith("execution starting: ")


def test_successful_popen_precedes_completion_marker_and_log_close(
    monkeypatch, tmp_path
):
    events: list[tuple[str, str]] = []
    log_path = tmp_path / "state" / "logs" / "key" / "executions" / "execution-1.log"
    class FakeLog:
        path = log_path
        def close(self):
            events.append(("close", str(self.path)))
    def open_log(*_args):
        events.append(("open", str(log_path)))
        return FakeLog()
    def popen(*_args, **_kwargs):
        events.append(("popen", "worker"))
        return FakeProcess()
    def log(message):
        events.append(("service", message))
    monkeypatch.setattr(worker_supervisor, "open_execution_log", open_log)
    monkeypatch.setattr(worker_supervisor, "service_log", log)
    monkeypatch.setattr(worker_supervisor.subprocess, "Popen", popen)
    supervisor = WorkerSupervisor("opencode", "provider/model", "", state_dir=tmp_path / "state", state_key="key")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    result = supervisor.run(workspace, "prompt", execution_id="execution-1")
    assert result.process_returncode == 0
    assert [event[0] for event in events] == ["open", "service", "popen", "service", "close"]
    assert events[3][1].startswith("execution finished: ")
    assert f"log={log_path}" in events[3][1]
