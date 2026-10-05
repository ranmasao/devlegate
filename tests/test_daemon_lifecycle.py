# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Daemon owner-loop lifecycle, drain, stop, and restart semantics."""

from _daemon_support import *  # noqa: F403,F405

def test_lifecycle_drain_rejects_submitted_command_before_owner_admission(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    submitted = []

    def submit():
        try:
            submitted.append(
                engine.submit_retry("T-1", request_id="retry-before-drain")
            )
        except DevlegateError as error:
            submitted.append(error)

    thread = threading.Thread(
        target=submit
    )
    thread.start()
    deadline = time.monotonic() + 5
    while not engine._operator_command_pending() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert engine._operator_command_pending()
    engine.request_lifecycle("stop", "stop-request")
    command, scheduler = engine._claim_owner_work(True)
    assert scheduler is False
    assert isinstance(command, OperatorCommand)
    with pytest.raises(DevlegateError, match="draining"):
        engine._admit_operator_command(command)
    engine._release_operator_command()
    thread.join(5)
    assert not thread.is_alive()
    assert len(submitted) == 1
    assert isinstance(submitted[0], DevlegateError)

def test_drop_rejects_lifecycle_drain_before_owner_admission(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    engine.request_lifecycle("stop", "stop-request")
    with pytest.raises(DevlegateError, match="service is draining"):
        engine.submit_drop("T-1", "execution-1", request_id="drop-request")

def test_force_lifecycle_promotes_existing_shutdown_intent(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    intent = daemon.ShutdownIntent()

    with engine._stop_context(intent):
        accepted = engine.request_lifecycle("stop", "force-request", force=True)

    assert accepted["request_id"] == "force-request"
    assert accepted["force"] is True
    assert intent.kind == "operator_abort"
    assert intent.source == "operator_abort"
    assert intent.is_set()

@pytest.mark.parametrize(
    "fault_stage",
    ["after-evidence", "after-disposition", "after-remote", "after-worktree"],
)
def test_restart_after_drop_disposition_finishes_drop_without_lifecycle(
    tmp_path, monkeypatch, fault_stage, capsys
):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    workspace, control = persist_agent_running(
        engine,
        state,
        execution_id="drop-replay",
        checkpointed=True,
        record_start=True,
        publish=True,
    )
    git(
        workspace.path,
        "commit",
        "--amend",
        "-m",
        "Devlegate checkpoint T-1 drop-replay",
    )
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "--force",
        "origin",
        f"HEAD:refs/heads/{workspace.branch}",
    )
    workspace = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    ).prepare(workspace.base_head)
    report = build_execution_report(
        execution_id="drop-replay",
        ticket_id="T-1",
        code_base_head=workspace.base_head,
        control_head=engine._state["execution_control_head"],
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        workspace_head=workspace.head,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        ),
    )
    engine._save_state(
        "agent_running",
        execution_stage="lifecycle",
        worker_identity=None,
        execution_start_head=workspace.base_head,
        execution_remote_head=checkpoint,
        pending_execution_report=report.as_dict(),
    )
    original_retire = engine._retire_drop_workspace
    original_pin = engine._pin_drop_evidence

    def crash_after_disposition(*args):
        raise RuntimeError("simulated crash after disposition")

    def crash_after_evidence(report):
        git(
            engine.repo,
            "update-ref",
            engine._drop_evidence_ref(report.ticket_id, report.execution_id),
            report.workspace_head,
        )
        raise RuntimeError("simulated crash after evidence")

    def crash_after_remote(report, workspace):
        git(
            engine.repo,
            "push",
            f"--force-with-lease=refs/heads/{report.execution_branch}:{report.workspace_head}",
            "origin",
            f":refs/heads/{report.execution_branch}",
        )
        if fault_stage == "after-worktree":
            ExecutionWorkspaceManager(
                engine.repo, engine.execution_worktree_root, report.ticket_id
            ).retire(workspace, report.workspace_head)
        raise RuntimeError(f"simulated crash {fault_stage}")

    if fault_stage == "after-evidence":
        monkeypatch.setattr(engine, "_pin_drop_evidence", crash_after_evidence)
        expected_error = "after evidence"
    elif fault_stage == "after-disposition":
        monkeypatch.setattr(engine, "_retire_drop_workspace", crash_after_disposition)
        expected_error = "after disposition"
    else:
        monkeypatch.setattr(engine, "_retire_drop_workspace", crash_after_remote)
        expected_error = f"after-{fault_stage.removeprefix('after-')}"
    with pytest.raises(RuntimeError, match=expected_error):
        engine._drop_owned("T-1", "drop-replay")
    evidence_ref = engine._drop_evidence_ref("T-1", "drop-replay")
    disposition_ref = engine._drop_disposition_ref("T-1", "drop-replay")
    assert git(engine.repo, "rev-parse", "--verify", evidence_ref).stdout.strip() == (
        workspace.head
    )
    assert git(engine.repo, "cat-file", "-t", disposition_ref).stdout.strip() == "blob"
    disposition = json.loads(
        git(engine.repo, "cat-file", "-p", disposition_ref).stdout
    )
    assert disposition["disposition"] == (
        "dropping" if fault_stage == "after-evidence" else "dropped"
    )
    assert engine._state["phase"] == "agent_running"

    monkeypatch.setattr(engine, "_retire_drop_workspace", original_retire)
    monkeypatch.setattr(engine, "_pin_drop_evidence", original_pin)
    restarted = ServiceEngine(config)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["phase"] == "idle"
    assert "execution_id" not in restarted._state
    assert not workspace.path.exists()
    assert not list((control / "executions").glob("**/*.json"))
    output = capsys.readouterr().out
    assert "recovering dropped execution T-1 (drop-replay)" in output
    assert "dropped execution cleanup complete: T-1 (drop-replay)" in output

def test_restart_recovers_dropped_submodule_worktree_without_lifecycle(
    tmp_path, monkeypatch
):
    engine, config, control, workspace, _command = _prepare_drop_owner_command(
        tmp_path, monkeypatch
    )
    sub_remote = tmp_path / "drop-submodule.git"
    sub_seed = tmp_path / "drop-submodule-seed"
    git(tmp_path, "init", "--bare", sub_remote)
    git(tmp_path, "init", "-b", "main", sub_seed)
    git(sub_seed, "config", "user.email", "test@example.com")
    git(sub_seed, "config", "user.name", "Test User")
    (sub_seed / "dependency.txt").write_text("submodule checkpoint\n")
    git(sub_seed, "add", "dependency.txt")
    git(sub_seed, "commit", "-m", "submodule checkpoint")
    git(sub_seed, "push", sub_remote, "HEAD:main")
    git(sub_remote, "symbolic-ref", "HEAD", "refs/heads/main")
    checkpoint_parent = git(workspace.path, "rev-parse", "HEAD").stdout.strip()

    git(
        workspace.path,
        "-c",
        "protocol.file.allow=always",
        "submodule",
        "add",
        "-b",
        "main",
        sub_remote,
        "dependency",
    )
    git(
        workspace.path,
        "commit",
        "-am",
        "Devlegate checkpoint T-1 drop-final-boundary",
    )
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "--force",
        "origin",
        f"HEAD:refs/heads/{workspace.branch}",
    )
    manager = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    )
    workspace = manager.prepare(workspace.base_head)
    report = build_execution_report(
        execution_id="drop-final-boundary",
        ticket_id="T-1",
        code_base_head=workspace.base_head,
        control_head=engine._state["execution_control_head"],
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        workspace_head=checkpoint,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        ),
    )
    engine._save_state(
        "agent_running",
        execution_stage="lifecycle",
        worker_identity=None,
        execution_start_head=checkpoint_parent,
        execution_remote_head=checkpoint,
        pending_execution_report=report.as_dict(),
    )
    engine._persist_drop_intent(report)
    engine._pin_drop_evidence(report)
    git(
        engine.repo,
        "push",
        f"--force-with-lease=refs/heads/{workspace.branch}:{checkpoint}",
        "origin",
        f":refs/heads/{workspace.branch}",
    )
    assert workspace.path.exists()
    assert any(
        item.get("branch") == workspace.branch
        for item in manager._registrations().values()
    )
    assert (
        git(engine.repo, "show-ref", "--verify", f"refs/heads/{workspace.branch}")
        .returncode
        == 0
    )

    restarted = ServiceEngine(config)
    assert (
        git(workspace.path, "rev-parse", f"{checkpoint}^").stdout.strip()
        == checkpoint_parent
    )
    assert (
        git(workspace.path, "log", "-1", "--format=%s").stdout.strip()
        == "Devlegate checkpoint T-1 drop-final-boundary"
    )
    assert git(workspace.path, "status", "--porcelain").stdout == ""
    applied = []
    monkeypatch.setattr(
        restarted,
        "_apply_execution_lifecycle",
        lambda recovered: applied.append(recovered.execution_id),
    )
    assert run_test_iteration(restarted) == 0

    assert applied == []
    assert restarted._state["phase"] == "idle"
    assert "execution_id" not in restarted._state
    assert not workspace.path.exists()
    assert not any(
        item.get("branch") == workspace.branch
        for item in manager._registrations().values()
    )
    assert git(engine.repo, "branch", "--list", workspace.branch).stdout == ""
    assert engine._read_drop_disposition("T-1", report.execution_id)[
        "disposition"
    ] == "dropped"
    assert (
        git(
            engine.repo,
            "rev-parse",
            "--verify",
            f"{engine._drop_evidence_ref('T-1', report.execution_id)}^{{commit}}",
        ).stdout.strip()
        == checkpoint
    )
    assert not list((control / "executions").glob("**/*.json"))

def test_polling_exits_cleanly_after_shutdown_interrupted_git(tmp_path, monkeypatch):
    engine, _, _ = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()

    def run_once():
        stop_intent.request("operator_abort")
        raise ShutdownInterrupted

    engine.run_iteration = run_once

    assert engine.serve(stop_intent) == 0
    assert engine.service_snapshot().lifecycle == "ready"

@pytest.mark.parametrize(
    ("signum", "expected_kind", "expected_status"),
    [
        (signal.SIGINT, "operator_abort", 130),
        (signal.SIGTERM, None, 0),
    ],
)
def test_daemon_host_signal_handler_splits_abort_from_graceful_lifecycle(
    monkeypatch, signum, expected_kind, expected_status
):
    installed = {}
    lifecycle_calls = []

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_event, *, lock_handle=None, once=False):
            installed[signum](signum, None)
            if expected_kind is None:
                assert not stop_event.is_set()
                assert lifecycle_calls == ["stop"]
            else:
                assert stop_event.is_set()
                assert stop_event.kind == expected_kind
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    intent = daemon.ShutdownIntent()
    host = daemon.ServiceHost(FakeServiceEngine())
    with host._signal_ownership(
        intent,
        lambda lifecycle, _request_id, _force: lifecycle_calls.append(lifecycle) or {},
    ):
        result = host._serve_engine(intent)
    assert result == expected_status
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}

@pytest.mark.parametrize(
    ("source", "expected_status"),
    [("operator_abort", 130), ("force", 0)],
)
def test_forced_operator_abort_is_successful_service_shutdown(
    source, expected_status
):
    class FakeServiceEngine:
        def serve(self, _stop_intent, *, lock_handle=None, once=False):
            return 0

    intent = daemon.ShutdownIntent()
    intent.request("operator_abort", source=source)
    host = daemon.ServiceHost(FakeServiceEngine())

    assert host._serve_engine(intent) == expected_status

def test_restart_authority_handoff_requires_internal_hosting(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setenv("DEVLEGATE_RESTART_AUTHORITY_FD", "99")
    with pytest.raises(DevlegateError, match="requires internal hosting mode"):
        daemon.ServiceHost(engine, host_mode=daemon.HostingMode.EXTERNAL)

@pytest.mark.parametrize(
    "signals", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)]
)
def test_daemon_host_operator_abort_precedes_graceful_lifecycle(monkeypatch, signals):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_intent, *, lock_handle=None, once=False):
            for signum in signals:
                installed[signum](signum, None)
            assert stop_intent.kind == "operator_abort"
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    intent = daemon.ShutdownIntent()
    host = daemon.ServiceHost(FakeServiceEngine())
    with host._signal_ownership(
        intent, lambda _intent, _request_id, _force: {}
    ):
        result = host._serve_engine(intent)
    assert result == 130

def test_stop_during_observation_prevents_fresh_mutation(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_sync = engine._sync_control
    calls = []

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    assert engine.serve(stop_event) == 0
    assert calls == []
    assert engine._state["phase"] == "idle"

def test_stop_before_merge_commit_does_not_fast_forward(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    before = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
    stop_event = threading.Event()
    original_sync = engine._sync_control

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    assert engine.serve(stop_event) == 0

    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == before
    assert engine._state["phase"] == "idle"
    assert engine._state.get("remote_head") != target

def test_committed_merge_pending_drains_before_stop(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = control_publisher(tmp_path)
    git(publisher, "switch", "main")
    (publisher / "remote-change.txt").write_text("remote\n")
    git(publisher, "add", "remote-change.txt")
    git(publisher, "commit", "-m", "remote change")
    target = git(publisher, "rev-parse", "HEAD").stdout.strip()
    git(publisher, "push", "origin", "HEAD:main")
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "merge_pending":
            stop_event.set()

    monkeypatch.setattr(engine, "_save_state", save_state)
    assert engine.serve(stop_event) == 0

    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    assert engine._state["phase"] == "idle"

@pytest.mark.parametrize(
    ("kind", "returncode"),
    [
        ("operator_abort", -signal.SIGINT),
    ],
)
def test_merge_pending_retries_matching_shutdown_fetch(
    tmp_path, monkeypatch, capsys, kind, returncode
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
    fetch_calls = 0

    def interrupt_first_fetch(repo, *args, check=True):
        nonlocal fetch_calls
        if args and args[0] == "fetch":
            fetch_calls += 1
            if fetch_calls == 1:
                return subprocess.CompletedProcess(["git"], returncode, "", "")
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", interrupt_first_fetch)

    expected_result = 130 if kind == "operator_abort" else 0
    stop_intent = daemon.ShutdownIntent()
    stop_intent.request(kind)
    host = daemon.ServiceHost(engine)
    with host._signal_ownership(stop_intent, lambda _intent, _request_id: {}):
        result = host._serve_engine(stop_intent)
    assert result == expected_result
    assert fetch_calls == 3
    assert engine._state["phase"] == "idle"
    assert git(engine.repo, "rev-parse", "HEAD").stdout.strip() == target
    output = capsys.readouterr().out
    assert "workflow blocked" not in output
    assert "execution failed" not in output
    assert "unknown git error" not in output

def test_existing_agent_pending_is_preserved_on_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    workspace, _control = persist_agent_running(engine, state)
    engine._save_state("agent_pending")
    before = dict(engine._state)
    calls = []
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )
    stop_event = threading.Event()
    stop_event.set()

    assert engine.serve(stop_event) == 0
    assert engine._state == before
    assert calls == []
    assert workspace.path.is_dir()

def test_stop_after_agent_pending_commit_preserves_binding(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "agent_pending":
            stop_event.set()

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker")
    )
    assert engine.serve(stop_event) == 0
    assert engine._state["phase"] == "agent_pending"
    assert engine._state["execution_id"]

def test_stop_after_workspace_preparation_preserves_pending_execution(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_prepare = engine._prepare_execution_workspace
    prepared = []

    def prepare(plan):
        workspace = original_prepare(plan)
        prepared.append(workspace)
        stop_event.set()
        return workspace

    monkeypatch.setattr(engine, "_prepare_execution_workspace", prepare)
    monkeypatch.setattr(
        engine._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker")
    )
    assert engine.serve(stop_event) == 0
    assert prepared and prepared[0].path.is_dir()
    assert engine._state["phase"] == "agent_pending"

def test_stop_after_agent_running_commit_still_drains_attempt(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state
    worker_calls = []

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if (
            phase == "agent_running"
            and fields.get("execution_stage") == "worker-launch"
        ):
            stop_event.set()

    def worker(_workspace, _prompt, **_kwargs):
        worker_calls.append(True)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_event) == 0
    assert worker_calls == [True]
    assert engine._state["phase"] == "idle"

def test_precheckpoint_integrity_failure_with_stop_keeps_retry_semantics(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt, **_kwargs):
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule changed")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        run_test_iteration(engine, stop_event)
    assert engine._state["phase"] == "idle"
    assert "interrupted" not in engine._state["failed_executions"]["T-1"]

def test_later_stage_failure_with_stop_remains_ambiguous(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt, **_kwargs):
        stop_event.set()
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        engine,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        run_test_iteration(engine, stop_event)
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == "publishing"
    assert engine._state.get("failed_executions", {}) == {}

def test_checkpoint_failure_during_lifecycle_drain_has_no_completion_boundary(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)

    def worker(_workspace, _prompt, **_kwargs):
        engine.request_lifecycle("stop", "stop-request")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "checkpoint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExecutionWorkspaceError("checkpoint unavailable")
        ),
    )
    with pytest.raises(DevlegateError, match="execution checkpoint failed"):
        run_test_iteration(engine)
    assert engine.lifecycle_intent() == "stop"
    assert engine._state["execution_stage"] == "checkpointing"

@pytest.mark.parametrize("drain_stage", ["post-checkpoint", "publishing", "lifecycle"])
def test_stop_at_committed_execution_stage_drains_to_idle(
    tmp_path, monkeypatch, drain_stage
):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if phase == "agent_running" and fields.get("execution_stage") == drain_stage:
            stop_event.set()

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_event) == 0

    control = next((state / "worktrees").glob("*/control"))
    assert engine._state["phase"] == "idle"
    assert (control / "kanban/review/T-1.md").is_file()
    assert engine.service_snapshot().worker_running is False

def test_stop_before_accepted_integration_leaves_ticket_accepted(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    todo = control / "kanban/todo/T-1.md"
    todo.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    original_sync = engine._sync_control

    def sync_control():
        result = original_sync()
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_sync_control", sync_control)
    assert engine.serve(stop_event) == 0
    assert (control / "kanban/accepted/T-1.md").is_file()
    assert not (engine.repo / "implementation.txt").exists()

def test_started_accepted_integration_drains_before_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    review = control / "kanban/review/T-1.md"
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    original_integrate = engine._integrate_accepted

    def integrate(*args):
        result = original_integrate(*args)
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_integrate_accepted", integrate)
    assert engine.serve(stop_event) == 0
    assert (control / "kanban/done/T-1.md").is_file()
    assert engine._state["phase"] == "idle"

def test_stop_during_worker_prevents_next_ticket_admission(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    git(control, "add", "kanban/todo/T-2.md")
    git(control, "commit", "-m", "add second ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    stop_event = threading.Event()
    calls = []

    def worker(workspace, _prompt, **_kwargs):
        calls.append(workspace.ticket_id)
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.serve(stop_event) == 0
    assert calls == ["T-1"]
    assert (control / "kanban/todo/T-2.md").is_file()

def test_daemon_stop_during_worker_finishes_attempt_without_next_ticket(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    worker_entered = threading.Event()
    worker_release = threading.Event()
    stop_event = threading.Event()
    worker_calls = []
    result = []

    def worker(_workspace, _prompt, **_kwargs):
        worker_calls.append(True)
        worker_entered.set()
        assert worker_release.wait(10)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine._workers, "run", worker)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert worker_entered.wait(10)
    stop_event.set()

    assert engine.service_snapshot().worker_running is True
    assert thread.is_alive()
    assert worker_calls == [True]

    worker_release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert result == [0]
    assert worker_calls == [True]
    assert engine.service_snapshot().worker_running is False
    assert engine._state["phase"] == "idle"

@pytest.mark.parametrize("once", [False, True])
def test_once_operator_abort_stops_after_one_iteration(tmp_path, monkeypatch, once):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    calls = []
    workspace_path = []

    def worker(workspace, _prompt, **_kwargs):
        calls.append(True)
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        signal.raise_signal(signal.SIGINT)
        return WorkerRunResult(-2, None, None, None, "operator_abort")

    monkeypatch.setattr(engine._workers, "run", worker)
    assert daemon.run_service(engine, once=once) == 130
    assert calls == [True]
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    assert engine._state["phase"] == "idle"
    assert engine._state["failed_executions"]["T-1"]["interruption_kind"] == (
        "operator_abort"
    )
