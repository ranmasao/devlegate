# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import pytest

from devlegate.worker_activity import WatchdogPolicy, WorkerActivity


def test_activity_rejects_stale_deadline_and_ignores_eof():
    now = [0.0]
    activity = WorkerActivity(lambda: now[0])
    recorded = []

    def persist(idle):
        recorded.append(idle)
        return True

    now[0] = 599.875
    assert activity.snapshot() == (599.875, 0)
    assert not activity.classify(0, 600, persist)
    assert activity.read(lambda: 1) == 1
    now[0] = 600
    assert not activity.classify(0, 600, persist)
    assert activity.read(lambda: 0) == 0
    assert activity.snapshot() == (0.125, 1)
    now[0] += 599.875
    assert activity.classify(1, 600, persist)
    activity.close()
    assert activity.snapshot() is None
    assert not activity.classify(1, 600, persist)
    assert recorded == [600]


@pytest.mark.parametrize("warning,recovery", [
    ("0", "10"), ("10", "10"), ("1", "nan"), ("nan", "20"),
    ("inf", "20"), ("1", "inf"), ("invalid", "20"),
])
def test_watchdog_policy_requires_ordered_finite_thresholds(warning, recovery):
    with pytest.raises(ValueError, match="WORKER_IDLE_WARNING_SECONDS"):
        WatchdogPolicy.parse(warning, recovery)
