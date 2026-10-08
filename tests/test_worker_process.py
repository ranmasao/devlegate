# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: E501

import io
import os
import signal
import subprocess
import sys
import threading
import time

import pytest

from devlegate.worker_supervisor import _run_opencode


@pytest.mark.parametrize("kind", ["operator_abort"])
@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_worker_process_group_isolated_and_interrupts_descendant(tmp_path, kind):
    marker = tmp_path / "processes.json"
    script = "import json, os, pathlib, subprocess, sys; child = subprocess.Popen(['sleep', '60']); pathlib.Path(sys.argv[1]).write_text(json.dumps({'worker': os.getpid(), 'session': os.getsid(0), 'group': os.getpgrp(), 'child': child.pid})); child.wait()"

    class Request:
        kind = None

    request = Request()
    result = []
    thread = threading.Thread(
        target=lambda: result.append(
            _run_opencode(
                [sys.executable, "-c", script, str(marker)],
                "prompt",
                stop_request=request,
            )
        )
    )
    thread.start()
    for _ in range(100):
        if marker.exists():
            break
        time.sleep(0.01)
    assert marker.exists()
    processes = __import__("json").loads(marker.read_text())
    assert processes["session"] != os.getsid(0)
    assert processes["group"] != os.getpgrp()
    request.kind = kind
    thread.join(5)
    assert not thread.is_alive()
    assert result[0].interruption_kind == kind
    for process_id in (processes["worker"], processes["child"]):
        for _ in range(100):
            try:
                os.kill(process_id, 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        else:
            pytest.fail(f"process {process_id} survived group interruption")


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_stubborn_worker_group_is_force_killed():
    class Request:
        kind = None

    request = Request()
    timer = threading.Timer(0.2, setattr, args=(request, "kind", "operator_abort"))
    timer.start()
    started = time.monotonic()
    result = _run_opencode(
        ["sh", "-c", "trap '' TERM; sleep 60"], "prompt", stop_request=request
    )
    timer.cancel()
    assert time.monotonic() - started < 2.5
    assert result.interruption_kind == "operator_abort"
    assert result.process_returncode == -signal.SIGINT


def test_worker_prompt_is_delivered_over_stdin_without_argv_pollution(tmp_path):
    prompt = (
        "UNIQUE_PROMPT_MARKER_123456\nmultiple lines, quotes ' \" and shell-looking $HOME; `rm -rf /`\n"
        + ("large prompt line\n" * 5000)
    )
    received = tmp_path / "received.bin"
    arguments = tmp_path / "arguments.json"
    script = "import json, pathlib, sys; pathlib.Path(sys.argv[1]).write_bytes(sys.stdin.buffer.read()); pathlib.Path(sys.argv[2]).write_text(json.dumps(sys.argv))"
    result = _run_opencode(
        [sys.executable, "-c", script, str(received), str(arguments)], prompt
    )
    assert result.process_returncode == 0
    assert result.transport_error is None
    assert received.read_bytes() == prompt.encode("utf-8")
    assert prompt not in __import__("json").loads(arguments.read_text())


def test_worker_prompt_delivery_handles_early_child_exit(tmp_path):
    result = _run_opencode(
        [sys.executable, "-c", "raise SystemExit(0)"], "prompt\n" * 100000
    )
    assert result.process_returncode == 0
    assert result.interruption_kind is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_worker_identity_persistence_failure_terminates_spawned_group():
    captured = []

    def fail(identity):
        captured.append(identity)
        raise RuntimeError("injected runtime-store failure")

    result = _run_opencode(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        "prompt",
        execution_id="execution-1",
        worker_identity_handler=fail,
    )
    assert captured
    assert "worker identity persistence failed" in (result.transport_error or "")
    with pytest.raises(ProcessLookupError):
        os.kill(captured[0].pid, 0)


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_worker_exit_wins_before_shutdown_observation():
    class Request:
        kind = None

    request = Request()
    result = _run_opencode(
        [sys.executable, "-c", "raise SystemExit(7)"], "prompt", stop_request=request
    )
    request.kind = "operator_abort"
    assert result.process_returncode == 7
    assert result.interruption_kind is None


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_leader_exit_does_not_prove_group_retirement(tmp_path):
    marker = tmp_path / "natural-exit.json"
    script = "import json, os, pathlib, subprocess, sys; child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); pathlib.Path(sys.argv[1]).write_text(json.dumps({'child': child.pid})); sys.stdout.close(); sys.stderr.close(); os._exit(0)"
    identities = []
    result = _run_opencode(
        [sys.executable, "-c", script, str(marker)],
        "prompt",
        execution_id="execution-1",
        worker_identity_handler=identities.append,
    )
    assert marker.exists()
    child = __import__("json").loads(marker.read_text())["child"]
    assert result.worker_group_retired is False
    assert (
        result.transport_error
        == "execution cgroup remains populated"
    )
    assert identities
    assert os.kill(child, 0) is None
    try:
        os.killpg(identities[0].pgid, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_setsid_descendant_keeps_execution_cgroup_populated(tmp_path):
    marker = tmp_path / "escaped.json"
    script = (
        "import json, os, pathlib, subprocess, sys, time; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import os, time; os.setsid(); time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'child': child.pid})); "
        "time.sleep(0.2); os._exit(0)"
    )
    identities = []
    result = _run_opencode(
        [sys.executable, "-c", script, str(marker)],
        "prompt",
        execution_id="execution-setsid",
        worker_identity_handler=identities.append,
    )
    child = __import__("json").loads(marker.read_text())["child"]
    assert result.worker_group_retired is False
    assert "execution cgroup remains populated" in (result.transport_error or "")
    try:
        os.kill(child, signal.SIGKILL)
    except ProcessLookupError:
        pass


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_immediate_reparented_child_keeps_execution_cgroup_populated(tmp_path):
    marker = tmp_path / "immediate-escaped.json"
    script = (
        "import json, os, pathlib, subprocess, sys; "
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import os, time; os.setsid(); time.sleep(60)'], "
        "stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL); "
        "pathlib.Path(sys.argv[1]).write_text(json.dumps({'child': child.pid})); "
        "os._exit(0)"
    )
    result = _run_opencode(
        [sys.executable, "-c", script, str(marker)],
        "prompt",
        execution_id="execution-setsid-immediate",
        worker_identity_handler=lambda _identity: None,
    )
    child = __import__("json").loads(marker.read_text())["child"]
    # The leader exits immediately, but the cgroup remains populated.
    assert result.worker_group_retired is False
    assert result.transport_error
    assert os.kill(child, 0) is None
    try:
        os.kill(child, signal.SIGKILL)
    except ProcessLookupError:
        pass


def test_natural_worker_exit_wins_between_timeout_and_signal(monkeypatch):
    class Process:
        pid = 12345

        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.calls = 0

        def wait(self, timeout=None):
            self.calls += 1
            if self.calls == 1:
                raise subprocess.TimeoutExpired("fake", timeout)
            return 7

        def poll(self):
            return 7

    process = Process()
    signals = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 12345)
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))

    class Request:
        kind = "operator_abort"

    result = _run_opencode(["fake"], "prompt", stop_request=Request())
    assert result.process_returncode == 7
    assert result.interruption_kind is None
    assert signals == []


def test_foreground_keyboard_interrupt_classifies_operator_abort(monkeypatch):
    class Process:
        pid = 12345

        def __init__(self):
            self.stdout = io.BytesIO()
            self.stderr = io.BytesIO()
            self.wait_calls = 0

        def wait(self, timeout=None):
            self.wait_calls += 1
            if self.wait_calls == 1:
                raise KeyboardInterrupt
            return -2

        def poll(self):
            return None

    process = Process()
    signals = []
    monkeypatch.setattr(subprocess, "Popen", lambda *args, **kwargs: process)
    monkeypatch.setattr(os, "getpgid", lambda _pid: 12345)
    monkeypatch.setattr(os, "killpg", lambda *args: signals.append(args))
    result = _run_opencode(["fake"], "prompt")
    assert result.process_returncode == -2
    assert result.interruption_kind == "operator_abort"
    assert signals == [(12345, signal.SIGINT)]
