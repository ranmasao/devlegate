# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import socket
import struct

from devlegate.change_channel import RevisionChannel, revisions_tool
from devlegate.change_revisions import RevisionNamespace
from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.worker_egress import OpenCodeRunResult
from devlegate.worker_supervisor import WorkerSupervisor


def exchange(channel, request):
    payload = json.dumps(request).encode()
    with socket.socket(socket.AF_UNIX) as client:
        client.connect(str(channel.path))
        client.sendall(struct.pack(">I", len(payload)) + payload)
        client.settimeout(1)
        for _ in range(10):
            channel.poll()
        length = struct.unpack(">I", client.recv(4))[0]
        return json.loads(client.recv(length))


def test_revision_channel_authorization_and_retirement(tmp_path):
    active = [True]
    with RevisionChannel(tmp_path / "socket", RevisionNamespace("base", ()),
                         lambda: active[0]) as channel:
        request = {"operation": "revisions", "token": channel.token}
        result = exchange(channel, request)
        assert result["checkpoint_count"] == 0
        assert [r["label"] for r in result["revisions"]] == [
            "BASE", "HEAD", "WORKTREE",
        ]
        assert exchange(channel, {**request, "token": "foreign"}) == {
            "status": "UNAVAILABLE",
        }
        assert exchange(channel, {**request, "path": "/other"}) == {
            "status": "UNAVAILABLE",
        }
        active[0] = False
        assert exchange(channel, request) == {"status": "UNAVAILABLE"}
        tool = revisions_tool(channel.path, channel.token)
        assert "task states, not raw Git commits" in tool
        assert "args: {}" in tool
    assert not (tmp_path / "socket").exists()


def test_unproven_history_is_not_invented(tmp_path):
    with RevisionChannel(tmp_path / "socket", None, lambda: True) as channel:
        request = {"operation": "revisions", "token": channel.token}
        assert exchange(channel, request) == {
            "status": "HISTORY_UNAVAILABLE",
        }


def test_supervisor_materializes_and_polls_revision_tool(tmp_path, monkeypatch):
    def fake_run(_command, _prompt, **kwargs):
        config = kwargs["env"]["OPENCODE_CONFIG_DIR"]
        from pathlib import Path

        tool = (Path(config) / "tools" / "revisions.ts").read_text()
        assert "select only useful comparisons" in tool
        request = json.dumps({
            "operation": "revisions", "token": channel.token,
        }).encode()
        with socket.socket(socket.AF_UNIX) as client:
            client.connect(str(channel.path))
            client.sendall(struct.pack(">I", len(request)) + request)
            for _ in range(10):
                kwargs["owner_poll"]()
            size = struct.unpack(">I", client.recv(4))[0]
            assert json.loads(client.recv(size))["status"] == "OK"
        return OpenCodeRunResult(0)

    monkeypatch.setattr("devlegate.worker_supervisor._run_opencode", fake_run)
    workspace = ExecutionWorkspace("T-1", "branch", tmp_path, "base", "base", False)
    with RevisionChannel(tmp_path / "socket", RevisionNamespace("base", ()),
                         lambda: True) as channel:
        WorkerSupervisor("opencode", "model", "").run(
            workspace, "prompt", execution_id="execution", revisions=channel,
        )
