# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Daemon host construction, readiness, diagnostics, and signal policy."""

from _daemon_support import *  # noqa: F403,F405

@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
    ],
)
def test_matching_shutdown_signal_is_control_flow(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    result = subprocess.CompletedProcess([], returncode, "", "")

    with engine._stop_context(stop_intent):
        with pytest.raises(ShutdownInterrupted):
            engine._raise_on_shutdown_interruption(result)

@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        (None, -signal.SIGINT),
        ("operator_abort", -signal.SIGTERM),
    ],
)
def test_unmatched_git_failure_remains_diagnostic(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    if kind is not None:
        stop_intent.request(kind)
    result = subprocess.CompletedProcess([], returncode, "", "")

    with engine._stop_context(stop_intent):
        engine._raise_on_shutdown_interruption(result)

def test_foreground_failure_reports_worker_diagnostic(tmp_path, monkeypatch, capsys):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )

    assert daemon.run_service(engine, once=True) == 1
    output = capsys.readouterr().out
    assert "worker failed: worker exited with status 1" in output
    assert "run failed with status 1" not in output
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None

def test_hosting_modes_are_explicit_and_startup_fd_is_only_transport(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    read_fd, write_fd = os.pipe()
    try:
        direct = daemon.ServiceHost(engine)
        internal = daemon.ServiceHost(engine, host_mode=daemon.HostingMode.INTERNAL)
        external = daemon.ServiceHost(engine, host_mode=daemon.HostingMode.EXTERNAL)
        transported = daemon.ServiceHost(engine, startup_fd=write_fd)

        assert direct.host_mode is daemon.HostingMode.DIRECT
        assert internal.host_mode is daemon.HostingMode.INTERNAL
        assert external.host_mode is daemon.HostingMode.EXTERNAL
        assert transported.host_mode is daemon.HostingMode.DIRECT
    finally:
        os.close(read_fd)
        os.close(write_fd)

def test_internal_reexec_preserves_explicit_host_mode(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    captured = {}
    read_fd, write_fd = os.pipe()
    authority = os.fdopen(write_fd, "w")
    monkeypatch.setattr(
        os,
        "execvpe",
        lambda executable, command, environment: captured.update(
            executable=executable, command=command, environment=environment
        ),
    )
    try:
        daemon.ServiceHost(
            engine, host_mode=daemon.HostingMode.INTERNAL
        )._reexec(authority, "request")
    finally:
        authority.close()
        os.close(read_fd)

    assert captured["environment"]["DEVLEGATE_HOST_MODE"] == "internal"

@pytest.mark.parametrize(
    "host_mode", [daemon.HostingMode.DIRECT, daemon.HostingMode.EXTERNAL]
)
def test_non_internal_host_cannot_self_reexec(tmp_path, monkeypatch, host_mode):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    with pytest.raises(DevlegateError, match="only internal hosting"):
        daemon.ServiceHost(engine, host_mode=host_mode)._reexec(object(), "request")

def test_external_host_uses_inherited_service_stream_and_no_service_log(
    tmp_path, monkeypatch, capsys
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "run_iteration", lambda: 0)

    assert daemon.run_service(
        engine,
        host_mode=daemon.HostingMode.EXTERNAL,
        once=True,
        startup_report=lambda: print("external service output"),
    ) == 0

    assert engine._workers.show_worker_output is False
    assert not engine._locator.service_log_path.exists()
    assert "external service output" in capsys.readouterr().out

def test_systemd_readiness_callback_follows_canonical_ready_boundary(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(engine, "run_iteration", lambda: 0)
    events = []

    def startup_report():
        events.append(
            ("startup", engine._service_ready, engine.ipc_socket_path.exists())
        )

    def readiness_report():
        events.append(
            ("systemd-ready", engine._service_ready, engine.ipc_socket_path.exists())
        )

    assert daemon.run_service(
        engine,
        host_mode=daemon.HostingMode.EXTERNAL,
        once=True,
        startup_report=startup_report,
        readiness_report=readiness_report,
    ) == 0

    assert events == [
        ("startup", False, True),
        ("systemd-ready", True, True),
    ]

def test_unhandled_host_failure_is_reported_by_offline_status(
    tmp_path, monkeypatch, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    failure = DevlegateError("unexpected owner failure")

    def fail_iteration(*_args, **_kwargs):
        raise failure

    monkeypatch.setattr(engine, "run_iteration", fail_iteration)
    with pytest.raises(DevlegateError, match="unexpected owner failure"):
        host.run()

    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is not None
    assert diagnostic.stage == "runtime"
    assert diagnostic.exception_type == "DevlegateError"
    assert diagnostic.message == "unexpected owner failure"

    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "status", "--json"],
    )
    assert cli.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"]["state"] == "stopped"
    assert payload["service"]["failure"]["message"] == "unexpected owner failure"
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "status"],
    )
    assert cli.main() == 1
    human = capsys.readouterr().out
    assert "Service failure:" in human
    assert "Detail: unexpected owner failure" in human

def test_unhandled_startup_failure_is_persisted_as_startup_diagnostic(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        engine,
        "ensure_hosted_views",
        lambda: (_ for _ in ()).throw(DevlegateError("startup views failed")),
    )
    with pytest.raises(DevlegateError, match="startup views failed"):
        host.run()
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is not None
    assert diagnostic.stage == "startup"
    assert diagnostic.message == "startup views failed"

def test_successful_ready_clears_terminal_service_failure(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda stop_intent, **_kwargs: (
            stop_intent.request("operator_abort", source="test") or 0
        ),
    )
    from devlegate.service_diagnostics import create, write

    write(engine._locator, create("runtime", DevlegateError("old failure")))
    assert host.run() == 0
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None

def test_diagnostic_write_failure_does_not_replace_original_failure(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            DevlegateError("original failure")
        ),
    )
    monkeypatch.setattr(
        daemon,
        "write",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    with pytest.raises(DevlegateError, match="original failure"):
        host.run()

def test_corrupt_service_diagnostic_is_reported_without_blocking_status(
    tmp_path, monkeypatch, capsys
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    diagnostic_path = engine._locator.state_dir / "diagnostics"
    diagnostic_path.mkdir(parents=True)
    (diagnostic_path / f"{engine._locator.state_key}.json").write_text("not json")
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "status", "--json"],
    )
    assert cli.main() == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"]["failure"] == {
        "state": "unavailable",
        "message": "service diagnostic unavailable/corrupt",
    }

def test_foreground_cli_remains_attached_until_host_returns(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result = []

    class FakeServiceEngine:
        def __init__(self, _env_file, *, read_only=False, repository=None):
            assert not read_only

    def host(_engine, **_kwargs):
        entered.set()
        assert release.wait(10)
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_service", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "foreground"],
    )
    monkeypatch.chdir(working)
    thread = threading.Thread(target=lambda: result.append(cli.main()), daemon=True)
    thread.start()
    assert entered.wait(10)
    assert thread.is_alive()
    release.set()
    thread.join(10)

    assert result == [0]

@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_daemon_signal_wakes_poll_wait_without_second_iteration(
    tmp_path, monkeypatch, signum
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    stop_event = threading.Event()
    calls = []
    result = []

    def iteration():
        calls.append(True)
        entered.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)
    stop_event.set()
    thread.join(10)

    assert not thread.is_alive()
    assert result == [0]
    assert len(calls) == 1

    lifecycle_config = _config
    lifecycle_config.write_text(lifecycle_config.read_text() + "POLL_INTERVAL=5\n")
    lifecycle_engine = ServiceEngine(lifecycle_config)
    lifecycle_stop = threading.Event()
    lifecycle_entered = threading.Event()
    lifecycle_release = threading.Event()
    idle_wait_entered = threading.Event()

    def lifecycle_iteration():
        lifecycle_entered.set()
        assert lifecycle_release.wait(5)
        return 0

    monkeypatch.setattr(lifecycle_engine, "run_iteration", lifecycle_iteration)

    class WakeProbe:
        def __init__(self):
            self.event = threading.Event()

        def clear(self):
            self.event.clear()

        def set(self):
            self.event.set()

        def wait(self, timeout=None):
            if timeout == 5:
                idle_wait_entered.set()
            return self.event.wait(timeout)

    monkeypatch.setattr(lifecycle_engine, "_service_wake", WakeProbe())
    lifecycle_result = []
    lifecycle_thread = threading.Thread(
        target=lambda: lifecycle_result.append(
            lifecycle_engine.serve(lifecycle_stop)
        ),
        daemon=True,
    )
    lifecycle_thread.start()
    assert lifecycle_entered.wait(5)
    accepted = lifecycle_engine.request_lifecycle("stop", "lost-wake-test")
    assert accepted["intent"] == "stop"
    lifecycle_release.set()
    lifecycle_thread.join(0.5)
    lifecycle_stop.set()
    lifecycle_engine.wake()
    lifecycle_thread.join(5)

    assert not lifecycle_thread.is_alive()
    assert lifecycle_result == [0]
    assert not idle_wait_entered.is_set()

def test_daemon_stop_already_requested_does_not_admit_iteration(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    stop_event.set()
    calls = []
    monkeypatch.setattr(
        engine, "run_iteration", lambda _intent=None: calls.append(True)
    )
    monkeypatch.setattr(
        "devlegate.runtime._git",
        lambda *_args, **_kwargs: pytest.fail("unexpected observation"),
    )

    assert engine.serve(stop_event) == 0
    assert calls == []

def test_persisted_merge_pending_drains_even_when_stop_already_set(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=target,
        changed_paths="remote-change.txt\n",
        control_head=control,
    )
    stop_event = threading.Event()
    stop_event.set()

    assert engine.serve(stop_event) == 0
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    assert engine._state["phase"] == "idle"
