# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: E501

import json
import subprocess
import sys
from pathlib import Path

import pytest
from argparse import Namespace

from devlegate.ipc_client import IPCClientError
from devlegate.project_registry import ProjectRegistry, ProjectRegistryError, canonical_env_path
from devlegate.runtime_locator import RuntimeLocator
from devlegate.runtime_store import SQLiteRuntimeStore
from devlegate.systemd_supervisor import SystemdSupervisor, render_unit, unit_path
import devlegate.cli as cli
from _project_support import project

























def test_project_rename_preserves_runtime_identity(
    tmp_path: Path, monkeypatch, capsys
):
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
    assert f"devlegate-{renamed.locator.state_key[:8]}.service" == (
        f"devlegate-{registered.locator.state_key[:8]}.service"
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

def test_project_list_reports_persisted_unit_names_and_missing_projects(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    env_short = project(tmp_path, "short-list")
    env_old = project(tmp_path, "old-list")
    env_plain = project(tmp_path, "plain-list")
    env_missing = project(tmp_path, "missing-list")
    registry = ProjectRegistry()
    targets = {
        alias: registry.register(alias, env)
        for alias, env in (
            ("short", env_short),
            ("old", env_old),
            ("plain", env_plain),
            ("missing", env_missing),
        )
    }
    short_name = f"devlegate-{targets['short'].locator.state_key[:8]}.service"
    old_name = f"devlegate-{targets['old'].locator.state_key}.service"
    for alias, name in (("short", short_name), ("old", old_name)):
        target = targets[alias]
        SQLiteRuntimeStore(
            target.locator.state_dir, target.locator.state_key
        ).establish_systemd_authority(
            unit_name=name,
            state_key=target.locator.state_key,
            env_file=target.env_file,
            repository=target.repo,
        )
    env_missing.unlink()

    assert (
        cli._project_command(
            Namespace(project_action="list", output_format="table")
        )
        == 0
    )
    table = capsys.readouterr().out
    assert short_name in table
    assert old_name in table
    assert "plain-list/.env" in table
    assert "missing-list/.env" in table

    assert (
        cli._project_command(
            Namespace(project_action="list", output_format="json")
        )
        == 0
    )
    rows = json.loads(capsys.readouterr().out)["projects"]
    by_alias = {row["alias"]: row for row in rows}
    assert by_alias["short"]["unit"] == short_name
    assert by_alias["old"]["unit"] == old_name
    assert by_alias["plain"]["unit"] == "-"
    assert by_alias["missing"] == {
        "alias": "missing",
        "env": str(env_missing),
        "state": "missing",
        "unit": "-",
    }

def test_project_alias_rename_does_not_change_systemd_identity(
    tmp_path: Path, monkeypatch
) -> None:
    env = project(tmp_path, "rename-unit")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    registry = ProjectRegistry()
    target = registry.register("rslab2", env)
    unit = f"devlegate-{target.locator.state_key[:8]}.service"
    store = SQLiteRuntimeStore(target.locator.state_dir, target.locator.state_key)
    store.establish_systemd_authority(
        unit_name=unit,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )

    registry.rename("rslab2", "ratil")

    assert (
        registry.target_for_alias("ratil").locator.state_key
        == target.locator.state_key
    )
    assert store.supervision_authority()["unit_name"] == unit

def test_start_systemd_reprovisions_persisted_legacy_name(tmp_path, monkeypatch):
    env = project(tmp_path, "legacy-reprovision")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = ProjectRegistry().register("legacy", env)
    legacy = f"devlegate-{target.locator.state_key}.service"
    store = SQLiteRuntimeStore(target.locator.state_dir, target.locator.state_key)
    store.establish_systemd_authority(
        unit_name=legacy,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )
    calls: list[tuple[str, str | None]] = []

    class FakeSupervisor:
        def install(self, _locator, _env_file, *, name=None, allow_legacy=False):
            calls.append(("install", name))
            return tmp_path / "units" / (name or "unexpected.service")

        def start(self, _locator, *, name=None):
            calls.append(("start", name))

    monkeypatch.setattr(cli, "SystemdSupervisor", FakeSupervisor)

    assert cli._start_systemd(target) == 0
    assert calls == [("install", legacy), ("start", legacy)]
    assert store.supervision_authority()["unit_name"] == legacy

class RecordingSupervisor:
    def __init__(self, calls: list[tuple[str, str | None]]) -> None:
        self._calls = calls

    def inspect(self, _locator, *, name=None, **_kwargs):
        self._calls.append(("inspect", name))
        return True

    def status(self, _locator, *, name=None):
        self._calls.append(("status", name))
        return True

    def stop(self, _locator, *, name=None):
        self._calls.append(("stop", name))

    def restart(self, _locator, *, name=None):
        self._calls.append(("restart", name))

def lifecycle_over_persisted_unit(
    env: Path, unit: str, monkeypatch, capsys
) -> list[tuple[str, str | None]]:
    locator = RuntimeLocator.from_env(env)
    store = SQLiteRuntimeStore(locator.state_dir, locator.state_key)
    store.establish_systemd_authority(
        unit_name=unit,
        state_key=locator.state_key,
        env_file=env.resolve(),
        repository=locator.repo,
    )
    calls: list[tuple[str, str | None]] = []
    monkeypatch.setattr(cli, "SystemdSupervisor", lambda: RecordingSupervisor(calls))
    monkeypatch.setattr(
        cli,
        "request",
        lambda *_args, **_kwargs: {
            "accepted": True,
            "request_id": "lifecycle-request",
            "instance_id": "service-instance",
            "workers": {"active": 0},
        },
    )

    assert cli._stop_service(env, "table") == 0
    assert cli._restart_service(env, "table") == 0

    assert [action for action, _name in calls] == [
        "inspect",
        "status",
        "stop",
        "inspect",
        "status",
        "restart",
    ]
    assert {name for _action, name in calls} == {unit}
    assert capsys.readouterr().out.splitlines() == [
        "service stopped",
        "service restarted",
    ]
    assert store.supervision_authority()["unit_name"] == unit
    return calls

def test_top_level_lifecycle_uses_persisted_legacy_unit_name(
    tmp_path, monkeypatch, capsys
) -> None:
    env = project(tmp_path, "legacy-lifecycle")
    locator = RuntimeLocator.from_env(env)
    legacy = f"devlegate-{locator.state_key}.service"
    compact = f"devlegate-{locator.state_key[:8]}.service"

    calls = lifecycle_over_persisted_unit(env, legacy, monkeypatch, capsys)

    assert legacy != compact
    assert compact not in {name for _action, name in calls}

def test_top_level_lifecycle_uses_persisted_compact_unit_name(
    tmp_path, monkeypatch, capsys
) -> None:
    env = project(tmp_path, "compact-lifecycle")
    locator = RuntimeLocator.from_env(env)
    compact = f"devlegate-{locator.state_key[:8]}.service"

    calls = lifecycle_over_persisted_unit(env, compact, monkeypatch, capsys)

    assert all(name == compact for _action, name in calls)

@pytest.mark.parametrize("action", ["stop", "restart"])
def test_top_level_lifecycle_systemd_failure_is_concise_cli_error(
    tmp_path, monkeypatch, capsys, action
) -> None:
    env = project(tmp_path, f"failing-{action}")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = ProjectRegistry().register("failing", env)
    unit = f"devlegate-{target.locator.state_key[:8]}.service"
    SQLiteRuntimeStore(
        target.locator.state_dir, target.locator.state_key
    ).establish_systemd_authority(
        unit_name=unit,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )

    class FailingSupervisor:
        def inspect(self, _locator, *, name=None, **_kwargs):
            return True

        def status(self, _locator, *, name=None):
            return True

        def stop(self, _locator, *, name=None):
            raise cli.SystemdSupervisorError("systemctl stop failed")

        def restart(self, _locator, *, name=None):
            raise cli.SystemdSupervisorError("systemctl restart failed")

    monkeypatch.setattr(cli, "SystemdSupervisor", FailingSupervisor)
    if action == "stop":
        monkeypatch.setattr(
            cli,
            "request",
            lambda *_args, **_kwargs: {
                "accepted": True,
                "request_id": "lifecycle-request",
                "instance_id": "service-instance",
                "workers": {"active": 0},
            },
        )
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "--env", str(env), action]
    )

    assert cli.main() == 1
    captured = capsys.readouterr()
    assert captured.err == f"devlegate: systemctl {action} failed\n"
    assert "Traceback" not in captured.err
    assert "Traceback" not in captured.out

def test_plain_managed_restart_does_not_require_service_ipc(
    tmp_path, monkeypatch, capsys
) -> None:
    env = project(tmp_path, "plain-restart")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = ProjectRegistry().register("plain", env)
    unit = f"devlegate-{target.locator.state_key[:8]}.service"
    SQLiteRuntimeStore(
        target.locator.state_dir, target.locator.state_key
    ).establish_systemd_authority(
        unit_name=unit,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )
    calls = []

    class Supervisor:
        def inspect(self, _locator, *, name=None, **_kwargs):
            return True

        def status(self, _locator, *, name=None):
            return True

        def restart(self, _locator, *, name=None):
            calls.append(name)

    monkeypatch.setattr(cli, "SystemdSupervisor", Supervisor)
    monkeypatch.setattr(
        cli,
        "request",
        lambda *_args, **_kwargs: pytest.fail("plain restart used service IPC"),
    )
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "--env", str(env), "restart"]
    )

    assert cli.main() == 0
    assert calls == [unit]
    assert capsys.readouterr().out == "service restarted\n"

def test_forced_managed_restart_admits_then_restarts_exact_unit(
    tmp_path, monkeypatch, capsys
) -> None:
    env = project(tmp_path, "forced-restart")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = ProjectRegistry().register("forced", env)
    unit = f"devlegate-{target.locator.state_key[:8]}.service"
    SQLiteRuntimeStore(
        target.locator.state_dir, target.locator.state_key
    ).establish_systemd_authority(
        unit_name=unit,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )
    calls: list[tuple[str, str | None, dict[str, object] | None]] = []

    class Supervisor:
        def inspect(self, _locator, *, name=None, **_kwargs):
            return True

        def status(self, _locator, *, name=None):
            return True

        def restart(self, _locator, *, name=None):
            calls.append(("restart", name, None))

    def request(*_args, **kwargs):
        calls.append(("ipc", None, kwargs.get("payload")))
        return {"accepted": True, "request_id": "lifecycle", "instance_id": "old"}

    monkeypatch.setattr(cli, "SystemdSupervisor", Supervisor)
    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "--env", str(env), "restart", "--force"]
    )

    assert cli.main() == 0
    assert calls == [
        ("ipc", None, {"force": True}),
        ("restart", unit, None),
    ]
    assert capsys.readouterr().out == "service restarted\n"

@pytest.mark.parametrize(
    ("error", "expected"),
    [
        (
            IPCClientError(
                "service error: restart payload fields are invalid",
                application=True,
                code="invalid_request",
            ),
            "installed CLI and daemon protocol versions differ",
        ),
        (
            IPCClientError(
                "service error: restart conflicts with an active stop",
                application=True,
                code="application_error",
            ),
            "restart conflicts with an active stop",
        ),
    ],
)
def test_forced_managed_restart_reports_skew_without_kill_fallback(
    tmp_path, monkeypatch, capsys, error, expected
) -> None:
    env = project(tmp_path, "forced-restart-error")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    target = ProjectRegistry().register("forced", env)
    unit = f"devlegate-{target.locator.state_key[:8]}.service"
    SQLiteRuntimeStore(
        target.locator.state_dir, target.locator.state_key
    ).establish_systemd_authority(
        unit_name=unit,
        state_key=target.locator.state_key,
        env_file=target.env_file,
        repository=target.repo,
    )
    restarted = False

    class Supervisor:
        def inspect(self, _locator, *, name=None, **_kwargs):
            return True

        def status(self, _locator, *, name=None):
            return True

        def restart(self, _locator, *, name=None):
            nonlocal restarted
            restarted = True

    monkeypatch.setattr(cli, "SystemdSupervisor", Supervisor)
    monkeypatch.setattr(
        cli, "request", lambda *_args, **_kwargs: (_ for _ in ()).throw(error)
    )
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "--env", str(env), "restart", "--force"]
    )

    assert cli.main() == 1
    assert expected in capsys.readouterr().err
    assert not restarted

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
