# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import test_project_registry as registry

globals().update(
    {
        name: getattr(registry, f"_{name}")
        for name in (
            "test_forced_managed_restart_admits_then_restarts_exact_unit",
            "test_forced_managed_restart_reports_skew_without_kill_fallback",
            "test_plain_managed_restart_does_not_require_service_ipc",
            "test_project_alias_rename_does_not_change_systemd_identity",
            "test_project_list_reports_persisted_unit_names_and_missing_projects",
            "test_project_remove_compare_and_remove_preserves_replaced_alias",
            "test_project_remove_preserves_project_and_retained_state",
            "test_project_remove_refuses_unmanaged_unit_and_keeps_alias",
            "test_project_remove_removes_only_managed_unit_and_keeps_other_project",
            "test_project_remove_unit_failure_keeps_alias",
            "test_project_rename_preserves_runtime_identity",
            "test_start_systemd_reprovisions_persisted_legacy_name",
            "test_top_level_lifecycle_systemd_failure_is_concise_cli_error",
            "test_top_level_lifecycle_uses_persisted_compact_unit_name",
            "test_top_level_lifecycle_uses_persisted_legacy_unit_name",
        )
    }
)
