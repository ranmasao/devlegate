# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import test_worker_protocol as protocol

globals().update(
    {
        name: getattr(protocol, f"_{name}")
        for name in (
            "test_execution_log_handoff_markers_follow_sink_and_worker_order",
            "test_execution_log_open_failure_has_no_handoff_or_worker_launch",
            "test_execution_logs_are_distinct_and_survive_worker_completion",
            "test_popen_failure_has_start_but_no_completion_marker",
            "test_successful_popen_precedes_completion_marker_and_log_close",
            "test_worker_output_is_sanitized_and_persisted_without_service_duplication",
            "test_worker_protocol_renders_events_and_stderr",
        )
    }
)
