# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from pathlib import Path

import pytest
from _control_support import git

from devlegate.agent_protocol import RenderContext, initialize_project
from devlegate.project_context import (
    ProjectContextError,
    load_project_context,
    load_worker_project_context,
)


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
        '"reviewer":\n  - "testing.md"\n'
        '"worker":\n  - "worker.md"',
    )

    context = load_project_context(tmp_path)

    assert context.for_role("architect") == (
        Path("README.md"),
        Path("architecture.md"),
    )
    assert context.for_role("reviewer") == (Path("README.md"), Path("testing.md"))
    assert context.for_role("worker") == (Path("worker.md"),)
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


@pytest.fixture
def context_repo(tmp_path):
    git(tmp_path, "init")
    git(tmp_path, "config", "user.name", "Context Test")
    git(tmp_path, "config", "user.email", "context@example.invalid")
    return tmp_path


def commit_context(repo):
    git(repo, "commit", "--allow-empty", "-m", "context fixture")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def test_worker_context_is_ordered_and_pinned_to_original_git_objects(context_repo):
    repo = context_repo
    manifest(repo, '"type": "devlegate.project"\n"worker":\n  - "z.md"\n  - "a.md"')
    (repo / "z.md").write_bytes("Café\r\n".encode())
    (repo / "a.md").write_text("second")
    git(repo, "add", ".")
    admitted = commit_context(repo)
    context = load_worker_project_context(repo, admitted)
    assert [doc.path for doc in context.documents] == ["z.md", "a.md"]
    assert [doc.text for doc in context.documents] == ["Café\r\n", "second"]
    assert context.manifest_blob == git(
        repo, "rev-parse", f"{admitted}:.devlegate/project.md",
    ).stdout.strip()
    assert context.documents[0].blob == git(
        repo, "rev-parse", f"{admitted}:z.md",
    ).stdout.strip()

    manifest(repo, '"type": "devlegate.project"\n"worker":\n  - "a.md"')
    (repo / "a.md").write_text("product advanced")
    git(repo, "add", ".")
    advanced = commit_context(repo)
    (repo / "a.md").write_text("uncommitted substitute")
    assert load_worker_project_context(repo, admitted) == context
    assert load_worker_project_context(repo, advanced).documents[0].text == (
        "product advanced"
    )


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("escape", "escapes project root"), ("absolute", "escapes project root"),
        ("missing", "missing tracked file"), ("untracked", "missing tracked file"),
        ("symlink", "non-regular Git path"), ("symlink-parent", "non-regular Git path"),
        ("gitlink", "non-regular Git path"), ("directory", "non-regular Git path"),
        ("invalid-utf8", "cannot read tracked UTF-8 file"),
        ("invalid-manifest", "invalid NanoYAML"), ("invalid-route", "block sequence"),
        ("manifest-link", "non-regular Git path"),
        ("manifest-parent-link", "non-regular Git path"),
    ],
)
def test_worker_project_context_refuses_unsafe_tracked_inputs(
    context_repo, case, message,
):
    repo = context_repo
    gitlink = commit_context(repo)
    route = {
        "escape": "../worker.md", "absolute": "/worker.md",
        "symlink-parent": "link/doc",
        "gitlink": "sub/doc", "directory": "docs",
    }.get(case, "worker.md")
    manifest(repo, f'"type": "devlegate.project"\n"worker":\n  - "{route}"')
    worker = repo / "worker.md"
    if case == "symlink":
        worker.symlink_to("real.md")
    elif case == "symlink-parent":
        (repo / "link").symlink_to("docs", target_is_directory=True)
    elif case == "directory":
        (repo / "docs").mkdir()
        (repo / "docs/doc").write_text("inside")
    elif case == "invalid-utf8":
        worker.write_bytes(b"\xff")
    elif case == "invalid-manifest":
        manifest(repo, '"type": [')
    elif case == "invalid-route":
        manifest(repo, '"type": "devlegate.project"\n"worker": "doc.md"')
    elif case == "manifest-link":
        path = repo / ".devlegate/project.md"
        path.rename(repo / "real.md")
        path.symlink_to("../real.md")
    elif case == "manifest-parent-link":
        (repo / ".devlegate").rename(repo / "real")
        (repo / ".devlegate").symlink_to("real", target_is_directory=True)
    git(repo, "add", ".")
    if case == "gitlink":
        git(repo, "update-index", "--add", "--cacheinfo", f"160000,{gitlink},sub")
    commit = commit_context(repo)
    if case == "untracked":
        worker.write_text("must not use")
    with pytest.raises(ProjectContextError, match=message):
        load_worker_project_context(repo, commit)


def test_missing_or_unreadable_source_commit_is_not_an_omitted_route(context_repo):
    commit = commit_context(context_repo)
    assert load_worker_project_context(context_repo, commit).manifest_blob is None
    with pytest.raises(ProjectContextError, match="cannot read admitted product"):
        load_worker_project_context(context_repo, "0" * 40)
    with pytest.raises(ProjectContextError, match="invalid admitted product commit"):
        load_worker_project_context(context_repo, "HEAD")
