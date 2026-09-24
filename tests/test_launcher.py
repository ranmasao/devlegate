# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import sys
from pathlib import Path

import devlegate.cli as cli
from devlegate.host_installation import HostInstallation
from devlegate.launcher import LaunchCommand
from devlegate.project_registry import ProjectTarget
from devlegate.runtime_locator import RuntimeLocator


def test_current_python_launcher_keeps_source_invocation(monkeypatch):
    monkeypatch.setattr("devlegate.launcher.sys.executable", "/python/bin/python")

    assert LaunchCommand.current_python().argv() == [
        "/python/bin/python",
        "-P",
        "-m",
        "devlegate",
    ]


def test_standalone_launcher_is_just_an_executable():
    launcher = LaunchCommand.executable(Path("/opt/devlegate/bin/devlegate"))

    assert launcher.argv("--env", "/repo/.env", "foreground") == [
        "/opt/devlegate/bin/devlegate",
        "--env",
        "/repo/.env",
        "foreground",
    ]
    assert "-P" not in launcher.argv()
    assert "devlegate" not in launcher.argv()[1:]


def test_internal_background_command_uses_injected_launcher(tmp_path):
    launcher = LaunchCommand.executable("/custom/path/devlegate")

    assert cli._background_command(tmp_path / ".env", launcher) == [
        "/custom/path/devlegate",
        "--env",
        str(tmp_path / ".env"),
        "foreground",
    ]


def test_unmanaged_systemd_unit_is_normal_cli_error_for_stop_and_restart(
    monkeypatch, tmp_path, capsys
):
    locator = RuntimeLocator(tmp_path, tmp_path / "state", "a" * 64)
    target = ProjectTarget("foo", tmp_path / ".env", tmp_path, locator)
    calls: list[str] = []

    class UnmanagedSupervisor:
        def inspect(self, _locator):
            calls.append("inspect")
            raise cli.SystemdSupervisorError("refusing to operate on unmanaged unit")

    monkeypatch.setattr(cli, "SystemdSupervisor", UnmanagedSupervisor)
    monkeypatch.setattr(cli, "_project_target", lambda **_kwargs: target)
    monkeypatch.setattr(cli.RuntimeLocator, "from_env", lambda _env: locator)
    monkeypatch.setattr(cli, "_host_installation", lambda: HostInstallation("internal"))

    for action in ("stop", "restart"):
        monkeypatch.setattr(sys, "argv", ["devlegate", "@foo", action])
        assert cli.main() == 1
        assert "unmanaged unit" in capsys.readouterr().err

    assert calls == ["inspect", "inspect"]
