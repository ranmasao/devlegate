# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Per-user Devlegate host installation policy."""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path


class HostInstallationError(RuntimeError):
    """The host installation record is invalid or cannot be changed safely."""


INSTALLATION_VERSION = 1
SUPERVISORS = ("internal", "systemd")


def installation_path(environment: dict[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    config_home = Path(values.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "devlegate" / "installation.json"


@dataclass(frozen=True)
class HostInstallation:
    supervisor: str


def _decode(path: Path) -> HostInstallation:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise HostInstallationError(
            f"cannot read host installation {path}: {error}"
        ) from error
    if (
        not isinstance(value, dict)
        or not isinstance(value.get("version"), int)
        or isinstance(value.get("version"), bool)
        or value.get("version") != INSTALLATION_VERSION
        or set(value) != {"version", "supervisor"}
        or value.get("supervisor") not in SUPERVISORS
    ):
        raise HostInstallationError(f"corrupt or unsupported host installation {path}")
    return HostInstallation(value["supervisor"])


def _read(path: Path) -> HostInstallation | None:
    if not path.exists():
        return None
    return _decode(path)


def _atomic_write(path: Path, installation: HostInstallation) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(
                {
                    "version": INSTALLATION_VERSION,
                    "supervisor": installation.supervisor,
                },
                handle,
                indent=2,
                sort_keys=True,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        try:
            os.unlink(temporary)
        except OSError:
            pass
        raise


class InstallationLock:
    def __init__(self, path: Path, exclusive: bool) -> None:
        self.path = path
        self.exclusive = exclusive
        self.handle = None

    def __enter__(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.path.open("a+")
        fcntl.flock(
            self.handle.fileno(),
            fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH,
        )
        return self.handle

    def __exit__(self, _type, _value, _traceback):
        assert self.handle is not None
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def lock(*, exclusive: bool, path: Path | None = None) -> InstallationLock:
    record = path or installation_path()
    return InstallationLock(record.with_name(f"{record.name}.lock"), exclusive)


def read(path: Path | None = None) -> HostInstallation | None:
    record = path or installation_path()
    with lock(exclusive=False, path=record):
        return _read(record)


def read_locked(path: Path | None = None) -> HostInstallation | None:
    return _read(path or installation_path())


def write(path: Path, installation: HostInstallation) -> None:
    if installation.supervisor not in SUPERVISORS:
        raise HostInstallationError(
            f"unsupported host supervisor: {installation.supervisor}"
        )
    try:
        _atomic_write(path, installation)
    except OSError as error:
        raise HostInstallationError(
            f"cannot write host installation {path}: {error}"
        ) from error


def remove(path: Path) -> None:
    try:
        path.unlink()
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except FileNotFoundError:
        return
    except OSError as error:
        raise HostInstallationError(
            f"cannot remove host installation {path}: {error}"
        ) from error
