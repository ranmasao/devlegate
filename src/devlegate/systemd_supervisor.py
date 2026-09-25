# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Systemd user-service backend for one Devlegate project runtime."""

from __future__ import annotations

import os
import re
import socket
import subprocess
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from devlegate.ipc_client import IPCClientError, request
from devlegate.launcher import LaunchCommand, product_launcher
from devlegate.runtime_locator import RuntimeLocator


class SystemdSupervisorError(RuntimeError):
    """A systemd user-service operation could not be completed safely."""


MANAGED_MARKER = "# Managed by Devlegate systemd backend"


def user_unit_dir(environment: dict[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    config_home = Path(values.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "systemd" / "user"


def unit_name(locator: RuntimeLocator) -> str:
    return f"devlegate-{locator.state_key}.service"


def unit_path(locator: RuntimeLocator, directory: Path | None = None) -> Path:
    return (user_unit_dir() if directory is None else directory) / unit_name(locator)


def _systemd_quote(value: str) -> str:
    if value and all(
        character.isalnum() or character in "_+-./:" for character in value
    ):
        return value
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\t", "\\t")
    return f'"{escaped}"'


def _systemd_unit_value(value: str) -> str:
    return _systemd_quote(value.replace("%", "%%"))


def _systemd_exec_argument(value: str) -> str:
    return _systemd_quote(value.replace("%", "%%").replace("$", "$$"))


def render_unit(
    locator: RuntimeLocator,
    env_file: Path,
    *,
    launcher: LaunchCommand | None = None,
) -> str:
    repository = locator.repo.resolve()
    environment = "DEVLEGATE_HOST_MODE=external"
    require_notify = "DEVLEGATE_REQUIRE_NOTIFY=1"
    selected_launcher = launcher or product_launcher()
    command = " ".join(
        _systemd_exec_argument(argument)
        for argument in selected_launcher.argv(
            "--env", str(env_file.resolve()), "foreground"
        )
    )
    return "\n".join(
        (
            MANAGED_MARKER,
            f"# state_key={locator.state_key}",
            "[Unit]",
            f"Description=Devlegate project {locator.state_key}",
            "After=default.target",
            "",
            "[Service]",
            "Type=notify",
            "NotifyAccess=main",
            f"WorkingDirectory={_systemd_unit_value(str(repository))}",
            f"ExecStart={command}",
            f"Environment={environment}",
            f"Environment={require_notify}",
            "StandardOutput=journal",
            "StandardError=journal",
            "KillMode=mixed",
            "TimeoutStopSec=infinity",
            "Restart=no",
            "",
            "[Install]",
            "WantedBy=default.target",
            "",
        )
    )


def _write_atomic(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o644)
        os.replace(temporary, path)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class SystemdSupervisor:
    """Own systemd user-manager commands for one project unit."""

    def __init__(
        self,
        *,
        unit_directory: Path | None = None,
        runner: Callable[..., subprocess.CompletedProcess[str]] | None = None,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        launcher: LaunchCommand | None = None,
    ) -> None:
        self.unit_directory = unit_directory or user_unit_dir()
        self._runner = runner or subprocess.run
        self._sleep = sleep
        self._monotonic = monotonic
        self.launcher = launcher or product_launcher()

    def _run(
        self, *arguments: str, allow_failure: bool = False
    ) -> subprocess.CompletedProcess[str]:
        command = ["systemctl", "--user", *arguments]
        try:
            result = self._runner(
                command, text=True, capture_output=True, check=False, timeout=5
            )
        except OSError as error:
            raise SystemdSupervisorError(
                f"systemd user manager is unavailable: {error}"
            ) from error
        except subprocess.TimeoutExpired as error:
            raise SystemdSupervisorError(
                "systemd user manager did not respond"
            ) from error
        if result.returncode and allow_failure:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            if not any(
                phrase in detail.lower()
                for phrase in (
                    "not loaded",
                    "not enabled",
                    "not found",
                    "does not exist",
                    "inactive",
                )
            ):
                raise SystemdSupervisorError(
                    f"systemd command failed ({' '.join(command)}): {detail}"
                )
        elif result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "unknown error"
            raise SystemdSupervisorError(
                f"systemd command failed ({' '.join(command)}): {detail}"
            )
        return result

    def install(self, locator: RuntimeLocator, env_file: Path) -> Path:
        path = unit_path(locator, self.unit_directory)
        if path.exists():
            self.inspect(locator)
        self.probe_user_manager()
        try:
            _write_atomic(path, render_unit(locator, env_file, launcher=self.launcher))
        except OSError as error:
            raise SystemdSupervisorError(
                f"cannot write systemd unit {path}: {error}"
            ) from error
        self._run("daemon-reload")
        return path

    def probe_user_manager(self) -> None:
        result = self._run("show-environment")
        manager_environment = {
            line.partition("=")[0]: line.partition("=")[2]
            for line in result.stdout.splitlines()
            if "=" in line
        }
        requested_config = user_unit_dir().parents[1]
        manager_config = manager_environment.get("XDG_CONFIG_HOME")
        if manager_config is not None and Path(manager_config) != requested_config:
            raise SystemdSupervisorError(
                "systemd user manager uses a different XDG_CONFIG_HOME"
            )

    def inspect(self, locator: RuntimeLocator, *, env_file: Path | None = None) -> bool:
        """Verify and report the exact managed unit registration."""
        path = unit_path(locator, self.unit_directory)
        if not path.exists():
            return False
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise SystemdSupervisorError(
                f"cannot read systemd unit {path}: {error}"
            ) from error
        if (
            MANAGED_MARKER not in content
            or f"# state_key={locator.state_key}" not in content
        ):
            raise SystemdSupervisorError(
                f"refusing to operate on unmanaged systemd unit {path}"
            )
        if env_file is not None and content != render_unit(
            locator, env_file, launcher=self.launcher
        ):
            raise SystemdSupervisorError(
                f"refusing to operate on systemd unit with unexpected identity {path}"
            )
        return True

    def remove(self, locator: RuntimeLocator, *, env_file: Path | None = None) -> Path:
        path = unit_path(locator, self.unit_directory)
        if path.exists():
            self.inspect(locator, env_file=env_file)
        self.probe_user_manager()
        if path.exists():
            self._run("stop", path.name, allow_failure=True)
            self._run("disable", path.name, allow_failure=True)
            try:
                path.unlink()
            except OSError as error:
                raise SystemdSupervisorError(
                    f"cannot remove systemd unit {path}: {error}"
                ) from error
        self._run("daemon-reload")
        return path

    def start(self, locator: RuntimeLocator, *, timeout: float = 15) -> None:
        self.probe_user_manager()
        self._run("start", unit_name(locator))
        self.wait_ready(locator, timeout=timeout)

    def stop(self, locator: RuntimeLocator) -> None:
        self.probe_user_manager()
        self._run("stop", unit_name(locator))

    def restart(self, locator: RuntimeLocator, *, timeout: float = 15) -> None:
        self.probe_user_manager()
        self._run("restart", unit_name(locator))
        self.wait_ready(locator, timeout=timeout)

    def status(self, locator: RuntimeLocator) -> bool:
        self.probe_user_manager()
        result = self._run("is-active", unit_name(locator), allow_failure=True)
        return result.returncode == 0

    def wait_ready(self, locator: RuntimeLocator, *, timeout: float = 15) -> None:
        deadline = self._monotonic() + timeout
        last_error = "service did not become ready"
        while self._monotonic() < deadline:
            if locator.daemon_authority_present():
                try:
                    response = request(locator.socket_path, "ping", timeout=0.5)
                except IPCClientError as error:
                    last_error = str(error)
                else:
                    if (
                        response.get("service") == "devlegate"
                        and response.get("ready") is True
                    ):
                        return
            self._sleep(0.05)
        raise SystemdSupervisorError(
            f"systemd service did not become ready: {last_error}"
        )


def managed_unit_paths(directory: Path | None = None) -> list[Path]:
    root = user_unit_dir() if directory is None else directory
    if not root.is_dir():
        return []
    paths = []
    for path in sorted(root.glob("devlegate-*.service")):
        try:
            content = path.read_text(encoding="utf-8")
        except OSError as error:
            raise SystemdSupervisorError(
                f"cannot read systemd unit {path}: {error}"
            ) from error
        if MANAGED_MARKER in content and re.search(
            r"^# state_key=[0-9a-f]{64}$", content, re.MULTILINE
        ):
            paths.append(path)
    return paths


def notify_ready(*, required: bool = False) -> None:
    """Send the minimal systemd READY datagram when external hosting requires it."""
    address = os.environ.get("NOTIFY_SOCKET")
    if not address:
        if required:
            raise SystemdSupervisorError(
                "NOTIFY_SOCKET is required for systemd readiness"
            )
        return
    if "\x00" in address or len(address.encode()) > 107:
        raise SystemdSupervisorError("NOTIFY_SOCKET is malformed")
    target = ("\0" + address[1:]) if address.startswith("@") else address
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as channel:
            channel.sendto(b"READY=1\n", target)
    except OSError as error:
        raise SystemdSupervisorError(
            f"systemd readiness notification failed: {error}"
        ) from error
