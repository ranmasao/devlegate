#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Synchronize source-checkout dependencies before creating a dev environment."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from source_preflight import (
    SourcePreflightError,
    _gitlinks,
    validate_source_submodules,
)


def git(repo: Path, *args: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args], text=True, capture_output=True,
    )
    if result.returncode:
        raise SourcePreflightError(
            f"git {' '.join(args)} in {repo} failed:\n{result.stderr.strip()}"
        )
    return result.stdout.strip()


def initialized(path: Path) -> bool:
    return (path / ".git").exists()


def check_local_work(repo: Path, expected: str) -> None:
    if Path(git(repo, "rev-parse", "--show-toplevel")).resolve() != repo.resolve():
        raise SourcePreflightError(f"submodule {repo} is not its own Git worktree")
    head = git(repo, "rev-parse", "HEAD")
    if git(repo, "status", "--porcelain", "--untracked-files=all",
           "--ignore-submodules=all"):
        raise SourcePreflightError(f"submodule {repo} has local content")
    if head != expected:
        ancestor = subprocess.run(
            ["git", "-C", str(repo), "merge-base", "--is-ancestor", head, expected],
            capture_output=True,
        )
        # Remote-tracking refs also permit clean rewinds between published pins.
        published = git(repo, "for-each-ref", "--format=%(refname)",
                        "--contains", head, "refs/remotes/")
        if ancestor.returncode and not published:
            raise SourcePreflightError(
                f"submodule {repo} has divergent/unpublished HEAD {head}; "
                f"expected {expected}"
            )
        ignored = git(repo, "ls-files", "-z", "--others", "--ignored",
                      "--exclude-standard").split("\0")
        if any(ignored):
            # Git checkout may overwrite ignored files. Ordinary ignored caches
            # are fine, but protect any content that overlaps the target tree.
            available = subprocess.run(
                ["git", "-C", str(repo), "cat-file", "-e", f"{expected}^{{commit}}"],
                capture_output=True,
            )
            if available.returncode:
                git(repo, "fetch", "--no-tags", "origin", expected)
            targets = git(repo, "ls-tree", "-r", "-z", "--name-only", expected)
            for local in filter(None, ignored):
                if any(
                    local == target or local.startswith(target + "/")
                    or target.startswith(local + "/")
                    for target in filter(None, targets.split("\0"))
                ):
                    raise SourcePreflightError(
                        f"submodule {repo} has ignored content at risk: {local}"
                    )
    # Protect old nested checkouts before changing the parent's recorded tree.
    for path, pin in _gitlinks(repo, "HEAD"):
        child = repo.joinpath(*path.parts)
        if initialized(child):
            check_local_work(child, pin)


def synchronize(repo: Path) -> None:
    links = _gitlinks(repo, "HEAD")
    paths = [str(path) for path, _ in links]
    # `submodule update` uses the index. Refuse staged overrides of HEAD's pins.
    if git(repo, "diff", "HEAD", "--", ".gitmodules") or git(
        repo, "diff", "--cached", "HEAD", "--", ".gitmodules", *paths
    ):
        raise SourcePreflightError(
            f"{repo} has local .gitmodules or staged submodule changes"
        )
    for path, pin in links:
        child = repo.joinpath(*path.parts)
        for parent in (child, *child.parents):
            if parent == repo:
                break
            if parent.is_symlink():
                raise SourcePreflightError(f"submodule {child} contains a symlink")
        if initialized(child):
            check_local_work(child, pin)
        elif child.exists() and any(child.iterdir()):
            raise SourcePreflightError(f"uninitialized submodule {child} is not empty")
    for path, _pin in links:
        # Explicit checkout ignores local submodule update=merge/rebase/none config.
        # Never use --force or --remote: only the checked-out commit's pins apply.
        git(repo, "submodule", "update", "--init", "--checkout", "--", str(path))
        synchronize(repo.joinpath(*path.parts))


def main() -> int:
    root = Path(sys.argv[1]).resolve()
    try:
        if not (root / ".git").exists() and not (root / ".git").is_symlink():
            # Full-source archives already contain materialized dependencies;
            # preserve their offline setup without discovering an enclosing repo.
            if not (root / "src/devlegate/_vendor/nanoyaml/__init__.py").is_file():
                raise SourcePreflightError(
                    "NanoYAML is absent; use a Git checkout or complete source archive"
                )
            return 0
        if Path(git(root, "rev-parse", "--show-toplevel")).resolve() != root:
            raise SourcePreflightError("setup requires a Git source checkout")
        synchronize(root)
        validate_source_submodules(root, "HEAD")
    except (OSError, SourcePreflightError) as error:
        print(
            f"dev: submodule synchronization failed: {error}\n"
            "Setup stopped before environment preparation. Save/commit or move "
            "local submodule content, and preserve unpublished commits on a branch "
            "before manually checking out the pinned commit. For access/fetch "
            "failures, restore access to the pinned commit, then rerun ./dev setup. "
            "No force reset is performed.",
            file=sys.stderr,
        )
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
