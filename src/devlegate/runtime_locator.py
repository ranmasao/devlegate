"""Deterministic project runtime paths and read-only authority guards."""

from __future__ import annotations

import contextlib
import fcntl
import hashlib
import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Iterator


class RuntimeLocatorError(Exception):
    """A project runtime identity or authority probe could not be resolved."""


class RuntimeAuthorityPresent(RuntimeLocatorError):
    """The project runtime lock is held by a mutation authority."""


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise RuntimeLocatorError(
            f"cannot read configuration file: {path}: {error}"
        ) from error
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip().replace("_", "a").isalnum():
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name.strip()] = value
    return values


def repository_root(cwd: Path | None = None) -> Path:
    directory = (cwd or Path.cwd()).resolve()
    result = subprocess.run(
        ["git", "-C", str(directory), "rev-parse", "--show-toplevel"],
        text=True,
        capture_output=True,
    )
    if result.returncode:
        raise RuntimeLocatorError(
            f"current directory is not a git repository: {directory}"
        )
    return Path(result.stdout.strip()).resolve()


@dataclass(frozen=True)
class RuntimeLocator:
    """Project-local runtime identity without loading persisted runtime state."""

    repo: Path
    state_dir: Path
    state_key: str
    socket_key_length: int = 32

    @classmethod
    def from_env(cls, env_file: Path) -> "RuntimeLocator":
        if not env_file.is_file():
            raise RuntimeLocatorError(
                f"configuration file not found: {env_file} "
                f"(copy devlegate's .env.example to $PWD/.env)"
            )
        repo = repository_root()
        if Path.cwd().resolve() != repo:
            raise RuntimeLocatorError(f"run devlegate from repository root: {repo}")
        return cls.from_config(env_file, repo, read_env(env_file))

    @classmethod
    def from_config(
        cls, env_file: Path, repo: Path, config: dict[str, str]
    ) -> "RuntimeLocator":
        del env_file
        state_default = (
            Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")))
            / "devlegate"
        )
        state_dir = (
            Path(config.get("STATE_DIR", os.environ.get("STATE_DIR", state_default)))
            .expanduser()
            .resolve()
        )
        state_key = hashlib.sha256(str(repo).encode()).hexdigest()
        return cls(repo, state_dir, state_key)

    @property
    def lock_path(self) -> Path:
        return self.state_dir / "locks" / f"{self.state_key}.lock"

    @property
    def socket_path(self) -> Path:
        socket_key = self.state_key[: self.socket_key_length]
        path = self.state_dir / "sockets" / f"{socket_key}.sock"
        if len(str(path).encode()) <= 107:
            return path
        fallback_directory = hashlib.sha256(
            str(self.state_dir).encode()
        ).hexdigest()[:8]
        return Path("/tmp") / (
            f".devlegate-sockets-{os.getuid()}-{fallback_directory}"
        ) / f"{socket_key}.sock"

    @contextlib.contextmanager
    def absence_guard(self) -> Iterator[object]:
        """Hold a shared lock while a direct read-only observation completes."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError as error:
                raise RuntimeAuthorityPresent(
                    "service is running but its IPC endpoint is unavailable"
                ) from error
            yield handle
        finally:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            finally:
                handle.close()

    def daemon_authority_present(self) -> bool:
        """Observe exclusive daemon authority without retaining a lock."""
        self.lock_path.parent.mkdir(parents=True, exist_ok=True)
        handle = self.lock_path.open("a+")
        try:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_SH | fcntl.LOCK_NB)
            except BlockingIOError:
                return True
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
            return False
        finally:
            handle.close()
