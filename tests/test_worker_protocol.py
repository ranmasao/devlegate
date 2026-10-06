# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import io
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.worker_egress import (
    OpenCodeRunResult,
    WorkerClaim,
    WorkerRunResult,
)
from devlegate.worker_supervisor import WorkerSupervisor


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def report(outcome="completed", summary="implemented", remaining=None, questions=None):
    return (
        json.dumps(
            {
                "type": "tool_use",
                "part": {
                    "type": "tool",
                    "tool": "devlegate_report",
                    "state": {
                        "status": "completed",
                        "input": {
                            "outcome": outcome,
                            "summary": summary,
                            "remaining": [] if remaining is None else remaining,
                            "questions": [] if questions is None else questions,
                        },
                    },
                },
            }
        )
        + "\n"
    ).encode()


def test_worker_boundary_uses_only_workspace_path_and_prompt(tmp_path, monkeypatch):
    calls = []
    parent_pwd = "/some/operator/product/checkout"
    monkeypatch.setenv("PWD", parent_pwd)

    def fake_run(
        command,
        prompt,
        *,
        cwd=None,
        env=None,
        event_handler=None,
        **kwargs,
    ):
        calls.append((command, prompt, cwd, env, event_handler))
        event_handler(json.loads(report().decode()))
        return OpenCodeRunResult(0)

    supervisor = WorkerSupervisor("opencode", "provider/model", "build")
    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    workspace = ExecutionWorkspace(
        "internal-id", "internal-branch", tmp_path, "head", "base", False
    )

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="assembled prompt"
    )
    assert result == WorkerRunResult(
        0, None, WorkerClaim("completed", "implemented", (), ()), None
    )
    assert calls[0][:3] == (
        [
            "opencode",
            "run",
            "--dir",
            str(tmp_path),
            "--format",
            "json",
            "--model",
            "provider/model",
            "--agent",
            "build",
        ],
        "assembled prompt",
        tmp_path,
    )
    assert calls[0][3]["OPENCODE_CONFIG_DIR"] != str(tmp_path)
    assert calls[0][3]["PWD"] == str(tmp_path)
    assert os.environ["PWD"] == parent_pwd


def test_worker_config_is_ephemeral_reserved_and_does_not_mutate_environment(
    tmp_path, monkeypatch
):
    project_tool = tmp_path / ".opencode/tools/devlegate_report.ts"
    project_tool.parent.mkdir(parents=True)
    project_tool.write_text("export default 'project fake'\n")
    captured = {}
    monkeypatch.setenv("WORKER_TEST_ENV", "preserved")

    def fake_popen(command, **kwargs):
        captured.update(command=command, **kwargs)
        config_dir = Path(kwargs["env"]["OPENCODE_CONFIG_DIR"])
        assert (config_dir / "tools/devlegate_report.ts").is_file()
        config = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        assert config["$schema"] == "https://opencode.ai/config.json"
        assert config["permission"]["external_directory"] == {
            "*": "deny",
            f"{tmp_path.resolve()}/**": "allow",
        }
        assert (
            config_dir / "tools/devlegate_report.ts"
        ).read_text() != project_tool.read_text()
        return FakeProcess(report())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is not None
    assert result.transport_ok
    assert result.egress_ok
    config_dir = Path(captured["env"]["OPENCODE_CONFIG_DIR"])
    assert not config_dir.exists()
    assert captured["cwd"] == tmp_path
    assert captured["env"]["WORKER_TEST_ENV"] == "preserved"
    assert os.environ["WORKER_TEST_ENV"] == "preserved"
    assert project_tool.read_text() == "export default 'project fake'\n"


def test_worker_config_permission_is_bound_to_exact_workspace(tmp_path, monkeypatch):
    captured = {}

    def fake_popen(command, **kwargs):
        captured["config"] = json.loads(kwargs["env"]["OPENCODE_CONFIG_CONTENT"])
        captured["command"] = command
        captured["cwd"] = kwargs["cwd"]
        return FakeProcess(report())

    monkeypatch.setattr(subprocess, "Popen", fake_popen)
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace(
        "T-1", "branch", tmp_path / "ticket", "head", "base", False
    )
    workspace.path.mkdir()

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is not None
    permission = captured["config"]["permission"]["external_directory"]
    assert permission["*"] == "deny"
    assert permission[f"{workspace.path.resolve()}/**"] == "allow"
    assert len(permission) == 2
    assert captured["cwd"] == workspace.path.resolve()
    assert captured["command"][3] == str(workspace.path.resolve())
    assert f"{tmp_path.resolve()}/sibling/**" not in permission
    assert f"{Path.home()}/**" not in permission


def test_opencode_consumes_inline_worker_config(tmp_path):
    opencode = shutil.which("opencode")
    if opencode is None:
        pytest.skip("opencode is not installed")
    workspace = (tmp_path / "ticket").resolve()
    workspace.mkdir()
    config = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {
            "external_directory": {
                "*": "deny",
                f"{workspace}/**": "allow",
            }
        },
    }
    result = subprocess.run(
        [opencode, "debug", "config"],
        cwd=workspace,
        env={**os.environ, "OPENCODE_CONFIG_CONTENT": json.dumps(config)},
        text=True,
        capture_output=True,
        check=True,
    )

    resolved = json.loads(result.stdout)
    assert (
        resolved["permission"]["external_directory"]
        == config["permission"]["external_directory"]
    )


@pytest.mark.parametrize("returncode", [0, 7])
def test_worker_result_requires_report_and_keeps_exit_status_distinct(
    tmp_path, monkeypatch, returncode
):
    monkeypatch.setattr(
        subprocess,
        "Popen",
        lambda *args, **kwargs: FakeProcess(b"", returncode=returncode),
    )
    supervisor = WorkerSupervisor("opencode", "provider/model", "")
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "head", "base", False)

    result = supervisor.run(
        execution_id="execution-1", workspace=workspace, prompt="prompt"
    )

    assert result.claim is None
    assert result.transport_ok
    assert not result.egress_ok
    if returncode:
        assert result.process_returncode == returncode
        assert result.egress_error is not None
    else:
        assert result.process_returncode == 0
        assert "exactly one" in result.egress_error
