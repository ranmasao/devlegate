# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import test_worker_protocol as protocol

globals().update(
    {
        name: getattr(protocol, f"_{name}")
        for name in (
            "test_foreground_keyboard_interrupt_classifies_operator_abort",
            "test_natural_leader_exit_does_not_prove_group_retirement",
            "test_natural_worker_exit_wins_before_shutdown_observation",
            "test_natural_worker_exit_wins_between_timeout_and_signal",
            "test_stubborn_worker_group_is_force_killed",
            "test_worker_identity_persistence_failure_terminates_spawned_group",
            "test_worker_process_group_isolated_and_interrupts_descendant",
            "test_worker_prompt_delivery_handles_early_child_exit",
            "test_worker_prompt_is_delivered_over_stdin_without_argv_pollution",
        )
    }
)
