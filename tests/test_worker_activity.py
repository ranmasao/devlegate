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
    project_stall_output,
    render_stall_context,
)
from devlegate.worker_supervisor import _run_opencode


def test_stall_context_keeps_decoded_opencode_output(tmp_path):
    events = [
        {"type": "text", "part": {"type": "text", "text": "  safe text  "}},
        {"type": "text", "part": {"type": "text", "text": (
            "/private/location " + "discard " * TAIL_BYTES + "private line suffix"
        )}},
        {"type": "tool_use", "part": {
            "type": "tool", "tool": "read", "state": {
                "status": "completed", "output": "safe tool output\n",
            },
        }},
        {"type": "text", "part": {"type": "text", "text": (
            "discard " * TAIL_BYTES + "  end of large payload  "
        )}},
    ]
    raw = b"".join((json.dumps({"sessionID": "ses_private", **event}) + "\n").encode()
                   for event in reversed(events))
    transport = tmp_path / "events.jsonl"
    transport.write_bytes(raw)
    activity = WorkerActivity(lambda: 0)
    result = _run_opencode(
        [sys.executable, "-c", "import pathlib, sys; "
         "sys.stdout.buffer.write(pathlib.Path(sys.argv[1]).read_bytes())",
         str(transport)],
        "prompt", show_worker_output=False,
        activity_handler=activity.observe,
        decoded_stdout_handler=activity.observe_decoded_stdout,
    )
    assert result.transport_error is None
    evidence = {}
    activity.classify(activity.snapshot()[1], 0, evidence.update, private_values=())
    assert bytes.fromhex(evidence["output"]["stdout"]["hex"]) == raw[-TAIL_BYTES:]
    output = evidence["prompt_output"]
    assert output["stdout"]["truncated"]
    assert "  safe text  \n" in output["stdout"]["text"]
    assert "safe tool output\n" in output["stdout"]["text"]
    assert "  end of large payload  \n" in output["stdout"]["text"]
    assert "ses_private" not in output["stdout"]["text"]
    assert "private line suffix" not in output["stdout"]["text"]
    assert "[private output omitted]\n" in output["stdout"]["text"]
    assert len(output["stdout"]["text"].encode()) <= TAIL_BYTES
    assert render_stall_context("?? a\n", output) == render_stall_context(
        "?? a\n", json.loads(json.dumps(evidence))["prompt_output"],
    )


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
def test_activity_just_before_each_deadline_invalidates_stale_poll(stream):
    now = [0.0]
    activity = WorkerActivity(lambda: now[0])
    recorded = []

    def persist(evidence):
        recorded.append(evidence)
        return True

    for _ in range(4):
        _, generation = activity.snapshot()
        now[0] += 599.875
        activity.observe(stream, b"partial bytes")
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
    now = [0.0]
    activity = WorkerActivity(lambda: now[0])
    raw = b"discard\n" * TAIL_BYTES + b"\n  visible output  \nfinal byte: \xff"
    activity.observe("stdout", raw)
    activity.observe("stderr", b"worker PID 1234\n/hidden/runtime/worktree\nOK\n")
    now[0] += 600
    _, generation = activity.snapshot()
    evidence = {}
    activity.classify(generation, 600, evidence.update)
    assert bytes.fromhex(evidence["output"]["stdout"]["hex"]) == raw[-TAIL_BYTES:]
    output = project_stall_output(evidence, ("1234",))
    statuses = [f"?? file-{i:03}" for i in range(MAX_PATHS + 1)]
    statuses[0] += "a" * 200
    first = render_stall_context("\n".join(statuses), output)
    assert first == render_stall_context("\n".join(reversed(statuses)), output)
    assert "  visible output  \nfinal byte: \ufffd" in first
    assert "\nOK\n" in first
    assert "[earlier output omitted]" in first
    assert "[additional paths omitted]" in first
    assert "[path truncated]" in first
    assert f"- file-{MAX_PATHS:03}" not in first
    assert "1234" not in first and "/hidden" not in first


def test_prompt_omits_partial_first_line_and_json_encoded_private_paths():
    activity = WorkerActivity(lambda: 0)
    suffix = (
        b'{"type":"text","part":{"text":"\\u002fhidden\\u002fdir"}}\n'
        b"  visible text  \n"
    )
    activity.observe("stdout", b"pid=" + b"x" * TAIL_BYTES + b"secret\n" + suffix)
    evidence = {}
    activity.classify(activity.snapshot()[1], 0, evidence.update)
    projected = project_stall_output(evidence, ())
    assert projected["stdout"]["text"] == "[private output omitted]\n  visible text  \n"
    assert projected["stdout"]["truncated"]
