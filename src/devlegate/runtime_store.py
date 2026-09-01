"""Durable local operational state for the foreground runtime."""

from __future__ import annotations

import json
import sqlite3
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


class SQLiteRuntimeStore:
    """Persist one opaque runtime state payload in a project-local database."""

    schema_version = 1

    def __init__(self, state_dir: Path, state_key: str) -> None:
        self.state_dir = state_dir
        self._path = state_dir / f"{state_key}.sqlite3"
        self._state_key = state_key

    @property
    def path(self) -> Path:
        return self._path

    def _connect(self, *, read_only: bool) -> sqlite3.Connection:
        try:
            if read_only:
                connection = sqlite3.connect(
                    f"file:{self._path}?mode=ro", uri=True, timeout=5
                )
            else:
                self.state_dir.mkdir(parents=True, exist_ok=True)
                connection = sqlite3.connect(self._path, timeout=5)
        except sqlite3.Error as error:
            raise RuntimeStoreError(
                f"cannot open runtime database {self._path}: {error}"
            ) from error
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        if not read_only:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
        return connection

    @staticmethod
    def _validate_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version != SQLiteRuntimeStore.schema_version:
            raise RuntimeStoreError(
                f"unsupported runtime database schema version: {version}"
            )
        table = connection.execute(
            "SELECT name FROM sqlite_master "
            "WHERE type = 'table' AND name = 'runtime_state'"
        ).fetchone()
        if table is None:
            raise RuntimeStoreError("runtime database schema is incomplete")

    @staticmethod
    def _initialize_schema(connection: sqlite3.Connection) -> None:
        version = connection.execute("PRAGMA user_version").fetchone()[0]
        if version > SQLiteRuntimeStore.schema_version:
            raise RuntimeStoreError(
                f"unsupported runtime database schema version: {version}"
            )
        if version == 0:
            connection.execute(
                "CREATE TABLE IF NOT EXISTS runtime_state ("
                "id INTEGER PRIMARY KEY CHECK (id = 1), "
                "payload TEXT NOT NULL, "
                "revision INTEGER NOT NULL"
                ")"
            )
            connection.execute(
                f"PRAGMA user_version = {SQLiteRuntimeStore.schema_version}"
            )
        SQLiteRuntimeStore._validate_schema(connection)

    def probe(self) -> None:
        connection = None
        try:
            connection = self._connect(read_only=False)
            self._initialize_schema(connection)
            connection.commit()
        except (OSError, sqlite3.Error, RuntimeStoreError) as error:
            if isinstance(error, RuntimeStoreError):
                raise
            raise RuntimeStoreError(
                f"cannot initialize runtime database {self._path}: {error}"
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def _read(self) -> tuple[object, int] | None:
        if not self._path.exists():
            return None
        connection = None
        try:
            connection = self._connect(read_only=True)
            self._validate_schema(connection)
            row = connection.execute(
                "SELECT payload, revision FROM runtime_state WHERE id = 1"
            ).fetchone()
            if row is None:
                return None
            payload, revision = row
            if (
                not isinstance(payload, str)
                or not isinstance(revision, int)
                or revision < 1
            ):
                raise RuntimeStoreError("runtime database state row is invalid")
            try:
                state = json.loads(payload)
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RuntimeStoreError(
                    f"cannot decode runtime state payload: {error}"
                ) from error
            if not isinstance(state, dict):
                raise RuntimeStoreError("runtime state payload is not an object")
            return state, revision
        except RuntimeStoreError:
            raise
        except (OSError, sqlite3.Error) as error:
            raise RuntimeStoreError(
                f"cannot read runtime database {self._path}: {error}"
            ) from error
        finally:
            if connection is not None:
                connection.close()

    def load(self) -> object:
        result = self._read()
        return {"phase": "idle"} if result is None else result[0]

    def observe(self) -> tuple[object, str]:
        result = self._read()
        if result is None:
            return {"phase": "idle"}, "missing"
        state, revision = result
        return state, f"sqlite:{revision}"

    def _commit(self, connection: sqlite3.Connection) -> None:
        connection.commit()

    def replace(self, state: dict[str, object]) -> None:
        payload = json.dumps(state, sort_keys=True)
        connection = None
        try:
            connection = self._connect(read_only=False)
            self._initialize_schema(connection)
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT revision FROM runtime_state WHERE id = 1"
            ).fetchone()
            revision = 1 if row is None else row[0] + 1
            if row is None:
                connection.execute(
                    "INSERT INTO runtime_state (id, payload, revision) "
                    "VALUES (1, ?, ?)",
                    (payload, revision),
                )
            else:
                connection.execute(
                    "UPDATE runtime_state SET payload = ?, revision = ? WHERE id = 1",
                    (payload, revision),
                )
            self._commit(connection)
        except Exception as error:
            if connection is not None:
                connection.rollback()
            if isinstance(error, RuntimeStoreError):
                raise
            raise RuntimeStoreError(
                f"cannot write runtime database {self._path}: {error}"
            ) from error
        finally:
            if connection is not None:
                connection.close()
