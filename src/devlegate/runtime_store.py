"""Durable local operational state for the foreground runtime."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Protocol


class RuntimeStoreError(Exception):
    """The local runtime state could not be accessed."""


class RuntimeStore(Protocol):
    """The narrow persistence boundary used by runtime orchestration."""

    @property
    def path(self) -> Path: ...

    def probe(self) -> None: ...

    def load(self) -> object: ...

    def observe(self) -> tuple[object, str]: ...

    def replace(self, state: dict[str, object]) -> None: ...


class FileRuntimeStore:
    """Read and atomically replace the existing JSON runtime state file."""

    def __init__(self, state_dir: Path, state_key: str) -> None:
        self.state_dir = state_dir
        self._path = state_dir / f"{state_key}.json"
        self._state_key = state_key

    @property
    def path(self) -> Path:
        return self._path

    def probe(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.state_dir,
            prefix=f".{self._state_key}.access.",
            delete=True,
        ):
            pass

    def load(self) -> object:
        try:
            with self._path.open() as state_file:
                return json.load(state_file)
        except FileNotFoundError:
            return {"phase": "idle"}
        except (OSError, json.JSONDecodeError) as error:
            raise RuntimeStoreError(
                f"cannot read state file {self._path}: {error}"
            ) from error

    def observe(self) -> tuple[object, str]:
        try:
            raw = self._path.read_bytes()
        except FileNotFoundError:
            return {"phase": "idle"}, "missing"
        except OSError as error:
            raise RuntimeStoreError(
                f"cannot read state file {self._path}: {error}"
            ) from error
        try:
            state = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeStoreError(
                f"cannot read state file {self._path}: {error}"
            ) from error
        return state, hashlib.sha256(raw).hexdigest()

    def replace(self, state: dict[str, object]) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.state_dir,
                prefix=f".{self._state_key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(state, temporary)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._path)
            directory_fd = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
            raise RuntimeStoreError(
                f"cannot write state file {self._path}: {error}"
            ) from error
