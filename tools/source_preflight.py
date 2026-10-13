#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate that a Git source checkout contains its pinned submodules."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path, PurePosixPath


class SourcePreflightError(RuntimeError):
    """The selected source checkout cannot safely be packaged."""


_OBJECT_ID = re.compile(r"[0-9a-f]{40,64}\Z")


def _git(repo: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *arguments],
        text=True,
        capture_output=True,
        check=False,
    )


def _gitlinks(repo: Path, commit: str) -> list[tuple[PurePosixPath, str]]:
    result = _git(repo, "ls-tree", "-r", "-z", commit)
    if result.returncode:
        raise SourcePreflightError(
            f"cannot inspect packaging source submodules for {commit}: "
            f"{result.stderr.strip() or 'git failed'}"
        )
    links: list[tuple[PurePosixPath, str]] = []
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        metadata, separator, raw_name = entry.partition("\t")
        fields = metadata.split()
        if not separator or len(fields) != 3:
            raise SourcePreflightError("malformed Git tree entry in packaging source")
        mode, _kind, expected = fields
        if mode != "160000":
            continue
        path = PurePosixPath(raw_name)
        if (
            not raw_name
            or path.is_absolute()
            or "." in path.parts
            or ".." in path.parts
            or not _OBJECT_ID.fullmatch(expected)
        ):
            raise SourcePreflightError(
                f"unsafe or malformed packaging submodule path: {raw_name!r}"
            )
        links.append((path, expected))
    return links


def _submodule_error(
    path: PurePosixPath, expected: str, detail: str
) -> SourcePreflightError:
    return SourcePreflightError(
        "packaging source is incomplete:\n"
        f"submodule {path} {detail}\n"
        f"expected {expected}\n"
        "run: git submodule update --init --recursive"
    )


def _validate(
    repo: Path, commit: str, prefix: PurePosixPath = PurePosixPath(".")
) -> None:
    for path, expected in _gitlinks(repo, commit):
        full_path = PurePosixPath(*prefix.parts, *path.parts)
        candidate = repo.joinpath(*path.parts)
        current = repo
        for component in path.parts:
            current /= component
            if current.is_symlink():
                raise _submodule_error(full_path, expected, "contains a symlink")
        if candidate.is_symlink() or not candidate.is_dir():
            raise _submodule_error(full_path, expected, "is not initialized")

        root = _git(candidate, "rev-parse", "--show-toplevel")
        head = _git(candidate, "rev-parse", "HEAD")
        if root.returncode or head.returncode:
            raise _submodule_error(full_path, expected, "is not a Git worktree")
        try:
            actual_root = Path(root.stdout.strip()).resolve()
        except OSError:
            actual_root = Path()
        if actual_root != candidate.resolve():
            raise _submodule_error(full_path, expected, "is not a Git worktree")
        observed = head.stdout.strip()
        if observed != expected:
            raise SourcePreflightError(
                "packaging source submodule mismatch:\n"
                f"{full_path}\nexpected {expected}\n"
                f"observed {observed or 'unavailable'}"
            )
        _validate(candidate, expected, full_path)


def validate_source_submodules(repo: Path, commit: str) -> None:
    """Verify every gitlink in *commit*, including recursively nested links."""
    _validate(repo, commit)
