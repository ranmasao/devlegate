# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import test_worker_protocol as protocol

globals().update(
    {
        name: getattr(protocol, f"_{name}")
        for name in (
            "test_duplicate_reports_fail_closed",
            "test_fake_report_text_reasoning_and_unrelated_tools_are_inert",
            "test_invalid_claim_schema_fails_closed",
            "test_lookalike_tools_are_not_claims",
            "test_malformed_report_then_valid_report_is_typed_egress_failure",
            "test_malformed_then_valid_report_remains_poisoned",
            "test_malformed_worker_events_fail_closed",
            "test_missing_report_is_protocol_failure",
            "test_non_completed_reserved_tool_is_protocol_failure",
            "test_nonzero_process_preserves_valid_claim_and_transport_status",
            "test_oversized_event_is_drained_and_not_echoed",
            "test_process_zero_and_malformed_json_preserve_independent_status",
            "test_process_zero_and_oversized_json_preserve_independent_status",
            "test_tool_and_error_events_are_rendered_inert",
            "test_transport_failure_preserves_valid_typed_egress",
            "test_valid_claim_outcomes_are_typed",
            "test_valid_then_malformed_report_remains_poisoned",
            "test_valid_transport_without_report_is_typed_egress_failure",
            "test_worker_event_just_below_limit_is_processed",
        )
    }
)
