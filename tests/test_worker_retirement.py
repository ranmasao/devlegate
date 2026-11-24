# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import json
import os
import subprocess
import sys
import time

import pytest
from _daemon_support import git, make_engine, run_test_iteration

from devlegate.execution_containment import (
    ContainmentError,
    DurableDeterministicContainmentProvider,
)
from devlegate.execution_result import ExecutionReportStore
from devlegate.runtime import DevlegateError, ServiceEngine


@pytest.mark.parametrize(
    ("mode", "returncode", "fault", "diagnostic"),
    [
        ("none", 0, None, None),
        ("closed", 0, None, None),
        ("inherited", 0, None, None),
        ("inherited", 7, None, None),
        ("inherited", 0, "kill-failed", "injected cgroup.kill failure"),
        ("closed", 0, "populated", "still populated after bounded forced termination"),
        ("closed", 0, "unreadable", "injected cgroup.events failure"),
    ],
)
def test_leader_exit_retires_exact_boundary_before_publication(
    tmp_path, monkeypatch, capsys, mode, returncode, fault, diagnostic,
):
    _, config, _ = make_engine(tmp_path, monkeypatch)
    marker = tmp_path / "leader-exit.jsonl"
    worker = tmp_path / "leader-exit"
    worker.write_text(
        f"#!{sys.executable}\n"
        "import json, os, pathlib, subprocess, sys\n"
        "assert sys.stdin.buffer.read()\n"
        "child = None\n"
        f"if {mode!r} != 'none':\n"
        f"    sink = subprocess.DEVNULL if {mode!r} == 'closed' else None\n"
        "    child = subprocess.Popen([sys.executable, '-c', 'import time; "
        "time.sleep(15)'], start_new_session=True, stdin=subprocess.DEVNULL, "
        "stdout=sink, stderr=sink)\n"
        "pathlib.Path('original-worker-change').write_text('original claim\\n')\n"
        "claim = dict(outcome='completed', summary='original claim', "
        "remaining=[], questions=[])\n"
        "print(json.dumps(dict(type='tool_use', part=dict(type='tool', "
        "tool='devlegate_report', state=dict(status='completed', input=claim)))), "
        "flush=True)\n"
        "print('stderr from the original worker', file=sys.stderr, flush=True)\n"
        f"with open({str(marker)!r}, 'a') as f:\n"
        "    print(json.dumps([os.getpid(), child.pid if child else None]), file=f)\n"
        f"sys.exit({returncode})\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = ServiceEngine(config)
    provider = DurableDeterministicContainmentProvider(tmp_path / "containment")
    engine._workers.containment_provider = provider
    monkeypatch.setattr("devlegate.worker_supervisor.WORKER_TERMINATION_TIMEOUT", 0.25)
    boundaries = []
    forces = []
    supervised_times = []
    create = provider.create
    run_worker = engine._workers.run

    def supervise(*args, **kwargs):
        started = time.monotonic()
        try:
            return run_worker(*args, **kwargs)
        finally:
            supervised_times.append(time.monotonic() - started)

    def create_boundary(execution_id):
        boundary = create(execution_id)
        force, wait = boundary.force_terminate, boundary.wait_empty
        boundaries.append((execution_id, boundary, force, wait))

        def terminate():
            forces.append(execution_id)
            if fault == "kill-failed":
                raise ContainmentError(diagnostic)
            if fault != "populated":
                force()

        def wait_empty(timeout):
            if fault == "unreadable":
                raise OSError(diagnostic)
            return wait(timeout)

        monkeypatch.setattr(boundary, "force_terminate", terminate)
        monkeypatch.setattr(boundary, "wait_empty", wait_empty)
        return boundary

    monkeypatch.setattr(provider, "create", create_boundary)
    monkeypatch.setattr(engine._workers, "run", supervise)
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        if fault:
            with pytest.raises(DevlegateError, match="retirement is unproven"):
                run_test_iteration(engine)
        else:
            assert run_test_iteration(engine) == bool(returncode)
        [duration] = supervised_times
        assert duration < 6
        [launch] = marker.read_text().splitlines()
        leader, child = json.loads(launch)
        [(execution_id, boundary, _, _)] = boundaries
        expected_forces = (
            [] if mode == "none" or fault == "unreadable" else [execution_id]
        )
        assert forces == expected_forces
        assert sentinel.poll() is None
        output = capsys.readouterr().out
        store = ExecutionReportStore(engine.control_worktree)
        if fault:
            assert diagnostic in output
            if mode == "inherited":
                assert "stdout/stderr did not drain before deadline" in output
            assert f"worker leader exited: execution={execution_id}" in output
            assert "execution finished:" not in output
            assert provider.observe(boundary.identity) == "matching-live"
            assert os.kill(child, 0) is None
            assert engine._state["execution_stage"] == "worker-running"
            assert engine._state["worker_identity"]["pid"] == leader
            assert not store.list("T-1")
            remote = git(engine.repo, "ls-remote", "origin", "refs/heads/*T-1*")
            assert not remote.stdout
            restarted = ServiceEngine(config)
            restarted._workers.containment_provider = provider
            with pytest.raises(DevlegateError, match="reconciliation is blocked"):
                run_test_iteration(restarted)
            assert len(boundaries) == 1
            assert not store.list("T-1")
        else:
            assert provider.observe(boundary.identity) == "absent"
            if child is not None:
                with pytest.raises(ProcessLookupError):
                    os.kill(child, 0)
            [report] = store.list("T-1")
            assert report.execution_id == execution_id
            assert report.result.process_returncode == returncode
            assert report.result.conclusion == ("failed" if returncode else "completed")
            assert report.result.transport_error is None
            assert report.result.claim.summary == "original claim"
            published = git(
                engine.repo, "ls-remote", "origin",
                f"refs/heads/{report.execution_branch}",
            ).stdout.split()
            assert published[0] == report.workspace_head
            assert git(
                engine.repo, "show", f"{report.workspace_head}:original-worker-change",
            ).stdout == "original claim\n"
            if not returncode:
                assert run_test_iteration(engine) == 0
                assert store.list("T-1") == (report,)
                assert len(boundaries) == 1
    finally:
        sentinel.kill()
        sentinel.wait(timeout=2)
        for _, boundary, force, wait in boundaries:
            if provider.observe(boundary.identity) == "matching-live":
                force()
                assert wait(3), "test containment cleanup did not finish"
