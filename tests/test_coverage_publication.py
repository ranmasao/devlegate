# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

"""Coverage badge publication against a real Git remote."""

import subprocess
import sys
from pathlib import Path

import pytest

import tools.publish_coverage as publisher
from tools.prepare_release_readme import prepare_readme
from tools.publish_coverage import badge_name, publish

RELEASE_COMMAND = Path(__file__).parents[1] / "tools/publish_release_branch.py"


def git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repo), *args], check=True, text=True, capture_output=True
    ).stdout.strip()


def test_badge_names() -> None:
    assert badge_name("master", "master") == "coverage.json"
    assert badge_name("release/1.2.3", "master") == "coverage-1.2.3.json"
    assert badge_name("release/1.2.3-extra", "master") is None


def test_readme_preparation_for_sequential_iterations(tmp_path: Path) -> None:
    readme = tmp_path / "README.md"
    readme.write_text("[coverage](https://example.com/badges/coverage.json)\n")
    prepare_readme(readme, "1.0.0")
    assert readme.read_text().endswith("/badges/coverage-1.0.0.json)\n")
    prepare_readme(readme, "1.1.0")
    assert readme.read_text().endswith("/badges/coverage-1.1.0.json)\n")
    with pytest.raises(ValueError):
        prepare_readme(readme, "../bad")


def test_initial_release_publication_and_rejections(tmp_path: Path) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "README.md").write_text("[coverage](https://example.com/badges/coverage.json)\n")
    (repo / "pyproject.toml").write_text('[project]\nversion = "1.0.0.dev0"\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "source")
    first = git(repo, "rev-parse", "HEAD")

    def run(version: str, source: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(RELEASE_COMMAND), version, source],
            cwd=repo, capture_output=True, text=True,
        )

    assert run("../bad", first).returncode != 0
    assert run("1.1.0", first).returncode != 0
    assert run("1.0.0", "HEAD").returncode != 0
    assert not git(remote, "for-each-ref", "refs/heads/release")
    assert run("1.0.0", first).returncode == 0
    assert "coverage-1.0.0.json" in git(remote, "show", "release/1.0.0:README.md")
    assert run("1.0.0", first).returncode != 0
    git(repo, "fetch", "origin", "release/1.0.0")
    git(repo, "reset", "--hard", "FETCH_HEAD")
    (repo / "pyproject.toml").write_text('[project]\nversion = "1.1.0.dev0"\n')
    git(repo, "commit", "-am", "next source")
    second = git(repo, "rev-parse", "HEAD")
    assert run("1.1.0", second).returncode == 0
    assert "coverage-1.1.0.json" in git(remote, "show", "release/1.1.0:README.md")
    assert "coverage-1.0.0.json" in git(remote, "show", "release/1.0.0:README.md")


def test_publication_preserves_versions_and_rejects_old_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "file").write_text("one")
    git(repo, "add", "file")
    git(repo, "commit", "-m", "one")
    first = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "HEAD:refs/heads/release/1.0.0")
    publish(repo, "release/1.0.0", "master", first, b"old")
    (repo / "file").write_text("two")
    git(repo, "commit", "-am", "two")
    second = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "HEAD:refs/heads/release/1.0.0")
    git(repo, "push", "origin", "HEAD:refs/heads/release/1.1.0")
    git(repo, "push", "origin", "HEAD:refs/heads/master")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "release/1.1.0", str(remote), str(other))
    real_git = publisher.git
    raced = False

    def race(repo_path: Path, *args: str, check: bool = True):
        nonlocal raced
        if args and args[0] == "push" and not raced:
            raced = True
            publish(other, "release/1.1.0", "master", second, b"next")
        return real_git(repo_path, *args, check=check)

    monkeypatch.setattr(publisher, "git", race)
    publish(repo, "release/1.0.0", "master", second, b"new")
    assert raced
    publish(repo, "master", "master", second, b"master")
    publish(repo, "release/1.0.0", "master", first, b"stale")
    git(repo, "fetch", "origin", "badges")
    assert git(repo, "show", "FETCH_HEAD:coverage-1.0.0.json") == "new"
    assert git(repo, "show", "FETCH_HEAD:coverage-1.1.0.json") == "next"
    assert git(repo, "show", "FETCH_HEAD:coverage.json") == "master"


def test_older_publication_loses_race_to_newer_commit(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    remote = tmp_path / "remote.git"
    remote.mkdir()
    git(remote, "init", "--bare")
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-b", "master")
    git(repo, "config", "user.name", "Test")
    git(repo, "config", "user.email", "test@example.com")
    git(repo, "remote", "add", "origin", str(remote))
    (repo / "file").write_text("old")
    git(repo, "add", "file")
    git(repo, "commit", "-m", "old")
    old = git(repo, "rev-parse", "HEAD")
    (repo / "file").write_text("new")
    git(repo, "commit", "-am", "new")
    new = git(repo, "rev-parse", "HEAD")
    git(repo, "push", "origin", "HEAD:refs/heads/release/1.0.0")
    other = tmp_path / "other"
    git(tmp_path, "clone", "-b", "release/1.0.0", str(remote), str(other))
    real_git = publisher.git
    raced = False

    def race(repo_path: Path, *args: str, check: bool = True):
        nonlocal raced
        if args and args[0] == "push" and not raced:
            raced = True
            publish(other, "release/1.0.0", "master", new, b"new")
        return real_git(repo_path, *args, check=check)

    monkeypatch.setattr(publisher, "git", race)
    publish(repo, "release/1.0.0", "master", old, b"old")
    assert raced
    git(repo, "fetch", "origin", "badges")
    assert git(repo, "show", "FETCH_HEAD:coverage-1.0.0.json") == "new"
