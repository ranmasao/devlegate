# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import subprocess

import pytest

from devlegate.change_explorer import ImmutableExplorer, ObservationError
from devlegate.change_revisions import Checkpoint, RevisionNamespace


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def comparison(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "file").write_text("after\n")
    for number in range(35):
        (tmp_path / f"added-{number:02}").write_text("x\n")
    (tmp_path / "binary").write_bytes(b"\0a")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "checkpoint")
    head = git(tmp_path, "rev-parse", "HEAD")
    return ImmutableExplorer(
        tmp_path, RevisionNamespace(base, (Checkpoint(head, base),)), b"x" * 32
    )


def test_inventory_and_single_file_diff(comparison):
    first = comparison.changes("BASE", "HEAD")
    second = comparison.changes("BASE", "HEAD", first["next_cursor"])
    records = first["records"] + second["records"]
    assert len(records) == 37
    assert len({item["path"] for item in records}) == 37
    assert "-before\n" not in str(records)
    result = comparison.diff("BASE", "HEAD", "file")
    assert "-before\n" in result["lines"]
    assert "+after\n" in result["lines"]
    assert result["complete"]
    assert comparison.revisions()["checkpoint_count"] == 1


@pytest.mark.parametrize("path", ["../file", "/file", "*", "file/..", ""])
def test_rejects_unsafe_paths(comparison, path):
    with pytest.raises(ObservationError, match="INVALID_PATH"):
        comparison.diff("BASE", "HEAD", path)


def test_unavailable_states_and_cursor_authentication(comparison):
    with pytest.raises(ObservationError, match="BINARY_DIFF_UNAVAILABLE"):
        comparison.diff("BASE", "HEAD", "binary")
    with pytest.raises(ObservationError, match="WORKTREE_UNAVAILABLE"):
        comparison.changes("HEAD", "WORKTREE")
    cursor = comparison.changes("BASE", "HEAD")["next_cursor"]
    with pytest.raises(ObservationError, match="INVALID_CURSOR"):
        comparison.changes(
            "BASE", "HEAD", cursor[:-1] + ("0" if cursor[-1] != "0" else "1")
        )


def test_submodule_and_symlink_are_not_dereferenced(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "regular").write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "link").symlink_to("/outside/secret")
    git(tmp_path, "add", "link")
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{base},module")
    git(tmp_path, "commit", "-qm", "add entries")
    head = git(tmp_path, "rev-parse", "HEAD")
    explorer = ImmutableExplorer(
        tmp_path, RevisionNamespace(base, (Checkpoint(head, base),)), b"x" * 32
    )
    entries = {
        entry["path"]: entry for entry in explorer.changes("BASE", "HEAD")["records"]
    }
    assert entries["module"]["kind"] == "submodule"
    assert entries["link"]["kind"] == "symlink"
    assert explorer.diff("BASE", "HEAD", "module")["status"] == (
        "SUBMODULE_CONTENT_UNAVAILABLE"
    )
    with pytest.raises(ObservationError, match="INVALID_PATH"):
        explorer.diff("BASE", "HEAD", "module/file")
    assert "+/outside/secret\n" in explorer.diff("BASE", "HEAD", "link")["lines"]


def test_inventory_pages_bound_serialized_metadata(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "commit", "--allow-empty", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    directory = tmp_path
    for _ in range(8):
        directory = directory / ("long" * 45)
        directory.mkdir()
    for number in range(20):
        (directory / f"{number:02}").write_text("value\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "checkpoint")
    head = git(tmp_path, "rev-parse", "HEAD")
    explorer = ImmutableExplorer(
        tmp_path, RevisionNamespace(base, (Checkpoint(head, base),)), b"x" * 32
    )
    cursor = None
    paths = []
    while True:
        page = explorer.changes("BASE", "HEAD", cursor)
        encoded = json.dumps(page, ensure_ascii=True, separators=(",", ":")).encode()
        assert len(encoded) <= 8192
        paths.extend(record["path"] for record in page["records"])
        if not page["has_more"]:
            break
        cursor = page["next_cursor"]
    assert len(paths) == len(set(paths)) == 20
