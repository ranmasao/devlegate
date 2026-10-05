# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Daemon tests outside host, lifecycle, and worker-loss ownership."""

from _daemon_support import *  # noqa: F403,F405

def test_explicit_retry_intent_cannot_leak_to_next_iteration(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    intents = []
    monkeypatch.setattr(
        engine,
        "_retry_candidates",
        lambda: (("T-1", "ticket", "retry"),),
    )

    def iteration(intent=None):
        intents.append(intent)
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)

    assert engine._retry_locked("T-1") == 0
    assert engine.run_iteration() == 0
    assert intents == [
        IterationIntent(retry_ticket_id="T-1"),
        None,
    ]

def test_drop_rejects_live_or_ambiguous_execution_state(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine._state.update(
        {
            "phase": "agent_running",
            "execution_ticket_id": "T-1",
            "execution_id": "execution-1",
            "execution_stage": "lifecycle",
            "worker_identity": {"execution_id": "execution-1"},
        }
    )
    with pytest.raises(DevlegateError, match="worker ownership"):
        engine._validate_drop_admission("T-1", "execution-1")
    engine._state["worker_identity"] = None
    engine._state["execution_stage"] = "post-checkpoint"
    with pytest.raises(DevlegateError, match="limited to blocked lifecycle"):
        engine._validate_drop_admission("T-1", "execution-1")

def test_iteration_body_does_not_reenter_scheduler():
    source = inspect.getsource(ServiceEngine._run_iteration_body)
    assert "run_iteration(" not in source

@pytest.mark.parametrize("boundary", ["after-clear", "before-receipt"])
def test_drop_final_durable_boundaries_replay_without_active_state(
    tmp_path, monkeypatch, boundary
):
    engine, config, control, workspace, command = _prepare_drop_owner_command(
        tmp_path, monkeypatch
    )
    original_save = engine._save_state

    def faulted_save(phase, **fields):
        if boundary == "before-receipt" and "mutable_receipts" in fields:
            raise RuntimeError("simulated crash before receipt")
        original_save(phase, **fields)
        if boundary == "after-clear" and fields.get("clear_execution"):
            raise RuntimeError("simulated crash after clear")

    monkeypatch.setattr(engine, "_save_state", faulted_save)
    with pytest.raises(RuntimeError, match=boundary.replace("-", " ")):
        engine._admit_operator_command(command)

    disposition_ref = engine._drop_disposition_ref("T-1", "drop-final-boundary")
    disposition = json.loads(
        git(engine.repo, "cat-file", "-p", disposition_ref).stdout
    )
    assert disposition["disposition"] == "dropped"
    assert engine._state["phase"] == "idle"
    assert "execution_id" not in engine._state
    assert "drop-final-boundary-request" not in engine._state.get(
        "mutable_receipts", {}
    )

    restarted = ServiceEngine(config)
    assert restarted._state["phase"] == "idle"
    assert "execution_id" not in restarted._state
    assert not workspace.path.exists()
    assert not list((control / "executions").glob("**/*.json"))

    applied = []
    monkeypatch.setattr(
        restarted,
        "_apply_execution_lifecycle",
        lambda report: applied.append(report.execution_id),
    )
    replay = OperatorCommand(
        request_id="drop-final-boundary-request",
        method="drop",
        fingerprint=runtime._drop_request_fingerprint(
            "T-1", "drop-final-boundary"
        ),
        ticket_id="T-1",
        execution_id="drop-final-boundary",
    )
    restarted._admit_operator_command(replay)
    assert applied == []
    assert replay.admission_result == {
        "accepted": True,
        "ticket_id": "T-1",
        "execution_id": "drop-final-boundary",
    }
    assert restarted._state["mutable_receipts"][
        "drop-final-boundary-request"
    ]["accepted"] is True

def test_drop_retirement_workspace_error_does_not_kill_owner_loop(
    tmp_path, monkeypatch, capsys
):
    engine, _config, _control, _workspace, command = _prepare_drop_owner_command(
        tmp_path, monkeypatch
    )
    engine.poll_interval = "1"
    stop_event = threading.Event()
    served = []
    submitted = []

    def fail_retirement(_manager, _workspace, _expected_head):
        raise ExecutionWorkspaceError("submodule retirement is unsafe")

    monkeypatch.setattr(ExecutionWorkspaceManager, "retire", fail_retirement)
    service_thread = threading.Thread(
        target=lambda: served.append(engine.serve(stop_event)), daemon=True
    )
    service_thread.start()

    def submit():
        try:
            submitted.append(
                engine.submit_drop(
                    command.ticket_id,
                    command.execution_id,
                    request_id=command.request_id,
                )
            )
        except DevlegateError as error:
            submitted.append(error)

    submit_thread = threading.Thread(target=submit, daemon=True)
    submit_thread.start()
    submit_thread.join(timeout=5)
    assert not submit_thread.is_alive()
    assert len(submitted) == 1
    assert isinstance(submitted[0], DevlegateError)
    assert "cannot retire execution worktree" in str(submitted[0])
    assert service_thread.is_alive()
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == "lifecycle"
    assert engine._read_drop_disposition("T-1", command.execution_id)[
        "disposition"
    ] == "dropped"

    stop_event.set()
    engine.wake()
    service_thread.join(timeout=5)
    assert not service_thread.is_alive()
    assert served == [0]

    engine._automatic_drop_recovery_execution = None
    with pytest.raises(DevlegateError, match="cannot retire execution worktree"):
        engine.run_iteration()
    output = capsys.readouterr().out
    assert "recovering dropped execution T-1 (drop-final-b)" in output
    assert "dropped execution cleanup complete" not in output

def test_foreground_service_command_constructs_one_service_engine(
    tmp_path, monkeypatch
):
    working, config, _state = control_fixture(tmp_path)
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False, repository=None):
            calls.append(("init", env_file, read_only))

    def host(engine, **_kwargs):
        calls.append(("host", engine))
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_service", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "--env", str(config), "foreground"],
    )
    monkeypatch.chdir(working)

    assert cli.main() == 0
    assert len(calls) == 2
    assert calls[0] == ("init", config, False)
    assert calls[1][0] == "host"
    assert isinstance(calls[1][1], FakeServiceEngine)

def test_operator_abort_does_not_create_terminal_service_failure(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    host = daemon.ServiceHost(engine)
    monkeypatch.setattr(
        host,
        "_serve_engine",
        lambda stop_intent, **_kwargs: (
            stop_intent.request("operator_abort", source="test") or 130
        ),
    )
    assert host.run() == 130
    diagnostic, corrupt = read_service_failure(engine._locator)
    assert not corrupt
    assert diagnostic is None

def test_foreground_repeated_blocker_is_reported_until_changed(
    tmp_path, monkeypatch, capsys
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    calls = 0

    def run_once():
        nonlocal calls
        calls += 1
        if calls == 1:
            raise WorkflowBlockedError("execution recovery is blocked")
        if calls == 2:
            raise WorkflowBlockedError("execution recovery is blocked")
        if calls == 3:
            raise WorkflowBlockedError("execution recovery changed")
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_once)
    engine.poll_interval = "0"
    assert engine.serve(stop_event) == 0
    output = capsys.readouterr().out
    assert output.count("workflow blocked: execution recovery is blocked") == 1
    assert output.count("workflow blocked: execution recovery changed") == 1

def test_unrelated_devlegate_error_still_escapes_service(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine,
        "run_iteration",
        lambda: (_ for _ in ()).throw(DevlegateError("fatal invariant")),
    )

    with pytest.raises(DevlegateError, match="fatal invariant"):
        engine.serve(threading.Event())

@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
    ],
)
def test_merge_pending_retries_matching_product_merge(
    tmp_path, monkeypatch, kind, returncode
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
    original_git = runtime._git
    merge_calls = 0

    def interrupt_product_merge(repo, *args, check=True):
        nonlocal merge_calls
        if repo == engine.repo and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_product_merge)

    expected_result = 130 if kind == "operator_abort" else 0
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    host = daemon.ServiceHost(engine)
    with host._signal_ownership(stop_intent, lambda _intent, _request_id: {}):
        result = host._serve_engine(stop_intent)
    assert result == expected_result
    assert merge_calls == 2
    assert engine._state["phase"] == "idle"
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target

def test_merge_pending_retries_product_merge_then_preserves_real_failure(
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
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")
    original_git = runtime._git
    merge_calls = 0

    def fail_product_retry(repo, *args, check=True):
        nonlocal merge_calls
        if repo == engine.repo and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
            return subprocess.CompletedProcess(["git"], 128, "", "fatal: merge failed")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", fail_product_retry)

    with engine._stop_context(stop_intent):
        assert engine.run_iteration() == 1
    assert merge_calls == 2
    assert engine._state["phase"] == "merge_pending"

def test_merge_pending_retries_matching_control_merge(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    remote_url = git(
        engine.control_worktree, "remote", "get-url", "origin"
    ).stdout.strip()
    publisher = tmp_path / "control-seed"
    git(tmp_path, "clone", remote_url, publisher)
    git(publisher, "switch", "--track", "origin/devlegate/control")
    git(publisher, "config", "user.name", "Devlegate Test")
    git(publisher, "config", "user.email", "devlegate@example.test")
    (publisher / "control-change.txt").write_text("control\n")
    git(publisher, "add", "control-change.txt")
    git(publisher, "commit", "-m", "control change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:refs/heads/devlegate/control")
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    original_git = runtime._git
    merge_calls = 0

    def interrupt_control_merge(repo, *args, check=True):
        nonlocal merge_calls
        if repo == engine.control_worktree and args[:2] == ("merge", "--ff-only"):
            merge_calls += 1
            if merge_calls == 1:
                return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_control_merge)
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")

    assert engine.serve(stop_intent) == 0
    assert merge_calls == 2
    assert engine._state["phase"] == "idle"
    assert git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip() == target

def test_merge_pending_repeated_matching_git_interrupt_is_bounded(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request("operator_abort")
    original_git = runtime._git
    fetch_calls = 0

    def interrupt_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            return subprocess.CompletedProcess(["git"], -signal.SIGINT, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_fetch)

    with engine._stop_context(stop_intent):
        with pytest.raises(ShutdownInterrupted):
            engine._git_runtime(
                engine.control_worktree,
                "fetch",
                "--prune",
                engine.remote_name,
                engine.control_branch,
            )
    assert fetch_calls == 2

@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", 128),
        ("operator_abort", -signal.SIGTERM),
    ],
)
def test_merge_pending_nonmatching_fetch_failure_is_not_retried(
    tmp_path, monkeypatch, kind, returncode
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    local = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    control = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    engine._save_state(
        "merge_pending",
        local_head=local,
        remote_head=local,
        changed_paths="",
        control_head=control,
    )
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    original_git = runtime._git
    fetch_calls = 0

    def fail_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", fail_fetch)

    with engine._stop_context(stop_intent):
        with pytest.raises(WorkflowBlockedError):
            engine.run_iteration()
    assert fetch_calls == 1

def test_daemon_holds_project_lock_while_service_is_active(tmp_path, monkeypatch):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()
    result = []

    def iteration():
        entered.set()
        assert release.wait(10)
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)

    second = ServiceEngine(config)
    before = dict(second._state)
    with pytest.raises(DevlegateError, match="another devlegate instance"):
        daemon.run_service(second, once=True)
    assert second.status_view().phase == "idle"
    assert second.plan_view().action == "run-worker"
    assert second._state == before

    release.set()
    thread.join(10)
    assert result == [0]
    assert state.exists()

def test_daemon_has_no_unix_daemonization_path():
    source = open(daemon.__file__, encoding="ascii").read()
    assert "os.fork" not in source
    assert "setsid" not in source
