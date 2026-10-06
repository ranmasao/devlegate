# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: E501

import pytest
import test_project_registry as registry

from devlegate.ipc_client import IPCClientError


def test_project_rename_preserves_runtime_identity(tmp_path, monkeypatch, capsys):
    return registry._test_project_rename_preserves_runtime_identity(
        tmp_path, monkeypatch, capsys
    )


def test_project_remove_preserves_project_and_retained_state(
    tmp_path, monkeypatch, capsys
):
    return registry._test_project_remove_preserves_project_and_retained_state(
        tmp_path, monkeypatch, capsys
    )


def test_project_remove_compare_and_remove_preserves_replaced_alias(
    tmp_path, monkeypatch
):
    return registry._test_project_remove_compare_and_remove_preserves_replaced_alias(
        tmp_path, monkeypatch
    )


def test_project_list_reports_persisted_unit_names_and_missing_projects(
    tmp_path, monkeypatch, capsys
):
    return (
        registry._test_project_list_reports_persisted_unit_names_and_missing_projects(
            tmp_path, monkeypatch, capsys
        )
    )


def test_project_alias_rename_does_not_change_systemd_identity(tmp_path, monkeypatch):
    return registry._test_project_alias_rename_does_not_change_systemd_identity(
        tmp_path, monkeypatch
    )


def test_start_systemd_reprovisions_persisted_legacy_name(tmp_path, monkeypatch):
    return registry._test_start_systemd_reprovisions_persisted_legacy_name(
        tmp_path, monkeypatch
    )


def test_top_level_lifecycle_uses_persisted_legacy_unit_name(
    tmp_path, monkeypatch, capsys
):
    return registry._test_top_level_lifecycle_uses_persisted_legacy_unit_name(
        tmp_path, monkeypatch, capsys
    )


def test_top_level_lifecycle_uses_persisted_compact_unit_name(
    tmp_path, monkeypatch, capsys
):
    return registry._test_top_level_lifecycle_uses_persisted_compact_unit_name(
        tmp_path, monkeypatch, capsys
    )


@pytest.mark.parametrize("action", ["stop", "restart"])
def test_top_level_lifecycle_systemd_failure_is_concise_cli_error(
    tmp_path, monkeypatch, capsys, action
):
    return registry._test_top_level_lifecycle_systemd_failure_is_concise_cli_error(
        tmp_path, monkeypatch, capsys, action
    )


def test_plain_managed_restart_does_not_require_service_ipc(
    tmp_path, monkeypatch, capsys
):
    return registry._test_plain_managed_restart_does_not_require_service_ipc(
        tmp_path, monkeypatch, capsys
    )


def test_forced_managed_restart_admits_then_restarts_exact_unit(
    tmp_path, monkeypatch, capsys
):
    return registry._test_forced_managed_restart_admits_then_restarts_exact_unit(
        tmp_path, monkeypatch, capsys
    )


@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            IPCClientError(
                "service error: restart payload fields are invalid",
                application=True,
                code="invalid_request",
            ),
            "installed CLI and daemon protocol versions differ",
        ),
        (
            IPCClientError(
                "service error: restart conflicts with an active stop",
                application=True,
                code="application_error",
            ),
            "restart conflicts with an active stop",
        ),
    ],
)
def test_forced_managed_restart_reports_skew_without_kill_fallback(
    tmp_path, monkeypatch, capsys, error, expected
):
    return registry._test_forced_managed_restart_reports_skew_without_kill_fallback(
        tmp_path, monkeypatch, capsys, error, expected
    )


def test_project_remove_refuses_unmanaged_unit_and_keeps_alias(tmp_path, monkeypatch):
    return registry._test_project_remove_refuses_unmanaged_unit_and_keeps_alias(
        tmp_path, monkeypatch
    )


def test_project_remove_unit_failure_keeps_alias(tmp_path, monkeypatch):
    return registry._test_project_remove_unit_failure_keeps_alias(tmp_path, monkeypatch)


def test_project_remove_removes_only_managed_unit_and_keeps_other_project(
    tmp_path, monkeypatch
):
    return (
        registry._test_project_remove_removes_only_managed_unit_and_keeps_other_project(
            tmp_path, monkeypatch
        )
    )
