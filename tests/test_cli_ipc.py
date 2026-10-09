# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""CLI client IPC routing and fail-closed authority behavior."""

from _cli_support import *  # noqa: F403,F405
from _cli_support import __version__

def test_reconcile_resume_routes_to_daemon_helper(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def resume(_env_file, ticket_id):
        accepted.append(ticket_id)
        return 0

    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr("devlegate.cli._reconcile_resume_daemon", resume)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "resume",
            "LAB-111",
        ],
    )
    assert main() == 0
    assert accepted == ["LAB-111"]
    assert capsys.readouterr().out == ""

def test_version_does_not_contact_daemon(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "version"])
    monkeypatch.setattr(cli, "request", pytest.fail)

    assert main() == 0
    assert "Devlegate runtime" in capsys.readouterr().out

def test_direct_cli_stop_waits_for_authority_release(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    with LiveService(git_fixture["working"], git_fixture["config"]) as service:
        stopped = service.cli("stop")
        log_path = RuntimeLocator.from_env(git_fixture["config"]).service_log_path
        assert stopped.returncode == 0, (
            f"{stopped.stderr}\n{log_path.read_text() if log_path.exists() else ''}"
        )
        assert not service.locator.daemon_authority_present()


def test_custom_service_command_uses_deterministic_containment(
    git_fixture, monkeypatch
):
    marker = git_fixture["tmp"] / "provider.txt"
    monkeypatch.setenv("DEVLEGATE_TEST_PROVIDER_MARKER", str(marker))
    service = LiveService(
        git_fixture["working"],
        git_fixture["config"],
        command=_recovery_driver(git_fixture["config"]),
    )
    with service:
        service.wait_ready()
        assert marker.read_text() == "DurableDeterministicContainmentProvider"

def test_stop_wait_requires_matching_completion_receipt(monkeypatch):
    class Locator:
        def daemon_authority_present(self):
            return False

    locator = Locator()
    monkeypatch.setattr(cli, "read_lifecycle_receipt", lambda _locator: None)
    clock = iter((0.0, 11.0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))
    with pytest.raises(DevlegateError, match="without graceful stop completion"):
        cli._wait_for_service_stop(locator, "request", "instance")

def test_stop_wait_accepts_receipt_published_after_authority_release(monkeypatch):
    class Locator:
        authority_states = iter((True, False))

        def daemon_authority_present(self):
            return next(self.authority_states)

    locator = Locator()
    receipt = {
        "request_id": "request",
        "instance_id": "instance",
        "action": "stop",
        "state": "completed",
    }
    receipts = iter((None, receipt))
    monkeypatch.setattr(cli, "read_lifecycle_receipt", lambda _locator: next(receipts))
    monkeypatch.setattr(cli.time, "sleep", lambda _seconds: None)
    clock = iter((0.0, 1.0, 2.0))
    monkeypatch.setattr(cli.time, "monotonic", lambda: next(clock))

    cli._wait_for_service_stop(locator, "request", "instance")

@pytest.mark.parametrize("replacement_id", ["T-1", "T-2"])
def test_real_service_drop_retire_old_lineage_and_runs_fresh(
    git_fixture, monkeypatch, replacement_id
):
    monkeypatch.chdir(git_fixture["working"])
    config = _recovery_config(git_fixture)
    control = _service_engine_with_control(git_fixture, config).control_worktree
    _add_service_ticket(ServiceEngine(config))
    worker = git_fixture["tmp"] / "drop-worker.py"
    marker = git_fixture["tmp"] / "drop-worker.jsonl"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "prompt = sys.stdin.read()\n"
        f"pathlib.Path({str(marker)!r}).open('a').write(json.dumps(prompt) + '\\n')\n"
        "time.sleep(1)\n"
        "(workspace / 'drop-worker.txt').write_text('completed\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'drop', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    worker.chmod(0o755)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    service = LiveService(git_fixture["working"], config)
    client = None
    try:
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running",
            timeout=30,
        )
        old_state = _disk_state(config)
        old_execution = old_state["execution_id"]
        old_control = old_state["execution_control_head"]
        publisher_control = _ensure_publisher_control(git_fixture)
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        old_path = publisher_control / "kanban/todo/T-1.md"
        old_path.unlink()
        git(publisher_control, "add", "-A")
        git(publisher_control, "commit", "-m", "withdraw old ticket")
        replacement_path = publisher_control / f"kanban/todo/{replacement_id}.md"
        replacement_path.write_text(ticket("Replacement ticket", "new work"))
        git(
            publisher_control,
            "add",
            str(replacement_path.relative_to(publisher_control)),
        )
        git(publisher_control, "commit", "-m", "create replacement ticket")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        control_before_restart = git(control, "rev-parse", "HEAD").stdout.strip()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "lifecycle",
            timeout=30,
        )
        blocked_state = _disk_state(config)
        assert blocked_state["phase"] == "agent_running"
        assert blocked_state["execution_id"] == old_execution
        assert blocked_state["execution_control_head"] == old_control
        assert blocked_state["pending_execution_report"]["conclusion"] == "completed"
        old_checkpoint = blocked_state["pending_execution_report"]["workspace_head"]
        old_workspace = Path(blocked_state["execution_path"])
        assert service.process is not None
        service.process.send_signal(signal.SIGTERM)
        service.wait_exited()
        publisher = _ensure_publisher_control(git_fixture)
        git(publisher, "fetch", "origin", "devlegate/control")
        git(publisher, "reset", "--hard", "origin/devlegate/control")
        publisher_ticket = publisher / f"kanban/todo/{replacement_id}.md"
        publisher_ticket.write_text(ticket("Later generation", "later work"))
        git(publisher, "add", str(publisher_ticket.relative_to(publisher)))
        git(publisher, "commit", "-m", "advance replacement generation")
        git(publisher, "push", "origin", "HEAD:refs/heads/devlegate/control")
        later_control = git(publisher, "rev-parse", "HEAD").stdout.strip()
        assert (
            git(control, "rev-parse", "HEAD").stdout.strip() == control_before_restart
        )
        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: git(control, "rev-parse", "origin/devlegate/control")
            .stdout.strip()
            == later_control,
            timeout=10,
        )
        recovered = _disk_state(config)
        assert (
            git(control, "rev-parse", "HEAD").stdout.strip()
            == control_before_restart
        )
        assert (
            git(control, "rev-parse", "origin/devlegate/control").stdout.strip()
            == later_control
        )
        assert recovered["execution_id"] == old_execution
        assert recovered["execution_control_head"] == old_control
        assert recovered["pending_execution_report"]["workspace_head"] == old_checkpoint
        assert recovered["execution_stage"] == "lifecycle"
        assert recovered["worker_identity"] is None
        assert not list((control / "executions").glob("**/*.json"))
        assert service.locator.daemon_authority_present()
        candidates = ipc_request(service.locator.socket_path, "drop-candidates")
        assert candidates["candidates"][0]["id"] == "T-1"
        assert candidates["candidates"][0]["execution_id"] == old_execution
        assert candidates["candidates"][0]["stage"] == "lifecycle"
        def submit_drop_when_owner_is_ready():
            result = service.cli("drop", "T-1")
            if result.returncode == 0:
                return True
            assert "service busy" in result.stderr
            return False

        service.wait_for(submit_drop_when_owner_is_ready, timeout=10)
        disposition_ref = f"refs/devlegate/dispositions/T-1/{old_execution}"
        evidence_ref = f"refs/devlegate/executions/T-1/{old_execution}"
        assert (
            git(service.locator.repo, "rev-parse", "--verify", evidence_ref)
            .stdout.strip()
            == old_checkpoint
        )
        disposition = git(
            service.locator.repo, "cat-file", "-p", disposition_ref
        )
        disposition_payload = json.loads(disposition.stdout)
        assert disposition_payload["disposition"] == "dropped"
        assert disposition_payload["worker_conclusion"] == "completed"
        assert disposition_payload["execution_report"]["execution_id"] == old_execution
        assert disposition_payload["execution_report"]["conclusion"] == "completed"
        assert disposition_payload["checkpoint"] == old_checkpoint
        assert git(
            service.locator.repo,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/work/T-1",
        ).stdout == ""
        assert not old_workspace.exists()
        after_drop = _disk_state(config)
        assert after_drop["phase"] == "idle"
        assert "execution_id" not in after_drop
        service.wait_for(
            lambda: (
                _disk_state(config).get("phase") == "agent_running"
                and _disk_state(config).get("execution_stage") == "worker-running"
                and _disk_state(config).get("execution_id") != old_execution
            ),
            timeout=30,
        )
        fresh = _disk_state(config)
        assert fresh["execution_id"] != old_execution
        assert fresh["execution_ticket_id"] == replacement_id
        assert fresh["execution_control_head"] != old_control
        assert fresh["execution_base_head"] == fresh["local_head"]
        assert "Continue the existing implementation" not in marker.read_text()
        replay = ServiceEngine(config)
        replay._drop_owned("T-1", old_execution)
        assert _disk_state(config)["execution_id"] == fresh["execution_id"]
        client = service.start_cli("stop")
        stdout, stderr = client.communicate(timeout=30)
        assert client.returncode == 0, (stdout, stderr)
        service.wait_exited()
    finally:
        if client is not None and client.poll() is None:
            client.kill()
            client.wait(timeout=5)
        if service.process is not None and service.process.poll() is None:
            service.kill()

def test_service_start_fails_closed_when_authority_has_no_healthy_ipc(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    locator = RuntimeLocator.from_env(git_fixture["config"])
    locator.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with locator.lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke(git_fixture, "--foreground")
    assert result.returncode == 1
    assert "authority exists but its IPC endpoint is unavailable" in result.stderr

def test_status_uses_daemon_ipc_without_fallback(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    expected = cli_daemon.status_view().as_dict()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "status"],
    )

    assert main() == (1 if expected["plan"]["action"] == "blocked" else 0)
    human = capsys.readouterr().out
    assert f"Service version: {__version__}" in human
    assert "Warning: the running service uses a different" not in human

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "status", "--json"],
    )
    assert main() == (1 if expected["plan"]["action"] == "blocked" else 0)
    payload = json.loads(capsys.readouterr().out)
    assert payload["client"] == {"version": __version__}
    assert payload["service"]["state"] == "running"
    assert payload["service"]["version"] == __version__
    assert isinstance(payload["service"]["pid"], int)
    assert set(payload) == {"client", "service", *expected}
    assert payload["execution"] == {
        **expected["execution"],
        "state": "idle",
    }
    for key, value in expected.items():
        if key not in {"execution", "service"}:
            assert payload[key] == value

def test_plan_uses_daemon_ipc_without_fallback(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    expected = cli_daemon.plan_view().as_dict()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "plan", "--json"],
    )

    assert main() == 0
    assert json.loads(capsys.readouterr().out) == expected

def test_blocked_dependency_status_uses_daemon_semantics(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    control = cli_daemon.control_worktree
    (control / "kanban/review/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\nreview\n'
    )
    (control / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Waiting"\n'
        '"depends_on":\n  - "D-1"\n---\nwaiting\n'
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "add blocked dependency")
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "status"],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "T-1" in output
    assert "Waiting" in output
    assert "D-1 unfinished (review)" in output

def test_retry_uses_daemon_authority_and_never_constructs_cli_engine(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, *, request_id):
        accepted.append(ticket_id)
        return {"accepted": True, "ticket_id": ticket_id}

    monkeypatch.setattr(cli_daemon, "submit_retry", submit)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI constructed mutable engine"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "retry",
            "T-1",
        ],
    )

    assert main() == 0
    assert accepted == ["T-1"]
    assert "retry accepted: T-1" in capsys.readouterr().out

def test_force_retry_rejection_is_synchronous_and_has_no_success_ack(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    monkeypatch.setattr(
        cli,
        "request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IPCClientError("worker ownership is matching-live", application=True)
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "retry",
            "T-1",
            "--force",
        ],
    )

    assert main() == 1
    output = capsys.readouterr()
    assert "matching-live" in output.err
    assert "forced retry admitted" not in output.out

def test_reconcile_uses_daemon_authority_and_never_constructs_cli_engine(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, onto, *, request_id):
        accepted.append((ticket_id, onto, request_id))
        return {"accepted": True, "ticket_id": ticket_id, "onto": onto}

    monkeypatch.setattr(cli_daemon, "submit_reconcile_update_base", submit)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI constructed mutable engine"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
        ],
    )

    assert main() == 0
    assert accepted and accepted[0][:2] == ("T-1", "abc123")
    assert "reconciliation request accepted: T-1" in capsys.readouterr().out

def test_canonical_reconcile_uses_automatic_ipc_operation(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, onto, *, request_id):
        accepted.append((ticket_id, onto, request_id))
        return {
            "accepted": True,
            "ticket_id": ticket_id,
            "resolution_class": "resume",
        }

    monkeypatch.setattr(cli_daemon, "submit_reconcile_auto", submit)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI constructed mutable engine"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "T-1",
        ],
    )

    assert main() == 0
    assert accepted and accepted[0][0] == "T-1"
    assert "reconciliation request accepted: T-1" in capsys.readouterr().out

def test_canonical_reconcile_onto_is_forwarded_as_assertion(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, onto, *, request_id):
        accepted.append((ticket_id, onto, request_id))
        return {
            "accepted": True,
            "ticket_id": ticket_id,
            "onto": onto,
            "resolution_class": "resume",
        }

    monkeypatch.setattr(cli_daemon, "submit_reconcile_auto", submit)
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "T-1",
            "--onto",
            "current-head",
        ],
    )

    assert main() == 0
    assert accepted and accepted[0][1] == "current-head"
    assert "reconciliation request accepted: T-1" in capsys.readouterr().out

def test_reconcile_refuses_without_daemon_without_constructing_engine(
    git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
        ],
    )

    assert main() == 1
    stderr = capsys.readouterr().err
    assert "service is not running" in stderr
    assert "run `devlegate start`" in stderr

def test_reconcile_refuses_connectable_socket_without_authority(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    locator.socket_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(locator.socket_path))
    listener.listen(1)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
        ],
    )
    try:
        assert main() == 1
        stderr = capsys.readouterr().err
        assert "service is not running" in stderr
        assert "run `devlegate start`" in stderr
        listener.settimeout(0.2)
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
        locator.socket_path.unlink(missing_ok=True)

def test_reconcile_fails_closed_when_authority_exists_without_socket(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(config),
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
        ],
    )
    try:
        assert main() == 1
        assert "service IPC unavailable" in capsys.readouterr().err
    finally:
        authority.close()

def test_retry_refuses_connectable_socket_without_authority(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    locator.socket_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(locator.socket_path))
    listener.listen(1)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct retry fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "retry", "T-1"],
    )
    try:
        assert main() == 1
        stderr = capsys.readouterr().err
        assert "service is not running" in stderr
        assert "run `devlegate start`" in stderr
        listener.settimeout(0.2)
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
        locator.socket_path.unlink(missing_ok=True)

def test_retry_fails_closed_when_authority_exists_but_socket_is_unavailable(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct retry fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "retry", "T-1"],
    )
    try:
        assert main() == 1
        assert "service IPC unavailable" in capsys.readouterr().err
    finally:
        authority.close()

def test_recover_cli_forwards_ticket_execution_and_observed_head(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    observed_head = "a" * 40
    calls = []
    monkeypatch.setattr(
        cli_daemon,
        "submit_recover",
        lambda ticket_id, execution_id, observed, *, request_id: (
            calls.append((ticket_id, execution_id, observed, request_id))
            or {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
                "observed_head": observed,
            }
        ),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "recover",
            "T-1",
            "execution-1",
            "--observed-head",
            observed_head,
        ],
    )

    assert main() == 0
    assert calls[0][:3] == ("T-1", "execution-1", observed_head)
    assert calls[0][3]
    assert "recovery accepted: T-1" in capsys.readouterr().out

def test_retry_interactive_candidates_are_rendered_and_selected_locally(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    submitted = []
    monkeypatch.setattr(
        cli_daemon,
        "published_retry_candidates_view",
        lambda: (
            {"id": "T-1", "title": "T-1", "reason": "failed", "kind": "failed"},
        ),
    )
    monkeypatch.setattr(
        cli_daemon,
        "submit_retry",
        lambda ticket_id, *, request_id: (
            submitted.append(ticket_id) or {"accepted": True, "ticket_id": ticket_id}
        ),
    )
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "retry",
        ],
    )

    assert main() == 0
    assert submitted == ["T-1"]
    output = capsys.readouterr().out
    assert "Retry candidates:" in output
    assert "  1) T-1\n" in output

def test_retry_interactive_cancel_submits_no_mutation(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    submitted = []
    monkeypatch.setattr(
        cli_daemon,
        "published_retry_candidates_view",
        lambda: (
            {"id": "T-1", "title": "Ticket", "reason": "failed", "kind": "failed"},
        ),
    )
    monkeypatch.setattr(cli_daemon, "submit_retry", submitted.append)
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "retry",
        ],
    )

    assert main() == 0
    assert submitted == []
    capsys.readouterr()

def test_drop_interactive_candidates_use_exact_execution_identity(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    submitted = []
    candidate = {
        "id": "T-1",
        "title": "Ticket",
        "reason": "control authority withdrawn",
        "kind": "lifecycle",
        "execution_id": "execution-1",
        "stage": "lifecycle",
    }
    monkeypatch.setattr(
        cli_daemon, "published_drop_candidates_view", lambda: (candidate,)
    )
    monkeypatch.setattr(
        cli_daemon,
        "submit_drop",
        lambda ticket_id, execution_id, *, request_id: (
            submitted.append((ticket_id, execution_id))
            or {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
            }
        ),
    )
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "drop",
        ],
    )

    assert main() == 0
    assert submitted == [("T-1", "execution-1")]
    assert "Drop candidates:" in capsys.readouterr().out

def test_drop_without_ticket_rejects_noninteractive_invocation(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: False)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "--env",
            str(_short_runtime_config(git_fixture)),
            "drop",
        ],
    )
    assert main() == 1
    assert "specify a ticket ID" in capsys.readouterr().err

@pytest.mark.parametrize(
    ("answer", "selected"),
    [("1", "T-1"), ("2", "T-2"), ("0", None), ("", None)],
)
def test_shared_candidate_selector_selection_and_cancel(
    monkeypatch, answer, selected
):
    candidates = (
        {"id": "T-1", "title": "One", "reason": "first", "kind": "x"},
        {"id": "T-2", "title": "Two", "reason": "second", "kind": "x"},
    )
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    result = cli._select_candidate(candidates, "Candidates", "invalid selection")
    assert result is None if selected is None else result["id"] == selected

@pytest.mark.parametrize(
    ("title", "expected_label"),
    [
        (
            "Generalize nominal lexical readiness",
            "T-1  Generalize nominal lexical readiness",
        ),
        ("T-1", "T-1"),
        ("", "T-1"),
    ],
)
def test_shared_candidate_selector_avoids_redundant_titles(
    monkeypatch, capsys, title, expected_label
):
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")

    cli._select_candidate(
        ({"id": "T-1", "title": title, "reason": "reason", "kind": "retry"},),
        "Candidates",
        "invalid selection",
    )

    output = capsys.readouterr().out
    assert f"  1) {expected_label}\n" in output
    assert "     reason\n" in output

@pytest.mark.parametrize("answer", ["not-a-number", "3"])
def test_shared_candidate_selector_rejects_invalid_selection(monkeypatch, answer):
    candidates = ({"id": "T-1", "title": "One", "reason": "first", "kind": "x"},)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)
    with pytest.raises(DevlegateError, match="invalid selection"):
        cli._select_candidate(candidates, "Candidates", "invalid selection")

def test_daemon_application_error_is_authoritative(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    def fail_status():
        raise DevlegateError("authoritative daemon failure")

    monkeypatch.setattr(cli_daemon, "status_view", fail_status)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "status"],
    )

    assert main() == 1
    assert "authoritative daemon failure" in capsys.readouterr().err

def test_daemon_protocol_error_is_not_bypassed(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    monkeypatch.setattr(
        "devlegate.cli.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IPCClientError("service IPC protocol error: malformed response")
        ),
    )
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "plan"],
    )

    assert main() == 1
    assert (
        "service is running but its IPC endpoint is unavailable"
        in capsys.readouterr().err
    )

def test_authority_guard_blocks_exclusive_lock_and_shared_guard_blocks_daemon(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    with locator.absence_guard():
        engine = ServiceEngine(config, read_only=True)
        with pytest.raises(DevlegateError, match="another devlegate instance"):
            engine._lock()

    authority = engine._lock()
    try:
        with pytest.raises(RuntimeAuthorityPresent):
            with locator.absence_guard():
                pass
    finally:
        authority.close()

@pytest.mark.parametrize("make_endpoint", [False, True])
def test_ipc_unavailable_with_authority_fails_closed(
    git_fixture, monkeypatch, capsys, make_endpoint
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    endpoint = engine.ipc_socket_path
    listener = None
    try:
        if make_endpoint:
            endpoint.parent.mkdir(parents=True, exist_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(endpoint))
            listener.close()
            listener = None
        monkeypatch.setattr(
            "devlegate.cli._service_engine",
            lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["devlegate", "--env", str(config), "status"],
        )

        assert main() == 1
        assert (
            "service is running but its IPC endpoint is unavailable"
            in capsys.readouterr().err
        )
    finally:
        if listener is not None:
            listener.close()
        endpoint.unlink(missing_ok=True)
        authority.close()

def test_stale_socket_without_authority_uses_guarded_fallback(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    endpoint = engine.ipc_socket_path
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))
    listener.close()
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "plan", "--json"],
    )

    try:
        assert main() == 0
        assert json.loads(capsys.readouterr().out)["action"] == "blocked"
        assert endpoint.exists()
    finally:
        endpoint.unlink(missing_ok=True)

@pytest.mark.parametrize("command", ["status", "plan"])
def test_explicit_project_works_from_subdirectory_without_daemon(
    git_fixture, monkeypatch, capsys, command
):
    config = _short_runtime_config(git_fixture)
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), command],
    )

    assert main() in (0, 1)
    assert "run devlegate from repository root" not in capsys.readouterr().err

@pytest.mark.parametrize("command", ["status", "plan"])
def test_explicit_project_works_from_subdirectory_with_daemon(
    cli_daemon, git_fixture, monkeypatch, capsys, command
):
    config = _short_runtime_config(git_fixture)
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), command],
    )

    assert main() in (0, 1)
    assert "run devlegate from repository root" not in capsys.readouterr().err

def test_foreground_hosts_real_ipc_status_and_plan_until_stopped(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    with LiveService(git_fixture["working"], git_fixture["config"]) as service:
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 0, status.stderr
        assert plan.returncode == 0, plan.stderr
        assert json.loads(status.stdout)["execution"]["phase"] == "idle"
        assert "action" in json.loads(plan.stdout)
        assert service.process is not None
        assert service.process.poll() is None
    assert service.stdout.count("---< D E V L E G A T E >---") == 1
    assert "mode    : direct" in service.stdout

def test_real_service_observers_succeed_during_owner_retry(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        result = service.cli("retry", "T-1", timeout=10)
        assert result.returncode == 0, result.stderr
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
        responses = _parallel_service_requests(service, ["status", "plan", "status"])
        assert all(isinstance(response, dict) for response in responses)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()

def test_real_service_admitted_retry_outlives_cli_process(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    service_pid = service.process.pid
    cli_process = None
    try:
        cli_process = service.start_cli("retry", "T-1")
        stdout, stderr = cli_process.communicate(timeout=15)
        assert cli_process.returncode == 0, stderr
        assert "retry accepted: T-1" in stdout

        def worker_active_after_cli_exit():
            state = _disk_state(config)
            return state.get("execution_stage") == "worker-running" and (
                _worker_body_started(
                    pid_file, state.get("worker_identity"), attempts, 1
                )
            )

        service.wait_for(worker_active_after_cli_exit, timeout=30)
        assert cli_process.poll() == 0
        assert service.process is not None
        assert service.process.pid == service_pid
        assert service.process.poll() is None
        status = service.cli("status", "--json")
        assert status.returncode in {0, 1}, status.stderr
        assert json.loads(status.stdout)["execution"]["phase"] == "agent_running"
        identity = _disk_state(config)["worker_identity"]
        os.kill(identity["pid"], 0)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        if cli_process is not None and cli_process.poll() is None:
            cli_process.kill()
            cli_process.wait(timeout=5)
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()

def test_real_service_stale_status_cannot_authorize_second_retry(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        stale = json.loads(service.cli("status", "--json").stdout)
        assert stale["execution"]["phase"] == "idle"
        def submit_first_retry_when_owner_is_ready():
            result = service.cli("retry", "T-1", timeout=10)
            if result.returncode == 0:
                return True
            assert "service busy" in result.stderr
            return False

        service.wait_for(submit_first_retry_when_owner_is_ready, timeout=10)
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
        with pytest.raises(IPCClientError, match="service busy"):
            ipc_request(
                service.locator.socket_path,
                "retry",
                {"ticket_id": "T-1"},
                mutable=True,
                request_id="stale-retry",
                timeout=10,
            )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()

def test_real_service_receipt_restart_is_not_a_persistent_command_queue(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "receipt-worker.py"
    attempts = git_fixture["tmp"] / "receipt-attempts.txt"
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
        try:
            service.cli("retry", "T-1", timeout=5)
        except subprocess.TimeoutExpired:
            pass
        _wait_process_death(service)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: isinstance(_disk_state(config).get("mutable_receipts"), dict)
        )
        assert not attempts.exists()
        result = service.cli("retry", "T-1")
        assert result.returncode == 0, result.stderr
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-1.md").is_file()
        )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()

def test_real_service_reconcile_receipt_restart_is_not_a_queue(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _recovery_config(git_fixture)
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
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
    git(git_fixture["working"], "pull", "--ff-only", "origin", "main")
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "receipt_after_save")
    release_marker = git_fixture["tmp"] / "reconcile-operator-released"
    release_marker.unlink(missing_ok=True)
    monkeypatch.setenv("DEVLEGATE_TEST_OPERATOR_RELEASE_MARKER", str(release_marker))
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "receipt_after_save"),
    )
    service.start()
    service.wait_ready()
    try:
        try:
            service.cli("reconcile", "update-base", "T-1", "--onto", target, timeout=5)
        except subprocess.TimeoutExpired:
            pass
        _wait_process_death(service)
        before = _disk_state(config)["reconciliation"]
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        assert _disk_state(config)["reconciliation"] == before
        result = service.cli("reconcile", "update-base", "T-1", "--onto", target)
        assert result.returncode == 0, result.stderr
        service.wait_for(
            lambda: _disk_state(config)["reconciliation"]["status"] == "resolved"
        )
        service.wait_for(lambda: release_marker.exists())
    finally:
        service.stop()

def test_real_service_process_executes_retry_from_real_cli(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "process-worker.py"
    attempts = git_fixture["tmp"] / "attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _short_runtime_config(git_fixture)
    config.write_text(
        config.read_text()
        .replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
        .replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1

    with LiveService(git_fixture["working"], config) as service:
        result = service.cli("retry", "T-1")
        assert result.returncode == 0, result.stderr
        assert "retry accepted: T-1" in result.stdout

        expected_review = [{"id": "T-1", "title": "Control ticket"}]

        def retry_completed_in_status():
            observed = service.cli("status", "--json")
            if observed.returncode != 0:
                return False
            try:
                payload = json.loads(observed.stdout)
            except json.JSONDecodeError:
                return False
            return payload["tickets"]["review"] == expected_review

        service.wait_for(retry_completed_in_status)
        assert (engine.control_worktree / "kanban/review/T-1.md").is_file()
        status = json.loads(service.cli("status", "--json").stdout)
        assert status["tickets"]["review"] == expected_review
        assert attempts.read_text().splitlines() == ["attempt"]

def test_real_service_reconcile_published_rewrite_restart_after_remote_effect(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _recovery_config(git_fixture)
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "first.txt").write_text("first\n")
        (git_fixture["working"] / "product-change.txt").write_text("product\n")
        git(git_fixture["working"], "add", "product-change.txt")
        git(git_fixture["working"], "commit", "-m", "advance product")
        git(git_fixture["working"], "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    reconciliation = dict(engine._state["reconciliation"])
    execution = next((engine.state_dir / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "second.txt").write_text("second\n")
    git(execution, "add", "second.txt")
    git(execution, "commit", "-m", "second checkpoint")
    second = git(execution, "rev-parse", "HEAD").stdout.strip()
    first = git(execution, "rev-parse", "HEAD^").stdout.strip()
    git(
        execution,
        "push",
        "origin",
        f"{first}:refs/heads/{reconciliation['execution_branch']}",
    )
    git(git_fixture["working"], "pull", "--ff-only", "origin", "main")
    target = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(execution, "update-ref", reconciliation["evidence_ref"], second)
    engine._save_state(
        "idle",
        reconciliation={
            **reconciliation,
            "worker_checkpoint": second,
            "execution_remote_head": first,
            "execution_report": {
                **reconciliation["execution_report"],
                "workspace_head": second,
            },
        },
    )

    monkeypatch.setenv(
        "DEVLEGATE_TEST_CRASH_POINT", "reconcile_publication_after_effect"
    )
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config)
    )
    service.start()
    service.wait_ready()
    try:
        service.cli(
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            target,
            "--rewrite-published",
        )
        _wait_process_death(service)
        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
        service.restart()
        finalized = service.cli(
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            target,
            "--rewrite-published",
        )
        assert finalized.returncode == 0, finalized.stderr
        service.wait_for(
            lambda: _disk_state(config)["reconciliation"]["status"] == "resolved"
        )
        resolved = _disk_state(config)["reconciliation"]
        assert resolved["effective_base"] == target
        assert (
            git(execution, "rev-parse", resolved["evidence_ref"]).stdout.strip()
            == resolved["worker_checkpoint"]
        )
        assert (
            git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
            == second
        )
    finally:
        service.stop()

def test_retry_refuses_without_daemon_without_constructing_engine(
    git_fixture, monkeypatch
):
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False, repository=None):
            calls.append(("init", env_file, read_only))

        def retry(self, ticket_id=None, _stop_event=None):
            calls.append(("retry", ticket_id))
            return 3

    monkeypatch.setattr("devlegate.cli._service_engine", FakeServiceEngine)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "retry", "T-1"],
    )
    assert main() == 1
    assert calls == []

def test_retry_without_ticket_rejects_non_tty(monkeypatch, tmp_path):
    devlegate = object.__new__(Devlegate)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr(os, "isatty", lambda _fd: False)

    with pytest.raises(DevlegateError, match="interactive retry requires a terminal"):
        devlegate.retry()

def test_interactive_retry_selects_only_requested_candidate(monkeypatch, capsys):
    devlegate = object.__new__(Devlegate)
    candidates = (("T-1", "one", "first"), ("T-2", "two", "second"))
    selected = []
    monkeypatch.setattr(devlegate, "_retry_candidates", lambda: candidates)
    monkeypatch.setattr(devlegate, "_lock", lambda: nullcontext())
    monkeypatch.setattr(
        devlegate,
        "run_iteration",
        lambda intent: selected.append(intent.retry_ticket_id) or 0,
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert devlegate.retry() == 0
    assert selected == ["T-2"]
    output = capsys.readouterr().out
    assert "1) T-1" in output
    assert "2) T-2" in output

@pytest.mark.parametrize(
    "answer", [pytest.param("", id="enter"), pytest.param("0", id="zero")]
)
def test_interactive_retry_cancel_without_running(monkeypatch, answer):
    devlegate = object.__new__(Devlegate)
    selected = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(devlegate, "_lock", lambda: nullcontext())
    monkeypatch.setattr(
        devlegate,
        "run_iteration",
        lambda intent: selected.append(intent.retry_ticket_id) or 0,
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    assert devlegate.retry() == 0
    assert selected == []

def test_interactive_retry_rejects_invalid_selection_without_running(monkeypatch):
    devlegate = object.__new__(Devlegate)
    ran = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(
        devlegate, "run_iteration", lambda _intent: ran.append(True) or 0
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")

    with pytest.raises(DevlegateError, match="invalid retry selection"):
        devlegate.retry()
    assert ran == []
