import pytest

from devlegate.agent_protocol import (
    AgentProtocolError,
    RenderContext,
    initialize_project,
    render_project,
)


def context() -> RenderContext:
    return RenderContext(
        "automation/state",
        "workflow/waiting",
        "workflow/ready",
        "workflow/inspection",
        "workflow/accepted",
        "develop",
    )


def test_init_seeds_templates_manifest_and_natural_targets(tmp_path):
    assert initialize_project(tmp_path, context()) == (2, 2)
    assert (tmp_path / ".devlegate/templates/artifacts.toml").is_file()
    assert (tmp_path / ".devlegate/templates/skills/architect/SKILL.md.tmpl").is_file()
    assert (tmp_path / ".devlegate/templates/skills/reviewer/SKILL.md.tmpl").is_file()
    architect = tmp_path / "skills/architect/SKILL.md"
    reviewer = tmp_path / "skills/reviewer/SKILL.md"
    assert "GENERATED FILE. DO NOT EDIT DIRECTLY." in architect.read_text()
    assert "automation/state" in architect.read_text()
    assert "workflow/inspection" in reviewer.read_text()
    assert "devlegate/control" not in reviewer.read_text()
    assert "`main`" not in reviewer.read_text()
    assert not (tmp_path / ".devlegate/generated").exists()
    for content in (architect.read_text(), reviewer.read_text()):
        assert "Devlegate" not in content
        assert ".env" not in content
        assert ".devlegate" not in content


def test_init_does_not_overwrite_custom_templates(tmp_path):
    initialize_project(tmp_path, context())
    template = tmp_path / ".devlegate/templates/skills/architect/SKILL.md.tmpl"
    template.write_text(template.read_text() + "\nCUSTOM PROJECT RULE\n")

    assert initialize_project(tmp_path, context()) == (2, 1)
    assert "CUSTOM PROJECT RULE" in (
        tmp_path / "skills/architect/SKILL.md"
    ).read_text()
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    manifest_before = manifest.read_bytes()
    initialize_project(tmp_path, context())
    assert manifest.read_bytes() == manifest_before


def test_render_is_deterministic_and_check_is_read_only(tmp_path):
    initialize_project(tmp_path, context())
    generated = tmp_path / "skills/reviewer/SKILL.md"
    before = generated.read_bytes()
    assert render_project(tmp_path, context()) == (2, 0)
    assert generated.read_bytes() == before
    assert render_project(tmp_path, context(), check=True) == (2, 0)
    template = tmp_path / ".devlegate/templates/skills/reviewer/SKILL.md.tmpl"
    template.write_text(template.read_text() + "\nchanged\n")
    with pytest.raises(AgentProtocolError, match="stale"):
        render_project(tmp_path, context(), check=True)
    assert generated.read_bytes() == before


def test_custom_target_mapping_and_recognized_target_update(tmp_path):
    initialize_project(tmp_path, context())
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    manifest.write_text(
        '[[artifact]]\nsource = "skills/architect/SKILL.md.tmpl"\n'
        'target = "docs/architect.md"\n\n'
        '[[artifact]]\nsource = "skills/reviewer/SKILL.md.tmpl"\n'
        'target = "docs/reviewer.md"\n'
    )

    assert render_project(tmp_path, context()) == (2, 2)
    assert (tmp_path / "docs/architect.md").is_file()
    template = tmp_path / ".devlegate/templates/skills/architect/SKILL.md.tmpl"
    template.write_text(template.read_text() + "\ncustomized\n")
    assert render_project(tmp_path, context()) == (2, 1)
    assert "customized" in (tmp_path / "docs/architect.md").read_text()


@pytest.mark.parametrize("template_text", ["{{ UNKNOWN }}", "{{ workflow.todo_path"])
def test_unknown_and_malformed_variables_fail_closed(tmp_path, template_text):
    initialize_project(tmp_path, context())
    template = tmp_path / ".devlegate/templates/skills/architect/SKILL.md.tmpl"
    template.write_text(template_text)

    with pytest.raises(AgentProtocolError):
        render_project(tmp_path, context())


def test_render_rejects_symlinked_protocol_root(tmp_path):
    real = tmp_path / "real"
    real.mkdir()
    (tmp_path / ".devlegate").symlink_to(real, target_is_directory=True)

    with pytest.raises(AgentProtocolError, match="symlinked"):
        initialize_project(tmp_path, context())


def test_render_rejects_symlinked_template_leaf(tmp_path):
    initialize_project(tmp_path, context())
    source = tmp_path / ".devlegate/templates/skills/architect/SKILL.md.tmpl"
    target = tmp_path / "outside.tmpl"
    target.write_text("outside\n")
    source.unlink()
    source.symlink_to(target)

    with pytest.raises(AgentProtocolError, match="template path"):
        render_project(tmp_path, context())


@pytest.mark.parametrize(
    "target",
    ["", "/outside.md", "../outside.md", ".git/config", "skills/../x.md"],
)
def test_manifest_rejects_unsafe_targets(tmp_path, target):
    initialize_project(tmp_path, context())
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    manifest.write_text(
        '[[artifact]]\nsource = "skills/architect/SKILL.md.tmpl"\n'
        f"target = {target!r}\n"
    )

    with pytest.raises(AgentProtocolError, match="manifest target"):
        render_project(tmp_path, context())


def test_manifest_rejects_duplicate_targets(tmp_path):
    initialize_project(tmp_path, context())
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    manifest.write_text(
        '[[artifact]]\nsource = "skills/architect/SKILL.md.tmpl"\n'
        'target = "same.md"\n\n'
        '[[artifact]]\nsource = "skills/reviewer/SKILL.md.tmpl"\n'
        'target = "same.md"\n'
    )

    with pytest.raises(AgentProtocolError, match="duplicate"):
        render_project(tmp_path, context())


def test_unrelated_existing_target_is_not_clobbered(tmp_path):
    initialize_project(tmp_path, context())
    target = tmp_path / "skills/architect/SKILL.md"
    target.write_text("project file\n")

    with pytest.raises(AgentProtocolError, match="not be clobbered"):
        render_project(tmp_path, context())
    assert target.read_text() == "project file\n"


def test_target_symlink_escape_is_rejected(tmp_path):
    initialize_project(tmp_path, context())
    outside = tmp_path / "outside"
    outside.mkdir()
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    manifest.write_text(
        '[[artifact]]\nsource = "skills/architect/SKILL.md.tmpl"\n'
        'target = "escape/SKILL.md"\n'
    )
    (tmp_path / "escape").symlink_to(outside, target_is_directory=True)

    with pytest.raises(AgentProtocolError, match="target path"):
        render_project(tmp_path, context())


def _replace_manifest_with_external_symlink(tmp_path):
    initialize_project(tmp_path, context())
    manifest = tmp_path / ".devlegate/templates/artifacts.toml"
    external = tmp_path.parent / "external-artifacts.toml"
    external.write_text(
        '[[artifact]]\nsource = "skills/architect/SKILL.md.tmpl"\n'
        'target = "external-target.md"\n'
    )
    manifest.unlink()
    manifest.symlink_to(external)
    return manifest, external


@pytest.mark.parametrize("check", [False, True])
def test_render_rejects_symlinked_manifest_without_using_external_mapping(
    tmp_path, check
):
    manifest, external = _replace_manifest_with_external_symlink(tmp_path)
    target = tmp_path / "skills/architect/SKILL.md"
    before = target.read_bytes()

    with pytest.raises(AgentProtocolError, match="unsafe symlinked artifact manifest"):
        render_project(tmp_path, context(), check=check)

    assert manifest.is_symlink()
    assert external.is_file()
    assert target.read_bytes() == before
    assert not (tmp_path / "external-target.md").exists()


def test_init_rejects_symlinked_manifest_without_replacing_it(tmp_path):
    manifest, external = _replace_manifest_with_external_symlink(tmp_path)

    with pytest.raises(AgentProtocolError, match="unsafe symlinked artifact manifest"):
        initialize_project(tmp_path, context())

    assert manifest.is_symlink()
    assert external.is_file()
