# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from devlegate.worker_activity import WorkerActivity


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
