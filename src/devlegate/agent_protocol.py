# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Deterministic rendering of project-local Architect and Reviewer skills."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path

from devlegate.project_context import (
    ProjectContextError,
    load_project_context,
    seed_project_context,
)


class AgentProtocolError(ValueError):
    """Raised when agent protocol templates or paths are invalid."""


@dataclass(frozen=True)
class RenderContext:
    control_branch: str
    backlog_path: str
    todo_path: str
    review_path: str
    done_path: str
    product_branch: str
    accepted_path: str = "kanban/accepted"
    execution_branch_prefix: str = "devlegate/work/"

    def values(self) -> dict[str, str]:
        return {
            "workflow.control_branch": self.control_branch,
            "workflow.backlog_path": self.backlog_path,
            "workflow.todo_path": self.todo_path,
            "workflow.review_path": self.review_path,
            "workflow.accepted_path": self.accepted_path,
            "workflow.done_path": self.done_path,
            "product.branch": self.product_branch,
            "execution.branch_prefix": self.execution_branch_prefix,
        }


_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_MANIFEST_NAME = "artifacts.toml"
_ENV_NAME = ".env.example"
_DEFAULT_RECEIPT = ".devlegate-defaults.json"
_DEFAULT_ARTIFACTS = (
    {
        "source": "skills/architect/SKILL.md.tmpl",
        "target": "skills/architect/SKILL.md",
    },
    {
        "source": "skills/reviewer/SKILL.md.tmpl",
        "target": "skills/reviewer/SKILL.md",
    },
)
_LEGACY_DEFAULT_HASHES = {
    "skills/architect/SKILL.md.tmpl": {
        "51cb4bed1639489131f9a64c01eab9856358ff764d3df081af913945b4c84c9d",
        "79733a491c00f08d1565a3fac83765a0ceaac00332737966d88e64a0c0574caa",
        "5b08f01a52d24c73ad27b60dfb2d8551a2fcc6ff9626881cb8a973a62398968c",
    },
    "skills/reviewer/SKILL.md.tmpl": {
        "d52fd6a57a9113c5e4c4e703acdb7eee0b1909dd5a030107f2d7cde5184f83b8",
        "30967c7480d70ee1327f20fb840560f7c890c788e512badf4fd692d7210c9abd",
        "5cfe7ee60efd2701f16583f00de3a51b1dd152eb0468961eb63ade78d2656b4c",
    },
}


def packaged_template_root() -> Path:
    """Return the read-only packaged bootstrap template directory."""
    return Path(__file__).parent / "default_templates"


def _generated_marker() -> str:
    path = packaged_template_root() / "generated_marker.txt"
    if not path.is_file():
        raise AgentProtocolError(f"packaged template is missing: {path}")
    return path.read_text().rstrip("\r\n") + "\n\n"


def seed_project_env(path: Path) -> bool:
    """Create a project environment file from the installed bootstrap template."""
    if path.exists() or path.is_symlink():
        return False
    packaged = packaged_template_root() / _ENV_NAME
    if not packaged.is_file():
        raise AgentProtocolError(
            f"packaged environment template is missing: {packaged}"
        )
    try:
        with path.open("xb") as destination:
            destination.write(packaged.read_bytes())
    except FileExistsError:
        return False
    except OSError as error:
        raise AgentProtocolError(
            f"cannot seed environment file {path}: {error}"
        ) from error
    return True


def _directory(path: Path, label: str, *, create: bool = True) -> None:
    if path.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked {label}: {path}")
    if path.exists() and not path.is_dir():
        raise AgentProtocolError(f"{label} is not a directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise AgentProtocolError(f"{label} is missing: {path}")


def _safe_project_roots(project: Path, *, create: bool = True) -> tuple[Path, Path]:
    protocol = project / ".devlegate"
    templates = protocol / "templates"
    _directory(protocol, ".devlegate root", create=create)
    _directory(templates, "template root", create=create)
    return protocol, templates


def _render_template(template: str, context: RenderContext, source: Path) -> str:
    values = context.values()
    if template.count("{{") != template.count("}}"):
        raise AgentProtocolError(f"malformed placeholder in template: {source}")

    def replace(match: re.Match[str]) -> str:
        name = match.group(1).strip()
        if not re.fullmatch(r"[a-z]+\.[a-z_]+", name):
            raise AgentProtocolError(
                f"malformed or unknown variable {name!r} in {source}"
            )
        if name not in values:
            raise AgentProtocolError(f"unknown render variable {name!r} in {source}")
        return values[name]

    rendered = _PLACEHOLDER.sub(replace, template)
    if "{{" in rendered or "}}" in rendered:
        raise AgentProtocolError(f"malformed placeholder in template: {source}")
    return rendered.rstrip("\r\n") + "\n"


def _relative_path(value: object, field: str) -> Path:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise AgentProtocolError(f"manifest {field} must be a non-empty path")
    path = Path(value)
    if path.is_absolute() or any(part == ".." for part in path.parts):
        raise AgentProtocolError(f"manifest {field} must stay within the project")
    if path == Path("."):
        raise AgentProtocolError(f"manifest {field} must be a file path")
    return path


def _manifest(templates: Path) -> tuple[tuple[Path, Path], ...]:
    path = templates / _MANIFEST_NAME
    if path.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked artifact manifest: {path}")
    if not path.is_file():
        raise AgentProtocolError(f"artifact manifest is unavailable: {path}")
    try:
        payload = tomllib.loads(path.read_text())
    except (OSError, tomllib.TOMLDecodeError) as error:
        raise AgentProtocolError(
            f"cannot read artifact manifest {path}: {error}"
        ) from error
    if set(payload) != {"artifact"} or not isinstance(payload["artifact"], list):
        raise AgentProtocolError(f"invalid artifact manifest: {path}")
    artifacts: list[tuple[Path, Path]] = []
    sources: set[Path] = set()
    targets: set[Path] = set()
    for item in payload["artifact"]:
        if not isinstance(item, dict) or set(item) != {"source", "target"}:
            raise AgentProtocolError(f"invalid artifact entry in {path}")
        source = _relative_path(item["source"], "source")
        target = _relative_path(item["target"], "target")
        if source.suffix != ".tmpl":
            raise AgentProtocolError(f"artifact source must end in .tmpl: {source}")
        if target == Path(".git") or ".git" in target.parts:
            raise AgentProtocolError(f"manifest target cannot be under .git: {target}")
        if source in sources or target in targets:
            raise AgentProtocolError(f"duplicate artifact mapping in {path}")
        if source == target:
            raise AgentProtocolError(
                f"artifact source and target must differ: {source}"
            )
        sources.add(source)
        targets.add(target)
        artifacts.append((source, target))
    if not artifacts:
        raise AgentProtocolError(f"artifact manifest has no artifacts: {path}")
    return tuple(artifacts)


def _check_components(root: Path, relative: Path, label: str) -> Path:
    current = root
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            raise AgentProtocolError(f"unsafe symlinked {label}: {current}")
        if (
            index < len(relative.parts) - 1
            and current.exists()
            and not current.is_dir()
        ):
            raise AgentProtocolError(f"unsafe structural {label}: {current}")
    return current


def _atomic_write(path: Path, content: str, *, allow_foreign: bool = False) -> bool:
    if path.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked target file: {path}")
    if path.exists() and not path.is_file():
        raise AgentProtocolError(f"target path is not a file: {path}")
    if path.is_file():
        existing = path.read_text()
        if not existing.startswith(_generated_marker()) and not allow_foreign:
            raise AgentProtocolError(
                f"unrelated existing target will not be clobbered: {path}"
            )
        if existing == content:
            return False
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    except OSError as error:
        raise AgentProtocolError(
            f"cannot write rendered target {path}: {error}"
        ) from error
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return True


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _write_default_receipt(templates: Path) -> None:
    sources = {
        item["source"]: _digest((templates / item["source"]).read_bytes())
        for item in _DEFAULT_ARTIFACTS
    }
    path = templates / _DEFAULT_RECEIPT
    content = json.dumps({"version": 1, "sources": sources}, sort_keys=True) + "\n"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=templates, prefix=".defaults.", suffix=".tmp", delete=False
        ) as temporary:
            temporary_name = temporary.name
            temporary.write(content)
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass


def _state_backup_path(state_dir: Path, project: Path, target: Path) -> Path:
    key = hashlib.sha256(str(project.resolve()).encode()).hexdigest()
    return state_dir / "backups" / key / target.relative_to(project)


def _render_plan(
    project: Path,
    templates: Path,
    context: RenderContext,
    conflicts: str,
    state_dir: Path | None,
) -> list[tuple[Path, str, Path | None, bool]]:
    artifacts = _manifest(templates)
    plan: list[tuple[Path, str, Path | None, bool]] = []
    conflicts_found: list[Path] = []
    backup_conflicts: list[Path] = []
    for source_relative, target_relative in artifacts:
        source = _check_components(templates, source_relative, "template path")
        if not source.is_file():
            default_sources = {Path(item["source"]) for item in _DEFAULT_ARTIFACTS}
            if (
                source_relative not in default_sources
                or templates == packaged_template_root()
            ):
                raise AgentProtocolError(f"template path is unavailable: {source}")
            source = _check_components(
                packaged_template_root(), source_relative, "packaged template path"
            )
            if not source.is_file():
                raise AgentProtocolError(f"packaged template is missing: {source}")
        target = _check_components(project, target_relative, "target path")
        if target == project / ".git":
            raise AgentProtocolError(f"unsafe target path: {target}")
        rendered = _generated_marker() + _render_template(
            source.read_text(), context, source
        )
        backup = None
        if target.is_symlink():
            raise AgentProtocolError(f"unsafe symlinked target file: {target}")
        if target.exists() and not target.is_file():
            raise AgentProtocolError(f"target path is not a file: {target}")
        if target.is_file() and not target.read_text().startswith(_generated_marker()):
            if conflicts == "abort":
                conflicts_found.append(target)
            if conflicts == "backup":
                if state_dir is None:
                    raise AgentProtocolError(
                        "backup conflict policy requires STATE_DIR"
                    )
                backup = _state_backup_path(state_dir, project, target)
                if state_dir.exists() and not state_dir.is_dir():
                    raise AgentProtocolError(
                        f"state directory is not a directory: {state_dir}"
                    )
                _check_components(
                    state_dir, backup.relative_to(state_dir), "backup path"
                )
                if backup.exists() or backup.is_symlink():
                    backup_conflicts.append(backup)
                    backup = None
        plan.append((target, rendered, backup, conflicts != "abort"))
    if conflicts_found:
        details = "\n".join(f"  {target}" for target in conflicts_found)
        raise AgentProtocolError(
            "existing files are not Devlegate-owned and will not be clobbered; "
            "no project artifacts were "
            f"changed:\n{details}"
        )
    if backup_conflicts:
        details = "\n".join(f"  {backup}" for backup in backup_conflicts)
        raise AgentProtocolError(
            f"backup already exists; no project artifacts were changed:\n{details}"
        )
    return plan


def _write_backup(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        with destination.open("xb") as backup:
            backup.write(source.read_bytes())
    except FileExistsError as error:
        raise AgentProtocolError(f"backup already exists: {destination}") from error
    except OSError as error:
        raise AgentProtocolError(
            f"cannot create backup {destination}: {error}"
        ) from error


def initialize_project(
    project: Path,
    context: RenderContext,
    *,
    conflicts: str = "abort",
    state_dir: Path | None = None,
) -> tuple[int, int]:
    """Seed missing project templates and manifest, then render targets."""
    if conflicts not in {"abort", "backup", "replace"}:
        raise AgentProtocolError(f"unknown conflict policy: {conflicts}")
    protocol = project / ".devlegate"
    templates = protocol / "templates"
    if protocol.exists() or protocol.is_symlink():
        _directory(protocol, ".devlegate root", create=False)
    if templates.exists() or templates.is_symlink():
        _directory(templates, "template root", create=False)
    manifest = templates / _MANIFEST_NAME
    if manifest.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked artifact manifest: {manifest}")
    fresh_defaults = not manifest.exists()
    receipt_safe = fresh_defaults and not any(
        (templates / item["source"]).exists()
        or (templates / item["source"]).is_symlink()
        for item in _DEFAULT_ARTIFACTS
    )
    if fresh_defaults:
        packaged_manifest = packaged_template_root() / "artifacts.toml"
        if not packaged_manifest.is_file():
            raise AgentProtocolError(
                f"packaged manifest is missing: {packaged_manifest}"
            )
    elif not manifest.is_file():
        raise AgentProtocolError(f"manifest path is not a file: {manifest}")
    # Validate the complete target set before creating directories or changing targets.
    template_root = templates if manifest.exists() else packaged_template_root()
    plan = _render_plan(project, template_root, context, conflicts, state_dir)
    _safe_project_roots(project)
    try:
        seed_project_context(project)
        load_project_context(project, require_context=False)
    except ProjectContextError as error:
        raise AgentProtocolError(str(error)) from error
    _directory(templates / "skills", "skills template directory")
    if not manifest.exists():
        packaged_manifest = packaged_template_root() / "artifacts.toml"
        manifest.write_bytes(packaged_manifest.read_bytes())
    for item in _DEFAULT_ARTIFACTS:
        source = templates / item["source"]
        if source.is_symlink() or source.parent.is_symlink():
            raise AgentProtocolError(f"unsafe symlinked template file: {source}")
        if source.exists():
            if not source.is_file():
                raise AgentProtocolError(f"template path is not a file: {source}")
            continue
        packaged = packaged_template_root() / item["source"]
        if not packaged.is_file():
            raise AgentProtocolError(f"packaged template is missing: {packaged}")
        source.parent.mkdir(parents=True, exist_ok=True)
        source.write_bytes(packaged.read_bytes())
    if receipt_safe:
        _write_default_receipt(templates)
    changed = 0
    for target, rendered, backup, allow_foreign in plan:
        _directory(target.parent, "target directory")
        if backup is not None:
            _write_backup(target, backup)
            print(f"backed up {target} to {backup}")
        if _atomic_write(target, rendered, allow_foreign=allow_foreign):
            changed += 1
    return len(plan), changed


def upgrade_project_templates(
    project: Path, context: RenderContext, *, check: bool = False
) -> tuple[int, int, int]:
    """Compare-and-swap known managed defaults, then render their targets."""
    _protocol, templates = _safe_project_roots(project, create=False)
    manifest = templates / _MANIFEST_NAME
    packaged_manifest = packaged_template_root() / _MANIFEST_NAME
    if manifest.read_bytes() != packaged_manifest.read_bytes():
        raise AgentProtocolError(
            "project artifact manifest is customized; merge packaged protocol "
            "updates manually and run `devlegate render`"
        )
    receipt_path = templates / _DEFAULT_RECEIPT
    receipt_sources: dict[str, str] = {}
    if receipt_path.is_file() and not receipt_path.is_symlink():
        try:
            receipt = json.loads(receipt_path.read_text())
            if receipt.get("version") == 1 and isinstance(receipt.get("sources"), dict):
                receipt_sources = receipt["sources"]
        except (OSError, json.JSONDecodeError):
            raise AgentProtocolError("managed template receipt is malformed") from None
    plan: list[tuple[Path, bytes]] = []
    conflicts: list[Path] = []
    for item in _DEFAULT_ARTIFACTS:
        relative = item["source"]
        local = _check_components(templates, Path(relative), "template path")
        packaged = _check_components(
            packaged_template_root(), Path(relative), "packaged template path"
        )
        if not local.is_file() or not packaged.is_file():
            raise AgentProtocolError(f"managed template is unavailable: {local}")
        current = local.read_bytes()
        replacement = packaged.read_bytes()
        current_hash = _digest(current)
        trusted = set(_LEGACY_DEFAULT_HASHES.get(relative, set()))
        receipt_hash = receipt_sources.get(relative)
        if isinstance(receipt_hash, str):
            trusted.add(receipt_hash)
        if current == replacement:
            continue
        if current_hash not in trusted:
            conflicts.append(local)
            continue
        plan.append((local, replacement))
    if conflicts:
        details = "\n".join(f"  {path}" for path in conflicts)
        raise AgentProtocolError(
            "project templates are customized; no files were changed. Merge the "
            f"packaged updates manually:\n{details}"
        )
    if check and plan:
        raise AgentProtocolError("managed project templates have a packaged update")
    if not check:
        # Validate every generated target before replacing any managed source.
        _render_plan(project, packaged_template_root(), context, "abort", None)
        for path, replacement in plan:
            temporary_name = None
            try:
                with tempfile.NamedTemporaryFile(
                    mode="wb",
                    dir=path.parent,
                    prefix=f".{path.name}.",
                    suffix=".tmp",
                    delete=False,
                ) as temporary:
                    temporary_name = temporary.name
                    temporary.write(replacement)
                    temporary.flush()
                    os.fsync(temporary.fileno())
                os.replace(temporary_name, path)
            finally:
                if temporary_name is not None:
                    try:
                        os.unlink(temporary_name)
                    except FileNotFoundError:
                        pass
        _write_default_receipt(templates)
    total, rendered = render_project(project, context, check=check)
    return total, len(plan), rendered


def render_project(
    project: Path, context: RenderContext, *, check: bool = False
) -> tuple[int, int]:
    """Render the declared natural project targets, or check them read-only."""
    _protocol, templates = _safe_project_roots(project, create=not check)
    if not check:
        _directory(templates / "skills", "skills template directory")
    elif not (templates / _MANIFEST_NAME).is_file():
        raise AgentProtocolError(
            f"artifact manifest is missing: {templates / _MANIFEST_NAME}"
        )
    plan = _render_plan(project, templates, context, "abort", None)
    changed = 0
    for target, rendered, _backup, _allow_foreign in plan:
        if check:
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_text() != rendered
            ):
                raise AgentProtocolError(f"stale or missing rendered target: {target}")
        else:
            _directory(target.parent, "target directory")
            if _atomic_write(target, rendered):
                changed += 1
    return len(plan), changed


__all__ = [
    "AgentProtocolError",
    "RenderContext",
    "initialize_project",
    "packaged_template_root",
    "render_project",
    "upgrade_project_templates",
    "seed_project_env",
]
