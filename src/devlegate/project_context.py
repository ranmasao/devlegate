# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Strict, project-owned routing for Devlegate agent context."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ._vendor.nanoyaml import NanoYAMLError, loads
from .change_explorer import _OBJECT, ObservationError, _git


class ProjectContextError(ValueError):
    """Raised when a project context adapter is invalid or unsafe."""


_ROLES = ("common", "architect", "reviewer", "worker")
_FIELDS = {"type", *_ROLES}
_MANIFEST = ".devlegate/project.md"
_CONTEXT_LIMIT = 256 * 1024


@dataclass(frozen=True)
class ProjectContext:
    common: tuple[Path, ...] = ()
    architect: tuple[Path, ...] = ()
    reviewer: tuple[Path, ...] = ()
    worker: tuple[Path, ...] = ()

    def for_role(self, role: str) -> tuple[Path, ...]:
        if role == "architect":
            return self.common + self.architect
        if role == "reviewer":
            return self.common + self.reviewer
        if role == "worker":
            return self.worker
        raise ValueError(f"unknown project context role: {role}")

    @property
    def is_usable(self) -> bool:
        return bool(self.common or self.architect or self.reviewer)


def _error(path: Path, field: str, reason: str) -> ProjectContextError:
    return ProjectContextError(f"invalid project context {path}: {field}: {reason}")


def _split_manifest(path: Path, text: str) -> str:
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


def _relative_path(value: object, field: str, manifest: Path) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise _error(manifest, field, "must be a non-empty relative path")
    relative = Path(value)
    if relative.is_absolute() or any(part == ".." for part in relative.parts):
        raise _error(manifest, field, "path escapes project root")
    if any(part.casefold() == ".git" for part in relative.parts) or any(
        char in value for char in "\\\r\n\t"
    ):
        raise _error(manifest, field, "unsafe relative path")
    if relative == Path("."):
        raise _error(manifest, field, "must be a file path")
    return relative


def _validate_context_file(
    root: Path, relative: Path, field: str, manifest: Path,
) -> None:
    current = root
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise _error(manifest, field, "path uses unsafe symlink traversal")
    if not current.is_file():
        raise _error(
            manifest,
            field,
            f"path does not exist or is not a regular file: {relative}",
        )
    try:
        with current.open("rb"):
            pass
    except OSError as error:
        raise _error(manifest, field, f"file is not readable: {relative}") from error


def _parse_project_context(manifest: Path, text: str) -> ProjectContext:
    try:
        metadata = loads(_split_manifest(manifest, text))
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
            _relative_path(item, role, manifest) for item in value
        )
    return ProjectContext(**routes)


def load_project_context(root: Path, *, require_context: bool = True) -> ProjectContext:
    """Load project routing for the interactive roles from their worktree."""
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
        text = manifest.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as error:
        raise ProjectContextError(
            f"cannot read project context manifest {manifest}"
        ) from error
    context = _parse_project_context(manifest, text)
    for role in ("common", "architect", "reviewer"):
        for path in getattr(context, role):
            _validate_context_file(root, path, role, manifest)
    if require_context and not context.is_usable:
        raise ProjectContextError(f"project context routing is empty: {manifest}")
    return context


@dataclass(frozen=True)
class WorkerContextDocument:
    path: str
    blob: str
    text: str


@dataclass(frozen=True)
class WorkerProjectContext:
    commit: str
    manifest_blob: str | None
    documents: tuple[WorkerContextDocument, ...] = ()


def load_worker_project_context(repo: Path, commit: str) -> WorkerProjectContext:
    """Read only the explicit Worker route from the original admitted commit."""
    manifest = Path(_MANIFEST)
    if not isinstance(commit, str) or not _OBJECT.fullmatch(commit):
        raise ProjectContextError("invalid admitted product commit for worker context")
    try:
        if _git(repo, "cat-file", "-t", commit, limit=32) != b"commit\n":
            raise ObservationError("not a commit")
    except (ObservationError, OSError) as error:
        raise ProjectContextError(
            "cannot read admitted product commit for worker context"
        ) from error

    def document(path: Path, field: str, *, optional: bool = False):
        try:
            prefix = Path()
            for index, part in enumerate(path.parts):
                prefix /= part
                name = prefix.as_posix()
                entry = _git(repo, "ls-tree", "-z", "--full-tree", commit, "--", name,
                             limit=_CONTEXT_LIMIT)
                if not entry:
                    if optional:
                        return None
                    raise _error(manifest, field, f"missing tracked file: {path!s}")
                meta, _, raw_path = entry.partition(b"\t")
                mode, kind, oid = meta.decode("ascii").split(" ")
                leaf = index == len(path.parts) - 1
                allowed = {("100644", "blob"), ("100755", "blob")} if leaf else {
                    ("040000", "tree"),
                }
                if (raw_path != name.encode() + b"\0" or not _OBJECT.fullmatch(oid)
                        or (mode, kind) not in allowed):
                    raise _error(
                        manifest, field, f"unsafe or non-regular Git path: {name!r}",
                    )
            raw = _git(repo, "cat-file", "blob", oid, limit=_CONTEXT_LIMIT)
            text = raw.decode("utf-8")
            if "\0" in text or (field == "worker" and not text.strip()):
                raise _error(manifest, field, f"expected non-empty UTF-8: {path!s}")
            return WorkerContextDocument(path.as_posix(), oid, text)
        except ProjectContextError:
            raise
        except (ObservationError, OSError, ValueError) as error:
            raise _error(
                manifest, field, f"cannot read tracked UTF-8 file: {path!s}",
            ) from error

    source = document(manifest, "frontmatter", optional=True)
    if source is None:
        return WorkerProjectContext(commit, None)
    context = _parse_project_context(manifest, source.text)
    documents = tuple(document(path, "worker") for path in context.for_role("worker"))
    return WorkerProjectContext(commit, source.blob, documents)


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
