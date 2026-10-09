# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Daemon worker-loss and recovery supervision semantics."""

from _daemon_support import *  # noqa: F403,F405

from devlegate.execution_containment import DeterministicContainmentProvider
from service_harness import compose_test_service_command

@pytest.mark.parametrize(
    "stage",
    ["checkpointing", "post-checkpoint", "publishing", "post-publication", "lifecycle"],
)
def test_service_survives_recoverable_stage_devlegate_error(
    tmp_path, monkeypatch, capsys, stage
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine.poll_interval = "1"
    first_iteration = threading.Event()
    allow_failure = threading.Event()
    calls = []
    stop_event = threading.Event()

    def iteration():
        calls.append(True)
        if len(calls) == 1:
            engine._state["phase"] = "agent_running"
            engine._state["execution_stage"] = stage
            first_iteration.set()
            allow_failure.wait(2)
            raise DevlegateError("simulated publication transport failure")
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)
    thread = threading.Thread(target=lambda: engine.serve(stop_event), daemon=True)
    thread.start()
    assert first_iteration.wait(2)
    allow_failure.set()
    engine.wake()
    thread.join(timeout=3)

    assert not thread.is_alive()
    assert calls == [True, True]
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == stage
    assert "simulated publication transport failure" in capsys.readouterr().out

def test_recoverable_stage_error_keeps_once_semantics(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine._state["phase"] = "agent_running"
    engine._state["execution_stage"] = "publishing"
    monkeypatch.setattr(
        engine,
        "run_iteration",
        lambda: (_ for _ in ()).throw(DevlegateError("one-shot failure")),
    )

    with pytest.raises(DevlegateError, match="one-shot failure"):
        daemon.run_service(engine, once=True)

@pytest.mark.parametrize("kind", ["operator_abort"])
def test_controlled_worker_interruption_is_persisted_and_preserves_workspace(
    tmp_path, monkeypatch, kind, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    workspace_path = []

    def worker(workspace, _prompt, **_kwargs):
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        stop_intent.request(kind)
        return WorkerRunResult(-2, None, None, None, kind)

    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_intent) == 0
    assert engine._state["phase"] == "idle"
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    metadata = engine._state["failed_executions"]["T-1"]
    assert metadata["interrupted"] is True
    assert metadata["interruption_kind"] == kind
    assert metadata["reason"] == "interrupted: " + kind.replace("_", " ")
    output = capsys.readouterr().out
    assert "execution interrupted: " + kind.replace("_", " ") in output
    assert "execution failed;" not in output

    fresh = ServiceEngine(config)
    failure = fresh.status_view().failed_executions[0]
    assert failure.interruption_kind == kind
    calls = []
    monkeypatch.setattr(
        fresh._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: (
            calls.append(True),
            WorkerRunResult(1, None, None, None),
        )[1],
    )
    assert run_test_iteration(fresh) == (0 if kind == "operator_abort" else 1)
    assert calls == ([] if kind == "operator_abort" else [True])

@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_sigkill_parent_and_retry_refuses_duplicate_worker(
    tmp_path, monkeypatch, short_state_dir
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(
        config.read_text().replace(str(tmp_path / "state"), str(short_state_dir))
    )
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    marker = tmp_path / "retry-processes.jsonl"
    attempt = tmp_path / "attempted"
    worker = tmp_path / "worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        f"attempt = pathlib.Path({str(attempt)!r})\n"
        "if not attempt.exists():\n"
        "    attempt.touch()\n"
        "    raise SystemExit(1)\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'])\n"
        "with pathlib.Path(os.environ['DEVLEGATE_TEST_MARKER']).open('a') as file:\n"
        "    file.write(json.dumps({'worker': os.getpid(), "
        "'child': child.pid}) + '\\n')\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    assert run_test_iteration(ServiceEngine(config)) == 1
    environment = {
        **os.environ,
        "DEVLEGATE_TEST_MARKER": str(marker),
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    daemon_process = subprocess.Popen(
        compose_test_service_command(
            [
                sys.executable,
                "-m",
                "devlegate",
                "--env",
                str(config),
                "foreground",
            ],
            tmp_path / "registry-config" / "containment",
        ),
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    socket_path = ServiceEngine(config).ipc_socket_path
    for _ in range(500):
        if socket_path.exists():
            break
        time.sleep(0.01)
    first = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "devlegate",
            "--env",
            str(config),
            "retry",
            "T-1",
        ],
        cwd=working,
        env=environment,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    try:
        for _ in range(500):
            if marker.exists():
                break
            time.sleep(0.01)
        assert marker.exists()
        first.communicate(timeout=10)
        processes = [json.loads(line) for line in marker.read_text().splitlines()]
        assert len(processes) == 1
        assert os.kill(processes[0]["worker"], 0) is None

        second = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "devlegate",
                "--env",
                str(config),
                "retry",
                "T-1",
            ],
            cwd=working,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        stdout, stderr = second.communicate(timeout=10)
        assert second.returncode == 1, (stdout, stderr)
        assert "service busy; mutable request was not admitted" in stderr
        assert len(marker.read_text().splitlines()) == 1
    finally:
        if first.poll() is None:
            first.communicate(timeout=10)
        if daemon_process.poll() is None:
            daemon_process.kill()
            daemon_process.wait(timeout=10)
        if marker.exists():
            processes = [json.loads(line) for line in marker.read_text().splitlines()]
            if processes:
                try:
                    os.killpg(processes[0]["worker"], signal.SIGKILL)
                except ProcessLookupError:
                    pass

@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_natural_leader_exit_with_live_descendant_blocks_post_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    marker = tmp_path / "natural-worker.json"
    worker = tmp_path / "natural-worker.py"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys\n"
        "child = subprocess.Popen([sys.executable, '-c', "
        "'import time; time.sleep(60)'], stdin=subprocess.DEVNULL, "
        "stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_MARKER']).write_text("
        "json.dumps({'worker': os.getpid(), 'child': child.pid}))\n"
        "sys.stdout.close(); sys.stderr.close(); os._exit(0)\n"
    )
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    worker.chmod(0o755)
    monkeypatch.chdir(working)
    monkeypatch.setenv("DEVLEGATE_TEST_MARKER", str(marker))
    engine = ServiceEngine(config)
    engine._workers.containment_provider = DeterministicContainmentProvider(
        populated_after_leader_exit=True
    )
    original_worker = engine._workers.run

    def run_worker(workspace, prompt, **_kwargs):
        result = original_worker(workspace, prompt, **_kwargs)
        assert result.worker_group_retired is False
        return result

    monkeypatch.setattr(engine._workers, "run", run_worker)
    try:
        with pytest.raises(
            DevlegateError, match="execution cgroup remains populated"
        ):
            run_test_iteration(engine)
        assert marker.exists()
        processes = json.loads(marker.read_text())
        assert os.kill(processes["child"], 0) is None
        assert engine._state["phase"] == "agent_running"
        assert engine._state["execution_stage"] == "worker-running"
        assert engine._state["worker_identity"]["pid"] == processes["worker"]
        control = next((state / "worktrees").glob("*/control"))
        assert not list((control / "executions").glob("**/*.json"))
        restarted = ServiceEngine(config)
        monkeypatch.setattr(
            restarted._workers,
            "run",
            lambda *_args, **_kwargs: pytest.fail(
                "restart reconciliation launched worker"
            ),
        )
        with pytest.raises(DevlegateError, match="ownership is indeterminate"):
            run_test_iteration(restarted)
        assert os.kill(processes["child"], 0) is None
        os.killpg(processes["worker"], signal.SIGKILL)
        for _ in range(100):
            try:
                os.kill(processes["child"], 0)
            except ProcessLookupError:
                break
            time.sleep(0.01)
        launches = []
        monkeypatch.setattr(
            restarted._workers,
            "run",
            lambda workspace, prompt, **_kwargs: (
                launches.append((workspace.path, prompt)),
                WorkerRunResult(1, None, None, None),
            )[1],
        )
        assert run_test_iteration(restarted) == 1
        assert len(launches) == 1
        assert "Continue the existing implementation" in launches[0][1]
        assert restarted._state["phase"] == "idle"
    finally:
        try:
            os.killpg(processes["worker"], signal.SIGKILL)
        except (NameError, ProcessLookupError):
            pass
