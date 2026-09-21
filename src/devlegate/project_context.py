# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Strict, project-owned routing for Devlegate agent context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._vendor.nanoyaml import NanoYAMLError, loads


class ProjectContextError(ValueError):
    """Raised when a project context adapter is invalid or unsafe."""


_FIELDS = {"type", "common", "architect", "reviewer"}
_ROLES = ("common", "architect", "reviewer")
_MANIFEST = ".devlegate/project.md"


@dataclass(frozen=True)
class ProjectContext:
    common: tuple[Path, ...] = ()
    architect: tuple[Path, ...] = ()
    reviewer: tuple[Path, ...] = ()

    def for_role(self, role: str) -> tuple[Path, ...]:
        if role == "architect":
            return self.common + self.architect
        if role == "reviewer":
            return self.common + self.reviewer
        raise ValueError(f"unknown project context role: {role}")

    @property
    def is_usable(self) -> bool:
        return bool(self.common or self.architect or self.reviewer)


def _error(path: Path, field: str, reason: str) -> ProjectContextError:
    return ProjectContextError(f"invalid project context {path}: {field}: {reason}")


def _split_manifest(path: Path) -> str:
    try:
        text = path.read_text()
    except OSError as error:
        raise ProjectContextError(
            f"cannot read project context manifest {path}: {error}"
        ) from error
    lines = text.splitlines(keepends=True)
    if not lines or lines[0].rstrip("\r\n") != "---":
        raise ProjectContextError(
            f"invalid project context {path}: frontmatter: missing opening delimiter"
        )
    closing = next(
        (
            index
            for index, line in enumerate(lines[1:], 1)
            if line.rstrip("\r\n") == "---"
        ),
        None,
    )
    if closing is None:
        raise ProjectContextError(
            f"invalid project context {path}: frontmatter: missing closing delimiter"
        )
    return "".join(lines[1:closing])


def _safe_context_file(root: Path, value: object, field: str, manifest: Path) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error(manifest, field, "must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise _error(manifest, field, "path escapes project root")
    if relative == Path("."):
        raise _error(manifest, field, "must be a file path")
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise _error(manifest, field, "path uses unsafe symlink traversal")
    if not current.is_file():
        raise _error(
            manifest,
            field,
            f"path does not exist or is not a regular file: {value}",
        )
    try:
        with current.open("rb"):
            pass
    except OSError as error:
        raise _error(manifest, field, f"file is not readable: {value}") from error
    return relative


def load_project_context(root: Path, *, require_context: bool = True) -> ProjectContext:
    """Load and validate the project adapter without interpreting its documents."""
    manifest = root / _MANIFEST
    if (root / ".devlegate").is_symlink():
        raise ProjectContextError(
            f"unsafe symlinked .devlegate root: {root / '.devlegate'}"
        )
    if manifest.is_symlink():
        raise ProjectContextError(
            f"unsafe symlinked project context manifest: {manifest}"
        )
    if not manifest.is_file():
        raise ProjectContextError(f"project context manifest is missing: {manifest}")
    try:
        metadata = loads(_split_manifest(manifest))
    except (NanoYAMLError, TypeError) as error:
        raise ProjectContextError(
            f"invalid project context {manifest}: frontmatter: "
            f"invalid NanoYAML: {error}"
        ) from error
    unknown = sorted(set(metadata) - _FIELDS)
    if unknown:
        raise _error(manifest, unknown[0], "unknown field")
    if metadata.get("type") != "devlegate.project":
        raise _error(manifest, "type", 'must be "devlegate.project"')
    routes: dict[str, tuple[Path, ...]] = {}
    for role in _ROLES:
        value = metadata.get(role, [])
        if role not in metadata:
            routes[role] = ()
            continue
        if not isinstance(value, list) or not value:
            raise _error(manifest, role, "must be a non-empty block sequence")
        routes[role] = tuple(
            _safe_context_file(root, item, role, manifest) for item in value
        )
    context = ProjectContext(**routes)
    if require_context and not context.is_usable:
        raise ProjectContextError(f"project context routing is empty: {manifest}")
    return context


def seed_project_context(root: Path) -> bool:
    """Create the intentionally incomplete project adapter skeleton."""
    manifest = root / _MANIFEST
    if manifest.exists() or manifest.is_symlink():
        return False
    protocol = root / ".devlegate"
    if protocol.is_symlink():
        raise ProjectContextError(f"unsafe symlinked .devlegate root: {protocol}")
    protocol.mkdir(parents=True, exist_ok=True)
    template = Path(__file__).parent / "default_templates/project_context.md"
    if not template.is_file():
        raise ProjectContextError(f"packaged project context is missing: {template}")
    manifest.write_bytes(template.read_bytes())
    return True


__all__ = [
    "ProjectContext",
    "ProjectContextError",
    "load_project_context",
    "seed_project_context",
]
