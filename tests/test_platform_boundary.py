# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import sys

import pytest
from test_control_plane import control_fixture, invoke

import devlegate.cli as cli
from devlegate.daemon import run_service
from devlegate.ipc_server import _peer_credentials_are_current_user
from devlegate.platform_support import HOSTED_RUNTIME_ERROR
from devlegate.runtime import DevlegateError, ServiceEngine
from devlegate.runtime_locator import RuntimeLocator


def prepared_project(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return working, config, state


def test_mutable_service_engine_rejects_unsupported_platform(
    tmp_path, monkeypatch
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")

    with pytest.raises(DevlegateError, match=HOSTED_RUNTIME_ERROR):
        ServiceEngine(config)

def test_read_only_service_engine_is_not_rejected_by_hosted_platform_check(
    tmp_path, monkeypatch
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")

    engine = ServiceEngine(config, read_only=True)

    assert engine.read_only is True


def test_service_host_rejects_unsupported_platform_before_authority(
    tmp_path, monkeypatch
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    engine = ServiceEngine(config, read_only=True)

    with pytest.raises(DevlegateError, match=HOSTED_RUNTIME_ERROR):
        run_service(engine, once=True)


@pytest.mark.parametrize("command", ["foreground", "once"])
def test_hosted_commands_reject_unsupported_platform(
    tmp_path, monkeypatch, capsys, command
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(
        cli.sys, "argv", ["devlegate", "--env", str(config), command]
    )

    assert cli.main() == 1
    assert HOSTED_RUNTIME_ERROR in capsys.readouterr().err


def test_background_command_rejects_unsupported_platform_before_spawn(
    tmp_path, monkeypatch, capsys
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")
    monkeypatch.setattr(cli.sys, "argv", ["devlegate", "--env", str(config)])

    assert cli.main() == 1
    assert HOSTED_RUNTIME_ERROR in capsys.readouterr().err
    locator = RuntimeLocator.from_env(config)
    assert not locator.daemon_authority_present()
    assert not locator.socket_path.exists()


def test_check_reports_unsupported_platform_in_human_and_json(
    tmp_path, monkeypatch, capsys
):
    _working, config, _state = prepared_project(tmp_path, monkeypatch)
    monkeypatch.setattr(sys, "platform", "darwin")

    monkeypatch.setattr(
        cli.sys, "argv", ["devlegate", "--env", str(config), "check"]
    )
    assert cli.main() == 1
    human = capsys.readouterr().out
    assert "Platform" in human
    assert HOSTED_RUNTIME_ERROR in human

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "check", "--json"],
    )
    assert cli.main() == 1
    payload = json.loads(capsys.readouterr().out)
    platform_check = next(
        item for item in payload["checks"] if item["name"] == "Platform"
    )
    assert platform_check == {
        "name": "Platform",
        "passed": False,
        "detail": HOSTED_RUNTIME_ERROR,
    }
    assert payload["ready"] is False


def test_ipc_peer_credentials_fail_closed_off_platform(monkeypatch):
    monkeypatch.setattr(sys, "platform", "darwin")

    assert _peer_credentials_are_current_user(object()) is False
