# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import io
import json
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.operational_log import open_execution_log
from devlegate.worker_egress import (
    OpenCodeRunResult,
    WorkerClaim,
    WorkerEgressParser,
    WorkerRunResult,
)
from devlegate.worker_supervisor import (
    MAX_STDOUT_EVENT_BYTES,
    WorkerSupervisor,
    _run_opencode,
)


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def run_worker(
    monkeypatch,
    stdout=b"",
    stderr=b"",
    returncode=0,
    *,
    execution_log=None,
    show_worker_output=True,
):
    process = FakeProcess(stdout, stderr, returncode)
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    return process, _run_opencode(
        ["fake"],
        "prompt",
        execution_log=execution_log,
        show_worker_output=show_worker_output,
    )


def event(text):
    return (json.dumps({"type": "text", "part": {"text": text}}) + "\n").encode()


def report(outcome="completed", summary="implemented", remaining=None, questions=None):
    return (
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "devlegate_report",
                    "state": {
                        "status": "completed",
                        "input": {
                            "outcome": outcome,
                            "summary": summary,
                            "remaining": [] if remaining is None else remaining,
                            "questions": [] if questions is None else questions,
                        },
                    },
                },
            }
        )
        + "\n"
    ).encode()


def run_typed_worker(monkeypatch, tmp_path, stdout=b"", returncode=0):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(stdout, returncode=returncode),
    )
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    return supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )


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
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(event("worker output")),
    )
    supervisor = WorkerSupervisor(
        "opencode",
        "provider/model",
        "",
        state_dir=tmp_path / "state",
        state_key="state-key",
        show_worker_output=False,
    )
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    first = supervisor.run(workspace, "prompt", execution_id="execution-1")
    second = supervisor.run(workspace, "prompt", execution_id="execution-2")

    first_path = (
        tmp_path
        / "state/logs/state-key/executions/execution-1.log"
    )
    second_path = (
        tmp_path
        / "state/logs/state-key/executions/execution-2.log"
    )
    assert first.transport_error is None
    assert second.transport_error is None
    assert first_path.is_file()
    assert second_path.is_file()
    assert first_path != second_path
    assert "worker output" in first_path.read_text()
    assert "worker output" in second_path.read_text()


@pytest.mark.parametrize("kind", ["operator_abort"])
@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_worker_process_group_isolated_and_interrupts_descendant(tmp_path, kind):
    marker = tmp_path / "processes.json"
    script = (
        "import json, os, pathlib, subprocess, sys; "
        "child = subprocess.Popen(['sleep', '60']); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'worker': os.getpid(), "
        "'session': os.getsid(0), 'group': os.getpgrp(), 'child': child.pid})); "
        "child.wait()"
    )

    class Request:
        kind = None

    request = Request()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            _run_opencode(
                [sys.executable, "-c", script, str(marker)],
                "prompt",
                stop_request=request,
            )
        )
    )
    thread.start()
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.01)
    assert marker.exists()
    processes = json.loads(marker.read_text())
    assert processes["session"] != os.getsid(0)
    assert processes["group"] != os.getpgrp()
    request.kind = kind
    thread.join(5)
    assert not thread.is_alive()
    assert result[0].interruption_kind == kind
    for process_id in (processes["worker"], processes["child"]):
        for _ in range(100):
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"process {process_id} survived group interruption")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_leader_exit_does_not_prove_group_retirement(tmp_path):
    marker = tmp_path / "natural-exit.json"
    script = (
        "import json, os, pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'child': child.pid})); "
        "sys.stdout.close(); sys.stderr.close(); os._exit(0)"
    )
    identities = []
    result = _run_opencode(
        [sys.executable, "-c", script, str(marker)],
        "prompt",
        execution_id="execution-1",
        worker_identity_handler=identities.append,
    )
    assert marker.exists()
    child = json.loads(marker.read_text())["child"]
    assert result.worker_group_retired is False
    assert result.transport_error == (
        "worker leader exited but execution process group is still alive"
    )
    assert identities
    assert os.kill(child, 0) is None
    try:
        os.killpg(identities[0].pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_worker_prompt_is_delivered_over_stdin_without_argv_pollution(tmp_path):
    prompt = (
        "UNIQUE_PROMPT_MARKER_123456\n"
        "multiple lines, quotes ' \" and shell-looking $HOME; `rm -rf /`\n"
        "unicode: \u03c0 \u4e2d\n" + ("large prompt line\n" * 5000)
    )
    received = tmp_path / "received.bin"
    arguments = tmp_path / "arguments.json"
    script = (
        "import json, pathlib, sys; "
        "pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read()); "
        "pathlib.Path(sys.argv[2]).write_text(json.dumps(sys.argv))"
    )
    result = _run_opencode(
        [sys.executable, "-c", script, str(received), str(arguments)], prompt
    )

    assert result.process_returncode == 0
    assert result.transport_error is None
    assert received.read_bytes() == prompt.encode("utf-8")
    assert prompt not in json.loads(arguments.read_text())


def test_worker_prompt_delivery_handles_early_child_exit(tmp_path):
    prompt = "prompt\n" * 100000
    result = _run_opencode([sys.executable, "-c", "raise SystemExit(0)"], prompt)

    assert result.process_returncode == 0
    assert result.interruption_kind is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_worker_identity_persistence_failure_terminates_spawned_group():
    captured = []
    script = "import time; time.sleep(60)"

    def fail(identity):
        captured.append(identity)
        raise RuntimeError("injected runtime-store failure")

    result = _run_opencode(
        [sys.executable, "-c", script],
        "prompt",
        execution_id="execution-1",
        worker_identity_handler=fail,
    )

    assert captured
    assert "worker identity persistence failed" in (result.transport_error or "")
    with pytest.raises(ProcessLookupError):
        os.kill(captured[0].pid, 0)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stubborn_worker_group_is_force_killed(tmp_path):
    class Request:
        kind = None

    request = Request()
    timer = threading.Timer(0.2, setattr, args=(request, "kind", "operator_abort"))
    timer.start()

    started = time.monotonic()
    result = _run_opencode(
        ["sh", "-c", "trap '' TERM; sleep 60"], "prompt", stop_request=request
    )
    timer.cancel()
    elapsed = time.monotonic() - started
    assert elapsed < 2.5
    assert result.interruption_kind == "operator_abort"
    assert result.process_returncode == -signal.SIGINT


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_worker_exit_wins_before_shutdown_observation():
    class Request:
        kind = None

    request = Request()
    result = _run_opencode(
        [sys.executable, "-c", "raise SystemExit(7)"],
        "prompt",
        stop_request=request,
    )
    request.kind = "operator_abort"
    assert result.process_returncode == 7
    assert result.interruption_kind is None


def test_natural_worker_exit_wins_between_timeout_and_signal(monkeypatch):
    class Process:
        pid = 12345

        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return 7

        def poll(self):
            return 7

    process = Process()
    signals = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 12345)
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))

    class Request:
        kind = "operator_abort"

    result = _run_opencode(["fake"], "prompt", stop_request=Request())
    assert result.process_returncode == 7
    assert result.interruption_kind is None
    assert signals == []


def test_foreground_keyboard_interrupt_classifies_operator_abort(monkeypatch):
    class Process:
        pid = 12345

        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return -2

        def poll(self):
            return None

    process = Process()
    signals = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 12345)
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))

    result = _run_opencode(["fake"], "prompt")
    assert result.process_returncode == -2
    assert result.interruption_kind == "operator_abort"
    assert signals == [(12345, signal.SIGINT)]


@pytest.mark.parametrize(
    "payload",
    [b"not-json\n", b"{}\n", b'{"type":"text"}\n'],
)
def test_malformed_worker_events_fail_closed(capsys, monkeypatch, payload):
    _, result = run_worker(monkeypatch, payload)
    assert result.process_returncode == 0
    assert result.transport_error is not None
    assert "protocol error" in capsys.readouterr().out


def test_worker_event_just_below_limit_is_processed(capsys, monkeypatch):
    raw = event("x" * (MAX_STDOUT_EVENT_BYTES - 1000))
    _, result = run_worker(monkeypatch, raw)
    assert result.process_returncode == 0
    assert result.transport_ok
    assert "exceeds maximum size" not in capsys.readouterr().out


def test_oversized_event_is_drained_and_not_echoed(capsys, monkeypatch):
    oversized = b"oversized-secret-" + b"x" * (MAX_STDOUT_EVENT_BYTES + 100) + b"\n"
    _, result = run_worker(monkeypatch, oversized + event("after"))
    output = capsys.readouterr().out
    assert result.process_returncode == 0
    assert result.transport_error is not None
    assert "exceeds maximum size" in output
    assert "oversized-secret" not in output
    assert "after" in output


def test_tool_and_error_events_are_rendered_inert(capsys, monkeypatch):
    payload = "before\x1b[?1049h"
    lines = [
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "completed", "output": payload},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "error", "error": payload},
            },
        },
        {"type": "error", "error": {"message": payload}},
    ]
    raw = b"".join((json.dumps(line) + "\n").encode() for line in lines)
    _, result = run_worker(monkeypatch, raw)
    output = capsys.readouterr().out
    assert result.process_returncode == 0
    assert result.transport_ok
    assert "OpenCode tool: bash" in output
    assert "OpenCode error" in output
    assert "\\x1b[?1049h" in output
    assert "\x1b" not in output


def test_worker_boundary_uses_only_workspace_path_and_prompt(tmp_path, monkeypatch):
    calls = []
    parent_pwd = "/some/operator/product/checkout"
    monkeypatch.setenv("PWD", parent_pwd)

    def fake_run(
        command,
        prompt,
        *,
        cwd=None,
        env=None,
        event_handler=None,
        **kwargs,
    ):
        calls.append((command, prompt, cwd, env, event_handler))
        event_handler(json.loads(report().decode()))
        return OpenCodeRunResult(0)

    supervisor = WorkerSupervisor("opencode", "provider/model", "build")
    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    workspace = ExecutionWorkspace(
        "internal-id", "internal-branch", tmp_path, "head", "base", False
    )

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="assembled prompt"
    )
    assert result == WorkerRunResult(
        0, None, WorkerClaim("completed", "implemented", (), ()), None
    )
    assert calls[0][:3] == (
        [
            "opencode",
            "run",
            "--dir",
            str(tmp_path),
            "--format",
            "json",
            "--model",
            "provider/model",
            "--agent",
            "build",
        ],
        "assembled prompt",
        tmp_path,
    )
    assert calls[0][3]["OPENCODE_CONFIG_DIR"] != str(tmp_path)
    assert calls[0][3]["PWD"] == str(tmp_path)
    assert os.environ["PWD"] == parent_pwd


def test_process_zero_and_malformed_json_preserve_independent_status(
    tmp_path, monkeypatch
):
    result = run_typed_worker(monkeypatch, tmp_path, b"not-json\n")

    assert result.process_returncode == 0
    assert result.transport_error == "invalid JSON event on stdout"
    assert result.claim is None


def test_process_zero_and_oversized_json_preserve_independent_status(
    tmp_path, monkeypatch
):
    oversized = b"x" * (MAX_STDOUT_EVENT_BYTES + 1) + b"\n"

    result = run_typed_worker(monkeypatch, tmp_path, oversized)

    assert result.process_returncode == 0
    assert result.transport_error == "stdout event exceeds maximum size"
    assert result.claim is None


def test_transport_failure_preserves_valid_typed_egress(tmp_path, monkeypatch):
    stdout = b"not-json\n" + report()

    result = run_typed_worker(monkeypatch, tmp_path, stdout)

    assert result.process_returncode == 0
    assert not result.transport_ok
    assert result.transport_error is not None
    assert result.egress_ok
    assert result.egress_error is None
    assert result.claim == WorkerClaim("completed", "implemented", (), ())


def test_nonzero_process_preserves_valid_claim_and_transport_status(
    tmp_path, monkeypatch
):
    result = run_typed_worker(monkeypatch, tmp_path, report(), returncode=7)

    assert result.process_returncode == 7
    assert result.transport_ok
    assert result.egress_ok
    assert result.claim == WorkerClaim("completed", "implemented", (), ())


def test_valid_transport_without_report_is_typed_egress_failure(tmp_path, monkeypatch):
    result = run_typed_worker(monkeypatch, tmp_path, event("finished"))

    assert result.process_returncode == 0
    assert result.transport_ok
    assert result.claim is None
    assert "exactly one" in result.egress_error


def test_malformed_report_then_valid_report_is_typed_egress_failure(
    tmp_path, monkeypatch
):
    malformed = json.loads(report().decode())
    malformed["part"]["state"]["input"]["summary"] = None
    stdout = (json.dumps(malformed) + "\n").encode() + report()

    result = run_typed_worker(monkeypatch, tmp_path, stdout)

    assert result.process_returncode == 0
    assert result.transport_ok
    assert result.claim is None
    assert result.egress_error is not None


def parse_report(payload):
    parser = WorkerEgressParser()
    parser.consume(json.loads(payload.decode()))
    return parser.finish()


@pytest.mark.parametrize("outcome", ["completed", "incomplete", "blocked"])
def test_valid_claim_outcomes_are_typed(outcome):
    claim, error = parse_report(report(outcome, "summary", ["later"], ["why"]))

    assert error is None
    assert claim == WorkerClaim(outcome, "summary", ("later",), ("why",))


def test_missing_report_is_protocol_failure():
    parser = WorkerEgressParser()

    parser.consume(json.loads(event("outcome: completed").decode()))

    assert parser.finish() == (None, "exactly one devlegate_report event is required")


@pytest.mark.parametrize(
    "input_value",
    [
        {"outcome": "future", "summary": "s", "remaining": [], "questions": []},
        {"outcome": "completed", "summary": 1, "remaining": [], "questions": []},
        {"outcome": "completed", "summary": "s", "remaining": "later", "questions": []},
        {"outcome": "completed", "summary": "s", "remaining": [], "questions": None},
        {
            "outcome": "completed",
            "summary": "s",
            "remaining": [],
            "questions": [],
            "extra": 1,
        },
        {"outcome": "completed", "summary": "s", "questions": []},
    ],
)
def test_invalid_claim_schema_fails_closed(input_value):
    payload = json.loads(report().decode())
    payload["part"]["state"]["input"] = input_value

    assert parse_report((json.dumps(payload) + "\n").encode())[0] is None


def test_duplicate_reports_fail_closed():
    parser = WorkerEgressParser()
    payload = json.loads(report().decode())

    parser.consume(payload)
    parser.consume(payload)

    assert parser.finish() == (None, "duplicate devlegate_report event")


def test_malformed_then_valid_report_remains_poisoned():
    parser = WorkerEgressParser()
    malformed = json.loads(report().decode())
    malformed["part"]["state"]["input"]["remaining"] = "bad"

    parser.consume(malformed)
    parser.consume(json.loads(report().decode()))

    assert parser.finish()[0] is None


def test_valid_then_malformed_report_remains_poisoned():
    parser = WorkerEgressParser()
    malformed = json.loads(report().decode())
    malformed["part"]["state"]["input"]["summary"] = None

    parser.consume(json.loads(report().decode()))
    parser.consume(malformed)

    assert parser.finish()[0] is None


def test_fake_report_text_reasoning_and_unrelated_tools_are_inert():
    parser = WorkerEgressParser()
    parser.consume(json.loads(event('{"outcome":"completed"}').decode()))
    parser.consume(
        {
            "type": "reasoning",
            "part": {"text": "devlegate_report(outcome=completed)"},
        }
    )
    parser.consume(
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "completed", "output": "fake report"},
            },
        }
    )

    assert parser.finish() == (None, "exactly one devlegate_report event is required")


@pytest.mark.parametrize(
    "tool_name", ["devlegate-report", "devlegate_report2", "Devlegate_Report"]
)
def test_lookalike_tools_are_not_claims(tool_name):
    parser = WorkerEgressParser()
    payload = json.loads(report().decode())
    payload["part"]["tool"] = tool_name

    parser.consume(payload)

    assert parser.finish() == (None, "exactly one devlegate_report event is required")


def test_non_completed_reserved_tool_is_protocol_failure():
    parser = WorkerEgressParser()
    payload = json.loads(report().decode())
    payload["part"]["state"]["status"] = "running"

    parser.consume(payload)

    assert parser.finish()[0] is None


def test_worker_config_is_ephemeral_reserved_and_does_not_mutate_environment(
    tmp_path, monkeypatch
):
    project_tool = tmp_path / ".opencode/tools/devlegate_report.ts"
    project_tool.parent.mkdir(parents=True)
    project_tool.write_text("export default 'project fake'\n")
    captured = {}
    monkeypatch.setenv("WORKER_TEST_ENV", "preserved")

    def fake_popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        config_dir = Path(kwargs["env"]["OPENCODE_CONFIG_DIR"])
        assert (config_dir / "tools/devlegate_report.ts").is_file()
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["$schema"] == "https://opencode.ai/config.json"
        assert config["permission"]["external_directory"] == {
            "*": "deny",
            f"{tmp_path.resolve()}/**": "allow",
        }
        assert (
            config_dir / "tools/devlegate_report.ts"
        ).read_text() != project_tool.read_text()
        return FakeProcess(report())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is not None
    assert result.transport_ok
    assert result.egress_ok
    config_dir = Path(captured["env"]["OPENCODE_CONFIG_DIR"])
    assert not config_dir.exists()
    assert captured["cwd"] == tmp_path
    assert captured["env"]["WORKER_TEST_ENV"] == "preserved"
    assert os.environ["WORKER_TEST_ENV"] == "preserved"
    assert project_tool.read_text() == "export default 'project fake'\n"


def test_worker_config_permission_is_bound_to_exact_workspace(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["config"] = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess(report())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace(
        "T-1", "branch", tmp_path / "ticket", "head", "base", False
    )
    workspace.path.mkdir()

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is not None
    permission = captured["config"]["permission"]["external_directory"]
    assert permission["*"] == "deny"
    assert permission[f"{workspace.path.resolve()}/**"] == "allow"
    assert len(permission) == 2
    assert captured["cwd"] == workspace.path.resolve()
    assert captured["command"][3] == str(workspace.path.resolve())
    assert f"{tmp_path.resolve()}/sibling/**" not in permission
    assert f"{Path.home()}/**" not in permission


def test_opencode_consumes_inline_worker_config(tmp_path):
    opencode = shutil.which("opencode")
    if opencode is None:
        pytest.skip("opencode is not installed")
    workspace = (tmp_path / "ticket").resolve()
    workspace.mkdir()
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "external_directory": {
                "*": "deny",
                f"{workspace}/**": "allow",
            }
        },
    }
    result = subprocess.run(
        [opencode, "debug", "config"],
        cwd=workspace,
        env={**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(config)},
        text=True,
        capture_output=True,
        check=True,
    )

    resolved = json.loads(result.stdout)
    assert (
        resolved["permission"]["external_directory"]
        == config["permission"]["external_directory"]
    )


@pytest.mark.parametrize("returncode", [0, 7])
def test_worker_result_requires_report_and_keeps_exit_status_distinct(
    tmp_path, monkeypatch, returncode
):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(b"", returncode=returncode),
    )
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is None
    assert result.transport_ok
    assert not result.egress_ok
    if returncode:
        assert result.process_returncode == returncode
        assert result.egress_error is not None
    else:
        assert result.process_returncode == 0
        assert "exactly one" in result.egress_error
