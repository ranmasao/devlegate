# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Independent CLI/service process, lifecycle, and recovery topology proof."""

import select
from contextlib import ExitStack

from _cli_support import *  # noqa: F403,F405
from _cli_support import __version__
from devlegate.execution_result import ExecutionReportStore
from devlegate.implementation_transfer import source_resolution_digest


def _authorize_product(service, config, pending, target, *, execution=None):
    result = None

    def attempt():
        nonlocal result
        result = service.cli(
            "reconcile", "authorize-product", "T-1", "--to", target,
            "--execution", execution or pending["execution_id"],
        )
        if result.returncode and (
            "service busy; mutable request was not admitted" in result.stderr
        ):
            assert _disk_state(config)["reconciliation"] == pending
            return False
        return True

    service.wait_for(attempt)
    return result


def test_reconciliation_admission_retry_only_retries_explicit_busy(monkeypatch):
    pending = {"status": "pending", "execution_id": "execution"}
    monkeypatch.setattr(__import__(__name__), "_disk_state", lambda _: {
        "reconciliation": pending
    })

    class Service:
        def __init__(self, responses):
            self.responses = iter(responses)
            self.attempts = 0

        def cli(self, *args):
            self.attempts += 1
            return next(self.responses)

        def wait_for(self, predicate):
            while not predicate():
                pass

    busy = subprocess.CompletedProcess(
        [], 1, "", "service busy; mutable request was not admitted"
    )
    success = subprocess.CompletedProcess([], 0, "", "")
    other = subprocess.CompletedProcess([], 1, "", "admission coordination timed out")
    service = Service([busy, success])
    assert _authorize_product(service, None, pending, "target") is success
    assert service.attempts == 2
    service = Service([other])
    assert _authorize_product(service, None, pending, "target") is other
    assert service.attempts == 1

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
    identity = {key: [] for key in (
        "version", "repo", "product", "control", "mode", "pid", "instance"
    )}
    for line in result.stdout.splitlines():
        message = line.partition("] ")[2]
        key, separator, value = message.partition(":")
        if separator and key.strip() in identity:
            identity[key.strip()].append(value.strip())

    assert all(len(values) == 1 and values[0] for values in identity.values())
    assert identity["version"] == [__version__]
    assert identity["repo"] == [str(git_fixture["working"])]
    assert identity["mode"] == ["direct"]
    assert int(identity["pid"][0]) > 0

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
        assert status.returncode == 0, status.stderr
        assert "action" in json.loads(status.stdout)["plan"]
        after = _disk_state(config)
        before["workspace_cleanup"].pop("observed_at_unix", None)
        after["workspace_cleanup"].pop("observed_at_unix", None)
        assert after == before
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
        statuses = _parallel_service_requests(service, ["status"] * 5)
        assert all(
            status["execution"]["phase"] in {"idle", "agent_running"}
            for status in statuses
        )
        assert all(isinstance(status["plan"]["action"], str) for status in statuses)
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
        assert status.returncode == 1
        assert json.loads(status.stdout)["execution"]["phase"] == "agent_running"
        assert "ambiguous outcome" in json.loads(status.stdout)["plan"]["reason"]
        assert not attempts.exists()
    finally:
        service.stop()

@pytest.mark.parametrize("interruption", [None, "worker_stalled"])
def test_real_service_matching_live_worker_restart_does_not_duplicate(
    git_fixture, monkeypatch, interruption
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
        if interruption is not None:
            store = ServiceEngine(config)._runtime_store
            state = store.load()
            state["execution_interruption_kind"] = interruption
            store.replace(state)
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").returncode == 1)
        assert _disk_state(config)["worker_identity"] == identity
        assert _disk_state(config).get("execution_interruption_kind") == interruption
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(identity)
        service.stop()

@pytest.mark.parametrize("interruption", ["process_loss", "worker_stalled"])
def test_real_service_absent_worker_restart_requires_trusted_interruption(
    git_fixture, monkeypatch, interruption
):
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
        workspace = Path(_disk_state(config)["execution_path"])
        tracked = workspace / "README.md"
        original = tracked.read_bytes()
        tracked.write_bytes(original + b"\ninterrupted work\n")
        untracked = workspace / "interrupted-work.txt"
        untracked.write_text("unfinished work\n")
        service.kill()
        if interruption == "worker_stalled":
            # Model a service loss after durable classification and before
            # retirement. The real watchdog must eventually write this state.
            store = ServiceEngine(config)._runtime_store
            state = store.load()
            state["execution_interruption_kind"] = interruption
            store.replace(state)
        _kill_worker_identity(old_identity)
        pid_file.unlink(missing_ok=True)
        service.restart()
        if interruption == "worker_stalled":
            service.wait_for(lambda: service.cli("status", "--json").returncode == 1)
            assert _disk_state(config)["worker_identity"] == old_identity
            assert attempts.read_text().splitlines() == ["attempt"]
            return
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
        assert Path(_disk_state(config)["execution_path"]) == workspace
        assert tracked.read_bytes() == original + b"\ninterrupted work\n"
        assert untracked.read_text() == "unfinished work\n"
        failure = _disk_state(config)["failed_executions"]["T-1"]
        assert failure["execution_id"] == old_identity["execution_id"]
        assert failure["interruption_kind"] == interruption
    finally:
        _kill_worker_identity(old_identity)
        _kill_worker_identity(new_identity)
        service.stop()

@pytest.mark.parametrize(
    "restart_at",
    [None, "empty-output", "warning", "post-worker", "stall_classified", "absent",
     "foreign-root", "foreign-generation", "foreign-containment", "optional-tools",
     "persistence", "termination", "membership", "changed-owner", "review"],
)
def test_real_service_live_watchdog_retires_before_resuming(
    git_fixture, monkeypatch, restart_at,
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "watchdog-worker.py"
    result = git_fixture["tmp"] / "continued.txt"
    release = result.with_suffix(".release")
    attempts = result.with_suffix(".attempts")
    faults = {
        "optional-tools", "persistence", "termination", "membership", "changed-owner",
    }
    monkeypatch.setenv("WATCHDOG_TEST_RESULT", str(result))
    if restart_at == "empty-output":
        monkeypatch.setenv("WATCHDOG_TEST_EMPTY_OUTPUT", "1")
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, subprocess, sys, time\n"
        "prompt = sys.stdin.read()\n"
        "result = pathlib.Path(os.environ['WATCHDOG_TEST_RESULT'])\n"
        "def complete():\n"
        "    print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'continued work', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
        "if os.environ.get('WATCHDOG_TEST_REVIEW') and not "
        "pathlib.Path('reviewed-checkpoint.txt').exists():\n"
        "    pathlib.Path('reviewed-checkpoint.txt').write_text('prior checkpoint')\n"
        "    complete()\n"
        "    sys.exit(0)\n"
        "with result.with_suffix('.attempts').open('a') as attempts:\n"
        "    attempts.write(str(os.getpid()) + '\\n')\n"
        "tracked = pathlib.Path('README.md')\n"
        "untracked = pathlib.Path('in-progress.txt')\n"
        "if not untracked.exists():\n"
        "    tracked.write_text(tracked.read_text() + '\\nwork in progress\\n')\n"
        "    untracked.write_text(str(os.getpid()))\n"
        "    def event(kind, part):\n"
        "        print(json.dumps({'type': kind, 'sessionID': 'ses_private_marker',\n"
        "                          'part': part}), flush=True)\n"
        "    if os.environ.get('WATCHDOG_TEST_EMPTY_OUTPUT'):\n"
        "        event('step_start', {})\n"
        "    else:\n"
        "        event('text', {'type': 'text', 'text': 'discard ' * 1024\n"
        "                       + '  end of large payload  '})\n"
        "        event('tool_use', {'type': 'tool', 'tool': 'read', 'state': {\n"
        "            'status': 'completed',\n"
        "            'output': '  observed tool output  \\n'}})\n"
        "        event('text', {'type': 'text', 'text': '  observed stdout  ',\n"
        "                       'metadata': {'ignored': 'x' * 4096}})\n"
        "        print('stderr marker', file=sys.stderr, flush=True)\n"
        "        print(f'pid={os.getpid()} dir={os.getcwd()}',\n"
        "              file=sys.stderr, flush=True)\n"
        "    subprocess.run([sys.executable, '-c', 'import time; time.sleep(2.4)'],\n"
        "                   check=True)\n"
        "    result.with_suffix('.child-done').touch()\n"
        "    while True: time.sleep(0.05)\n"
        "else:\n"
        "    assert not pathlib.Path('/proc/' + untracked.read_text()).exists()\n"
        "    assert tracked.read_text().endswith('work in progress\\n')\n"
        "    assert result.with_suffix('.child-done').exists()\n"
        "    with result.with_suffix('.pending').open('x') as handle:\n"
        "        handle.write(prompt)\n"
        "    os.link(result.with_suffix('.pending'), result)\n"
        "    while not result.with_suffix('.release').exists():\n"
        "        print('continued work', file=sys.stderr, flush=True)\n"
        "        time.sleep(0.05)\n"
        "    complete()\n"
    )
    worker.chmod(0o755)
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
        + "\nWORKER_IDLE_WARNING_SECONDS=0.2\nWORKER_IDLE_RECOVERY_SECONDS=1.5\n"
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    live = {None, "empty-output", "warning", "review"} | faults
    if restart_at in faults:
        monkeypatch.setenv("DEVLEGATE_TEST_WATCHDOG_FAULT", restart_at)
    if restart_at == "review":
        monkeypatch.setenv("WATCHDOG_TEST_REVIEW", "1")
    if restart_at not in live:
        point = "stall_classified" if restart_at == "absent" else (
            "post-worker" if restart_at in {"foreign-root", "foreign-generation"}
            else "stall_classified" if restart_at == "foreign-containment"
            else restart_at
        )
        monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config),
    )
    sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        service.start()
        service.wait_ready()
        if restart_at == "review":
            review = engine.control_worktree / "kanban/review/T-1.md"
            service.wait_for(
                lambda: review.exists() and _disk_state(config)["phase"] == "idle",
            )
            publisher = _ensure_publisher_control(git_fixture)
            git(publisher, "fetch", "origin", "devlegate/control")
            git(publisher, "reset", "--hard", "origin/devlegate/control")
            review = publisher / "kanban/review/T-1.md"
            reviewed = ExecutionReportStore(publisher).list("T-1")[0]
            artifact = publisher / "reviews/T-1" / f"{reviewed.execution_id}.md"
            artifact.parent.mkdir(parents=True)
            artifact.write_text("# REWORK\nVerify the retained checkpoint.\n")
            review.rename(publisher / "kanban/todo/T-1.md")
            git(publisher, "add", "-A")
            git(publisher, "commit", "-m", "return watchdog test to todo")
            git(publisher, "push", "origin", "HEAD:refs/heads/devlegate/control")
        if restart_at in live:
            def warning_visible():
                status = json.loads(service.cli("status", "--json").stdout)
                return status["execution"].get("worker_liveness", {}).get("state") == (
                    "idle/warning"
                )

            service.wait_for(warning_visible)
            if restart_at in faults - {"optional-tools"}:
                service.wait_for(lambda: result.with_suffix(".child-done").exists())
                if restart_at in {"persistence", "termination"}:
                    service.wait_for(
                        lambda: service.cli("status", "--json").returncode == 1,
                    )
                else:
                    # Membership and ownership are still unproven well after
                    # the recovery threshold and the actual child's exit.
                    time.sleep(0.8)
                state = _disk_state(config)
                assert state.get("execution_interruption_kind") == (
                    "worker_stalled" if restart_at == "termination" else None
                )
                assert not result.exists()
                assert len(attempts.read_text().splitlines()) == 1
                assert Path(f"/proc/{state['worker_identity']['pid']}").is_dir()
                return
            if restart_at == "warning":
                observed = _disk_state(config)
                assert observed.get("execution_interruption_kind") is None
                service.kill()
                service.restart()
                service.wait_for(
                    lambda: service.cli("status", "--json").returncode == 1,
                )
                assert _disk_state(config)["worker_identity"] == (
                    observed["worker_identity"]
                )
                assert len(attempts.read_text().splitlines()) == 1
                return
        else:
            _wait_process_death(service)
            classified = _disk_state(config)
            assert classified["execution_interruption_kind"] == "worker_stalled"
            assert len(attempts.read_text().splitlines()) == 1
            if point == "stall_classified":
                assert Path(f"/proc/{classified['worker_identity']['pid']}").is_dir()
            monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
            if restart_at == "absent":
                _kill_worker_identity(classified["worker_identity"])
            elif restart_at == "foreign-containment":
                _kill_worker_identity(classified["worker_identity"])
                root = service.registry_home / "containment"
                pid_file = root / f"execution-{classified['execution_id']}.pid"
                pid_file.rename(pid_file.with_suffix(".original"))
                pid_file.write_text(str(sentinel.pid))
            elif restart_at == "foreign-root":
                root = service.registry_home / "containment"
                root.rename(root.with_name("original-containment"))
            elif restart_at == "foreign-generation":
                workspace = Path(classified["execution_path"])
                admin = Path(subprocess.check_output(
                    ["git", "rev-parse", "--absolute-git-dir"],
                    cwd=workspace, text=True,
                ).strip())
                marker = admin / "devlegate-generation.json"
                generation = json.loads(marker.read_text())
                generation["generation"] = "f" * 64
                marker.write_text(json.dumps(generation))
            service.restart()
            if restart_at in {
                "stall_classified", "foreign-root", "foreign-generation",
                "foreign-containment",
            }:
                service.wait_for(
                    lambda: service.cli("status", "--json").returncode == 1,
                )
                assert not result.exists()
                assert len(attempts.read_text().splitlines()) == 1
                assert _disk_state(config)["worker_identity"] == (
                    classified["worker_identity"]
                )
                return
        service.wait_for(lambda: result.is_file() and result.stat().st_size > 0)
        state = _disk_state(config)
        failure = state["failed_executions"]["T-1"]
        assert failure["execution_id"] != state["execution_id"]
        assert failure["interruption_kind"] == "worker_stalled"
        assert state["worker_prompt_directive"] == "resume"
        assert len(attempts.read_text().splitlines()) == 2
        prompt = result.read_text()
        assert "interrupted after extended output silence" in prompt
        assert "do not assume the previous attempt completed" in prompt
        assert "## Assigned Work\n~~~text\nwork\n~~~" in prompt
        if restart_at == "optional-tools":
            assert "devlegate_blackboard" not in prompt
        if restart_at == "review":
            assert "Verify the retained checkpoint." in prompt
            assert (Path(state["execution_path"]) / "reviewed-checkpoint.txt").is_file()
        for excluded in (
            "## Recovery Context", "README.md", "in-progress.txt",
            "end of large payload", "observed tool output", "observed stdout",
            "stderr marker", "ses_private_marker",
            str(state["execution_path"]), failure["execution_id"],
        ):
            assert excluded not in prompt
        release.touch()
        service.wait_for(lambda: _disk_state(config)["phase"] == "idle")
        if restart_at is None:
            service.restart()
            status = json.loads(service.cli("status", "--json").stdout)
            assert status["execution"]["phase"] == "idle"
            assert "worker_liveness" not in status["execution"]
            assert len(attempts.read_text().splitlines()) == 2
    finally:
        try:
            assert sentinel.poll() is None
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=5)
            service.retire_tagged_processes("WATCHDOG_TEST_RESULT", str(result))

def test_publisher_index_is_independent_of_active_control_commit(git_fixture):
    control = git_fixture["control"]
    publisher = _ensure_publisher_control(git_fixture)
    hooks = git_fixture["tmp"] / "blocking-hooks"
    hooks.mkdir()
    fifo = git_fixture["tmp"] / "commit-release"
    os.mkfifo(fifo)
    hook = hooks / "pre-commit"
    hook.write_text('#!/bin/sh\nprintf "lock-held\\n"\nread token < "$LOCK_FIFO"\n')
    hook.chmod(0o755)
    (control / "owner-change.txt").write_text("owner\n")
    git(control, "add", "owner-change.txt")
    fifo_fd = os.open(fifo, os.O_RDWR)
    process = subprocess.Popen(
        ["git", "-c", f"core.hooksPath={hooks}", "commit", "-a", "-m", "owner change"],
        cwd=control,
        env={**os.environ, "LOCK_FIFO": str(fifo)},
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        assert select.select([process.stderr], [], [], 10)[0], (
            "commit hook did not start"
        )
        assert process.stderr.readline().strip() == "lock-held"
        lock = Path(
            git(control, "rev-parse", "--git-path", "index.lock").stdout.strip()
        )
        assert lock.exists() and process.poll() is None
        collision = git(
            control, "commit", "--allow-empty", "-m", "collision", check=False
        )
        assert collision.returncode != 0 and "index.lock" in collision.stderr

        (publisher / "publisher-change.txt").write_text("publisher\n")
        git(publisher, "add", "publisher-change.txt")
        git(publisher, "commit", "-m", "publisher change")
        git(publisher, "push", "origin", "HEAD:refs/heads/devlegate/control")
        assert lock.exists() and process.poll() is None
    finally:
        os.write(fifo_fd, b"release\n")
        os.close(fifo_fd)
        stdout, stderr = process.communicate(timeout=10)
    assert process.returncode == 0, (stdout, stderr)
    assert not lock.exists()


@pytest.mark.parametrize(
    "rework", ["same-base", "advanced", "conflict", "lost-confirmation"],
)
def test_real_service_auto_resume_survives_review_barrier_and_reschedules(
    git_fixture, monkeypatch, rework,
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "auto-resume-worker.py"
    pid_file = git_fixture["tmp"] / "auto-resume-worker.pid"
    attempts = git_fixture["tmp"] / "auto-resume-attempts.txt"
    prompts = git_fixture["tmp"] / "auto-resume-prompt.txt"
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
        "ack = os.environ.get('DEVLEGATE_TEST_IDENTITY_ACK')\n"
        "while ack and not pathlib.Path(ack).exists():\n"
        "    time.sleep(.01)\n"
        "prompt = sys.stdin.read()\n"
        "(attempts.parent / 'auto-resume-prompt.txt').write_text(prompt)\n"
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
    conflicting = rework in {"conflict", "lost-confirmation"}
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
            response = service.cli("status", "--json")
            if response.returncode:
                return False
            try:
                return json.loads(response.stdout)["plan"].get("action") == "none"
            except json.JSONDecodeError:
                return False

        service.wait_for(review_barrier_plan_is_stable, timeout=30)
        plan = json.loads(service.cli("status", "--json").stdout)["plan"]
        assert plan["action"] == "none"
        assert "waiting for review" in plan["reason"]

        # A scheduler action of "none" is not exclusive ownership of the
        # service's index. Publish from a separate clone at the remote review
        # checkpoint, leaving synchronization of its checkout to the service.
        publisher = _ensure_publisher_control(git_fixture)
        git(publisher, "fetch", "origin", "devlegate/control")
        git(publisher, "reset", "--hard", "origin/devlegate/control")
        review = publisher / "kanban/review/T-1.md"
        report = ExecutionReportStore(publisher).list("T-1")[0]
        artifact = (
            publisher
            / "reviews"
            / "T-1"
            / f"{report.execution_id}.md"
        )
        artifact.parent.mkdir(parents=True)
        artifact.write_text(
            "<!-- devlegate-review-v1\n"
            "ticket: T-1\n"
            f"execution: {report.execution_id}\n"
            f"checkpoint: {report.workspace_head}\n"
            f"control: {report.control_head}\n"
            "disposition: rework\n"
            "-->\n"
            "Re-run the recovered generation through the review barrier.\n"
        )
        review.rename(publisher / "kanban/todo/T-1.md")
        git(publisher, "add", "-A")
        git(publisher, "commit", "-m", "return T-1 to todo")
        control_head = git(publisher, "rev-parse", "HEAD").stdout.strip()
        product = git_fixture["working"]
        if rework != "same-base":
            # Separate publisher; don't touch the active product checkout.
            publisher_product = git_fixture["tmp"] / "rework-product-publisher"
            git(product, "clone", str(product), str(publisher_product))
            git(publisher_product, "config", "user.name", "Product Publisher")
            git(publisher_product, "config", "user.email", "product@example.test")
            path = "process-worker.txt" if conflicting else "advance.txt"
            (publisher_product / path).write_text("product change\n")
            git(publisher_product, "add", path)
            git(publisher_product, "commit", "-m", "advance product")
            base = git(publisher_product, "rev-parse", "HEAD").stdout.strip()
            upstream = git(product, "remote", "get-url", "origin").stdout.strip()
            git(publisher_product, "push", upstream, "HEAD:main")
        else:
            base = report.code_base_head
        git(
            publisher,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        if conflicting:
            def waiting():
                progress = _disk_state(config).get("transfer_progress") or {}
                return progress.get("conflict")

            service.wait_for(waiting, timeout=30)
            conflict = waiting()
            candidate = Path(conflict["path"])
            source = Path(report.execution_path)
            attempt = _disk_state(config)["execution_id"]

            def confirm_cli(path):
                response = None

                def submitted():
                    nonlocal response
                    response = service.cli(
                        "reconcile", "resolve", "T-1", str(path),
                    )
                    return "service busy; mutable request was not admitted" not in (
                        response.stderr
                    )

                service.wait_for(submitted)
                return response

            def published_wait():
                result = service.cli("status", "--json")
                if not result.stdout:
                    return False
                plan = json.loads(result.stdout)["plan"]
                return plan["action"] == "blocked" and str(candidate) in plan["reason"]

            service.wait_for(published_wait, timeout=30)
            inspection = service.cli("inspect", "ticket", "T-1", "--json")
            assert inspection.returncode == 0, inspection.stderr
            observation = json.loads(inspection.stdout)["observation"]
            observed_conflict = observation.get("transfer_conflict")
            if observed_conflict is None:
                observed_conflict = observation.get("rework_conflict")
            assert observed_conflict == conflict
            assert conflict["identity"]["target"] == base
            assert conflict["identity"]["checkpoint"] == report.workspace_head
            assert git(source, "rev-parse", "HEAD").stdout.strip() == (
                report.workspace_head
            )
            assert len(attempts.read_text().splitlines()) == 2
            service.stop()
            service.start()
            service.wait_ready()
            assert waiting() == conflict
            foreign = confirm_cli(git_fixture["publisher"])
            assert foreign.returncode != 0 and "waiting candidate" in foreign.stderr
            response = confirm_cli(candidate)
            assert response.returncode == 0, response.stderr
            service.wait_for(lambda: "conflict markers" in (
                _disk_state(config)["transfer_progress"].get("resolution_error") or ""
            ))
            assert git(source, "rev-parse", "HEAD").stdout.strip() == (
                report.workspace_head
            )
            (candidate / "process-worker.txt").write_text("product and worker\n")
            doctest = "A justified addition:\n>>> print(1 + 1)\n2\n"
            (candidate / "resolution.txt").write_text(doctest)
            service.stop()
            if rework == "lost-confirmation":
                monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "receipt_after_save")
            else:
                gate = git_fixture["tmp"] / "resolution.release"
                monkeypatch.setenv("DEVLEGATE_TEST_RESOLUTION_GATE", str(gate))
            service = LiveService(product, config, command=_recovery_driver(config))
            service.start()
            service.wait_ready()
            assert waiting() == conflict
            assert _disk_state(config)["execution_id"] == attempt
            assert len(attempts.read_text().splitlines()) == 2
            if rework == "lost-confirmation":
                payload = {"ticket_id": "T-1", "candidate": str(candidate),
                           "candidate_digest": source_resolution_digest(candidate)}

                def confirm():
                    result = None

                    def submit():
                        nonlocal result
                        try:
                            result = ipc_request(
                                service.locator.socket_path, "reconcile-resolve",
                                payload,
                                mutable=True, request_id="confirm-rework",
                            )
                        except IPCClientError as error:
                            if "mutable request was not admitted" in str(error):
                                return False
                            raise
                        return True

                    service.wait_for(submit)
                    return result

                with pytest.raises(IPCClientError, match="outcome is uncertain"):
                    confirm()
                _wait_process_death(service)
                crashed = _disk_state(config)
                assert crashed["transfer_progress"]["confirmation"] == {
                    "request_id": "confirm-rework",
                    "source_digest": payload["candidate_digest"],
                }
                assert crashed["mutable_receipts"]["confirm-rework"]["accepted"]
                assert not list(candidate.parent.glob("*.approval.json"))
                assert git(source, "rev-parse", "HEAD").stdout.strip() == (
                    report.workspace_head
                )
                monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
                service.locator.socket_path.unlink(missing_ok=True)
                service = LiveService(product, config)
                service.start()
                service.wait_ready()
                assert confirm() == {"accepted": True, "ticket_id": "T-1"}
            else:
                try:
                    response = confirm_cli(candidate)
                    assert response.returncode == 0, response.stderr
                    assert "decision recorded" in response.stdout
                    service.wait_for(gate.with_suffix(".waiting").exists)
                    progress = _disk_state(config)["transfer_progress"]
                    confirmation = progress["confirmation"]
                    assert ipc_request(
                        service.locator.socket_path, "reconcile-resolve", {
                            "ticket_id": "T-1", "candidate": str(candidate),
                            "candidate_digest": confirmation["source_digest"],
                        }, mutable=True, request_id=confirmation["request_id"],
                    ) == {"accepted": True, "ticket_id": "T-1"}
                    assert not list(candidate.parent.glob("*.approval.json"))
                    assert len(attempts.read_text().splitlines()) == 2
                finally:
                    gate.touch()
        service.wait_for(
            lambda: (
                _disk_state(config)["phase"] == "idle"
                and (engine.control_worktree / "kanban/review/T-1.md").is_file()
                and attempts.read_text().splitlines()
                == ["attempt", "attempt", "attempt"]
            ),
            timeout=30,
        )
        reports = ExecutionReportStore(engine.control_worktree).list("T-1")
        assert len(reports) == 2 and report in reports
        reworked = next(item for item in reports if item != report)
        assert reworked.code_base_head == base and reworked.control_head == control_head
        assert reworked.resource_generation != report.resource_generation
        assert "Re-run the recovered generation" in prompts.read_text()
        pin = f"refs/devlegate/recovery/workspace/T-1/{report.execution_id}"
        assert git(product, "ls-remote", "origin", pin).stdout.split()[0] == (
            report.workspace_head
        )
        assert "REWORK" in prompts.read_text()
        if conflicting:
            assert reworked.execution_id == attempt
            added = Path(reworked.execution_path) / "resolution.txt"
            assert added.read_text() == doctest
        if rework == "lost-confirmation":
            assert confirm() == {"accepted": True, "ticket_id": "T-1"}
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

@pytest.mark.parametrize(
    "point",
    [
        "integration_merging_after_save",
        "integration_product_fast_forward_after_effect",
        "integration_merge_after_effect",
        "integration_published_after_save",
    ],
)
def test_real_service_descendant_integration_replays_every_product_effect_boundary(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "integration_before_effect")
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        before_advance, descendant = _advance_product(git_fixture)
        pending = _disk_state(config)["accepted_integration"]
        checkpoint = pending["checkpoint"]
        assert pending.get("product_transition") is None
        monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
        service.restart()
        service.wait_ready()
        _wait_process_death(service)
        crashed = _disk_state(config)
        transition = crashed["accepted_integration"].get("product_transition")
        assert isinstance(transition, dict)
        assert transition["admitted_base"] == before_advance
        assert transition["predecessor"] == descendant
        expected_status = (
            "published" if point == "integration_published_after_save" else "merging"
        )
        assert transition["status"] == expected_status
        if point == "integration_merge_after_effect":
            local_merge = git(
                git_fixture["working"], "rev-parse", "HEAD"
            ).stdout.strip()
            assert git(
                git_fixture["working"], "rev-list", "--parents", "-n", "1", local_merge
            ).stdout.split()[1:] == [descendant, checkpoint]
        if point == "integration_published_after_save":
            assert transition["integration"] == git(
                git_fixture["working"], "ls-remote", "origin", "refs/heads/main"
            ).stdout.split()[0]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config)["phase"] == "idle"
            and (engine.control_worktree / "kanban/done/T-1.md").is_file()
        )
        merges = git(
            git_fixture["working"], "rev-list", "--merges", "origin/main"
        ).stdout.splitlines()
        assert len(merges) == 1
        product_head = git(
            git_fixture["working"], "rev-parse", "origin/main"
        ).stdout.strip()
        assert git(
            git_fixture["working"], "rev-list", "--parents", "-n", "1", product_head
        ).stdout.split()[1:] == [descendant, checkpoint]
        assert (
            git(git_fixture["working"], "show", f"{product_head}:downtime.txt").stdout
            == "advanced\n"
        )
        assert (
            git(
                git_fixture["working"], "show", f"{product_head}:process-worker.txt"
            ).stdout
            == "completed\n"
        )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_replays_legacy_accepted_state_after_product_descendant(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    point = "integration_before_effect"
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        database = next(engine.state_dir.glob("*.sqlite3"))
        connection = sqlite3.connect(database)
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM runtime_state WHERE id = 1"
            ).fetchone()[0]
        )
        payload["accepted_integration"].pop("product_transition", None)
        connection.execute(
            "UPDATE runtime_state SET payload = ? WHERE id = 1", (json.dumps(payload),)
        )
        connection.commit()
        connection.close()
        _advance_product(git_fixture)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config)["phase"] == "idle"
            and (engine.control_worktree / "kanban/done/T-1.md").is_file()
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        merges = git(
            git_fixture["working"], "rev-list", "--merges", "origin/main"
        ).stdout.splitlines()
        assert len(merges) == 1
    finally:
        service.stop()


def test_real_service_recovers_legacy_published_integration_after_control_advance(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_published_after_save"),
    )
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "integration_before_effect")
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        _advance_product(git_fixture)
        monkeypatch.setenv(
            "DEVLEGATE_TEST_CRASH_POINT", "integration_published_after_save"
        )
        service.restart()
        service.wait_ready()
        _wait_process_death(service)

        database = next(engine.state_dir.glob("*.sqlite3"))
        connection = sqlite3.connect(database)
        payload = json.loads(
            connection.execute(
                "SELECT payload FROM runtime_state WHERE id = 1"
            ).fetchone()[0]
        )
        pending = payload["accepted_integration"]
        assert pending["control_head"] == control_head
        assert pending["product_transition"]["status"] == "published"
        pending.pop("control_predecessor", None)
        connection.execute(
            "UPDATE runtime_state SET payload = ? WHERE id = 1", (json.dumps(payload),)
        )
        connection.commit()
        connection.close()

        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "kanban/backlog/DRAFT-007.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Later ticket"\n---\n\nLater\n'
        )
        git(publisher_control, "add", "kanban/backlog/DRAFT-007.md")
        git(publisher_control, "commit", "-m", "add later ticket")
        git(publisher_control, "push", "origin", "HEAD:refs/heads/devlegate/control")

        monkeypatch.setenv(
            "DEVLEGATE_TEST_CRASH_POINT",
            "integration_control_predecessor_after_save",
        )
        service.restart()
        _wait_process_death(service)
        adopted = _disk_state(config)["accepted_integration"]
        assert adopted["control_head"] == control_head
        assert adopted["control_predecessor"] == git(
            publisher_control, "rev-parse", "HEAD"
        ).stdout.strip()
        product_merge = adopted["product_transition"]["integration"]

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config)["phase"] == "idle"
            and (engine.control_worktree / "kanban/done/T-1.md").is_file()
        )
        assert (engine.control_worktree / "kanban/backlog/DRAFT-007.md").is_file()
        remote_ref = "refs/devlegate/tests/legacy-accepted-control"

        def remote_done_commit():
            remote_control = git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()[0]
            git(
                engine.control_worktree,
                "fetch",
                "origin",
                f"{remote_control}:{remote_ref}",
            )
            return git(
                engine.control_worktree,
                "log",
                "-1",
                "--format=%H",
                remote_ref,
                "--",
                "kanban/done/T-1.md",
            ).stdout.strip()

        service.wait_for(lambda: bool(remote_done_commit()))
        remote_done = remote_done_commit()
        assert git(
            engine.control_worktree, "rev-parse", f"{remote_done}^"
        ).stdout.strip() == adopted["control_predecessor"]
        assert len(
            git(
                engine.control_worktree,
                "log",
                "--format=%H",
                remote_ref,
                "--",
                "kanban/done/T-1.md",
            ).stdout.splitlines()
        ) == 1
        published_product = git(
            git_fixture["working"], "rev-parse", "origin/main"
        ).stdout.strip()
        assert published_product == product_merge
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


@pytest.mark.parametrize("mutation", ["ticket", "evidence", "dependency"])
def test_real_service_rejects_changed_accepted_integration_descendant(
    git_fixture, monkeypatch, mutation
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture,
        monkeypatch,
        depends=("D-1",) if mutation == "dependency" else None,
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
        if mutation == "ticket":
            (publisher_control / "kanban/accepted/T-1.md").write_text(
                ticket("Control ticket", "changed")
            )
            changed_path = "kanban/accepted/T-1.md"
        elif mutation == "evidence":
            (publisher_control / "executions/T-1/extra.txt").write_text("changed\n")
            changed_path = "executions/T-1/extra.txt"
        else:
            (publisher_control / "kanban/done/D-1.md").write_text(
                ticket("Dependency", "changed")
            )
            changed_path = "kanban/done/D-1.md"
        git(publisher_control, "add", changed_path)
        git(publisher_control, "commit", "-m", f"change accepted {mutation}")
        git(publisher_control, "push", "origin", "HEAD:refs/heads/devlegate/control")
        remote_before = git(
            publisher_control,
            "rev-parse",
            "HEAD",
        ).stdout.strip()

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()

        expected_reason = (
            "accepted integration eligibility changed"
            if mutation == "dependency"
            else (
                "review content changed after admission; "
                "specification changes are not trusted"
                if mutation == "ticket"
                else "accepted ticket changed during integration"
            )
        )

        def rejected():
            status = json.loads(service.cli("status", "--json").stdout)
            return (
                status["plan"]["action"] == "blocked"
                and status["plan"]["reason"] == expected_reason
            )

        service.wait_for(rejected)
        assert service.process is not None and service.process.poll() is None
        assert not (engine.control_worktree / "kanban/done/T-1.md").exists()
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


def test_real_service_recovers_zero_delta_accepted_integration_after_control_advance(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch, zero_delta=True
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
        product_head = git(
            git_fixture["working"], "rev-parse", "origin/main"
        ).stdout.strip()
        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "kanban/backlog/DRAFT-007.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Later ticket"\n---\n\nLater\n'
        )
        git(publisher_control, "add", "kanban/backlog/DRAFT-007.md")
        git(publisher_control, "commit", "-m", "add later ticket")
        git(publisher_control, "push", "origin", "HEAD:refs/heads/devlegate/control")
        predecessor = git(publisher_control, "rev-parse", "HEAD").stdout.strip()

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        remote_ref = "refs/devlegate/tests/zero-delta-accepted-control"

        def integration_published():
            remote = git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()
            if not remote:
                return False
            git(
                engine.control_worktree,
                "fetch",
                "origin",
                f"{remote[0]}:{remote_ref}",
            )
            return (
                _disk_state(config)["phase"] == "idle"
                and (engine.control_worktree / "kanban/done/T-1.md").is_file()
                and bool(
                    git(
                        engine.control_worktree,
                        "log",
                        "-1",
                        "--format=%H",
                        remote_ref,
                        "--",
                        "kanban/done/T-1.md",
                    ).stdout.strip()
                )
            )

        service.wait_for(integration_published)
        service.stop()
        # A later control update must not make the durable integration
        # transition look like it has a different parent.
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "post-integration.txt").write_text("preserve update\n")
        git(publisher_control, "add", "post-integration.txt")
        git(publisher_control, "commit", "-m", "post-integration control update")
        git(publisher_control, "push", "origin", "HEAD:refs/heads/devlegate/control")
        remote_control = git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        git(
            engine.control_worktree,
            "fetch",
            "origin",
            f"{remote_control}:{remote_ref}",
        )
        integration_commits = git(
            engine.control_worktree,
            "log",
            "--format=%H",
            remote_ref,
            "--",
            "kanban/done/T-1.md",
        ).stdout.splitlines()
        control_topology = git(
            engine.control_worktree,
            "log",
            "--graph",
            "--format=%H %P %s",
            "-n",
            "12",
            remote_ref,
        ).stdout
        topology_diagnostic = (
            f"remote control tip: {remote_control}\n{control_topology}"
        )
        assert control_head != predecessor
        assert len(integration_commits) == 1, topology_diagnostic
        integration_commit = integration_commits[0]
        assert integration_commit != remote_control
        assert git(
            engine.control_worktree, "rev-parse", f"{integration_commit}^"
        ).stdout.strip() == predecessor, topology_diagnostic
        assert (
            git(
                engine.control_worktree,
                "show",
                f"{remote_ref}:kanban/backlog/DRAFT-007.md",
            ).stdout
            == (
                '---\n"type": "devlegate.ticket"\n'
                '"title": "Later ticket"\n---\n\nLater\n'
            )
        )
        assert (
            git(
                engine.control_worktree,
                "show",
                f"{remote_ref}:post-integration.txt",
            ).stdout
            == "preserve update\n"
        )
        published_product = git(
            git_fixture["working"], "rev-parse", "origin/main"
        ).stdout.strip()
        assert published_product == product_head
        assert attempts.read_text().splitlines() == ["attempt"]
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
        "publication_ack_lost",
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
        if point == "publication_ack_lost":
            bare = git_fixture["bare"]
            unavailable = bare.with_suffix(".unavailable")
            bare.rename(unavailable)
            try:
                service.restart()
                service.wait_for(lambda: "cannot observe remote ref" in (
                    service.cli("status", "--json").stdout
                ))
                assert _disk_state(config)["execution_stage"] == "publishing"
                assert attempts.read_text().splitlines() == ["attempt"]
            finally:
                unavailable.rename(bare)
        else:
            service.restart()

        if point == "checkpoint_after_effect":
            # The commit is observable, but there is no independent effect
            # receipt. A matching parent/SHA must not fabricate a success.
            service.wait_ready()
            service.stop()
            retained = _disk_state(config)
            assert retained["execution_id"] == execution_id
            assert retained["execution_stage"] == "checkpointing"
            assert attempts.read_text().splitlines() == ["attempt"]
            observer = type(engine)(config, read_only=True)
            assert "receipt missing" in observer.execution_resource_proof().reason
            assert not (engine.control_worktree / "kanban/review/T-1.md").exists()
            return

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


@pytest.mark.parametrize(
    "interruption", [None, "same_base_resolving", "publication_ack_lost", "retry_hold"],
)
def test_real_service_resumes_same_base_when_checkout_is_clean_again(
    git_fixture, monkeypatch, interruption,
):
    repo = git_fixture["working"]
    monkeypatch.chdir(repo)
    service, config, engine, attempts = _recovery_execution_service(
        git_fixture, monkeypatch, "post-checkpoint",
    )
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        dirty = repo / "operator-edit.txt"
        dirty.write_text("operator work\n")
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: (
            (_disk_state(config).get("reconciliation") or {}).get("status") == "pending"
        ))
        assert attempts.read_text().splitlines() == ["attempt"]
        assert not (engine.control_worktree / "kanban/review/T-1.md").exists()
        renewed_drift = interruption in {"same_base_resolving", "publication_ack_lost"}
        if interruption:
            service.stop()
        if interruption == "retry_hold":
            # Model the durable operator-abort hold at the retained checkpoint.
            owner = ServiceEngine(config)
            pending = owner._state["reconciliation"]
            with owner._lock():
                owner._save_state("idle", manual_retry_required={
                    "ticket_id": "T-1", "execution_id": pending["execution_id"],
                    "generation": pending["original_base"], "request_id": "forced-stop",
                })
        dirty.unlink()
        if interruption == "retry_hold":
            service.restart()
            service.wait_for(lambda: "run devlegate retry T-1" in (
                service.cli("status", "--json").stdout
            ))
            assert _disk_state(config)["reconciliation"] == pending
            assert attempts.read_text().splitlines() == ["attempt"]
            result = service.cli("retry", "T-1")
            assert result.returncode == 0, result.stderr
        if renewed_drift:
            monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", interruption)
            service.start()
            _wait_process_death(service)
            state = _disk_state(config)
            original = state["reconciliation"]
            assert original["status"] == "resolving"
            assert attempts.read_text().splitlines() == ["attempt"]
            branch = f"refs/heads/{original['execution_branch']}"
            upstream = git(repo, "ls-remote", "origin", branch).stdout.split()
            if interruption == "publication_ack_lost":
                assert upstream[0] == original["worker_checkpoint"]
            else:
                assert not upstream
            _advance_product(git_fixture)
            monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
            service.restart()
        service.wait_for(lambda: (
            (_disk_state(config).get("reconciliation") or {}).get("status")
            == "resolved"
        ))
        service.wait_for((engine.control_worktree / "kanban/review/T-1.md").is_file)
        reports = ExecutionReportStore(engine.control_worktree).list("T-1")
        assert len(reports) == 1
        assert reports[0].result.conclusion == "completed"
        if renewed_drift:
            assert reports[0].execution_id != crashed["execution_id"]
            target = git(repo, "rev-parse", "HEAD").stdout.strip()
            assert reports[0].code_base_head == target
            assert git(repo, "rev-parse", original["evidence_ref"]).stdout.strip() == (
                original["worker_checkpoint"]
            )
            assert attempts.read_text().splitlines() == ["attempt", "attempt"]
        else:
            assert reports[0].execution_id == crashed["execution_id"]
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
        crashed = _disk_state(config)
        base, target = _advance_product(git_fixture)
        # Settle on a durable boundary rather than race the service through the
        # transient post-checkpoint stage after restart.
        monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "transfer_candidate")
        service.restart()
        _wait_process_death(service)
        pending = _disk_state(config)["reconciliation"]
        assert pending["status"] == "pending"
        assert pending["original_base"] == base
        assert pending["observed_product"] == target
        assert pending["execution_report"] == crashed["pending_execution_report"]
        assert attempts.read_text().splitlines() == ["attempt"]
        execution = engine.execution_worktree_root / "work" / "T-1"
        checkpoint = pending["worker_checkpoint"]
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint
        assert not git(
            execution, "ls-remote", "origin", "refs/heads/devlegate/work/T-1",
        ).stdout.strip()
        review = engine.control_worktree / "kanban/review/T-1.md"
        assert not review.exists()

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: review.is_file() and _disk_state(config)["phase"] == "idle"
        )
        reports = ExecutionReportStore(engine.control_worktree).list("T-1")
        assert len(reports) == 1
        assert reports[0].code_base_head == target
        assert reports[0].execution_id != pending["execution_id"]
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]
        assert git(execution, "rev-parse", pending["evidence_ref"]).stdout.strip() == (
            checkpoint
        )
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

@pytest.mark.parametrize("case, transfer_fault", [
    ("descendant", None), ("published", None), ("no-op", None), ("conflict", None),
    ("stale-conflict", None),
    ("reset-conflict", None), ("reset-stale", None), ("reset-foreign", None),
    ("reset-ineligible", None), ("reset-chain", "transfer_creation"),
    ("reset-settled", None),
    ("unrelated", None), ("divergent", None), ("same-parent", None),
    ("published", "transfer_candidate"), ("published", "transfer_install_intent"),
    ("published", "transfer_creation"), ("published", "transfer_receipt"),
])
def test_real_service_owner_reconciles_without_cli_when_lineage_is_safe(
    git_fixture, monkeypatch, case, transfer_fault,
):
    repo = git_fixture["working"]
    monkeypatch.chdir(repo)
    config = _short_runtime_config(git_fixture)
    worker = git_fixture["tmp"] / "active-worker.py"
    ready = worker.with_suffix(".ready")
    release = worker.with_suffix(".release")
    product_target = worker.with_suffix(".product-target")
    monkeypatch.setenv("DEVLEGATE_TEST_DRIFT_CASE", case)
    monkeypatch.setenv("DEVLEGATE_TEST_POST_CHECKPOINT_PRODUCT", str(product_target))
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "script = pathlib.Path(__file__)\n"
        "with script.with_suffix('.attempts').open('a') as attempts:\n"
        "    attempts.write('attempt\\n')\n"
        "prompt = sys.stdin.read()\n"
        "if script.with_suffix('.ready').exists():\n"
        "    script.with_suffix('.resume').write_text(prompt)\n"
        "else:\n"
        "    workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "    if os.environ['DEVLEGATE_TEST_DRIFT_CASE'] != 'no-op':\n"
        "        (workspace / 'implementation.txt').write_text('worker work\\n')\n"
        "    script.with_suffix('.ready').touch()\n"
        "    while not script.with_suffix('.release').exists():\n"
        "        time.sleep(.01)\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'process worker', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    worker.chmod(0o755)
    config.write_text(config.read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
                      .replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}"))
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    git(repo, "commit", "--allow-empty", "-m", "active base A")
    git(repo, "push", "origin", "HEAD:main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    published = None
    if case in {"published", "reset-chain", "reset-ineligible"}:
        from test_rework_handoff import completed, publish_rework

        def seed(workspace, *_args, **_kwargs):
            (workspace.path / "reviewed.txt").write_text("published predecessor\n")
            return completed()

        monkeypatch.setattr(engine._workers, "run", seed)
        assert run_test_iteration(engine) == 0
        published = ExecutionReportStore(engine.control_worktree).list("T-1")[0]
        publish_rework(engine, published)
    publisher = _ensure_publisher(git_fixture)
    ineligible = case in {"unrelated", "divergent", "same-parent"}
    conflicting = case in {
        "conflict", "stale-conflict", "reset-conflict", "reset-stale",
        "reset-foreign", "reset-ineligible", "reset-settled",
    }

    with ExitStack() as cleanup:
        service = LiveService(repo, config, command=_recovery_driver(config))
        cleanup.push(service)
        cleanup.callback(release.touch)
        service.start()
        service.wait_ready()
        service.wait_for(ready.exists)
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == base
        product_file = (
            "implementation.txt" if conflicting else "product-change.txt"
        )
        (publisher / product_file).write_text("product B\n")
        git(publisher, "add", product_file)
        git(publisher, "commit", "-m", "advance product")
        if ineligible:
            parents = ()
            if case == "divergent":
                other = git(
                    publisher, "commit-tree", "HEAD^^{tree}", "-p", "HEAD^^",
                    "-m", "divergent parent",
                ).stdout.strip()
                parents = ("-p", other)
            elif case == "same-parent":
                parents = ("-p", "HEAD^^")
            other = git(
                publisher, "commit-tree", "HEAD^{tree}", *parents,
                "-m", "replace product history",
            ).stdout.strip()
            git(publisher, "reset", "--hard", other)
        target = git(publisher, "rev-parse", "HEAD").stdout.strip()
        git(
            publisher, "push", f"--force-with-lease=refs/heads/main:{base}",
            "origin", "HEAD:main",
        )
        # Leave the product checkout at A in ordinary cases. Only the
        # exceptional manual cases need a locally asserted rewritten target.
        if ineligible:
            product_target.write_text(target)
        release.touch()
        if case == "no-op":
            review = engine.control_worktree / "kanban/review/T-1.md"
            service.wait_for(lambda: (
                review.is_file() and _disk_state(config)["phase"] == "idle"
            ), timeout=45)
            reports = ExecutionReportStore(engine.control_worktree).list("T-1")
            assert len(reports) == 2
            assert worker.with_suffix(".attempts").read_text() == "attempt\nattempt\n"
            stale = next(r for r in reports if r.code_base_head == base)
            assert stale.workspace_head == base
            fresh = next(r for r in reports if r.code_base_head == target)
            assert fresh.workspace_head == target
            assert fresh.result.conclusion == "completed"
            assert git(
                engine.control_worktree, "show", "HEAD^:kanban/todo/T-1.md",
            ).stdout
            return
        service.wait_for(lambda: (
            (_disk_state(config).get("reconciliation") or {}).get("status") == "pending"
        ))
        pending_before = _disk_state(config)["reconciliation"]
        assert pending_before["original_base"] == base
        assert pending_before["observed_product"] == target
        execution = Path(pending_before["execution_path"])
        source = pending_before["worker_checkpoint"]
        if published:
            assert pending_before["execution_remote_head"] == published.workspace_head
        predecessor = published.workspace_head if published else base
        assert git(execution, "rev-parse", f"{source}^").stdout.strip() == predecessor
        assert git(execution, "show", f"{source}:implementation.txt").stdout == (
            "worker work\n"
        )
        service.kill()
        attempt = None
        if transfer_fault:
            monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", transfer_fault)
            service.start()
            _wait_process_death(service)
            assert not worker.with_suffix(".resume").exists()
            crashed_transfer = _disk_state(config)["transfer_progress"]
            attempt = crashed_transfer["active_binding"]["execution_id"]
            monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        if case == "reset-chain":
            (publisher / "implementation.txt").write_text("product C\n")
            git(publisher, "add", "implementation.txt")
            git(publisher, "commit", "-m", "advance product into conflict")
            git(publisher, "push", "origin", "HEAD:main")
            conflicting = True
        service.restart()
        if ineligible or (conflicting and case != "reset-chain"):
            assert _disk_state(config)["reconciliation"] == pending_before
        if ineligible and case == "same-parent":
            service.wait_for(lambda: "authorize-product" in (
                service.cli("status", "--json").stdout
            ))
            assert not _disk_state(config).get("transfer_progress")
            wrong_execution = _authorize_product(
                service, config, pending_before, target, execution="wrong-execution",
            )
            assert wrong_execution.returncode != 0
            assert "does not match the pending execution" in wrong_execution.stderr
            stale = _authorize_product(service, config, pending_before, base)
            assert stale.returncode != 0 and "must match current" in stale.stderr
            assert not _disk_state(config).get("transfer_progress")
            assert not worker.with_suffix(".resume").exists()

            service.stop()
            monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "receipt_after_save")
            service.start()
            service.wait_ready()
            result = _authorize_product(service, config, pending_before, target)
            assert result.returncode != 0 and "outcome is uncertain" in result.stderr
            _wait_process_death(service)
            admitted = _disk_state(config)
            request_id, = (
                key for key, receipt in admitted["mutable_receipts"].items()
                if receipt["method"] == "reconcile-authorize-product"
            )
            binding = admitted["transfer_progress"]["active_binding"]
            assert binding["base"] == target
            attempt = binding["execution_id"]
            assert git(execution, "rev-parse", "HEAD").stdout.strip() == source
            assert not worker.with_suffix(".resume").exists()
            monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
            service.restart()
            payload = {
                "ticket_id": "T-1", "execution_id": pending_before["execution_id"],
                "to": target,
            }
            assert ipc_request(
                service.locator.socket_path, "reconcile-authorize-product", payload,
                mutable=True, request_id=request_id,
            ) == {"accepted": True, **payload}
        elif ineligible:
            result = _authorize_product(service, config, pending_before, target)
            assert result.returncode != 0 and "not a descendant" in result.stderr
            state = _disk_state(config)
            assert state["reconciliation"] == pending_before
            assert not state.get("transfer_progress")
            assert state["execution_id"] == pending_before["execution_id"]
            assert state["worker_identity"] is None
            assert not worker.with_suffix(".resume").exists()
            assert git(repo, "rev-parse", "HEAD").stdout.strip() == target
            product = git(repo, "ls-remote", "origin", "refs/heads/main")
            control = git(repo, "ls-remote", "origin", "refs/heads/devlegate/control")
            assert product.stdout.split()[0] == target
            assert control.stdout.split()[0] == pending_before["control_head"]
            assert git(execution, "rev-parse", "HEAD").stdout.strip() == source
            pin = git(repo, "rev-parse", pending_before["evidence_ref"])
            assert pin.stdout.strip() == source
            branch = pending_before["execution_branch"]
            assert not git(repo, "ls-remote", "origin", f"refs/heads/{branch}").stdout
            return
        if conflicting:
            service.wait_for(lambda: bool(
                (_disk_state(config).get("transfer_progress") or {}).get("conflict")
            ))
            progress = _disk_state(config)["transfer_progress"]
            waiting = _disk_state(config)["reconciliation"]
            candidate = Path(progress["conflict"]["path"])
            assert git(execution, "rev-parse", "HEAD").stdout.strip() == (
                waiting["worker_checkpoint"]
            )
            if case == "reset-chain":
                assert waiting["installed_source"]["resumed_execution_id"] == attempt
            service.kill()
            if case == "reset-settled":
                monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "resolved_transfer")
            service.restart()
            assert _disk_state(config)["transfer_progress"] == progress
            if case.startswith("reset-"):
                ref = f"refs/heads/{waiting['execution_branch']}"
                if case == "reset-foreign":
                    git(publisher, "push", "origin", f"{target}:{ref}")
                if case == "reset-stale":
                    (candidate / "implementation.txt").write_text("keep manual edits\n")
                    git(publisher, "commit", "--allow-empty", "-m", "advance again")
                    git(publisher, "push", "origin", "HEAD:main")
                    confirmation = service.cli(
                        "reconcile", "resolve", "T-1", str(candidate),
                    )
                    assert confirmation.returncode != 0
                    assert "stale candidate retained" in confirmation.stderr
                    assert not _disk_state(config)["transfer_progress"].get(
                        "confirmation"
                    )
                    service.restart()
                if case == "reset-settled":
                    (candidate / "implementation.txt").write_text("manual resolution\n")
                    confirmation = service.cli(
                        "reconcile", "resolve", "T-1", str(candidate),
                    )
                    assert confirmation.returncode == 0, confirmation.stderr
                    _wait_process_death(service)
                    waiting = _disk_state(config)["reconciliation"]
                    assert waiting["status"] == "resolved"
                    assert waiting["externally_resolved"]
                    git(publisher, "commit", "--allow-empty", "-m", "advance again")
                    git(publisher, "push", "origin", "HEAD:main")
                    monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
                    service.restart()
                    service.wait_for(lambda: "installed human resolution retained" in (
                        service.cli("status", "--json").stdout
                    ))
                result = service.cli("reset", "T-1", "--json")
                if case in {"reset-foreign", "reset-ineligible"}:
                    assert result.returncode != 0
                    expected = {
                        "reset-foreign": "upstream predecessor changed",
                        "reset-ineligible": "does not match the exact generation",
                    }
                    assert expected[case] in result.stderr
                    assert _disk_state(config)["transfer_progress"] == progress
                    assert not worker.with_suffix(".resume").exists()
                    assert git(execution, "rev-parse", "HEAD").stdout.strip() == source
                    return
                assert result.returncode == 0, result.stderr
                service.wait_for(lambda: (
                    (_disk_state(config).get("ticket_reset") or {}).get("status")
                    == "started"
                ))
                reset = _disk_state(config)["ticket_reset"]
                assert reset["binding"]["execution_id"] == waiting.get(
                    "resumed_execution_id", waiting["execution_id"],
                )
                assert reset["fresh_execution_id"] != (
                    progress["active_binding"]["execution_id"]
                )
                if case == "reset-settled":
                    manual = git(
                        repo, "show", f"{waiting['evidence_ref']}:implementation.txt",
                    )
                    assert manual.stdout == "manual resolution\n"
                else:
                    assert candidate.is_dir()
                pin = git(repo, "rev-parse", pending_before["evidence_ref"])
                assert pin.stdout.strip() == source
                if case == "reset-stale":
                    assert (candidate / "implementation.txt").read_text() == (
                        "keep manual edits\n"
                    )
                assert (execution / "implementation.txt").read_text() != "worker work\n"
                return
            (candidate / "implementation.txt").write_text("confirmed source\n")
            if case == "stale-conflict":
                git(publisher, "commit", "--allow-empty", "-m", "advance past conflict")
                git(publisher, "push", "origin", "HEAD:main")
            confirmation = service.cli(
                "reconcile", "resolve", "T-1", str(candidate),
            )
            if case == "stale-conflict":
                assert confirmation.returncode != 0
                assert "stale candidate retained" in confirmation.stderr
                service.wait_for(lambda: "stale candidate retained" in (
                    service.cli("status", "--json").stdout
                ))
                state = _disk_state(config)
                assert state["transfer_progress"]["conflict"] == progress["conflict"]
                assert (candidate / "implementation.txt").read_text() == (
                    "confirmed source\n"
                )
                assert not ExecutionReportStore(engine.control_worktree).list("T-1")
                assert worker.with_suffix(".attempts").read_text() == "attempt\n"
                return
            assert confirmation.returncode == 0, confirmation.stderr

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
        assert git(repo, "rev-parse", "HEAD").stdout.strip() == target
        service.wait_for((engine.control_worktree / "kanban/review/T-1.md").is_file)
        reports = ExecutionReportStore(engine.control_worktree).list("T-1")
        assert len(reports) == (2 if published else 1)
        report = next(r for r in reports if r.code_base_head == target)
        assert report.result.conclusion == "completed"
        assert report.execution_id != pending_before["execution_id"]
        if attempt:
            assert report.execution_id == attempt
        assert worker.with_suffix(".attempts").read_text() == "attempt\nattempt\n"
        (git_fixture["working"] / "operator-downtime.txt").write_text("hold\n")
        service.kill()
        service.restart()
        status = service.cli("status", "--json")
        assert status.stdout
        payload = json.loads(status.stdout)
        assert payload["reconciliation"]["effective_base"] == target
        effective_base = git(execution, "rev-parse", f"HEAD~{2 if published else 1}")
        assert effective_base.stdout.strip() == target
        expected = "confirmed source\n" if case == "conflict" else "worker work\n"
        assert (execution / "implementation.txt").read_text() == expected
        pinned = git(execution, "rev-parse", pending_before["evidence_ref"]).stdout
        assert pinned.strip() == pending_before["worker_checkpoint"]
    database = next(engine.state_dir.glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    persisted = json.loads(
        connection.execute("SELECT payload FROM runtime_state WHERE id = 1").fetchone()[
            0
        ]
    )
    connection.close()
    assert persisted["execution_id"] != pending_before["execution_id"]
    resolved = persisted["reconciliation"]
    assert persisted["execution_id"] == resolved["resumed_execution_id"]
    assert resolved["execution_report"] == pending_before["execution_report"]
