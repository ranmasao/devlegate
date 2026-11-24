# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import subprocess

import pytest

import devlegate.change_explorer as change_explorer
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
    assert comparison.changes("HEAD", "WORKTREE")["records"] == []
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


def test_worktree_index_untracked_and_live_cursor(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file").write_text("base\n")
    (tmp_path / ".gitignore").write_text("ignored\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "file").write_text("staged\n")
    git(tmp_path, "add", "file")
    (tmp_path / "file").write_text("unstaged\n")
    (tmp_path / "ignored").write_text("ignored\n")
    for number in range(35):
        (tmp_path / f"new-{number:02}").write_text("new\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    first = explorer.changes("HEAD", "WORKTREE")
    second = explorer.changes("HEAD", "WORKTREE", first["next_cursor"])
    paths = {item["path"] for item in first["records"] + second["records"]}
    assert len(paths) == 36 and "ignored" not in paths
    diff = explorer.diff("BASE", "WORKTREE", "file")
    assert "-base\n" in diff["lines"] and "+unstaged\n" in diff["lines"]
    assert "+new\n" in explorer.diff("HEAD", "WORKTREE", "new-00")["lines"]
    (tmp_path / "file").write_text("base\n")
    assert explorer.diff("HEAD", "WORKTREE", "file")["lines"] == []
    reverted = explorer.changes("HEAD", "WORKTREE")
    assert "file" not in {r["path"] for r in reverted["records"]}
    (tmp_path / "file").write_text("changed!\n")
    continued = explorer.changes("HEAD", "WORKTREE", first["next_cursor"])
    assert first["live"] and continued["live"]
    assert "best-effort" in continued["observation"]


def test_worktree_does_not_follow_symlinked_parent(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    git(repo, "config", "user.email", "test@example.invalid")
    git(repo, "config", "user.name", "Test")
    (repo / "directory").mkdir()
    (repo / "directory" / "file").write_text("tracked\n")
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "base")
    base = git(repo, "rev-parse", "HEAD")
    (repo / "directory" / "file").unlink()
    (repo / "directory").rmdir()
    (repo / "directory").symlink_to(tmp_path, target_is_directory=True)
    (tmp_path / "file").write_text("outside secret\n")
    explorer = ImmutableExplorer(repo, RevisionNamespace(base, ()), b"x" * 32)
    with pytest.raises(ObservationError, match="WORKTREE_UNAVAILABLE"):
        explorer.diff("BASE", "WORKTREE", "directory/file")


def test_worktree_gitlink_has_explicit_unknown_checkout_status(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "commit", "--allow-empty", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{base},module")
    (tmp_path / "module").mkdir()
    (tmp_path / "module" / "nested").write_text("do not expose\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    records = explorer.changes("HEAD", "WORKTREE")["records"]
    assert len(records) == 1 and records[0]["path"] == "module"
    assert records[0]["kind"] == "submodule"
    assert records[0]["checkout_status"] == "uninitialized_or_absent"
    result = explorer.diff("HEAD", "WORKTREE", "module")
    assert result["status"] == "SUBMODULE_CONTENT_UNAVAILABLE"
    assert result["checkout_status"] == "uninitialized_or_absent"
    with pytest.raises(ObservationError, match="INVALID_PATH"):
        explorer.diff("HEAD", "WORKTREE", "module/nested")


def test_submodule_pointer_update_and_removal_are_parent_only(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "commit", "--allow-empty", "-qm", "initial")
    pointer = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-index", "--add", "--cacheinfo", f"160000,{pointer},module")
    git(tmp_path, "commit", "-qm", "add module")
    base = git(tmp_path, "rev-parse", "HEAD")
    git(tmp_path, "update-index", "--cacheinfo", f"160000,{base},module")
    (tmp_path / "module").mkdir()
    git(tmp_path / "module", "init", "-q")
    (tmp_path / "module" / "private").write_text("not part of parent\n")
    (tmp_path / ".gitmodules").write_text("# Separate text file\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    records = explorer.changes("HEAD", "WORKTREE")["records"]
    assert [entry["path"] for entry in records] == [".gitmodules", "module"]
    assert records[1]["pointer_changed"] and records[1]["checkout_status"] == "unknown"
    patch = explorer.diff("HEAD", "WORKTREE", ".gitmodules")
    assert "+# Separate text file\n" in patch["lines"]
    result = explorer.check("HEAD", "WORKTREE")
    assert result["status"] == "unavailable"
    assert result["reason"] == "SUBMODULE_CONTENT_UNAVAILABLE"
    git(tmp_path, "update-index", "--force-remove", "--", "module")
    records = explorer.changes("HEAD", "WORKTREE")["records"]
    assert [entry["path"] for entry in records] == [".gitmodules", "module"]
    assert records[1]["change"] == "deleted"
    with pytest.raises(ObservationError, match="INVALID_PATH"):
        explorer.diff("HEAD", "WORKTREE", "module/private")


def test_large_unrelated_worktree_file_does_not_block_inventory(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "large").write_bytes(b"x" * (17 * 1024 * 1024))
    (tmp_path / "small").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "small").write_text("after\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    records = {r["path"]: r for r in explorer.changes("BASE", "WORKTREE")["records"]}
    assert records["small"]["change"] == "modified"
    assert "large" not in records
    assert "+after\n" in explorer.diff("BASE", "WORKTREE", "small")["lines"]
    (tmp_path / "large").write_bytes(b"y" * (17 * 1024 * 1024))
    records = {r["path"]: r for r in explorer.changes("BASE", "WORKTREE")["records"]}
    assert records["large"]["after_size"] == 17 * 1024 * 1024
    with pytest.raises(ObservationError, match="TOO_LARGE"):
        explorer.diff("BASE", "WORKTREE", "large")


def test_large_file_inventory_pages_and_reports_same_size_binary_edit(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "large").write_bytes(b"\0" + b"x" * 39999)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    for number in range(35):
        (tmp_path / f"new-{number:02}").write_text("new\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    page = explorer.changes("BASE", "WORKTREE")
    assert page["has_more"]
    first = [record["path"] for record in page["records"]]
    second = explorer.changes("BASE", "WORKTREE", page["next_cursor"])
    assert first + [record["path"] for record in second["records"]] == [
        f"new-{number:02}" for number in range(35)
    ]
    (tmp_path / "large").write_bytes(b"\0" + b"y" * 39999)
    changed = explorer.changes("BASE", "WORKTREE")["records"][0]
    assert changed["path"] == "large" and changed["binary"] is True
    assert changed["before_size"] == changed["after_size"] == 40000


def test_unchanged_files_are_not_read_by_worktree_inventory(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    tracked = tmp_path / "tracked"
    tracked.write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "changed").write_text("new\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    original = explorer._live_file

    def observe(path, read=0):
        assert path != "tracked"
        return original(path, read)

    monkeypatch.setattr(explorer, "_live_file", observe)
    records = explorer.changes("BASE", "WORKTREE")["records"]
    assert [record["path"] for record in records] == ["changed"]


def test_expensive_observation_does_not_claim_complete(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "large").write_bytes(b"a" * 4096)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    for number in range(35):
        (tmp_path / f"new-{number:02}").write_text("new\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    page = explorer.changes("BASE", "WORKTREE")
    assert page["has_more"]
    original = change_explorer._git

    def observe(repo, *args, **kwargs):
        if args[:2] == ("ls-files", "--others"):
            kwargs["limit"] = 100
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(change_explorer, "_git", observe)
    for cursor in (None, page["next_cursor"]):
        with pytest.raises(ObservationError, match="TOO_EXPENSIVE"):
            explorer.changes("BASE", "WORKTREE", cursor)
    # An unrelated large entry must not prevent a bounded one-path diff.
    assert "+new\n" in explorer.diff("BASE", "WORKTREE", "new-00")["lines"]


def test_large_text_with_small_change_can_be_diffed(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    lines = [f"{number:04} " + "x" * 100 + "\n" for number in range(400)]
    (tmp_path / "large").write_text("".join(lines))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    lines[200] = "changed\n"
    (tmp_path / "large").write_text("".join(lines))
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    result = explorer.diff("BASE", "WORKTREE", "large")
    assert result["complete"]
    assert "+changed\n" in result["lines"]


def test_worktree_git_observations_share_deadline(tmp_path, monkeypatch):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file").write_text("base\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    original = change_explorer._git
    deadlines = []

    def observe(repo, *args, **kwargs):
        if args[0] in {"ls-files", "ls-tree", "diff-index"}:
            deadlines.append(kwargs["deadline"])
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(change_explorer, "_git", observe)
    explorer.changes("BASE", "WORKTREE")
    assert len(deadlines) == 4
    assert len(set(deadlines)) == 1
    with pytest.raises(ObservationError, match="TOO_EXPENSIVE"):
        original(tmp_path, "ls-files", limit=1024, deadline=0)


def test_worktree_diff_does_not_read_unrelated_large_file(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / "file").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "file").write_text("after\n")
    with (tmp_path / "unrelated").open("wb") as stream:
        stream.truncate(65 * 1024 * 1024)
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    result = explorer.diff("BASE", "WORKTREE", "file")
    assert result["complete"]
    assert "-before\n" in result["lines"]
    assert "+after\n" in result["lines"]


def test_many_line_low_delta_diff_has_original_coordinates(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    original = [f"line {number}\n" for number in range(10000)]
    (tmp_path / "file").write_text("".join(original))
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    modified = original.copy()
    modified[5000] = "changed\n"
    (tmp_path / "file").write_text("".join(modified))
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    result = explorer.diff("BASE", "WORKTREE", "file")
    assert result["complete"]
    assert any(line.startswith("@@ -4998,7 +4998,7 @@") for line in result["lines"])
    (tmp_path / "file").write_text("".join(original))
    subprocess.run(["git", "apply", "-"], cwd=tmp_path, check=True,
                   input="".join(result["lines"]), text=True, capture_output=True)
    assert (tmp_path / "file").read_text() == "".join(modified)


@pytest.mark.parametrize(
    "whitespace", ["blank-at-eof,space-before-tab", "-blank-at-eol"],
)
def test_check_matches_git_and_keeps_worktree_index_unchanged(tmp_path, whitespace):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "config", "core.whitespace", whitespace)
    (tmp_path / "tracked").write_text("base\n")
    git(tmp_path, "add", "tracked")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "tracked").write_text("base\nbreak  \n \tindent\n<<<<<<< ours\n\n")
    git(tmp_path, "add", "tracked")
    git(tmp_path, "commit", "-qm", "checkpoint")
    head = git(tmp_path, "rev-parse", "HEAD")
    explorer = ImmutableExplorer(
        tmp_path, RevisionNamespace(base, (Checkpoint(head, base),)), b"x" * 32,
    )
    expected = subprocess.run(
        ["git", "diff", "--check", base, head], cwd=tmp_path,
        capture_output=True, text=True,
    ).stdout
    result = explorer.check("BASE", "HEAD")
    assert result["status"] == "findings", result
    assert result["complete"]
    assert [f'{d["path"]}:{d["line"]}: {", ".join(d["kinds"])}'
            for d in result["diagnostics"]] == [
                line.rstrip(".") for line in expected.splitlines()
                if line.startswith("tracked:")
            ]
    assert explorer.check("HEAD", "WORKTREE")["status"] == "clean"
    staged = "base\n<<<<<<< staged\n"
    (tmp_path / "tracked").write_text(staged)
    git(tmp_path, "add", "tracked")
    index = (tmp_path / ".git" / "index").read_bytes()
    (tmp_path / "tracked").write_text(staged + ">>>>>>> unstaged\n")
    (tmp_path / "untracked").write_text("=======\n")
    result = explorer.check("HEAD", "WORKTREE")
    assert result["status"] == "findings", result
    assert {(d["path"], d["line"]) for d in result["diagnostics"]} == {
        ("tracked", 2), ("tracked", 3), ("untracked", 1),
    }
    assert (tmp_path / ".git" / "index").read_bytes() == index
    assert (tmp_path / "tracked").read_text() == staged + ">>>>>>> unstaged\n"
    assert (tmp_path / "untracked").read_text() == "=======\n"


def test_check_honors_path_whitespace_and_conflict_marker_attributes(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    (tmp_path / ".gitattributes").write_text("*.md whitespace=-blank-at-eol\n"
                                            "*.md conflict-marker-size=9\n")
    (tmp_path / "notes.md").write_text("before\n")
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "notes.md").write_text("intentional break  \n<<<<<<<<< ours\n")
    explorer = ImmutableExplorer(tmp_path, RevisionNamespace(base, ()), b"x" * 32)
    result = explorer.check("HEAD", "WORKTREE")
    expected = subprocess.run(
        ["git", "diff", "--check", base], cwd=tmp_path, capture_output=True, text=True,
    ).stdout
    assert expected == "notes.md:2: leftover conflict marker\n"
    assert result["complete"]
    assert [(d["path"], d["line"], d["kinds"]) for d in result["diagnostics"]] == [
        ("notes.md", 2, ["leftover conflict marker"]),
    ]


def test_patch_and_check_pages_are_bounded_and_stable(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.invalid")
    git(tmp_path, "config", "user.name", "Test")
    git(tmp_path, "commit", "--allow-empty", "-qm", "base")
    base = git(tmp_path, "rev-parse", "HEAD")
    (tmp_path / "short").write_text("short\n" * 200)
    (tmp_path / "wide").write_text("é" * 240 + " \n")
    with (tmp_path / "wide").open("a") as stream:
        stream.write(("é" * 240 + " \n") * 79)
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "checkpoint")
    head = git(tmp_path, "rev-parse", "HEAD")
    explorer = ImmutableExplorer(
        tmp_path, RevisionNamespace(base, (Checkpoint(head, base),)), b"x" * 32,
    )
    short = explorer.diff("BASE", "HEAD", "short")
    assert len(short["lines"]) == 120 and short["has_more"] and not short["complete"]
    second = explorer.diff("BASE", "HEAD", "short", short["next_cursor"])
    assert second["complete"]
    assert (short["lines"] + second["lines"]).count("+short\n") == 200
    wide = explorer.diff("BASE", "HEAD", "wide")
    assert wide["has_more"] and len(change_explorer.serialize(wide)) <= 8192
    assert len(wide["lines"]) < 120
    result = explorer.check("BASE", "HEAD")
    numbers = []
    while True:
        assert len(result["diagnostics"]) <= 30
        assert len(change_explorer.serialize(result)) <= 8192
        numbers.extend(d["line"] for d in result["diagnostics"])
        if not result["has_more"]:
            assert result["status"] == "findings" and result["complete"]
            break
        assert result["status"] == "partial" and not result["complete"]
        result = explorer.check("BASE", "HEAD", result["next_cursor"])
    assert numbers == list(range(1, 81))


def test_check_never_reports_clean_for_unavailable_inputs(comparison, monkeypatch):
    repo = comparison._repo
    (repo / "clean.md").write_text("  deliberately indented\n")
    assert comparison.check("HEAD", "WORKTREE")["status"] == "clean"
    (repo / "binary").write_bytes(b"\0b")
    result = comparison.check("HEAD", "WORKTREE")
    assert result["status"] == "unavailable" and not result["complete"]
    assert result["reason"] == "BINARY_DIFF_UNAVAILABLE"
    (repo / "bad").write_text("x" * 5000 + " \n")
    with pytest.raises(ObservationError, match="LINE_TOO_LONG"):
        comparison.diff("HEAD", "WORKTREE", "bad")
    result = comparison.check("HEAD", "WORKTREE")
    assert result["status"] == "partial" and not result["complete"]
    assert result["diagnostics"][0]["excerpt_status"] == "LINE_TOO_LONG"
    (repo / "bad").write_text(("x" * 128 + " \n") * 3000)
    with pytest.raises(ObservationError, match="TOO_EXPENSIVE"):
        comparison.diff("HEAD", "WORKTREE", "bad")
    assert not comparison.check("HEAD", "WORKTREE")["complete"]

    def fail(*_args, **_kwargs):
        raise ObservationError("UNAVAILABLE")

    monkeypatch.setattr(change_explorer, "_git", fail)
    result = comparison.check("BASE", "HEAD")
    assert result["status"] == "unavailable" and not result["complete"]
