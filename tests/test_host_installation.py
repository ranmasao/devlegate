# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

import devlegate.cli as cli
from devlegate.host_installation import (
    HostInstallation,
    HostInstallationError,
    installation_path,
    read,
    write,
)
from devlegate.project_registry import ProjectRegistry
from devlegate.runtime_locator import RuntimeLocator
from devlegate.systemd_supervisor import MANAGED_MARKER, unit_name


def test_installation_record_round_trips_and_is_private(tmp_path: Path):
    path = tmp_path / "config" / "devlegate" / "installation.json"

    write(path, HostInstallation("internal"))

    assert read(path) == HostInstallation("internal")
    assert path.stat().st_mode & 0o777 == 0o600
    assert json.loads(path.read_text()) == {"version": 1, "supervisor": "internal"}


@pytest.mark.parametrize(
    "value",
    [
        {"version": 2, "supervisor": "internal"},
        {"version": 1, "supervisor": "unknown"},
        {"version": 1, "supervisor": "internal", "extra": True},
        [],
    ],
)
def test_invalid_installation_record_fails_closed(tmp_path: Path, value):
    path = tmp_path / "installation.json"
    path.write_text(json.dumps(value))

    with pytest.raises(HostInstallationError):
        read(path)


def test_top_level_install_and_uninstall_are_not_commands(monkeypatch):
    parser = cli.build_parser()

    with pytest.raises(SystemExit):
        parser.parse_args(["install", "--supervisor", "internal"])
    with pytest.raises(SystemExit):
        parser.parse_args(["uninstall"])


def test_host_install_internal_is_idempotent_and_rejects_change(
    monkeypatch, tmp_path, capsys
):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "host", "install", "--supervisor", "internal"],
    )

    assert cli.main() == 0
    assert cli.main() == 0
    assert "already installed" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "host", "install", "--supervisor", "systemd"],
    )
    assert cli.main() == 1
    assert read() == HostInstallation("internal")


def test_systemd_preflight_failure_does_not_commit_record(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))

    class Unavailable:
        def probe_user_manager(self):
            raise cli.SystemdSupervisorError("systemd user manager is unavailable")

    monkeypatch.setattr(cli, "SystemdSupervisor", Unavailable)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "host", "install", "--supervisor", "systemd"],
    )

    assert cli.main() == 1
    assert not installation_path().exists()


def test_host_uninstall_requires_empty_registry(monkeypatch, tmp_path, capsys):
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    write(installation_path(), HostInstallation("internal"))
    registry = ProjectRegistry()
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(
        json.dumps({"version": 1, "projects": {"stale": {"env": "/missing/.env"}}})
    )

    monkeypatch.setattr(sys, "argv", ["devlegate", "host", "uninstall"])

    assert cli.main() == 1
    assert installation_path().exists()
    assert "@stale" in capsys.readouterr().err


def test_host_uninstall_removes_only_record(monkeypatch, tmp_path):
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    write(installation_path(), HostInstallation("internal"))
    registry = ProjectRegistry()
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(json.dumps({"version": 1, "projects": {}}))
    retained = tmp_path / "state" / "runtime.sqlite3"
    retained.parent.mkdir()
    retained.write_text("retained")

    monkeypatch.setattr(sys, "argv", ["devlegate", "host", "uninstall"])

    assert cli.main() == 0
    assert not installation_path().exists()
    assert retained.read_text() == "retained"


def test_host_uninstall_delete_failure_preserves_record(monkeypatch, tmp_path):
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    write(installation_path(), HostInstallation("internal"))
    registry = ProjectRegistry()
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(json.dumps({"version": 1, "projects": {}}))
    monkeypatch.setattr(
        cli,
        "remove_installation",
        lambda _path: (_ for _ in ()).throw(
            HostInstallationError("delete failed")
        ),
    )
    monkeypatch.setattr(sys, "argv", ["devlegate", "host", "uninstall"])

    assert cli.main() == 1
    assert read() == HostInstallation("internal")


def test_host_uninstall_rejects_residual_managed_unit(monkeypatch, tmp_path):
    config = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config))
    write(installation_path(), HostInstallation("systemd"))
    registry = ProjectRegistry()
    registry.path.parent.mkdir(parents=True, exist_ok=True)
    registry.path.write_text(json.dumps({"version": 1, "projects": {}}))
    units = config / "systemd" / "user"
    units.mkdir(parents=True)

    residual = units / f"devlegate-{'a' * 64}.service"
    residual.write_text(f"{MANAGED_MARKER}\n# state_key={'a' * 64}\n")
    monkeypatch.setattr(sys, "argv", ["devlegate", "host", "uninstall"])

    assert cli.main() == 1
    assert installation_path().exists()


def test_systemd_default_start_never_uses_internal_background(monkeypatch, tmp_path):
    target = cli.ProjectTarget(
        "foo",
        tmp_path / ".env",
        tmp_path,
        RuntimeLocator(tmp_path, tmp_path / "state", "a" * 64),
    )
    calls: list[str] = []

    class FakeSupervisor:
        unit_directory = tmp_path / "units"

        def probe_user_manager(self):
            calls.append("probe")

        def install(self, locator, env):
            calls.append("install")
            return self.unit_directory / unit_name(locator)

        def start(self, locator):
            calls.append("start")

        def inspect(self, locator, **_kwargs):
            calls.append("inspect")
            return False

    monkeypatch.setattr(cli, "SystemdSupervisor", FakeSupervisor)
    monkeypatch.setattr(cli, "_project_target", lambda **_: target)
    monkeypatch.setattr(cli, "_healthy_service", lambda _env: None)
    monkeypatch.setattr(cli, "_host_installation", lambda: HostInstallation("systemd"))
    monkeypatch.setattr(
        cli,
        "_start_background",
        lambda _env: pytest.fail("internal fallback"),
    )

    assert (
        cli._run_default_command(
            project_alias=None, service_env=target.env_file, startup_fd=None
        )
        == 0
    )
    assert calls == ["probe", "install", "start"]
