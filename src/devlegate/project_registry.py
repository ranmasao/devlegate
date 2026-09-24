# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""User-local aliases for explicitly addressable Devlegate projects."""

from __future__ import annotations

import fcntl
import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path

from devlegate.runtime_locator import RuntimeLocator, RuntimeLocatorError


class ProjectRegistryError(RuntimeError):
    """The local project registry or a project target is invalid."""


ALIAS_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
REGISTRY_VERSION = 1


def validate_alias(alias: str) -> str:
    if not ALIAS_PATTERN.fullmatch(alias):
        raise ProjectRegistryError(
            "project alias must contain only letters, digits, '-' or '_' "
            "and must not be empty"
        )
    return alias


def registry_path(environment: dict[str, str] | None = None) -> Path:
    values = os.environ if environment is None else environment
    config_home = Path(values.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return config_home / "devlegate" / "projects.json"


def canonical_env_path(path: Path) -> Path:
    return path.expanduser().resolve(strict=False)


@dataclass(frozen=True)
class ProjectTarget:
    alias: str
    env_file: Path
    repo: Path
    locator: RuntimeLocator


def _read_registry(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise ProjectRegistryError(
            f"cannot read project registry {path}: {error}"
        ) from error
    if not isinstance(value, dict) or value.get("version") != REGISTRY_VERSION:
        raise ProjectRegistryError(
            f"unsupported or corrupt project registry {path}"
        )
    projects = value.get("projects")
    if not isinstance(projects, dict):
        raise ProjectRegistryError(f"corrupt project registry {path}")
    result: dict[str, str] = {}
    for alias, entry in projects.items():
        if not isinstance(alias, str) or not isinstance(entry, dict):
            raise ProjectRegistryError(f"corrupt project registry {path}")
        validate_alias(alias)
        env = entry.get("env")
        if (
            not isinstance(env, str)
            or not env
            or not Path(env).is_absolute()
        ):
            raise ProjectRegistryError(f"corrupt project registry {path}")
        result[alias] = env
    return result


def _serialize(projects: dict[str, str]) -> str:
    return json.dumps(
        {
            "version": REGISTRY_VERSION,
            "projects": {
                alias: {"env": projects[alias]}
                for alias in sorted(projects)
            },
        },
        indent=2,
        sort_keys=True,
    ) + "\n"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(
        prefix=f".{path.name}.", suffix=".tmp", dir=path.parent
    )
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
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


class ProjectRegistry:
    """Read and mutate the registry under a stable sibling lock file."""

    def __init__(self, path: Path | None = None) -> None:
        self.path = path or registry_path()
        self.lock_path = self.path.with_name(f"{self.path.name}.lock")

    def projects(self) -> dict[str, str]:
        with self._lock(False):
            return _read_registry(self.path)

    def _lock(self, exclusive: bool):
        return _LockContext(self, exclusive)

    def register(self, alias: str, env_file: Path) -> ProjectTarget:
        validate_alias(alias)
        env = canonical_env_path(env_file)
        if not env.is_file():
            raise ProjectRegistryError(f"configuration file not found: {env}")
        repo, locator = _resolve_runtime(env)
        with self._lock(True):
            projects = _read_registry(self.path)
            existing = projects.get(alias)
            if existing is not None and canonical_env_path(Path(existing)) != env:
                raise ProjectRegistryError(
                    f"project alias @{alias} is already registered for {existing}"
                )
            for registered_alias, registered_env in projects.items():
                if (
                    registered_alias != alias
                    and canonical_env_path(Path(registered_env)) == env
                ):
                    raise ProjectRegistryError(
                        f"project is already registered as @{registered_alias}"
                    )
            projects[alias] = str(env)
            _atomic_write(self.path, _serialize(projects))
        return ProjectTarget(alias, env, repo, locator)

    def rename(self, old: str, new: str) -> None:
        validate_alias(old)
        validate_alias(new)
        with self._lock(True):
            projects = _read_registry(self.path)
            if old not in projects:
                raise ProjectRegistryError(f"unknown project alias @{old}")
            if new in projects:
                raise ProjectRegistryError(
                    f"project alias @{new} is already registered"
                )
            projects[new] = projects.pop(old)
            _atomic_write(self.path, _serialize(projects))

    def target_for_alias(self, alias: str) -> ProjectTarget:
        validate_alias(alias)
        with self._lock(False):
            projects = _read_registry(self.path)
            env_value = projects.get(alias)
        if env_value is None:
            raise ProjectRegistryError(f"unknown project alias @{alias}")
        env = canonical_env_path(Path(env_value))
        if not env.is_file():
            raise ProjectRegistryError(
                f"project alias @{alias} points to missing configuration: {env}"
            )
        repo, locator = _resolve_runtime(env)
        return ProjectTarget(alias, env, repo, locator)

    def alias_for_env(self, env_file: Path) -> str | None:
        env = canonical_env_path(env_file)
        with self._lock(False):
            projects = _read_registry(self.path)
        for alias, registered_env in projects.items():
            if canonical_env_path(Path(registered_env)) == env:
                return alias
        return None


class _LockContext:
    def __init__(self, registry: ProjectRegistry, exclusive: bool) -> None:
        self.registry = registry
        self.exclusive = exclusive
        self.handle = None

    def __enter__(self):
        self.registry.lock_path.parent.mkdir(parents=True, exist_ok=True)
        self.handle = self.registry.lock_path.open("a+")
        fcntl.flock(
            self.handle.fileno(),
            fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH,
        )
        return self.handle

    def __exit__(self, _type, _value, _traceback):
        assert self.handle is not None
        fcntl.flock(self.handle.fileno(), fcntl.LOCK_UN)
        self.handle.close()


def _resolve_runtime(env_file: Path) -> tuple[Path, RuntimeLocator]:
    try:
        repo = repository_root_for_env(env_file)
        locator = RuntimeLocator.from_env(env_file, repository=repo)
    except RuntimeLocatorError as error:
        raise ProjectRegistryError(str(error)) from error
    return repo, locator


def repository_root_for_env(env_file: Path) -> Path:
    from devlegate.runtime_locator import repository_root

    return repository_root(env_file.parent)
