# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from devlegate.runtime import ServiceEngine


def run_test_iteration(
    engine: ServiceEngine, stop_event=None
) -> int:
    """Run one canonical iteration with the direct-test authority contract."""
    with engine._lock():
        if stop_event is None:
            return engine.run_iteration()
        with engine._stop_context(stop_event):
            return engine.run_iteration()
