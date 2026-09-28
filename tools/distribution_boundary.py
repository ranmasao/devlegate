#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""The repository-to-product distribution boundary."""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path, PurePosixPath


class BoundaryError(RuntimeError):
    """Repository integration cannot be identified safely."""


DEFAULT_GENERATED_TARGETS = {
    "skills/architect/SKILL.md",
    "skills/reviewer/SKILL.md",
}
GENERATED_MARKER = "<!-- GENERATED FILE. DO NOT EDIT DIRECTLY. -->\n"


def generated_targets(root: Path) -> set[str]:
    manifest = root / ".devlegate/templates/artifacts.toml"
    if not manifest.is_file():
        return set()
    try:
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise BoundaryError(
            f"cannot read project artifact manifest: {error}"
        ) from error
    artifacts = data.get("artifact")
    if not isinstance(artifacts, list):
        raise BoundaryError(
            "project artifact manifest must contain [[artifact]] entries"
        )
    targets: set[str] = set()
    for artifact in artifacts:
        if not isinstance(artifact, dict):
            raise BoundaryError("project artifact entry is not a table")
        source = artifact.get("source")
        target = artifact.get("target")
        if not isinstance(source, str) or not isinstance(target, str):
            raise BoundaryError("project artifact entry lacks source or target")
        target_path = PurePosixPath(target)
        source_path = PurePosixPath(source)
        if (
            not target
            or target_path.is_absolute()
            or ".." in target_path.parts
            or not target.startswith("skills/")
            or source_path.is_absolute()
            or ".." in source_path.parts
            or not source.startswith("skills/")
            or not source.endswith(".tmpl")
        ):
            raise BoundaryError(f"unsafe generated project target: {target!r}")
        template = root / ".devlegate/templates" / source
        if template.is_symlink() or not template.is_file():
            raise BoundaryError(f"generated project template is unavailable: {source!r}")
        generated = root / target
        if not generated.exists():
            continue
        if generated.is_symlink() or not generated.is_file():
            raise BoundaryError(
                f"generated project target is not a regular file: {target!r}"
            )
        try:
            owned = generated.read_text(encoding="utf-8").startswith(
                GENERATED_MARKER
            )
        except (OSError, UnicodeError) as error:
            raise BoundaryError(
                f"cannot inspect generated project target: {target!r}"
            ) from error
        if not owned:
            raise BoundaryError(
                f"generated project target is not Devlegate-owned: {target!r}"
            )
        targets.add(target)
    return targets


def is_prohibited(path: str, root: Path | None = None) -> bool:
    parts = PurePosixPath(path).parts
    if not parts:
        return False
    if parts[0] in {".env", ".devlegate"}:
        return True
    targets = set(DEFAULT_GENERATED_TARGETS)
    if root is not None:
        targets.update(generated_targets(root))
    return any(
        str(PurePosixPath(*parts[:index])) in targets
        for index in range(1, len(parts) + 1)
    )


def validate_members(members: set[str], root: Path, label: str) -> None:
    forbidden = sorted(name for name in members if is_prohibited(name, root))
    if forbidden:
        raise BoundaryError(f"{label} contains repository integration: {forbidden}")


def remove_integration(root: Path) -> None:
    for relative in (".env", ".devlegate", *sorted(generated_targets(root))):
        path = root / relative
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        elif path.exists() or path.is_symlink():
            path.unlink()
