# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import sqlite3

import pytest

from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore


def test_sqlite_store_missing_state_is_idle_without_creating_database(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    (store.state_dir / "checkout.json").write_text('{"phase":"agent_running"}\n')

    assert store.load() == {"phase": "idle"}
    assert store.observe() == ({"phase": "idle"}, "missing")
    assert not store.path.exists()


def test_sqlite_store_round_trip_and_revisions(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.probe()
    assert store.path.suffix == ".sqlite3"

    store.replace({"phase": "idle", "handled_remote_head": "abc"})
    assert store.load()["handled_remote_head"] == "abc"
    assert store.observe()[1] == "sqlite:1"
    assert store.observe()[1] == "sqlite:1"
    store.replace({"phase": "idle", "handled_remote_head": "abc"})
    assert store.observe()[1] == "sqlite:2"


def test_sqlite_store_configures_schema_and_pragmas(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.probe()
    connection = store._connect(read_only=False)
    assert connection.execute("PRAGMA user_version").fetchone()[0] == 2
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'runtime_state'"
    ).fetchone() == ("runtime_state",)
    assert connection.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' "
        "AND name = 'supervision_authority'"
    ).fetchone() == ("supervision_authority",)
    assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
    assert connection.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert connection.execute("PRAGMA synchronous").fetchone()[0] == 2
    connection.close()


def test_sqlite_store_enforces_singleton_row(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.replace({"phase": "idle"})
    connection = sqlite3.connect(store.path)
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO runtime_state (id, payload, revision) VALUES (2, '{}', 2)"
        )
    connection.close()


def test_sqlite_store_two_instances_see_committed_updates(tmp_path):
    first = SQLiteRuntimeStore(tmp_path, "checkout")
    second = SQLiteRuntimeStore(tmp_path, "checkout")
    first.replace({"phase": "merge_pending"})

    assert second.load() == {"phase": "merge_pending"}
    before = second.observe()[1]
    first.replace({"phase": "agent_pending"})
    assert second.load() == {"phase": "agent_pending"}
    assert second.observe()[1] != before


def test_sqlite_store_rejects_malformed_payload(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.probe()
    connection = sqlite3.connect(store.path)
    connection.execute(
        "INSERT INTO runtime_state (id, payload, revision) VALUES (1, ?, 1)",
        ("not json",),
    )
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeStoreError):
        store.load()
    with pytest.raises(RuntimeStoreError):
        store.observe()


def test_sqlite_store_failed_commit_keeps_previous_state(tmp_path, monkeypatch):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.replace({"phase": "idle", "revision": "old"})
    monkeypatch.setattr(store, "_commit", lambda _connection: (_ for _ in ()).throw(
        RuntimeError("forced commit failure")
    ))

    with pytest.raises(RuntimeStoreError):
        store.replace({"phase": "idle", "revision": "new"})

    fresh = SQLiteRuntimeStore(tmp_path, "checkout")
    assert fresh.load() == {"phase": "idle", "revision": "old"}
    assert fresh.observe()[1] == "sqlite:1"


def test_sqlite_store_rejects_unsupported_schema_version(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.probe()
    connection = sqlite3.connect(store.path)
    connection.execute("PRAGMA user_version = 3")
    connection.commit()
    connection.close()

    with pytest.raises(RuntimeStoreError, match="unsupported"):
        store.load()


def test_sqlite_store_persists_only_systemd_authority(tmp_path):
    store = SQLiteRuntimeStore(tmp_path, "checkout")
    store.establish_systemd_authority(
        unit_name="devlegate-unit.service",
        state_key="a" * 64,
        env_file=tmp_path / "repo" / ".env",
        repository=tmp_path / "repo",
    )

    assert store.supervision_authority() == {
        "authority": "systemd",
        "unit_name": "devlegate-unit.service",
        "state_key": "a" * 64,
        "env_file": str(tmp_path / "repo" / ".env"),
        "repository": str(tmp_path / "repo"),
    }
    store.clear_systemd_authority()
    assert store.supervision_authority() is None
