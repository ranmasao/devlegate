import json
from pathlib import Path

import pytest

from devlegate.runtime_store import FileRuntimeStore, RuntimeStoreError


def test_file_store_reads_existing_runtime_json(tmp_path):
    store = FileRuntimeStore(tmp_path, "checkout")
    store.path.write_text('{"phase":"agent_running","execution_id":"e-1"}\n')

    assert store.load() == {"phase": "agent_running", "execution_id": "e-1"}
    observed, fingerprint = store.observe()
    assert observed == store.load()
    assert fingerprint != "missing"


def test_file_store_missing_state_is_idle(tmp_path):
    store = FileRuntimeStore(tmp_path, "checkout")

    assert store.load() == {"phase": "idle"}
    assert store.observe() == ({"phase": "idle"}, "missing")


def test_file_store_rejects_malformed_json(tmp_path):
    store = FileRuntimeStore(tmp_path, "checkout")
    store.path.write_text("not json\n")

    with pytest.raises(RuntimeStoreError):
        store.load()
    with pytest.raises(RuntimeStoreError):
        store.observe()


def test_file_store_replaces_state_atomically(tmp_path, monkeypatch):
    store = FileRuntimeStore(tmp_path, "checkout")
    store.replace({"phase": "idle", "handled_remote_head": "abc"})
    replacements = []
    original_replace = __import__("os").replace

    def replace(source, destination):
        replacements.append((source, destination))
        source_path = Path(source)
        assert source_path.parent == tmp_path
        assert source_path.name.endswith(".tmp")
        assert source_path.exists()
        original_replace(source, destination)

    monkeypatch.setattr("devlegate.runtime_store.os.replace", replace)
    state = {"phase": "merge_pending", "remote_head": "def"}
    store.replace(state)

    assert len(replacements) == 1
    assert Path(replacements[0][1]) == store.path
    assert json.loads(store.path.read_text()) == state
    assert not list(tmp_path.glob("*.tmp"))
