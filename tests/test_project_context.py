from pathlib import Path

import pytest

from devlegate.agent_protocol import RenderContext, initialize_project
from devlegate.project_context import ProjectContextError, load_project_context


def manifest(root: Path, frontmatter: str) -> None:
    path = root / ".devlegate/project.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f"---\n{frontmatter}\n---\n# Guidance\n")


def test_routes_common_to_both_roles_without_embedding_documents(tmp_path):
    for name in ("README.md", "architecture.md", "testing.md"):
        (tmp_path / name).write_text(name)
    manifest(
        tmp_path,
        '"type": "devlegate.project"\n'
        '"common":\n  - "README.md"\n'
        '"architect":\n  - "architecture.md"\n'
        '"reviewer":\n  - "testing.md"',
    )

    context = load_project_context(tmp_path)

    assert context.for_role("architect") == (
        Path("README.md"),
        Path("architecture.md"),
    )
    assert context.for_role("reviewer") == (Path("README.md"), Path("testing.md"))
    assert "README contents" not in (tmp_path / ".devlegate/project.md").read_text()


def test_project_context_accepts_blank_frontmatter_lines(tmp_path):
    (tmp_path / "README.md").write_text("readme")
    manifest(
        tmp_path,
        '"type": "devlegate.project"\n\n"common":\n  - "README.md"',
    )

    assert load_project_context(tmp_path).for_role("architect") == (Path("README.md"),)


@pytest.mark.parametrize(
    "field, value",
    [
        ("common", '"/tmp/context.md"'),
        ("common", '"../context.md"'),
        ("common", '"missing.md"'),
        ("common", '"."'),
        ("common", '"README.md"'),
    ],
)
def test_routes_fail_closed(tmp_path, field, value):
    if value == '"README.md"':
        (tmp_path / "README.md").mkdir()
    manifest(tmp_path, f'"type": "devlegate.project"\n"{field}":\n  - {value}')

    with pytest.raises(ProjectContextError):
        load_project_context(tmp_path)


def test_routes_reject_unknown_fields(tmp_path):
    manifest(tmp_path, '"type": "devlegate.project"\n"magic_context": "x"')
    with pytest.raises(ProjectContextError, match="magic_context"):
        load_project_context(tmp_path, require_context=False)


def test_flow_sequence_routes_are_accepted(tmp_path):
    (tmp_path / "README.md").write_text("readme")
    manifest(tmp_path, '"type": "devlegate.project"\n"common": ["README.md"]')

    context = load_project_context(tmp_path)

    assert context.for_role("architect") == (Path("README.md"),)
    assert context.for_role("reviewer") == (Path("README.md"),)


def test_flow_sequence_routes_use_normal_path_validation(tmp_path):
    manifest(
        tmp_path,
        '"type": "devlegate.project"\n"common": ["missing.md"]',
    )

    with pytest.raises(ProjectContextError, match="path does not exist"):
        load_project_context(tmp_path)


def test_empty_context_is_valid_for_init_but_not_readiness(tmp_path):
    initialize_project(
        tmp_path,
        RenderContext("control", "backlog", "todo", "review", "done", "main"),
    )
    with pytest.raises(ProjectContextError, match="routing is empty"):
        load_project_context(tmp_path)


def test_symlink_escape_is_rejected(tmp_path):
    outside = tmp_path.parent / "outside-context.md"
    outside.write_text("outside")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/link.md").symlink_to(outside)
    manifest(tmp_path, '"type": "devlegate.project"\n"common":\n  - "docs/link.md"')

    with pytest.raises(ProjectContextError, match="symlink"):
        load_project_context(tmp_path)


def test_init_preserves_existing_project_adapter(tmp_path):
    (tmp_path / "README.md").write_text("project")
    manifest(tmp_path, '"type": "devlegate.project"\n"common":\n  - "README.md"')
    before = (tmp_path / ".devlegate/project.md").read_bytes()

    initialize_project(
        tmp_path,
        RenderContext("control", "backlog", "todo", "review", "done", "main"),
    )

    assert (tmp_path / ".devlegate/project.md").read_bytes() == before
    architect = (tmp_path / "skills/architect/SKILL.md").read_text()
    reviewer = (tmp_path / "skills/reviewer/SKILL.md").read_text()
    assert ".devlegate/project.md" in architect
    assert ".devlegate/project.md" in reviewer
    assert "README.md" not in architect
    assert "testing.md" not in reviewer


def test_init_does_not_discover_or_rewrite_foreign_agent_files(tmp_path):
    (tmp_path / "README.md").write_text("readme\n")
    (tmp_path / "AGENTS.md").write_text("agents\n")
    (tmp_path / "CLAUDE.md").write_text("claude\n")
    manifest(tmp_path, '"type": "devlegate.project"\n"common":\n  - "README.md"')
    before = {
        name: (tmp_path / name).read_bytes()
        for name in ("README.md", "AGENTS.md", "CLAUDE.md")
    }

    initialize_project(
        tmp_path,
        RenderContext("control", "backlog", "todo", "review", "done", "main"),
    )

    assert {
        name: (tmp_path / name).read_bytes()
        for name in before
    } == before
