# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import os
import shutil
import socket
import struct
import subprocess
import time
from types import SimpleNamespace

import pytest

from devlegate.change_channel import RevisionChannel, comparison_tool, revisions_tool
from devlegate.change_explorer import serialize
from devlegate.change_revisions import (
    RevisionNamespace,
    RevisionUnavailable,
    published_checkpoint_history,
)
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
                         lambda pid: pid == os.getpid() and active[0]) as channel:
        request = {"operation": "revisions", "token": channel.token}
        result = exchange(channel, request)
        assert result["checkpoint_count"] == 0
        assert [r["label"] for r in result["revisions"]] == [
            "BASE", "HEAD", "WORKTREE",
        ]
        for extras in ({"token": "foreign"}, {"path": "/other"}):
            assert exchange(channel, {**request, **extras})["status"] == "UNAVAILABLE"
        active[0] = False
        assert exchange(channel, request)["status"] == "UNAVAILABLE"
        tool = revisions_tool(channel.path, channel.token)
        assert "task states, not raw Git commits" in tool
        assert "args: {}" in tool
    assert not (tmp_path / "socket").exists()


def test_unproven_history_is_not_invented(tmp_path):
    with RevisionChannel(tmp_path / "socket", None, lambda _pid: True) as channel:
        request = {"operation": "revisions", "token": channel.token}
        assert exchange(channel, request)["status"] == "HISTORY_UNAVAILABLE"


def test_supervisor_materializes_and_polls_revision_tool(tmp_path, monkeypatch):
    def fake_run(_command, _prompt, **kwargs):
        config = kwargs["env"]["OPENCODE_CONFIG_DIR"]
        from pathlib import Path

        tool = (Path(config) / "tools" / "revisions.ts").read_text()
        assert "select only useful comparisons" in tool
        assert "only paths and metadata" in (
            Path(config) / "tools" / "changes.ts"
        ).read_text()
        assert "has_more" in (Path(config) / "tools" / "diff.ts").read_text()
        assert "git diff --check" in (Path(config) / "tools" / "check.ts").read_text()
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
                         lambda _pid: True) as channel:
        WorkerSupervisor("opencode", "model", "").run(
            workspace, "prompt", execution_id="execution", revisions=channel,
        )


def test_comparisons_and_report_provenance(tmp_path):
    repo = tmp_path / "code"
    repo.mkdir()

    def git(*args):
        return subprocess.run(["git", *args], cwd=repo, check=True,
                              capture_output=True, text=True).stdout.strip()

    git("init", "-q")
    git("config", "user.email", "test@example.invalid")
    git("config", "user.name", "Test")
    (repo / "file").write_text("before\n")
    git("add", "file")
    git("commit", "-qm", "base")
    base = git("rev-parse", "HEAD")
    (repo / "file").write_text("after\n")
    git("add", "file")
    git("commit", "-qm", "T-1: checkpoint\n\nDevlegate-Execution: first")
    first = git("rev-parse", "HEAD")
    (repo / "file").write_text("last\n")
    git("add", "file")
    git("commit", "-qm", "T-1: checkpoint\n\nDevlegate-Execution: second")
    last = git("rev-parse", "HEAD")

    def report(execution, head):
        return SimpleNamespace(ticket_id="T-1", execution_branch="task",
                               code_base_head=base, workspace_head=head,
                               execution_id=execution)

    reports = [report("second", last), report("noop", first), report("first", first)]
    control = tmp_path / "control"
    control.mkdir()

    def publish(execution):
        relative = f"executions/T-1/{execution}.json"
        (control / relative).write_text("{}")
        git("-C", str(control), "add", relative)
        git("-C", str(control), "-c", "user.name=Owner", "-c", "user.email=owner@test",
            "commit", "-qm", "Publish report")

    git("init", "-q", str(control))
    (control / "executions" / "T-1").mkdir(parents=True)
    for execution in ("first", "noop", "second"):
        publish(execution)
    namespace = published_checkpoint_history(
        repo, control, base, last, reports, "T-1", "task",
    )
    assert namespace.resolve("HEAD-1").identity == first
    assert namespace.inventory()["checkpoint_count"] == 2
    with pytest.raises(RevisionUnavailable):
        published_checkpoint_history(
            repo, control, base, last, [report("foreign", last)], "T-1", "task",
        )
    with RevisionChannel(
        tmp_path / "socket", namespace, lambda _pid: True, repo
    ) as channel:
        request = {"token": channel.token, "from": "BASE", "to": "HEAD"}
        changes = exchange(channel, {**request, "operation": "changes"})
        assert changes["records"][0]["path"] == "file"
        assert "before\\n" not in json.dumps(changes)
        diff = exchange(channel, {**request, "operation": "diff", "path": "file"})
        assert "-before\n" in diff["lines"] and "+last\n" in diff["lines"]
        assert exchange(channel, {**request, "operation": "diff"})["status"] == (
            "UNAVAILABLE"
        )
        assert exchange(channel, {**request, "operation": "diff", "path": "../file"})[
            "status"] == "INVALID_PATH"
        worktree = exchange(channel, {
            **request, "operation": "changes", "to": "WORKTREE",
        })
        assert worktree["status"] == "OK"
        assert [record["path"] for record in worktree["records"]] == ["file"]
        (repo / "file").write_text("live\n")
        (repo / "untracked").write_text("new\n")
        live = exchange(channel, {**request, "operation": "changes", "from": "HEAD",
                                  "to": "WORKTREE"})
        assert {record["path"] for record in live["records"]} == {"file", "untracked"}
        patch = exchange(channel, {**request, "operation": "diff", "from": "HEAD",
                                   "to": "WORKTREE", "path": "file"})
        assert "-last\n" in patch["lines"] and "+live\n" in patch["lines"]
    git("reset", "--hard", base)
    (repo / "untracked").unlink()
    (repo / "product-only").write_text("product\n")
    git("add", "product-only")
    git("commit", "-qm", "Advance product")
    current_base = git("rev-parse", "HEAD")
    git("cherry-pick", first, last)
    replay = git("rev-parse", "HEAD")
    assert replay != last
    rebased = published_checkpoint_history(
        repo, control, current_base, replay, reports, "T-1", "task", allow_replay=True,
    )
    assert rebased.resolve("HEAD").identity == last
    assert rebased.resolve("HEAD-1").identity == first
    assert rebased.inventory()["checkpoint_count"] == 2
    with pytest.raises(RevisionUnavailable, match="cross-base"):
        rebased.comparison("BASE", "HEAD")
    with RevisionChannel(
        tmp_path / "socket", rebased, lambda _pid: True, repo,
    ) as channel:
        request = {"operation": "changes", "token": channel.token, "to": "WORKTREE"}
        assert exchange(channel, {**request, "from": "HEAD"})["records"] == []
        cumulative = exchange(channel, {**request, "from": "BASE"})
        assert [entry["path"] for entry in cumulative["records"]] == ["file"]
    assert "one explicit file" in comparison_tool(tmp_path / "socket", "token", "diff")


def test_budget_is_shared_across_socket_reconnects(tmp_path):
    namespace = RevisionNamespace("base", ())
    with RevisionChannel(tmp_path / "socket", namespace, lambda _pid: True) as channel:
        request = {"token": channel.token, "operation": "revisions"}
        total = 0
        while True:
            result = exchange(channel, request)
            total += len(serialize(result)) + 4
            if result["status"] == "BUDGET_EXHAUSTED":
                break
            assert result["status"] == "OK"
        assert 65000 <= total <= 65536
        assert not result["complete"] and result["next_cursor"] is None


def test_generated_opencode_tools_execute_with_real_peer_credentials(tmp_path):
    node = shutil.which("node")
    assert node is not None, "Node is required to exercise the OpenCode adapter"
    repo = tmp_path / "repo"
    repo.mkdir()

    def git(*args):
        return subprocess.check_output(["git", "-C", repo, *args], text=True).strip()

    git("init", "-q")
    git("-c", "user.name=Owner", "-c", "user.email=owner@test", "commit",
        "--allow-empty", "-qm", "Base")
    (repo / "worker.txt").write_text("<<<<<<< ours\n")
    # The OpenCode plugin helper forwards the tool definition. Use an isolated
    # loader stub, but execute the exact emitted modules and real socket client.
    plugin = tmp_path / "node_modules" / "@opencode-ai" / "plugin"
    plugin.mkdir(parents=True)
    (plugin / "package.json").write_text('{"type":"module","main":"index.js"}')
    (plugin / "index.js").write_text(
        "export const tool = x => x; tool.schema = {string: () => ({optional() {}})};"
    )
    peer = None
    namespace = RevisionNamespace(git("rev-parse", "HEAD"), ())
    with RevisionChannel(tmp_path / "socket", namespace,
                         lambda pid: pid == peer, repo) as channel:
        tools = {"revisions": revisions_tool(channel.path, channel.token)}
        tools.update({op: comparison_tool(channel.path, channel.token, op)
                      for op in ("changes", "diff", "check")})
        for name, content in tools.items():
            (tmp_path / f"{name}.mjs").write_text(content)
        script = tmp_path / "run.mjs"
        script.write_text('''
for (const [name, args] of Object.entries({revisions: {},
    changes: {from: 'BASE', to: 'WORKTREE'},
    diff: {from: 'HEAD', to: 'WORKTREE', path: 'worker.txt'},
    check: {from: 'BASE', to: 'WORKTREE'}})) {
  const {default: tool} = await import(`./${name}.mjs`);
  console.log(await tool.execute(args));
}
''')
        request = {"token": channel.token, "operation": "revisions"}
        assert exchange(channel, request)["status"] == "UNAVAILABLE"
        with subprocess.Popen([node, script], stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE, text=True) as process:
            peer = process.pid
            deadline = time.monotonic() + 10
            try:
                while process.poll() is None and time.monotonic() < deadline:
                    channel.poll()
                    time.sleep(0.005)
                output, error = process.communicate(timeout=1)
            finally:
                if process.poll() is None:
                    process.kill()
            assert process.returncode == 0, error
        revision, paths, patch, hygiene = map(json.loads, output.splitlines())
        assert revision["checkpoint_count"] == 0
        assert [path["path"] for path in paths["records"]] == ["worker.txt"]
        assert "+<<<<<<< ours\n" in patch["lines"]
        assert hygiene["status"] == "findings"
        assert git("rev-parse", "HEAD") not in output
        assert exchange(channel, request)["status"] == "UNAVAILABLE"
