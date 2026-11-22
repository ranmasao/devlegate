# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import base64

import pytest

from devlegate.worker_activity import OUTPUT_TAIL_BYTES, WorkerActivity


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_partial_bytes_reset_warning_at_boundary(stream):
    now = [0.0]
    activity = WorkerActivity(5, clock=lambda: now[0])
    for timestamp in (4.9, 9.8, 14.7):
        now[0] = timestamp
        activity.observe(stream, b"x")
        assert not activity.snapshot()["warning"]
    now[0] += 5
    assert activity.snapshot() == {"idle_seconds": 5, "warning": True}
    activity.observe(stream, b"")
    assert activity.snapshot()["warning"]
    activity.observe(stream, b"x")
    assert activity.snapshot() == {"idle_seconds": 0, "warning": False}


@pytest.mark.parametrize("value", [0, -1, float("nan"), float("inf")])
def test_invalid_warning_threshold_is_rejected(value):
    with pytest.raises(ValueError):
        WorkerActivity(value)


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_late_output_prevents_idle_decision(stream):
    now = [0.0]
    tracker = WorkerActivity(300, clock=lambda: now[0])
    calls = []
    now[0] = 300
    assert tracker.snapshot()["warning"]
    now[0] = 599.9
    tracker.observe(stream, b"partial")
    now[0] = 600
    assert not tracker.if_idle(600, lambda: calls.append("intent") or True)
    now[0] = 1199.9
    assert tracker.if_idle(600, lambda: calls.append("intent") or True)
    assert calls == ["intent"]


def test_idle_evidence_preserves_bounded_bytes_per_stream():
    now = [0.0]
    activity = WorkerActivity(1, clock=lambda: now[0])
    activity.observe("stdout", b"a" * OUTPUT_TAIL_BYTES + b"\xffend")
    activity.observe("stderr", b"error\x00\n")
    now[0] = 10
    captured = []
    assert activity.if_idle_with_output(
        10, lambda output: captured.append(output) or True
    )
    assert base64.b64decode(captured[0]["stdout"]["base64"]) == (
        b"a" * (OUTPUT_TAIL_BYTES - 4) + b"\xffend"
    )
    assert captured[0]["stdout"]["truncated"] is True
    assert base64.b64decode(captured[0]["stderr"]["base64"]) == b"error\x00\n"
    assert captured[0]["stderr"]["truncated"] is False
