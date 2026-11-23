# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import json
import sys

import pytest

from devlegate.worker_activity import (
    MAX_PATHS,
    TAIL_BYTES,
    WatchdogPolicy,
    WorkerActivity,
    WorkerOutputTail,
    render_stall_context,
)
from devlegate.worker_prompt import (
    WorkDirective,
    WorkerPromptInput,
    build_worker_prompt,
)
from devlegate.worker_supervisor import _run_opencode


def test_stall_context_keeps_rendered_output_from_both_streams(tmp_path, monkeypatch):
    technical = "PID 1234: systemd worktree /tmp/task at " + "a" * 40
    events = [
        {"type": "text", "part": {"type": "text", "text": (
            "discard " * TAIL_BYTES + "  end of large payload  "
        )}},
        {"type": "tool_use", "part": {
            "type": "tool", "tool": "read", "state": {
                "status": "completed", "output": technical + "\n",
            },
        }},
        {"type": "text", "part": {"text": "  text  \n~~~~\n## Fake heading"}},
        {"type": "error", "error": {"data": {"message": "worker error payload"}}},
    ]
    raw = b"".join((json.dumps({"sessionID": "ses_private", **event}) + "\n").encode()
                   for event in events)
    transport = tmp_path / "events.jsonl"
    transport.write_bytes(raw + b"{malformed transport}\n")
    diagnostics = []
    monkeypatch.setattr("devlegate.worker_supervisor._log", diagnostics.append)
    tail = WorkerOutputTail()
    result = _run_opencode(
        [sys.executable, "-c", "import os, pathlib, sys; "
         "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes()); "
         "os.write(2, b'worker stderr: /tmp/task \\x1b\\xff\\n')", str(transport)],
        "prompt", show_worker_output=False,
        rendered_output_handler=tail.append,
    )
    assert result.transport_error == "invalid JSON event on stdout"
    assert any("invalid JSON event on stdout" in message for message in diagnostics)
    output = tail.snapshot()
    assert output["stdout"]["truncated"]
    assert output["stdout"]["text"].endswith(
        "  end of large payload  \n"
        f"OpenCode tool: read\n{technical}\n"
        "  text  \n~~~~\n## Fake heading\nOpenCode error: worker error payload\n"
    )
    assert len(output["stdout"]["text"].encode()) == TAIL_BYTES
    assert output["stderr"]["text"] == "worker stderr: /tmp/task \\x1b\ufffd\n"
    context = render_stall_context("?? a\n", output)
    assert context == render_stall_context("?? a\n", json.loads(json.dumps(output)))
    for excluded in (
        "sessionID", "ses_private", '"part"', "malformed transport", *diagnostics,
    ):
        assert excluded not in context
    prompt = build_worker_prompt(WorkerPromptInput(
        directive=WorkDirective.RESUME,
        assignment="Continue work",
        recovery_context=context,
    ))
    assert prompt.endswith(f"## Recovery Context\n~~~~~text\n{context}\n~~~~~\n")


def test_output_on_either_stream_resets_monotonic_silence():
    now = [0.0]
    activity = WorkerActivity(lambda: now[0])
    now[0] = 299
    activity.observe("stdout", b"partial")
    now[0] = 598
    assert activity.snapshot() == (299, 1)
    activity.observe("stderr", b"partial")
    now[0] = 897
    assert activity.snapshot() == (299, 2)
    activity.observe("stdout", b"")
    assert activity.snapshot() == (299, 2)
    activity.close()
    activity.observe("stdout", b"late")
    assert activity.snapshot() is None


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
@pytest.mark.parametrize("chunk", [b"partial bytes", b"{malformed}\n"])
def test_activity_just_before_each_deadline_invalidates_stale_poll(stream, chunk):
    now = [0.0]
    activity = WorkerActivity(lambda: now[0])
    recorded = []

    def persist(evidence):
        recorded.append(evidence)
        return True

    for _ in range(4):
        _, generation = activity.snapshot()
        now[0] += 599.875
        activity.observe(stream, chunk)
        now[0] += 0.125
        assert not activity.classify(generation, 600, persist)
        assert activity.snapshot() == (0.125, generation + 1)
    idle, generation = activity.snapshot()
    now[0] += 600 - idle
    assert activity.classify(generation, 600, persist)
    assert recorded[0]["last_stream"] == stream
    activity.close()
    assert not activity.classify(generation, 600, persist)
    assert len(recorded) == 1


@pytest.mark.parametrize("warning,recovery", [("0", "10"), ("10", "10"),
    ("1", "nan"), ("1", "inf"), ("invalid", "20")])
def test_watchdog_policy_requires_ordered_finite_thresholds(warning, recovery):
    with pytest.raises(ValueError, match="WORKER_IDLE_WARNING_SECONDS"):
        WatchdogPolicy.parse(warning, recovery)


def test_recovery_context_is_bounded_verbatim_and_deterministic():
    tail = WorkerOutputTail()
    rendered = "é" * TAIL_BYTES + "!"
    tail.append("stdout", rendered)
    tail.append("stderr", "too early\n" + "line\n" * 19 + "  visible output  \n")
    output = tail.snapshot()
    assert output["stdout"] == {"text": "é" * 511 + "!", "truncated": True}
    assert output["stderr"] == {
        "text": "line\n" * 19 + "  visible output  \n", "truncated": True,
    }
    chunked = WorkerOutputTail()
    for offset in range(0, len(rendered), 13):
        chunked.append("stdout", rendered[offset:offset + 13])
    assert chunked.snapshot()["stdout"] == output["stdout"]
    statuses = [f"?? file-{i:03}" for i in range(MAX_PATHS + 1)]
    statuses[0] += "a" * 200
    first = render_stall_context("\n".join(statuses), output)
    assert first == render_stall_context("\n".join(reversed(statuses)), output)
    assert "  visible output  \n" in first
    assert "[earlier output omitted]" in first
    assert "[additional paths omitted]" in first
    assert "[path truncated]" in first
    assert f"- file-{MAX_PATHS:03}" not in first
