import io
import json
import os
import subprocess
from pathlib import Path

import pytest

import devlegate.cli as cli
from devlegate.cli import MAX_STDOUT_EVENT_BYTES, _run_opencode
from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.worker_egress import (
    OpenCodeRunResult,
    WorkerClaim,
    WorkerEgressParser,
    WorkerRunResult,
)


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def run_worker(monkeypatch, stdout=b"", stderr=b"", returncode=0):
    process = FakeProcess(stdout, stderr, returncode)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: process
    )
    return process, _run_opencode(["fake"], "prompt")


def event(text):
    return (json.dumps({"type": "text", "part": {"text": text}}) + "\n").encode()


def report(
    outcome="completed", summary="implemented", remaining=None, questions=None
):
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
    devlegate = object.__new__(cli.Devlegate)
    devlegate.opencode_bin = "opencode"
    devlegate.opencode_model = "provider/model"
    devlegate.opencode_agent = ""
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)
    return devlegate._run_worker(workspace, "prompt")


def test_worker_protocol_renders_events_and_stderr(capsys, monkeypatch):
    stdout = event("ordinary")
    _, result = run_worker(monkeypatch, stdout, b"stderr\n")
    output = capsys.readouterr()
    assert result.process_returncode == 0
    assert result.transport_ok
    assert "ordinary" in output.out
    assert "stderr" in output.err


@pytest.mark.parametrize(
    "payload",
    [b"not-json\n", b'{}\n', b'{"type":"text"}\n'],
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

    def fake_run(command, prompt, *, cwd=None, env=None, event_handler=None):
        calls.append((command, prompt, cwd, env, event_handler))
        event_handler(json.loads(report().decode()))
        return OpenCodeRunResult(0)

    monkeypatch.setattr(cli, "_run_opencode", fake_run)
    devlegate = object.__new__(cli.Devlegate)
    devlegate.opencode_bin = "opencode"
    devlegate.opencode_model = "provider/model"
    devlegate.opencode_agent = "build"
    workspace = ExecutionWorkspace(
        "internal-id", "internal-branch", tmp_path, "head", "base", False
    )

    result = devlegate._run_worker(workspace, "assembled prompt")
    assert result == WorkerRunResult(
        0, None, WorkerClaim("completed", "implemented", (), ()), None
    )
    assert calls[0][:3] == (
        ["opencode", "run", "--model", "provider/model", "--agent", "build"],
        "assembled prompt",
        tmp_path,
    )
    assert calls[0][3]["OPENCODE_CONFIG_DIR"] != str(tmp_path)


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
        assert (
            config_dir / "tools/devlegate_report.ts"
        ).read_text() != project_tool.read_text()
        return FakeProcess(report())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    devlegate = object.__new__(cli.Devlegate)
    devlegate.opencode_bin = "opencode"
    devlegate.opencode_model = "provider/model"
    devlegate.opencode_agent = ""
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = devlegate._run_worker(workspace, "prompt")

    assert result.claim is not None
    assert result.transport_ok
    assert result.egress_ok
    config_dir = Path(captured["env"]["OPENCODE_CONFIG_DIR"])
    assert not config_dir.exists()
    assert captured["cwd"] == tmp_path
    assert captured["env"]["WORKER_TEST_ENV"] == "preserved"
    assert os.environ["WORKER_TEST_ENV"] == "preserved"
    assert project_tool.read_text() == "export default 'project fake'\n"


@pytest.mark.parametrize("returncode", [0, 7])
def test_worker_result_requires_report_and_keeps_exit_status_distinct(
    tmp_path, monkeypatch, returncode
):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(b"", returncode=returncode),
    )
    devlegate = object.__new__(cli.Devlegate)
    devlegate.opencode_bin = "opencode"
    devlegate.opencode_model = "provider/model"
    devlegate.opencode_agent = ""
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = devlegate._run_worker(workspace, "prompt")

    assert result.claim is None
    assert result.transport_ok
    assert not result.egress_ok
    if returncode:
        assert result.process_returncode == returncode
        assert result.egress_error is not None
    else:
        assert result.process_returncode == 0
        assert "exactly one" in result.egress_error
