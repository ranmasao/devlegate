# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import json
import subprocess
from argparse import Namespace
from pathlib import Path

import pytest

import devlegate.cli as cli
from devlegate.project_registry import (
    ProjectRegistry,
    ProjectRegistryError,
    canonical_env_path,
    validate_alias,
)
from devlegate.systemd_supervisor import SystemdSupervisor, render_unit, unit_path


def git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )


def project(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / ".env").write_text("STATE_DIR=" + str(tmp_path / (name + "-state")))
    return repo / ".env"


def test_alias_validation_is_conservative() -> None:
    for alias in ("rslab2", "devlegate", "rat-slayer", "lab_3"):
        assert validate_alias(alias) == alias
    for alias in ("", "@foo", "foo/bar", "foo bar", "foo\nbar"):
        with pytest.raises(ProjectRegistryError):
            validate_alias(alias)


def test_registry_is_versioned_deterministic_and_one_to_one(tmp_path: Path) -> None:
    env_a = project(tmp_path, "a")
    env_b = project(tmp_path, "b")
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")

    registry.register("rslab2", env_a)
    registry.register("devlegate", env_b)
    assert registry.target_for_alias("rslab2").env_file == canonical_env_path(env_a)
    assert registry.alias_for_env(env_a) == "rslab2"
    assert registry.projects() == {
        "devlegate": str(canonical_env_path(env_b)),
        "rslab2": str(canonical_env_path(env_a)),
    }
    value = json.loads(registry.path.read_text())
    assert value["version"] == 1
    assert list(value["projects"]) == ["devlegate", "rslab2"]

    with pytest.raises(ProjectRegistryError, match="already registered"):
        registry.register("other", env_a)
    with pytest.raises(ProjectRegistryError, match="already registered"):
        registry.register("rslab2", env_b)


def test_stale_alias_lists_but_does_not_resolve(tmp_path: Path) -> None:
    env = project(tmp_path, "stale")
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")
    registry.register("old", env)
    env.unlink()

    assert registry.projects()["old"] == str(canonical_env_path(env))
    with pytest.raises(ProjectRegistryError, match="missing"):
        registry.target_for_alias("old")


def test_symlink_env_spellings_share_one_registration(tmp_path: Path) -> None:
    env = project(tmp_path, "real")
    link = tmp_path / "alias.env"
    link.symlink_to(env)
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")
    registry.register("real", env)

    with pytest.raises(ProjectRegistryError, match="already registered"):
        registry.register("link", link)


def test_registry_rejects_second_env_in_one_repository(tmp_path: Path) -> None:
    env = project(tmp_path, "collision")
    nested = env.parent / "subdir"
    nested.mkdir()
    nested_env = nested / ".env"
    nested_env.write_text("STATE_DIR=" + str(tmp_path / "nested-state"))
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")
    registry.register("root", env)

    with pytest.raises(ProjectRegistryError, match="must be"):
        registry.register("nested", nested_env)


def test_registry_rejects_alternate_configuration_filename(tmp_path: Path) -> None:
    env = project(tmp_path, "alternate")
    alternate = env.parent / "other.env"
    alternate.write_text(env.read_text())
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")

    with pytest.raises(ProjectRegistryError, match=r"must be .*\.env"):
        registry.register("alternate", alternate)


def test_directory_adoption_uses_repository_root_env(tmp_path: Path) -> None:
    env = project(tmp_path, "adopt")
    nested = env.parent / "subdir"
    nested.mkdir()
    registry = ProjectRegistry(tmp_path / "config" / "projects.json")

    target = registry.register("adopted", cli._project_path(nested))

    assert target.env_file == canonical_env_path(env)
    assert target.repo == env.parent.resolve()


def test_relative_state_dir_is_repository_relative_from_any_cwd(
    tmp_path: Path, monkeypatch
) -> None:
    env = project(tmp_path, "relative-state")
    env.write_text("STATE_DIR=.devlegate-state\n")
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    registry = ProjectRegistry()
    registered = registry.register("relative", env)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    by_alias = registry.target_for_alias("relative")
    by_path = cli._project_target(alias=None, env_file=env)
    monkeypatch.chdir(env.parent)
    by_root = cli._project_target(alias=None, env_file=None)

    assert by_path is not None and by_root is not None
    assert by_alias.locator.state_dir == env.parent / ".devlegate-state"
    assert by_alias.locator.state_dir == by_path.locator.state_dir
    assert by_alias.locator.state_dir == by_root.locator.state_dir
    assert by_alias.locator.state_key == registered.locator.state_key
    assert by_alias.locator.socket_path == by_path.locator.socket_path


def test_global_selector_and_command_local_env_are_distinct() -> None:
    parser = cli.build_parser()
    args = parser.parse_args(["--env", "/tmp/project.env", "status"])
    assert args.service_env == Path("/tmp/project.env")
    assert args.command == "status"
    with pytest.raises(SystemExit):
        parser.parse_args(["status", "--env", "/tmp/project.env"])


def test_selector_exclusivity_and_non_project_rejection() -> None:
    parser = cli.build_parser()
    with pytest.raises(SystemExit):
        cli._selector_argv(
            ["--env", "/tmp/project.env", "@foo", "status"], parser
        )
    with pytest.raises(SystemExit):
        cli._selector_argv(
            ["@foo", "--env", "/tmp/project.env", "status"], parser
        )


def test_alias_and_explicit_env_resolve_from_unrelated_cwd(tmp_path: Path, monkeypatch):
    env = project(tmp_path, "addressed")
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    ProjectRegistry().register("rslab2", env)
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    monkeypatch.chdir(unrelated)

    by_alias = cli._project_target(alias="rslab2", env_file=None)
    by_path = cli._project_target(alias=None, env_file=env)
    assert by_alias is not None and by_path is not None
    assert by_alias.env_file == by_path.env_file
    assert by_alias.repo == by_path.repo
    assert by_alias.locator.state_key == by_path.locator.state_key

    with pytest.raises(cli.DevlegateError, match="not registered"):
        cli._project_target(alias=None, env_file=tmp_path / "unregistered.env")


def test_systemd_address_forms_share_the_same_state_key_and_unit(
    tmp_path: Path, monkeypatch
):
    env = project(tmp_path, "systemd-addressed")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    registered = registry.register("rslab2", env)
    seen: list[str] = []

    class FakeSupervisor:
        def status(self, locator):
            seen.append(f"devlegate-{locator.state_key}.service")
            return False

    monkeypatch.setattr(cli, "SystemdSupervisor", FakeSupervisor)
    for alias, selected_env, cwd in (
        ("rslab2", None, tmp_path / "elsewhere"),
        (None, env, tmp_path / "elsewhere"),
        (None, None, env.parent),
    ):
        cwd.mkdir(exist_ok=True)
        monkeypatch.chdir(cwd)
        args = Namespace(
            service_action="status",
            service_env=selected_env,
            project_alias=alias,
        )
        assert cli._systemd_service_command(args) == 3

    assert seen == [
        f"devlegate-{registered.locator.state_key}.service",
        f"devlegate-{registered.locator.state_key}.service",
        f"devlegate-{registered.locator.state_key}.service",
    ]


def test_project_rename_preserves_runtime_identity(tmp_path: Path, monkeypatch, capsys):
    env = project(tmp_path, "renameable")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    registered = registry.register("rslab2", env)

    assert cli._project_command(
        Namespace(
            project_action="rename",
            old_alias="rslab2",
            new_alias="ratil",
            output_format="table",
        )
    ) == 0
    renamed = registry.target_for_alias("ratil")
    assert renamed.env_file == registered.env_file
    assert renamed.repo == registered.repo
    assert renamed.locator.state_key == registered.locator.state_key
    assert renamed.locator.state_dir == registered.locator.state_dir
    assert renamed.locator.lock_path == registered.locator.lock_path
    assert renamed.locator.socket_path == registered.locator.socket_path
    assert renamed.locator.log_dir == registered.locator.log_dir
    assert f"devlegate-{renamed.locator.state_key}.service" == (
        f"devlegate-{registered.locator.state_key}.service"
    )
    assert "renamed @rslab2 to @ratil" in capsys.readouterr().out


def test_project_remove_preserves_project_and_retained_state(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    env = project(tmp_path, "decommission")
    repo = env.parent
    (repo / ".devlegate").mkdir()
    (repo / ".devlegate" / "project.md").write_text("project\n")
    before_env = env.read_bytes()
    before_project = (repo / ".devlegate" / "project.md").read_bytes()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    registered = registry.register("foo", env)
    state_key = registered.locator.state_key
    retained = registered.locator.state_dir / "runtime.sqlite3"
    retained.parent.mkdir()
    retained.write_text("retained\n")

    assert cli._project_command(
        Namespace(
            project_action="remove",
            alias="@foo",
            output_format="table",
        )
    ) == 0

    assert registry.projects() == {}
    assert env.read_bytes() == before_env
    assert (repo / ".devlegate" / "project.md").read_bytes() == before_project
    assert retained.read_text() == "retained\n"
    with pytest.raises(ProjectRegistryError, match="unknown project alias"):
        registry.target_for_alias("foo")
    assert "project data preserved" in capsys.readouterr().out

    restored = registry.register("bar", env)
    assert restored.locator.state_key == state_key
    assert restored.locator.state_dir == registered.locator.state_dir


def test_project_remove_compare_and_remove_preserves_replaced_alias(
    tmp_path: Path, monkeypatch
) -> None:
    env = project(tmp_path, "compare-remove")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    registry.register("foo", env)
    registry.rename("foo", "bar")

    with pytest.raises(ProjectRegistryError, match="unknown project alias"):
        registry.unregister("foo", expected_env=env)
    assert registry.target_for_alias("bar").env_file == canonical_env_path(env)


def test_project_remove_refuses_unmanaged_unit_and_keeps_alias(
    tmp_path: Path, monkeypatch
) -> None:
    env = project(tmp_path, "unmanaged")
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    registry = ProjectRegistry()
    target = registry.register("foo", env)
    path = unit_path(target.locator)
    path.parent.mkdir(parents=True)
    path.write_text("[Service]\nExecStart=other\n")

    with pytest.raises(cli.DevlegateError, match="unmanaged"):
        cli._project_command(
            Namespace(project_action="remove", alias="@foo", output_format="table")
        )
    assert registry.target_for_alias("foo").env_file == canonical_env_path(env)


def test_project_remove_unit_failure_keeps_alias(tmp_path: Path, monkeypatch) -> None:
    env = project(tmp_path, "unit-failure")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    registry.register("foo", env)

    class FailingSupervisor:
        def inspect(self, _locator, **_kwargs):
            return True

        def status(self, _locator):
            return False

        def remove(self, _locator, **_kwargs):
            raise cli.SystemdSupervisorError("remove failed")

    monkeypatch.setattr(cli, "SystemdSupervisor", FailingSupervisor)
    with pytest.raises(cli.DevlegateError, match="remove failed"):
        cli._project_command(
            Namespace(project_action="remove", alias="@foo", output_format="table")
        )
    assert registry.target_for_alias("foo").env_file == canonical_env_path(env)


def test_project_remove_removes_only_managed_unit_and_keeps_other_project(
    tmp_path: Path, monkeypatch
) -> None:
    env_a = project(tmp_path, "unit-a")
    env_b = project(tmp_path, "unit-b")
    config_home = tmp_path / "config"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    registry = ProjectRegistry()
    target_a = registry.register("a", env_a)
    target_b = registry.register("b", env_b)
    unit_a = unit_path(target_a.locator)
    unit_b = unit_path(target_b.locator)
    unit_a.parent.mkdir(parents=True)
    unit_a.write_text(render_unit(target_a.locator, env_a))
    unit_b.write_text(render_unit(target_b.locator, env_b))

    calls: list[list[str]] = []

    def runner(command: list[str], **_: object):
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr(
        cli, "SystemdSupervisor", lambda: SystemdSupervisor(runner=runner)
    )
    assert cli._project_command(
        Namespace(project_action="remove", alias="@a", output_format="table")
    ) == 0

    assert not unit_a.exists()
    assert unit_b.exists()
    assert registry.target_for_alias("b").env_file == canonical_env_path(env_b)
    assert any(call[-1] == "daemon-reload" for call in calls)
