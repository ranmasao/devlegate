# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Small shared Git fixtures for isolated integration-test repositories.

The prepared repositories are read-only inputs. Each test receives filesystem
copies of its bare remote and working checkout before creating mutable
worktrees, so refs and worktree registrations cannot leak between tests or
pytest workers. The copied Git databases are independent; only the remote URL
is rewritten to connect the test actors.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import pytest


@dataclass(frozen=True)
class PreparedGit:
    bare: Path
    seed: Path
    working: Path


_BASELINES: dict[str, PreparedGit] = {}


def git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def _create_product(root: Path, filename: str, content: str) -> Path:
    bare = root / "remote.git"
    seed = root / "seed"
    git(root, "init", "--bare", bare)
    git(root, "init", "-b", "main", seed)
    (seed / filename).write_text(content)
    (seed / "README.md").write_text("project context\n")
    (seed / ".devlegate").mkdir()
    (seed / ".devlegate/project.md").write_text(
        '---\n"type": "devlegate.project"\n"common":\n'
        '  - "README.md"\n---\n'
    )
    git(seed, "add", ".")
    git(seed, "commit", "-m", "product")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    return bare


def _add_control(
    bare: Path, root: Path, *, ticket: bool, remove_product: str | None
) -> None:
    seed = root / "seed"
    git(seed, "remote", "set-url", "origin", bare)
    git(seed, "switch", "--orphan", "devlegate/control")
    if remove_product is not None:
        (seed / remove_product).unlink(missing_ok=True)
    for name in ("backlog", "todo", "review", "accepted", "done"):
        (seed / "kanban" / name).mkdir(parents=True)
        (seed / "kanban" / name / ".gitkeep").touch()
    if ticket:
        (seed / "kanban/todo/T-1.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Control ticket"\n'
            "---\nwork\n"
        )
    git(seed, "add", ".")
    git(seed, "commit", "-m", "control workflow")
    git(seed, "push", "origin", "devlegate/control")


def _prepare_baselines(root: Path) -> dict[str, PreparedGit]:
    cli_product_root = root / "cli-product"
    cli_product_root.mkdir()
    _create_product(cli_product_root, "tracked.txt", "initial\n")

    empty_root = root / "empty-control"
    shutil.copytree(cli_product_root, empty_root)
    _add_control(
        empty_root / "remote.git", empty_root, ticket=False, remove_product=None
    )

    control_product_root = root / "control-product"
    control_product_root.mkdir()
    control_product_bare = _create_product(
        control_product_root, "product.txt", "product\n"
    )

    ticket_root = root / "ticket-control"
    shutil.copytree(control_product_root, ticket_root)
    _add_control(
        ticket_root / "remote.git",
        ticket_root,
        ticket=True,
        remove_product="product.txt",
    )

    def prepared(directory: Path, bare: Path) -> PreparedGit:
        working = directory / "working"
        git(directory, "clone", "-b", "main", bare, working)
        return PreparedGit(bare, directory / "seed", working)

    return {
        "product": prepared(control_product_root, control_product_bare),
        "empty-control": prepared(empty_root, empty_root / "remote.git"),
        "ticket-control": prepared(ticket_root, ticket_root / "remote.git"),
    }


@pytest.fixture(scope="session", autouse=True)
def prepared_git_baselines(tmp_path_factory: pytest.TempPathFactory):
    """Prepare immutable, worker-local Git histories once per pytest process."""
    previous = {
        name: os.environ.get(name)
        for name in (
            "GIT_AUTHOR_NAME",
            "GIT_AUTHOR_EMAIL",
            "GIT_COMMITTER_NAME",
            "GIT_COMMITTER_EMAIL",
            "GIT_CONFIG_NOSYSTEM",
            "GIT_CONFIG_GLOBAL",
        )
    }
    os.environ.update(
        {
            "GIT_AUTHOR_NAME": "Test User",
            "GIT_AUTHOR_EMAIL": "test@example.com",
            "GIT_COMMITTER_NAME": "Test User",
            "GIT_COMMITTER_EMAIL": "test@example.com",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": os.devnull,
        }
    )
    root = tmp_path_factory.mktemp("prepared-git-baselines")
    _BASELINES.update(_prepare_baselines(root))
    try:
        yield _BASELINES
    finally:
        _BASELINES.clear()
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def _baseline(name: str) -> PreparedGit:
    try:
        return _BASELINES[name]
    except KeyError as error:
        raise RuntimeError("prepared Git baselines are not initialized") from error


def clone_world(
    tmp_path: Path,
    *,
    baseline: str,
    state: Path,
    with_publisher: bool = False,
    publisher_name: str = "publisher",
    publisher_branch: str = "main",
) -> dict[str, Path]:
    """Prepare one isolated remote and the actors requested by a scenario."""
    bare = tmp_path / "remote.git"
    working = tmp_path / "working"
    prepared = _baseline(baseline)
    shutil.copytree(prepared.bare, bare)
    shutil.copytree(prepared.working, working)
    git(working, "remote", "set-url", "origin", bare)

    result = {
        "bare": bare,
        "working": working,
        "state": state,
        "tmp": tmp_path,
    }
    if with_publisher:
        publisher = tmp_path / publisher_name
        shutil.copytree(prepared.working, publisher)
        git(publisher, "remote", "set-url", "origin", bare)
        git(publisher, "switch", publisher_branch)
        result["publisher"] = publisher
    return result


def control_publisher(tmp_path: Path) -> Path:
    """Create the control-branch publisher only for tests that need it."""
    prepared = _baseline("ticket-control")
    publisher = tmp_path / "seed"
    shutil.copytree(prepared.seed, publisher)
    git(publisher, "remote", "set-url", "origin", tmp_path / "remote.git")
    return publisher
