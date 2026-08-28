"""Deterministic rendering of project-local Architect and Reviewer skills."""

from __future__ import annotations

import os
import re
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path


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
    execution_branch_prefix: str = "devlegate/work/"

    def values(self) -> dict[str, str]:
        return {
            "workflow.control_branch": self.control_branch,
            "workflow.backlog_path": self.backlog_path,
            "workflow.todo_path": self.todo_path,
            "workflow.review_path": self.review_path,
            "workflow.done_path": self.done_path,
            "product.branch": self.product_branch,
            "execution.branch_prefix": self.execution_branch_prefix,
        }


_PLACEHOLDER = re.compile(r"\{\{(.*?)\}\}", re.DOTALL)
_MANIFEST_NAME = "artifacts.toml"
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
_MARKER = "<!-- GENERATED FILE. DO NOT EDIT DIRECTLY. -->\n\n"


def packaged_template_root() -> Path:
    """Return the read-only packaged bootstrap template directory."""
    return Path(__file__).parent / "default_templates"


def _directory(path: Path, label: str, *, create: bool = True) -> None:
    if path.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked {label}: {path}")
    if path.exists() and not path.is_dir():
        raise AgentProtocolError(f"{label} is not a directory: {path}")
    if create:
        path.mkdir(parents=True, exist_ok=True)
    elif not path.is_dir():
        raise AgentProtocolError(f"{label} is missing: {path}")


def _safe_project_roots(
    project: Path, *, create: bool = True
) -> tuple[Path, Path]:
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
            raise AgentProtocolError(
                f"manifest target cannot be under .git: {target}"
            )
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
    for part in relative.parts:
        current = current / part
        if current.is_symlink():
            raise AgentProtocolError(f"unsafe symlinked {label}: {current}")
    return current


def _atomic_write(path: Path, content: str) -> bool:
    if path.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked target file: {path}")
    if path.exists() and not path.is_file():
        raise AgentProtocolError(f"target path is not a file: {path}")
    if path.is_file():
        existing = path.read_text()
        if not existing.startswith(_MARKER):
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


def initialize_project(project: Path, context: RenderContext) -> tuple[int, int]:
    """Seed missing project templates and manifest, then render targets."""
    _protocol, templates = _safe_project_roots(project)
    _directory(templates / "skills", "skills template directory")
    _directory(templates / "prompts", "prompts template directory")
    manifest = templates / _MANIFEST_NAME
    if manifest.is_symlink():
        raise AgentProtocolError(f"unsafe symlinked artifact manifest: {manifest}")
    if not manifest.exists():
        packaged_manifest = packaged_template_root() / "artifacts.toml"
        if not packaged_manifest.is_file():
            raise AgentProtocolError(
                f"packaged manifest is missing: {packaged_manifest}"
            )
        manifest.write_bytes(packaged_manifest.read_bytes())
    elif not manifest.is_file():
        raise AgentProtocolError(f"manifest path is not a file: {manifest}")
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
    return render_project(project, context)


def render_project(
    project: Path, context: RenderContext, *, check: bool = False
) -> tuple[int, int]:
    """Render the declared natural project targets, or check them read-only."""
    _protocol, templates = _safe_project_roots(project, create=not check)
    if not check:
        _directory(templates / "skills", "skills template directory")
        _directory(templates / "prompts", "prompts template directory")
    elif not (templates / _MANIFEST_NAME).is_file():
        raise AgentProtocolError(
            f"artifact manifest is missing: {templates / _MANIFEST_NAME}"
        )
    artifacts = _manifest(templates)
    changed = 0
    for source_relative, target_relative in artifacts:
        source = _check_components(templates, source_relative, "template path")
        if not source.is_file():
            raise AgentProtocolError(f"template path is unavailable: {source}")
        target = _check_components(project, target_relative, "target path")
        if target.parent == project and target.name == ".git":
            raise AgentProtocolError(f"unsafe target path: {target}")
        rendered = _MARKER + _render_template(source.read_text(), context, source)
        if check:
            if (
                target.is_symlink()
                or not target.is_file()
                or target.read_text() != rendered
            ):
                raise AgentProtocolError(
                    f"stale or missing rendered target: {target}"
                )
        else:
            _directory(target.parent, "target directory")
            if _atomic_write(target, rendered):
                changed += 1
    return len(artifacts), changed


__all__ = [
    "AgentProtocolError",
    "RenderContext",
    "initialize_project",
    "packaged_template_root",
    "render_project",
]
