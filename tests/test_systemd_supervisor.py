# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from devlegate.runtime_locator import RuntimeLocator
from devlegate.systemd_supervisor import (
    MANAGED_MARKER,
    SystemdSupervisor,
    SystemdSupervisorError,
    notify_ready,
    render_unit,
    unit_name,
)


def locator(tmp_path: Path) -> RuntimeLocator:
    return RuntimeLocator(tmp_path / "repo", tmp_path / "state", "a" * 64)


def locator_pair(tmp_path: Path) -> tuple[RuntimeLocator, RuntimeLocator]:
    return (
        RuntimeLocator(tmp_path / "repo-a", tmp_path / "state-a", "a" * 64),
        RuntimeLocator(tmp_path / "repo-b", tmp_path / "state-b", "b" * 64),
    )


def test_rendered_unit_has_external_ownership_contract(tmp_path: Path) -> None:
    value = render_unit(locator(tmp_path), tmp_path / "repo" / ".env")

    assert MANAGED_MARKER in value
    assert "Type=notify" in value
    assert "NotifyAccess=main" in value
    assert "Environment=DEVLEGATE_HOST_MODE=external" in value
    assert "Environment=DEVLEGATE_REQUIRE_NOTIFY=1" in value
    assert "DEVLEGATE_RESTART" not in value
    assert "KillMode=mixed" in value
    assert "TimeoutStopSec=infinity" in value
    assert "Restart=no" in value
    assert "service.log" not in value


def test_exec_start_is_absolute_and_safe_path_protected(tmp_path: Path) -> None:
    value = render_unit(
        locator(tmp_path),
        tmp_path / "repo" / ".env",
        python_executable=Path("/opt/devlegate/bin/python"),
    )

    assert "ExecStart=/opt/devlegate/bin/python -P -m devlegate --env" in value
    assert (
        "--env " + str((tmp_path / "repo" / ".env").resolve()) + " foreground"
        in value
    )
    assert "--env \"/tmp" not in value
    assert "foreground --" not in value
    assert "WorkingDirectory=" + str(tmp_path / "repo") in value
    assert "DEVLEGATE_HOST_MODE=external" in value


def test_install_is_atomic_and_uses_user_manager(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    item = locator(tmp_path)
    path = tmp_path / "units"
    installed = SystemdSupervisor(unit_directory=path, runner=runner).install(
        item, tmp_path / ".env"
    )

    assert installed.is_file()
    assert installed.read_text().startswith(MANAGED_MARKER)
    assert calls == [["systemctl", "--user", "daemon-reload"]]


def test_two_project_units_and_operations_are_independent(tmp_path: Path) -> None:
    project_a, project_b = locator_pair(tmp_path)
    unit_directory = tmp_path / "units"
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "active", "")

    supervisor = SystemdSupervisor(unit_directory=unit_directory, runner=runner)
    supervisor.wait_ready = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    path_a = supervisor.install(project_a, tmp_path / "a.env")
    content_b_before = render_unit(project_b, tmp_path / "b.env")
    path_b = supervisor.install(project_b, tmp_path / "b.env")
    assert path_a != path_b
    assert path_a.name == f"devlegate-{project_a.state_key}.service"
    assert path_b.name == f"devlegate-{project_b.state_key}.service"
    assert path_a.read_text() != path_b.read_text()
    assert path_b.read_text() == content_b_before

    supervisor.start(project_a)
    supervisor.stop(project_a)
    supervisor.restart(project_a)
    assert supervisor.status(project_a)
    supervisor.start(project_b)
    supervisor.stop(project_b)
    supervisor.restart(project_b)
    assert supervisor.status(project_b)

    lifecycle = [call for call in calls if call[2] != "daemon-reload"]
    assert lifecycle == [
        ["systemctl", "--user", "start", path_a.name],
        ["systemctl", "--user", "stop", path_a.name],
        ["systemctl", "--user", "restart", path_a.name],
        ["systemctl", "--user", "is-active", path_a.name],
        ["systemctl", "--user", "start", path_b.name],
        ["systemctl", "--user", "stop", path_b.name],
        ["systemctl", "--user", "restart", path_b.name],
        ["systemctl", "--user", "is-active", path_b.name],
    ]

    content_a_before = path_a.read_text()
    supervisor.install(project_a, tmp_path / "a-updated.env")
    assert path_a.read_text() != content_a_before
    assert path_b.read_text() == content_b_before

    supervisor.remove(project_a)
    assert not path_a.exists()
    assert path_b.exists()
    assert path_b.read_text() == content_b_before


def test_remove_cannot_cross_project_provenance(tmp_path: Path) -> None:
    project_a, project_b = locator_pair(tmp_path)
    unit_directory = tmp_path / "units"
    path_a = unit_directory / unit_name(project_a)
    path_a.parent.mkdir()
    path_a.write_text(render_unit(project_b, tmp_path / "b.env"))

    with pytest.raises(SystemdSupervisorError, match="unmanaged"):
        SystemdSupervisor(unit_directory=unit_directory).remove(project_a)


def test_start_stop_restart_never_use_ipc_lifecycle_requests(tmp_path: Path) -> None:
    item = locator(tmp_path)
    calls: list[list[str]] = []

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    supervisor = SystemdSupervisor(runner=runner)
    supervisor.wait_ready = lambda *_args, **_kwargs: None  # type: ignore[method-assign]
    supervisor.start(item)
    supervisor.stop(item)
    supervisor.restart(item)

    assert calls == [
        ["systemctl", "--user", "start", unit_name(item)],
        ["systemctl", "--user", "stop", unit_name(item)],
        ["systemctl", "--user", "restart", unit_name(item)],
    ]


def test_systemctl_executable_failure_is_explicit(tmp_path: Path) -> None:
    def runner(_command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(
            _command, 1, "", "systemd user manager is not running"
        )

    with pytest.raises(SystemdSupervisorError, match="not running"):
        SystemdSupervisor(runner=runner).stop(locator(tmp_path))


def test_remove_refuses_unmanaged_unit(tmp_path: Path) -> None:
    item = locator(tmp_path)
    path = tmp_path / "units" / unit_name(item)
    path.parent.mkdir()
    path.write_text("[Service]\nExecStart=some-other-program\n")

    with pytest.raises(SystemdSupervisorError, match="unmanaged"):
        SystemdSupervisor(unit_directory=path.parent).remove(item)


def test_systemctl_failure_is_not_silenced_on_remove(tmp_path: Path) -> None:
    item = locator(tmp_path)
    path = tmp_path / "units" / unit_name(item)
    path.parent.mkdir()
    path.write_text(f"{MANAGED_MARKER}\n# state_key={item.state_key}\n")

    def runner(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "Failed to connect to bus")

    with pytest.raises(SystemdSupervisorError, match="connect to bus"):
        SystemdSupervisor(unit_directory=path.parent, runner=runner).remove(item)


def test_missing_notify_socket_fails_when_required(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    with pytest.raises(SystemdSupervisorError, match="NOTIFY_SOCKET"):
        notify_ready(required=True)


def test_malformed_notify_socket_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NOTIFY_SOCKET", "x" * 108)

    with pytest.raises(SystemdSupervisorError, match="malformed"):
        notify_ready(required=True)
