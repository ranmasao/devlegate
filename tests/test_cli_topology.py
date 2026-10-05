# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Independent CLI/service process, lifecycle, and recovery topology proof."""

from _cli_support import *  # noqa: F403,F405
from _cli_support import __version__

@pytest.mark.parametrize("output", ["--json", "--yaml"])
def test_restart_rejects_output_formats(output):
    with pytest.raises(SystemExit):
        build_parser().parse_args(["restart", output])

@pytest.mark.parametrize(
    "selector", [["@project"], ["--env", "/tmp/project.env"]]
)
def test_selector_without_command_fails_without_starting(
    monkeypatch, capsys, selector
):
    monkeypatch.setattr("sys.argv", ["devlegate", *selector])
    monkeypatch.setattr(cli, "_run_default_command", pytest.fail)
    monkeypatch.setattr(cli, "_project_target", pytest.fail)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    assert "a command is required when selecting a project" in capsys.readouterr().err

@pytest.mark.parametrize(
    ("argv", "alias", "env_file"),
    [
        (["@project", "start"], "project", None),
        (["--env", "/tmp/project.env", "start"], None, Path("/tmp/project.env")),
    ],
)
def test_start_routes_selected_project_to_authoritative_start_path(
    monkeypatch, argv, alias, env_file
):
    calls = []

    def start(**kwargs):
        calls.append(kwargs)
        return 0

    monkeypatch.setattr("sys.argv", ["devlegate", *argv])
    monkeypatch.setattr(cli, "_run_default_command", start)

    assert main() == 0
    assert calls == [
        {"project_alias": alias, "service_env": env_file, "startup_fd": None}
    ]

def test_stop_waits_for_authoritative_repeated_lifecycle_request(monkeypatch):
    class Locator:
        socket_path = Path("/tmp/devlegate-test-stop.sock")

        def daemon_authority_present(self):
            return True

    waited = []
    monkeypatch.setattr(
        cli,
        "request",
        lambda *_args, **_kwargs: {
            "accepted": True,
            "request_id": "original-request",
            "instance_id": "instance",
            "workers": {"active": 0},
        },
    )
    monkeypatch.setattr(
        cli,
        "_wait_for_service_stop",
        lambda _locator, request_id, instance_id: waited.append(
            (request_id, instance_id)
        ),
    )

    cli._stop_runtime(Locator(), force=True)

    assert waited == [("original-request", "instance")]

@pytest.mark.parametrize("command", ["stop", "restart"])
@pytest.mark.parametrize("output", ["--json", "--yaml"])
def test_lifecycle_commands_reject_structured_output(command, output):
    with pytest.raises(SystemExit) as error:
        build_parser().parse_args([command, output])
    assert error.value.code == 2

def test_restart_wait_requires_ready_replacement(monkeypatch):
    class Locator:
        socket_path = Path("/tmp/devlegate-test-restart.sock")

        def daemon_authority_present(self):
            return True

    receipt = {
        "request_id": "request",
        "instance_id": "old",
        "replacement_instance_id": "new",
        "action": "restart",
        "state": "completed",
    }
    monkeypatch.setattr(cli, "read_lifecycle_receipt", lambda _locator: receipt)
    monkeypatch.setattr(
        cli,
        "request",
        lambda *_args, **_kwargs: {
            "service": "devlegate",
            "instance_id": "new",
            "ready": False,
        },
    )
    clock = iter((0.0, 11.0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    with pytest.raises(DevlegateError, match="did not become ready"):
        cli._wait_for_service_restart(Locator(), "request", "old")

@pytest.mark.parametrize("command", ["stop", "restart", "signal", "sigint"])
def test_real_service_graceful_lifecycle_waits_for_active_worker(
    git_fixture, monkeypatch, command
):
    monkeypatch.chdir(git_fixture["working"])
    if command == "stop":
        monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    if command == "restart":
        monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    worker = git_fixture["tmp"] / f"graceful-{command}-worker.py"
    pid_file = git_fixture["tmp"] / f"graceful-{command}-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(5)\n"
        "(workspace / 'graceful-worker.txt').write_text('completed\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'graceful', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    worker.chmod(0o755)
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    client = None
    stop_client_completed = False
    natural_exit_completed = False
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
            ),
            timeout=30,
        )
        execution_id = _disk_state(config)["execution_id"]
        action = "stop" if command == "signal" else command
        worker_pid = int(pid_file.read_text())
        if command in {"signal", "sigint"}:
            assert service.process is not None
            service.process.send_signal(
                signal.SIGINT if command == "sigint" else signal.SIGTERM
            )
            if command == "signal":
                os.kill(worker_pid, 0)
        else:
            client = service.start_cli(command)
        status_text = ""

        def lifecycle_status_visible():
            nonlocal status_text
            observed = service.cli("status")
            status_text = observed.stdout
            return (
                observed.returncode == 0
                and f"will {action} at checkpoint" in status_text
            )

        if command in {"signal", "sigint"}:
            assert service.process is not None
            service.process.wait(timeout=30)
            if command == "sigint":
                assert service.process.returncode == 130
        else:
            service.wait_for(lifecycle_status_visible, timeout=10)
            assert "a worker is active" in status_text
        if client is not None:
            if command == "stop":
                assert client.wait(timeout=10) == 0
            else:
                assert client.poll() is None
            if command in {"stop", "restart"}:
                os.kill(worker_pid, 0)
        if command == "restart":
            handoff_path = service.locator.state_dir / "lifecycle" / (
                f"{service.locator.state_key}.json"
            )
            service.wait_for(
                lambda: (
                    handoff_path.exists()
                    and json.loads(handoff_path.read_text())["state"] == "handoff"
                ),
                timeout=10,
            )
            contender = service.start_cli()
            contender_stdout, contender_stderr = contender.communicate(timeout=10)
            assert "service started" not in contender_stdout
            assert "service started" not in contender_stderr
        if client is not None:
            stdout, stderr = client.communicate(timeout=30)
            assert client.returncode == 0, (stdout, stderr)
            stop_client_completed = command == "stop"
        if command == "restart":
            service.wait_for(
                lambda: (
                    service.process is not None
                    and service.process.poll() is None
                    and _disk_state(config)["phase"] == "idle"
                )
            )
            receipt_path = service.locator.state_dir / "lifecycle" / (
                f"{service.locator.state_key}.json"
            )
            receipt = json.loads(receipt_path.read_text())
            assert receipt["action"] == "restart"
            assert receipt["state"] == "completed"
        else:
            if command == "signal":
                assert service.process is not None
                service.process.wait(timeout=30)
            elif command == "stop":
                service.wait_exited(timeout=30)
            assert not service.locator.daemon_authority_present()
        if command == "stop":
            receipt_path = service.locator.state_dir / "lifecycle" / (
                f"{service.locator.state_key}.json"
            )
            receipt = json.loads(receipt_path.read_text())
            assert receipt["action"] == "stop"
            assert receipt["state"] == "completed"
            service.wait_exited()
            execution_path = service.locator.execution_log_dir / f"{execution_id}.log"
            service._collect_output()
            service_log_text = service.stdout
            assert (
                f"execution starting: ticket=T-1 execution={execution_id} "
                f"log={execution_path}"
            ) in service_log_text
            assert (
                f"execution finished: ticket=T-1 execution={execution_id} "
                f"log={execution_path}"
            ) in service_log_text
            natural_exit_completed = True
    finally:
        if client is not None and client.poll() is None:
            client.kill()
            client.wait(timeout=5)
        if service.process is not None and service.process.poll() is None:
            if stop_client_completed and not natural_exit_completed:
                service.kill()
            else:
                service.stop()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_real_cli_force_stop_preserves_interrupted_execution(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "force-worker.py"
    pid_file = git_fixture["tmp"] / "force-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
        "(workspace / 'must-not-complete.txt').write_text('completed\\n')\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
                and _disk_state(config).get("worker_identity") is not None
            ),
            timeout=30,
        )
        before = _disk_state(config)
        execution_id = before["execution_id"]
        execution_path = Path(before["execution_path"])
        execution_branch = before["execution_branch"]
        worker_pid = int(pid_file.read_text())

        result = service.cli("stop", "--force", timeout=30)

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "service stopped" in result.stdout
        service.wait_exited(timeout=30)
        assert not service.locator.daemon_authority_present()
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        state = _disk_state(config)
        assert state["execution_id"] == execution_id
        assert state["execution_path"] == str(execution_path)
        assert state["execution_branch"] == execution_branch
        assert state["execution_interruption_kind"] == "operator_abort"
        assert state["execution_stage"] == "post-checkpoint"
        assert execution_path.exists()
        assert state["pending_execution_report"]["execution_id"] == execution_id

        restarted = ServiceEngine(config)
        assert restarted._state["execution_id"] == execution_id
        assert restarted._state["execution_ticket_id"] == "T-1"
        assert restarted._has_recoverable_execution_stage()
    finally:
        if service.process is not None and service.process.poll() is None:
            service.kill()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_real_cli_force_stop_escalates_existing_graceful_lifecycle(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "force-escalation-worker.py"
    pid_file = git_fixture["tmp"] / "force-escalation-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
            ),
            timeout=30,
        )
        graceful = service.cli("stop", timeout=10)
        assert graceful.returncode == 0, (graceful.stdout, graceful.stderr)
        lifecycle = ipc_request(service.locator.socket_path, "ping")["lifecycle"]
        first_request_id = lifecycle["request_id"]
        assert lifecycle["intent"] == "stop"
        assert lifecycle["force"] is False
        assert lifecycle["phase"] == "draining"

        forced = service.cli("stop", "--force", timeout=30)

        assert forced.returncode == 0, (forced.stdout, forced.stderr)
        service.wait_exited(timeout=30)
        receipt_path = service.locator.state_dir / "lifecycle" / (
            f"{service.locator.state_key}.json"
        )
        receipt = json.loads(receipt_path.read_text())
        assert receipt["request_id"] == first_request_id
        assert receipt["state"] == "completed"
        assert _disk_state(config)["execution_interruption_kind"] == "operator_abort"
    finally:
        if service.process is not None and service.process.poll() is None:
            service.kill()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_real_cli_force_restart_preserves_interrupted_execution(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "force-restart-worker.py"
    pid_file = git_fixture["tmp"] / "force-restart-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
        "(workspace / 'must-not-complete.txt').write_text('completed\\n')\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
            ),
            timeout=30,
        )
        before = _disk_state(config)
        execution_id = before["execution_id"]
        execution_path = Path(before["execution_path"])
        execution_branch = before["execution_branch"]
        worker_pid = int(pid_file.read_text())

        result = service.cli("restart", "--force", timeout=30)

        assert result.returncode == 0, (result.stdout, result.stderr)
        assert "service restarted" in result.stdout
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_id") == execution_id
                and _disk_state(config).get("execution_interruption_kind")
                == "operator_abort"
            ),
            timeout=30,
        )
        with pytest.raises(ProcessLookupError):
            os.kill(worker_pid, 0)
        state = _disk_state(config)
        assert state["execution_path"] == str(execution_path)
        assert state["execution_branch"] == execution_branch
        assert state["execution_stage"] == "post-checkpoint"
        assert execution_path.exists()
        assert state["pending_execution_report"]["execution_id"] == execution_id
    finally:
        if service.process is not None and service.process.poll() is None:
            service.kill()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_real_cli_force_restart_escalates_existing_graceful_lifecycle(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "force-restart-escalation-worker.py"
    pid_file = git_fixture["tmp"] / "force-restart-escalation-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    graceful = None
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
            ),
            timeout=30,
        )
        graceful = service.start_cli("restart")
        service.wait_for(
            lambda: ipc_request(service.locator.socket_path, "ping")["lifecycle"][
                "phase"
            ]
            == "draining",
            timeout=10,
        )
        first_request_id = ipc_request(service.locator.socket_path, "ping")[
            "lifecycle"
        ]["request_id"]
        forced = service.cli("restart", "--force", timeout=30)

        assert forced.returncode == 0, (forced.stdout, forced.stderr)
        assert graceful.wait(timeout=30) == 0
        service.wait_for(
            lambda: (
                service.process is not None
                and service.process.poll() is None
                and _disk_state(config).get("execution_interruption_kind")
                == "operator_abort"
            ),
            timeout=30,
        )
        receipt_path = service.locator.state_dir / "lifecycle" / (
            f"{service.locator.state_key}.json"
        )
        receipt = json.loads(receipt_path.read_text())
        assert receipt["request_id"] == first_request_id
        assert receipt["state"] == "completed"
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        if graceful is not None and graceful.poll() is None:
            graceful.kill()
            graceful.wait(timeout=5)
        if service.process is not None and service.process.poll() is None:
            service.kill()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_real_cli_force_restart_escalates_uncooperative_worker(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("DEVLEGATE_HOST_MODE", "internal")
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "force-restart-uncooperative-worker.py"
    pid_file = git_fixture["tmp"] / "force-restart-uncooperative-worker.pid"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, signal, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        f"pathlib.Path({str(pid_file)!r}).write_text(str(os.getpid()))\n"
        "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        "time.sleep(60)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and pid_file.exists()
            ),
            timeout=30,
        )
        result = service.cli("restart", "--force", timeout=30)

        assert result.returncode == 0, (result.stdout, result.stderr)
        service.wait_for(
            lambda: _disk_state(config).get("execution_interruption_kind")
            == "operator_abort",
            timeout=30,
        )
        with pytest.raises(ProcessLookupError):
            os.kill(int(pid_file.read_text()), 0)
    finally:
        if service.process is not None and service.process.poll() is None:
            service.kill()
        if pid_file.exists():
            try:
                os.kill(int(pid_file.read_text()), signal.SIGKILL)
            except (ProcessLookupError, ValueError):
                pass

def test_service_start_forms_are_idempotent_for_healthy_owner(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    with LiveService(git_fixture["working"], git_fixture["config"]) as service:
        locator = RuntimeLocator.from_env(git_fixture["config"])
        assert locator.daemon_authority_present()
        assert locator.socket_path.exists()
        status = service.cli("status", "--json")
        assert status.returncode == 0, status.stderr
        assert json.loads(status.stdout)["execution"]["phase"] == "idle"
        for start_args in (("start",), ("foreground",), ("once",)):
            repeated = service.cli(*start_args)
            assert repeated.returncode == 0, repeated.stderr
            assert repeated.stdout.strip() == "Devlegate service is already running."
        stopped = service.cli("stop")
        assert stopped.returncode == 0, stopped.stderr
    for _attempt in range(100):
        if not locator.daemon_authority_present():
            break
        time.sleep(0.02)
    assert not locator.daemon_authority_present()
    assert not locator.socket_path.exists()
    assert not (
        locator.state_dir / "diagnostics" / f"{locator.state_key}.json"
    ).exists()

def test_once_emits_one_startup_identity_block(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    result = invoke(git_fixture, "once")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("---< D E V L E G A T E >---") == 1
    assert "mode    : direct" in result.stdout
    assert "version :" in result.stdout
    assert "repo    :" in result.stdout
    assert "product :" in result.stdout
    assert "control :" in result.stdout
    assert "pid     :" in result.stdout

def test_stop_without_service_is_conclusive(git_fixture):
    result = invoke(git_fixture, "stop")
    assert result.returncode == 1
    assert result.stderr.strip() == "devlegate: service is not running"

def test_status_without_service_reports_stopped_from_local_observation(git_fixture):
    result = invoke(git_fixture, "status", "--json")

    assert result.returncode == 0
    payload = json.loads(result.stdout)
    assert payload["client"] == {"version": __version__}
    assert payload["service"] == {"state": "stopped"}

def test_persistent_service_survives_successful_iteration_until_stop(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    engine = ServiceEngine(git_fixture["config"])
    stop_event = threading.Event()
    calls = []

    def run_once():
        calls.append(True)
        if len(calls) == 3:
            stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_once)
    engine.poll_interval = "0"
    assert engine.serve(stop_event) == 0
    assert len(calls) == 3

def test_external_attached_host_reports_readiness_without_startup_fd(monkeypatch):
    target = Namespace(env_file=Path("/tmp/service.env"), repo=Path("/tmp/repo"))
    readiness_report = object()
    captured = {}
    startup_modes = []

    monkeypatch.setattr(cli, "hosted_runtime_supported", lambda: True)
    monkeypatch.setattr(cli, "_service_engine", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "_systemd_readiness_report", lambda: readiness_report)
    monkeypatch.setattr(
        cli,
        "_startup_report",
        lambda engine, mode: startup_modes.append(mode),
    )
    monkeypatch.setattr(
        cli,
        "run_service",
        lambda engine, **kwargs: captured.update(kwargs) or 0,
    )

    assert (
        cli._run_attached_target(
            target,
            host_mode=cli.HostingMode.EXTERNAL,
            once=False,
            startup_fd=None,
        )
        == 0
    )
    assert captured["startup_fd"] is None
    assert captured["readiness_report"] is readiness_report
    captured["startup_report"]()
    assert startup_modes == ["external"]

@pytest.mark.parametrize(
    ("host_mode", "startup_fd", "expected_mode"),
    [
        (cli.HostingMode.DIRECT, None, "direct"),
        (cli.HostingMode.INTERNAL, 42, "internal"),
    ],
)
def test_attached_host_startup_report_uses_hosting_mode(
    monkeypatch, host_mode, startup_fd, expected_mode
):
    target = Namespace(env_file=Path("/tmp/service.env"), repo=Path("/tmp/repo"))
    startup_modes = []

    monkeypatch.setattr(cli, "hosted_runtime_supported", lambda: True)
    monkeypatch.setattr(
        cli, "_systemd_authority_established", lambda target: (None, False)
    )
    monkeypatch.setattr(cli, "_service_engine", lambda *args, **kwargs: object())
    monkeypatch.setattr(cli, "_systemd_readiness_report", lambda: None)
    monkeypatch.setattr(
        cli,
        "_startup_report",
        lambda engine, mode: startup_modes.append(mode),
    )
    monkeypatch.setattr(
        cli,
        "run_service",
        lambda engine, **kwargs: kwargs["startup_report"]() or 0,
    )

    assert (
        cli._run_attached_target(
            target,
            host_mode=host_mode,
            once=False,
            startup_fd=startup_fd,
        )
        == 0
    )
    assert startup_modes == [expected_mode]

def test_project_remove_stops_internal_service_and_preserves_project(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    service = LiveService(git_fixture["working"], config)
    env_before = config.read_bytes()
    readme_before = (git_fixture["working"] / "README.md").read_bytes()
    service.start()
    service.wait_ready()
    locator = RuntimeLocator.from_env(config)
    retained = locator.state_dir / "retained-evidence.txt"
    retained.parent.mkdir(parents=True, exist_ok=True)
    retained.write_text("keep\n")

    try:
        result = cli._project_command(
            Namespace(
                project_action="remove",
                alias="@test",
                output_format="table",
            )
        )
        assert result == 0
        service.wait_exited()
    finally:
        if service.process is not None and service.process.poll() is None:
            service.stop()

    assert not locator.daemon_authority_present()
    assert config.read_bytes() == env_before
    assert (git_fixture["working"] / "README.md").read_bytes() == readme_before
    assert retained.read_text() == "keep\n"
    assert "project data preserved" in capsys.readouterr().out

def test_real_service_sigkill_restarts_without_mutation(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    try:
        service.wait_for(lambda: "handled_remote_head" in _disk_state(config))
        before = _disk_state(config)
        service.kill()
        service.restart()
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 0, status.stderr
        assert plan.returncode == 0, plan.stderr
        assert _disk_state(config) == before
    finally:
        service.kill()

@pytest.mark.parametrize("point", ["merge_pending", "merge_after_effect"])
def test_real_service_merge_pending_restart_is_observe_first(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    _advance_product(git_fixture)
    config = _recovery_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        assert crashed["phase"] == "merge_pending"
        assert crashed["remote_head"]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: _disk_state(config)["phase"] == "idle")
        assert (
            git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
            == (crashed["remote_head"])
        )
    finally:
        service.stop()

def test_real_service_many_observers_succeed_while_worker_runs(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "live-worker.py"
    pid_file = git_fixture["tmp"] / "live-worker.pid"
    attempts = git_fixture["tmp"] / "live-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    identity = None
    try:
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                    _disk_state(config).get("worker_identity"),
                    attempts,
                    1,
                )
            ),
            timeout=30,
        )
        identity = _disk_state(config)["worker_identity"]
        responses = _parallel_service_requests(
            service, ["status", "status", "plan", "status", "plan"]
        )
        statuses = [
            response
            for response, method in zip(
                responses, ["status", "status", "plan", "status", "plan"]
            )
            if method == "status"
        ]
        plans = [
            response
            for response, method in zip(
                responses, ["status", "status", "plan", "status", "plan"]
            )
            if method == "plan"
        ]
        assert all(
            status["execution"]["phase"] in {"idle", "agent_running"}
            for status in statuses
        )
        assert all(isinstance(plan["action"], str) for plan in plans)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(identity)
        service.stop()

def test_real_service_same_request_id_retries_concurrently_once(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait(timeout=3)
            return ipc_request(
                service.locator.socket_path,
                "retry",
                {"ticket_id": "T-1"},
                mutable=True,
                request_id="same-request",
                timeout=10,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(submit) for _index in range(2)]
            replies = [future.result(timeout=15) for future in results]
        assert replies == [
            {"accepted": True, "ticket_id": "T-1"},
            {"accepted": True, "ticket_id": "T-1"},
        ]
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                    _disk_state(config).get("worker_identity"),
                    attempts,
                    1,
                )
            ),
            timeout=30,
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        assert set(_disk_state(config)["mutable_receipts"]) == {"same-request"}
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()

def test_real_service_distinct_concurrent_retries_do_not_duplicate_worker(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        barrier = threading.Barrier(2)

        def submit(request_id):
            barrier.wait(timeout=3)
            try:
                return ipc_request(
                    service.locator.socket_path,
                    "retry",
                    {"ticket_id": "T-1"},
                    mutable=True,
                    request_id=request_id,
                    timeout=10,
                )
            except IPCClientError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, request_id) for request_id in ("q1", "q2")]
            replies = [future.result(timeout=15) for future in futures]
        assert sum(isinstance(reply, dict) for reply in replies) == 1
        errors = [reply for reply in replies if isinstance(reply, IPCClientError)]
        assert len(errors) == 1
        assert "service busy; mutable request was not admitted" in str(errors[0])
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                    _disk_state(config).get("worker_identity"),
                    attempts,
                    1,
                )
            ),
            timeout=30,
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        assert len(_disk_state(config)["mutable_receipts"]) == 1
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()

def test_real_service_worker_launch_restart_stays_fail_closed(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts = _recovery_execution_service(
        git_fixture, monkeypatch, "worker-launch"
    )
    try:
        _wait_process_death(service)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-launch"
        )
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 1
        assert json.loads(status.stdout)["execution"]["phase"] == "agent_running"
        assert "ambiguous outcome" in json.loads(plan.stdout)["reason"]
        assert not attempts.exists()
    finally:
        service.stop()

def test_real_service_matching_live_worker_restart_does_not_duplicate(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "live-worker.py"
    pid_file = git_fixture["tmp"] / "live-worker.pid"
    attempts = git_fixture["tmp"] / "live-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    identity = None
    try:
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    pid_file, _disk_state(config).get("worker_identity"), attempts, 1
                )
            )
        )
        identity = _disk_state(config)["worker_identity"]
        service.kill()
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").returncode == 1)
        assert _disk_state(config)["worker_identity"] == identity
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(identity)
        service.stop()

def test_real_service_absent_worker_uses_process_loss_resume(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "absent-worker.py"
    pid_file = git_fixture["tmp"] / "absent-worker.pid"
    attempts = git_fixture["tmp"] / "absent-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    old_identity = None
    new_identity = None
    try:
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    pid_file, _disk_state(config).get("worker_identity"), attempts, 1
                )
            )
        )
        old_identity = _disk_state(config)["worker_identity"]
        service.kill()
        _kill_worker_identity(old_identity)
        pid_file.unlink(missing_ok=True)
        service.restart()
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and isinstance(_disk_state(config).get("worker_identity"), dict)
                and _disk_state(config).get("execution_id")
                != old_identity.get("execution_id")
            )
        )
        new_identity = _disk_state(config)["worker_identity"]
        assert new_identity["execution_id"] != old_identity["execution_id"]
        service.wait_for(
            lambda: _worker_body_started(pid_file, new_identity, attempts, 2),
            timeout=15,
        )
    finally:
        _kill_worker_identity(old_identity)
        _kill_worker_identity(new_identity)
        service.stop()

def test_real_service_auto_resume_survives_review_barrier_and_reschedules(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "auto-resume-worker.py"
    pid_file = git_fixture["tmp"] / "auto-resume-worker.pid"
    attempts = git_fixture["tmp"] / "auto-resume-attempts.txt"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "attempts = pathlib.Path(os.environ['DEVLEGATE_TEST_ATTEMPTS'])\n"
        "count = len(attempts.read_text().splitlines()) if attempts.exists() else 0\n"
        "with attempts.open('a') as handle:\n"
        "    handle.write('attempt\\n')\n"
        "    handle.flush()\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_WORKER_PID']).write_text(str(os.getpid()))\n"
        "if count == 0:\n"
        "    while True:\n"
        "        time.sleep(1)\n"
        "(workspace / 'process-worker.txt').write_text('completed\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'process worker', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    worker.chmod(0o755)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    try:
        service.wait_for(
            lambda: (
                _disk_state(config).get("execution_stage") == "worker-running"
                and _worker_body_started(
                    pid_file, _disk_state(config).get("worker_identity"), attempts, 1
                )
            ),
            timeout=30,
        )
        service.kill()
        os.kill(int(pid_file.read_text()), signal.SIGKILL)
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: (
                _disk_state(config)["phase"] == "idle"
                and (engine.control_worktree / "kanban/review/T-1.md").is_file()
            ),
            timeout=30,
        )
        service.wait_for(
            lambda: (
                _disk_state(config)["phase"] == "idle"
                and len(attempts.read_text().splitlines()) == 2
            ),
            timeout=30,
        )
        assert service.process is not None and service.process.poll() is None
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]

        def review_barrier_plan_is_stable():
            response = service.cli("plan", "--json")
            if response.returncode:
                return False
            try:
                return json.loads(response.stdout).get("action") == "none"
            except json.JSONDecodeError:
                return False

        service.wait_for(review_barrier_plan_is_stable, timeout=30)
        plan = json.loads(service.cli("plan", "--json").stdout)
        assert plan["action"] == "none"
        assert "waiting for review" in plan["reason"]

        review = engine.control_worktree / "kanban/review/T-1.md"
        review.rename(engine.control_worktree / "kanban/todo/T-1.md")
        git(engine.control_worktree, "add", "-A")
        git(engine.control_worktree, "commit", "-m", "return T-1 to todo")
        git(
            engine.control_worktree,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        service.wait_for(
            lambda: (
                (engine.control_worktree / "kanban/review/T-1.md").is_file()
                and attempts.read_text().splitlines()
                == ["attempt", "attempt", "attempt"]
            ),
            timeout=30,
        )
        assert service.process is not None and service.process.poll() is None
    finally:
        if service.process is not None and service.process.poll() is None:
            service.stop()

def test_real_service_post_worker_loss_requires_resume(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _recovery_execution_service(
        git_fixture, monkeypatch, "post-worker"
    )
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        old_id = crashed["execution_id"]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()

        def resumed():
            state = _disk_state(config)
            return (
                state["phase"] == "idle"
                and (engine.control_worktree / "kanban/review/T-1.md").is_file()
            )

        service.wait_for(resumed)
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]
        reports = list((engine.control_worktree / "executions/T-1").glob("*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text())["execution_id"] != old_id
    finally:
        service.stop()

@pytest.mark.parametrize(
    "point",
    [
        "integration_before_effect",
        "integration_product_after_effect",
        "integration_control_commit_after_effect",
        "integration_control_push_after_effect",
    ],
)
def test_real_service_accepted_integration_restart_is_idempotent(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        assert crashed["accepted_integration"]["ticket_id"] == "T-1"
        assert crashed["accepted_integration"]["control_head"] == control_head
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()

        def completed():
            local = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
            remote = git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()
            return (
                _disk_state(config)["phase"] == "idle"
                and (engine.control_worktree / "kanban/done/T-1.md").is_file()
                and remote
                and remote[0] == local
            )

        service.wait_for(completed)
        assert attempts.read_text().splitlines() == ["attempt"]
        assert not (engine.control_worktree / "kanban/accepted/T-1.md").exists()
    finally:
        service.stop()

def test_real_service_foreign_local_control_descendant_stays_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "integration_before_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_before_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        remote_before = git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        foreign = engine.control_worktree / "foreign-control.txt"
        foreign.write_text("unrelated\n")
        git(engine.control_worktree, "add", "foreign-control.txt")
        git(engine.control_worktree, "commit", "-m", "foreign control change")
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process is not None and service.process.poll() is None
        assert (
            git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            == remote_before
        )
        assert foreign.is_file()
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_exact_local_integration_with_foreign_descendant_stays_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv(
        "DEVLEGATE_TEST_CRASH_POINT", "integration_control_commit_after_effect"
    )
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_control_commit_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        local_c = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
        remote_r = git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        assert local_c != control_head
        assert (
            git(engine.control_worktree, "rev-parse", f"{local_c}^").stdout.strip()
            == control_head
        )
        assert remote_r == control_head

        foreign = engine.control_worktree / "foreign-after-integration.txt"
        foreign.write_text("unrelated\n")
        git(engine.control_worktree, "add", foreign.name)
        git(engine.control_worktree, "commit", "-m", "foreign after integration")
        local_x = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
        assert local_x != local_c
        assert (
            git(engine.control_worktree, "rev-parse", f"{local_x}^").stdout.strip()
            == local_c
        )
        assert (
            git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            == control_head
        )

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process is not None and service.process.poll() is None
        json.loads(service.cli("status", "--json").stdout)
        assert (
            git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip() == local_x
        )
        assert (
            git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            == control_head
        )
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_remote_exact_control_descendant_is_recognized(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv(
        "DEVLEGATE_TEST_CRASH_POINT", "integration_control_push_after_effect"
    )
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_control_push_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "later-control.txt").write_text("later\n")
        git(publisher_control, "add", "later-control.txt")
        git(publisher_control, "commit", "-m", "later control change")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/done/T-1.md").is_file()
        )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_remote_interleaving_before_control_commit_blocks(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "integration_before_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_before_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "interleaving.txt").write_text("interleaving\n")
        git(publisher_control, "add", "interleaving.txt")
        git(publisher_control, "commit", "-m", "interleaving control change")
        accepted = publisher_control / "kanban/accepted/T-1.md"
        accepted.rename(publisher_control / "kanban/done/T-1.md")
        git(publisher_control, "add", "-A")
        git(publisher_control, "commit", "-m", "Devlegate integrate T-1")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        remote_before = git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process.poll() is None
        assert (
            git(
                publisher_control,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            == remote_before
        )
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_divergent_control_histories_stay_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv(
        "DEVLEGATE_TEST_CRASH_POINT", "integration_control_commit_after_effect"
    )
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_control_commit_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "divergent.txt").write_text("divergent\n")
        git(publisher_control, "add", "divergent.txt")
        git(publisher_control, "commit", "-m", "divergent control change")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        remote_before = git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process.poll() is None
        assert (
            git(
                publisher_control,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            == remote_before
        )
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

@pytest.mark.parametrize(
    "point",
    [
        "agent_pending",
        "checkpointing",
        "checkpoint_after_effect",
        "post-checkpoint",
        "publishing",
        "publication_after_effect",
        "post-publication",
        "lifecycle",
        "lifecycle_after_effect",
    ],
)
def test_real_service_transaction_stage_restart_preserves_execution(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _recovery_execution_service(
        git_fixture, monkeypatch, point
    )
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        execution_id = crashed.get("execution_id")
        assert isinstance(execution_id, str)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()

        def completed():
            state = _disk_state(config)
            return (
                state["phase"] == "idle"
                and (engine.control_worktree / "kanban/review/T-1.md").is_file()
            )

        service.wait_for(completed)
        reports = list((engine.control_worktree / "executions/T-1").glob("*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text())["execution_id"] == execution_id
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_product_movement_during_downtime_blocks_stale_publish(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _recovery_execution_service(
        git_fixture, monkeypatch, "post-checkpoint"
    )
    try:
        _wait_process_death(service)
        _advance_product(git_fixture)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "post-checkpoint"
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        execution = engine.execution_worktree_root / "work" / "T-1"
        assert not git(
            execution,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/work/T-1",
        ).stdout.strip()
    finally:
        service.stop()

def test_real_service_crash_during_mutable_response_reports_uncertain_delivery(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "uncertain-worker.py"
    attempts = git_fixture["tmp"] / "uncertain-attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "receipt_after_save")
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "receipt_after_save"),
    )
    service.start()
    service.wait_ready()
    try:
        result = service.cli("retry", "T-1", timeout=20)
        assert result.returncode != 0
        assert "retry request outcome is uncertain" in result.stderr
        assert "inspect status before retrying" in result.stderr
        _wait_process_death(service)
        assert service.process.returncode == -signal.SIGKILL
        crashed = _disk_state(config)
        receipts = crashed.get("mutable_receipts")
        assert isinstance(receipts, dict)
        assert any(
            receipt.get("method") == "retry"
            and receipt.get("ticket_id") == "T-1"
            and receipt.get("accepted") is True
            for receipt in receipts.values()
            if isinstance(receipt, dict)
        )
        service.locator.socket_path.unlink(missing_ok=True)
    finally:
        service.stop()

def test_real_service_process_executes_reconciliation_from_real_cli(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    config.write_text(config.read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1"))
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker work\n")
        (git_fixture["working"] / "product-change.txt").write_text("product B\n")
        git(git_fixture["working"], "add", "product-change.txt")
        git(git_fixture["working"], "commit", "-m", "advance product")
        git(git_fixture["working"], "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    target = engine._state["reconciliation"]["observed_product"]
    execution = next((engine.state_dir / "worktrees").glob("*/work/T-1"))
    git(git_fixture["working"], "pull", "--ff-only", "origin", "main")

    with LiveService(git_fixture["working"], config) as service:
        pending_before = _disk_state(config)["reconciliation"]
        service.kill()
        service.restart()
        assert _disk_state(config)["reconciliation"] == pending_before
        result = service.cli("reconcile", "update-base", "T-1", "--onto", target)
        assert result.returncode == 0, result.stderr
        assert "reconciliation request accepted: T-1" in result.stdout

        def resolved():
            status = service.cli("status", "--json")
            if status.returncode != 0:
                return False
            reconciliation = json.loads(status.stdout).get("reconciliation")
            return (
                isinstance(reconciliation, dict)
                and reconciliation.get("status") == "resolved"
            )

        service.wait_for(resolved)
        (git_fixture["working"] / "operator-downtime.txt").write_text("hold\n")
        service.kill()
        service.restart()
        status = service.cli("status", "--json")
        assert status.stdout
        payload = json.loads(status.stdout)
        assert payload["reconciliation"]["effective_base"] == target
        assert git(execution, "rev-parse", "HEAD^").stdout.strip() == target
        assert (execution / "implementation.txt").read_text() == "worker work\n"
    database = next(engine.state_dir.glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    persisted = json.loads(
        connection.execute("SELECT payload FROM runtime_state WHERE id = 1").fetchone()[
            0
        ]
    )
    connection.close()
    assert persisted["resume_required"]["status"] == "required"
