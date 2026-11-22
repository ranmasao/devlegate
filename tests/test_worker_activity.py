# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import pytest

from devlegate.worker_activity import WorkerActivity


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
