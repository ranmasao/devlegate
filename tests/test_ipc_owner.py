# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Real owner-thread handoff, mutation admission, and receipt semantics."""

from _ipc_support import *  # noqa: F403,F405

def test_stop_completed_receipt_cannot_overtake_accepted_receipt(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    original_write = daemon.write_lifecycle_receipt
    accepted_entered = threading.Event()
    completion_attempted = threading.Event()
    release_accepted = threading.Event()
    ordering = []
    client_result = []
    helper_threads = []

    def gated_write(locator, payload):
        if payload.get("action") == "stop" and payload.get("state") == "accepted":
            accepted_entered.set()
            assert release_accepted.wait(timeout=5)
        original_write(locator, payload)
        if payload.get("action") == "stop" and payload.get("state") == "completed":
            completion_attempted.set()

    monkeypatch.setattr(daemon, "write_lifecycle_receipt", gated_write)
    receipt_path = engine._locator.state_dir / "lifecycle" / (
        f"{engine._locator.state_key}.json"
    )
    original_serve = daemon.ServiceHost._serve_engine

    def host(service_host, stop_intent, **kwargs):
        client = threading.Thread(
            target=lambda: client_result.append(
                request(engine.ipc_socket_path, "stop", "stop")
            )
        )

        def release_after_owner_probe():
            try:
                assert accepted_entered.wait(timeout=5)
                ordering.append(completion_attempted.wait(timeout=0.5))
            finally:
                release_accepted.set()

        watcher = threading.Thread(target=release_after_owner_probe)
        helper_threads.extend((client, watcher))
        client.start()
        watcher.start()
        accepted_entered.wait(timeout=5)
        return 0

    monkeypatch.setattr(daemon.ServiceHost, "_serve_engine", host)
    try:
        assert daemon.run_service(engine) == 0
    finally:
        release_accepted.set()
        for helper in helper_threads:
            helper.join(timeout=5)
        monkeypatch.setattr(daemon.ServiceHost, "_serve_engine", original_serve)

    assert all(not helper.is_alive() for helper in helper_threads)
    assert ordering == [False]
    assert json.loads(receipt_path.read_text())["state"] == "completed"

@pytest.mark.parametrize("retained_report", [True, False])
def test_reconcile_resume_runs_full_live_owner_path(
    tmp_path, monkeypatch, short_state_dir, retained_report
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(_state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def initial_worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", initial_worker)
    assert run_test_iteration(engine) == 1
    reconciliation = dict(engine._state["reconciliation"])
    checkpoint = reconciliation["worker_checkpoint"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    (working / "dirty-product.txt").unlink()
    if not retained_report:
        reconciliation.pop("execution_report")
        engine._save_state("idle", reconciliation=reconciliation)

    resumed = threading.Event()
    original_resume = engine._reconcile_resume_owned

    def resume(ticket_id, **kwargs):
        result = original_resume(ticket_id, **kwargs)
        resumed.set()
        return result

    monkeypatch.setattr(engine, "_reconcile_resume_owned", resume)
    if retained_report:
        monkeypatch.setattr(
            engine._workers,
            "run",
            lambda *_args, **_kwargs: pytest.fail("retained resume launched a worker"),
        )
    else:

        def resumed_worker(workspace, prompt, **_kwargs):
            assert "Continue the existing implementation" in prompt
            assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == checkpoint
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "validated", (), ()), None
            )

        monkeypatch.setattr(engine._workers, "run", resumed_worker)

    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            server.path,
            "resume-live",
            "reconcile-resume",
            {"ticket_id": "T-1"},
        )
        assert response.ok
        assert response.result == {"accepted": True, "ticket_id": "T-1"}
        assert resumed.wait(5)
        assert owner.is_alive()
        assert engine._state["reconciliation"]["status"] == "resolved"
        assert engine._state["reconciliation"]["resolution"] == "resume"
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint
        assert (
            git(
                execution,
                "ls-remote",
                "origin",
                f"refs/heads/{reconciliation['execution_branch']}",
            ).stdout.split()[0]
            == checkpoint
        )
        assert (execution / "implementation.txt").read_text() == "worker\n"
        if retained_report:
            assert (
                next((short_state_dir / "worktrees").glob("*/control"))
                / "kanban/review/T-1.md"
            ).is_file()
        else:
            assert engine._state["resume_required"]["status"] == "required"
    finally:
        stop_event.set()
        engine.wake()
        owner.join(timeout=5)
        server.stop()
        authority.close()
    assert not owner.is_alive()

def test_unknown_owner_command_fails_closed(running_server):
    engine, _state, _server = running_server
    command = OperatorCommand("unknown", "unknown", "fingerprint", "T-1")

    with pytest.raises(DevlegateError, match="unsupported operator command: unknown"):
        engine._dispatch_operator_command(command, None)

def test_resolving_resume_recovers_on_fresh_owner_and_fences_plan(
    tmp_path, monkeypatch, short_state_dir
):
    working, config, _state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(_state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    reconciliation = dict(engine._state["reconciliation"])
    checkpoint = reconciliation["worker_checkpoint"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    (working / "dirty-product.txt").unlink()
    command = OperatorCommand(
        "resume-crash",
        "reconcile-resume",
        _reconcile_resume_request_fingerprint("T-1"),
        "T-1",
    )
    engine._record_operator_admission(command)

    restarted = ServiceEngine(config)
    assert restarted._state["reconciliation"]["status"] == "resolving"
    assert restarted.plan_view().action == "blocked"
    assert "recovery in progress" in restarted.plan_view().reason
    monkeypatch.setattr(
        restarted._workers,
        "run",
        lambda *_args, **_kwargs: pytest.fail(
            "ordinary worker launched before recovery"
        ),
    )
    recovered = threading.Event()
    original_resume = restarted._reconcile_resume_owned

    def resume(ticket_id, **kwargs):
        result = original_resume(ticket_id, **kwargs)
        recovered.set()
        return result

    monkeypatch.setattr(restarted, "_reconcile_resume_owned", resume)
    authority = restarted._lock()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=restarted.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert recovered.wait(5)
        assert owner.is_alive()
        assert restarted._state["reconciliation"]["status"] == "resolved"
        assert restarted._state["reconciliation"]["resolution"] == "resume"
        assert restarted._state["mutable_receipts"]["resume-crash"]["accepted"]
        assert (
            git(
                execution,
                "ls-remote",
                "origin",
                f"refs/heads/{reconciliation['execution_branch']}",
            ).stdout.split()[0]
            == checkpoint
        )
    finally:
        stop_event.set()
        restarted.wake()
        owner.join(timeout=5)
        authority.close()
    assert not owner.is_alive()

def test_published_reads_do_not_observe_repository_during_owner_iteration(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    def run_iteration(_intent=None):
        started.set()
        assert release.wait(3)
        return 0

    def forbidden_repository_observation():
        raise DevlegateError("IPC read performed repository observation")

    monkeypatch.setattr(engine, "run_iteration", run_iteration)
    for method in ("status_view", "plan_view", "retry_candidates_view"):
        monkeypatch.setattr(
            engine,
            method,
            forbidden_repository_observation,
        )
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert started.wait(2)
        responses = [
            request(engine.ipc_socket_path, f"read-{method}", method)
            for method in ("status", "plan", "retry-candidates")
        ]
        assert all(response.ok for response in responses)
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()

def test_mutation_after_owner_commits_iteration_is_definitely_busy(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    def run_iteration(_intent=None):
        started.set()
        assert release.wait(3)
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_iteration)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        assert started.wait(2)
        response = request(
            engine.ipc_socket_path,
            "busy-retry",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not response.ok
        assert response.error["message"] == (
            "service busy; mutable request was not admitted"
        )
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()

def test_shutdown_unblocks_partial_client(tmp_path, monkeypatch, short_state_dir):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.connect(str(server.path))
    connection.sendall(b"\x00")
    started = time.monotonic()
    server.stop()
    elapsed = time.monotonic() - started
    connection.close()
    assert elapsed < 1
    assert not server.path.exists()

def test_retry_submission_runs_on_service_owner_thread(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    submitted_thread = []
    executed_thread = []
    executed = threading.Event()
    stop_event = threading.Event()

    original_submit = engine.submit_retry

    def submit(ticket_id, *, request_id):
        submitted_thread.append(threading.get_ident())
        return original_submit(ticket_id, request_id=request_id)

    def execute(ticket_id, _stop_event=None):
        assert ticket_id == "T-1"
        executed_thread.append(threading.get_ident())
        executed.set()
        return 0

    monkeypatch.setattr(engine, "submit_retry", submit)
    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            engine.ipc_socket_path,
            "retry",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert response.ok
        assert response.result == {"accepted": True, "ticket_id": "T-1"}
        assert executed.wait(2)
        assert submitted_thread[0] != executed_thread[0]
        assert executed_thread[0] == owner.ident
    finally:
        stop_event.set()
        engine.wake()
        owner.join(timeout=2)
        server.stop()
        authority.close()
    assert not owner.is_alive()

def test_read_only_ipc_remains_available_while_owner_retry_is_active(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    started = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()

    engine.ensure_hosted_views()

    def execute(_ticket_id, _stop_event=None):
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    original_wake = engine.wake
    queued = threading.Event()

    def wake_after_queue():
        original_wake()
        queued.set()

    monkeypatch.setattr(engine, "wake", wake_after_queue)
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    retry_result = {}
    retry_client = threading.Thread(
        target=lambda: retry_result.setdefault(
            "response",
            request(
                engine.ipc_socket_path,
                "retry",
                "retry",
                {"ticket_id": "T-1"},
            ),
        )
    )
    retry_client.start()
    assert queued.wait(2)
    owner.start()
    try:
        retry_client.join(timeout=3)
        retry_response = retry_result["response"]
        assert retry_response.ok
        assert started.wait(2)
        status_response = request(engine.ipc_socket_path, "status", "status")
        assert status_response.ok
        assert status_response.result["execution"]["phase"] == "idle"
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=2)
        server.stop()
        authority.close()
    assert not owner.is_alive()

def test_shutdown_rejects_operator_command_waiting_for_owner_admission(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    admission_started = threading.Event()
    release_admission = threading.Event()
    stop_event = threading.Event()
    original_validate = engine._validate_operator_admission

    def validate(command):
        admission_started.set()
        assert release_admission.wait(3)
        original_validate(command)

    monkeypatch.setattr(engine, "_validate_operator_admission", validate)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    result = {}

    def submit():
        result["response"] = request(
            engine.ipc_socket_path,
            "shutdown-race",
            "retry",
            {"ticket_id": "T-1"},
        )

    client = threading.Thread(target=submit)
    client.start()
    try:
        assert admission_started.wait(2)
        stop_event.set()
        engine.wake()
        release_admission.set()
        owner.join(timeout=2)
        assert not owner.is_alive()
        release_admission.set()
        client.join(timeout=3)
        assert not client.is_alive()
        response = result["response"]
        assert response.ok
    finally:
        release_admission.set()
        stop_event.set()
        engine.wake()
        client.join(timeout=2)
        owner.join(timeout=2)
        server.stop()
        authority.close()

def test_retry_request_receipt_coalesces_duplicates_and_survives_restart(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    started = threading.Event()
    release = threading.Event()
    executions = []

    def execute(ticket_id, _stop_event=None):
        executions.append(ticket_id)
        started.set()
        release.wait(2)
        return 0

    monkeypatch.setattr(engine, "run_iteration", lambda _intent=None: 0)
    monkeypatch.setattr(engine, "_retry_owned", execute)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        first = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert first.ok
        assert started.wait(2)
        duplicate = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert duplicate.ok
        assert duplicate.result == first.result
        different_id = request(
            engine.ipc_socket_path,
            "request-y",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not different_id.ok
        assert "service busy" in different_id.error["message"]
        collision = request(
            engine.ipc_socket_path,
            "request-x",
            "retry",
            {"ticket_id": "T-2"},
        )
        assert not collision.ok
        assert "request id collision" in collision.error["message"]
        assert executions == ["T-1"]
    finally:
        release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()
    assert not owner.is_alive()

    restarted = ServiceEngine(engine.env_file)
    replay = restarted.submit_retry("T-1", request_id="request-x")
    assert replay == {"accepted": True, "ticket_id": "T-1"}
    assert not restarted._operator_command_pending()

@pytest.mark.parametrize("move_after_admission", [False, True])
def test_reconcile_update_base_binds_omitted_target_before_effect(
    tmp_path, monkeypatch, short_state_dir, move_after_admission
):
    working, config, state = control_fixture(tmp_path)
    config.write_text(config.read_text().replace(str(state), str(short_state_dir)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker work\n")
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    reconciliation = engine._state["reconciliation"]
    target = reconciliation["observed_product"]
    original_base = reconciliation["original_base"]
    execution = next((short_state_dir / "worktrees").glob("*/work/T-1"))
    git(working, "pull", "--ff-only", "origin", "main")

    submitted_thread = []
    executed_thread = []
    reconciliation_started = threading.Event()
    reconciliation_release = threading.Event()
    executed = threading.Event()
    original_owned = engine._reconcile_update_base_owned

    def owned(ticket_id, onto, **kwargs):
        executed_thread.append(threading.get_ident())
        reconciliation_started.set()
        assert reconciliation_release.wait(3)
        try:
            return original_owned(ticket_id, onto, **kwargs)
        finally:
            executed.set()

    monkeypatch.setattr(engine, "_reconcile_update_base_owned", owned)
    original_submit = engine.submit_reconcile_update_base

    def submit(ticket_id, onto, *, request_id):
        submitted_thread.append(threading.get_ident())
        return original_submit(ticket_id, onto, request_id=request_id)

    monkeypatch.setattr(engine, "submit_reconcile_update_base", submit)
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        response = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1"},
        )
        assert response.ok
        assert response.result == {
            "accepted": True,
            "ticket_id": "T-1",
            "onto": target,
        }
        assert engine._state["mutable_receipts"]["reconcile-request"]["onto"] == target
        assert reconciliation_started.wait(2)
        busy = request(
            engine.ipc_socket_path,
            "retry-while-reconcile",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not busy.ok
        assert "service busy" in busy.error["message"]
        if move_after_admission:
            (working / "product-after-admission.txt").write_text("product C\n")
            git(working, "add", "product-after-admission.txt")
            git(working, "commit", "-m", "advance product after admission")
            git(working, "push", "origin", "HEAD:main")
            git(working, "pull", "--ff-only", "origin", "main")
        reconciliation_release.set()
        assert executed.wait(3)
        assert submitted_thread[0] != executed_thread[0]
        assert executed_thread[0] == owner.ident
        duplicate = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1"},
        )
        assert duplicate.ok
        assert duplicate.result == {
            "accepted": True,
            "ticket_id": "T-1",
            "onto": target,
        }
        collision = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "reconcile-update-base",
            {"ticket_id": "T-1", "onto": "different-target"},
        )
        assert not collision.ok
        assert "request id collision" in collision.error["message"]
        cross_method = request(
            engine.ipc_socket_path,
            "reconcile-request",
            "retry",
            {"ticket_id": "T-1"},
        )
        assert not cross_method.ok
        assert "request id collision" in cross_method.error["message"]
    finally:
        reconciliation_release.set()
        stop_event.set()
        engine.wake()
        owner.join(timeout=3)
        server.stop()
        authority.close()

    assert engine._state["reconciliation"]["status"] == (
        "pending" if move_after_admission else "resolved"
    )
    if not move_after_admission:
        assert engine._state["resume_required"]["status"] == "required"
        assert (execution / "implementation.txt").read_text() == "worker work\n"
    else:
        assert git(execution, "rev-parse", "HEAD^").stdout.strip() == original_base
    restarted = ServiceEngine(config)
    assert restarted.submit_reconcile_update_base(
        "T-1", None, request_id="reconcile-request"
    ) == {"accepted": True, "ticket_id": "T-1", "onto": target}

@pytest.mark.parametrize("interactive", [False, True])
def test_interrupted_retry_candidate_is_admitted_and_recovered_end_to_end(
    tmp_path, monkeypatch, short_state_dir, capsys, interactive
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    engine._save_state(
        "agent_running",
        execution_stage="pre-checkpoint",
        worker_identity=None,
    )
    candidate_response = []
    recovered = threading.Event()
    original_retry_owned = engine._retry_owned

    def retry_owned(ticket_id, stop_event=None):
        recovered.set()
        candidate_response.append(ticket_id)
        result = original_retry_owned(ticket_id, stop_event)
        return result

    monkeypatch.setattr(engine, "_retry_owned", retry_owned)
    monkeypatch.setattr(engine, "run_iteration", lambda _intent=None: 0)
    engine.ensure_hosted_views()
    authority = engine._lock()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    stop_event = threading.Event()
    owner = threading.Thread(
        target=engine.serve,
        args=(stop_event,),
        kwargs={"lock_handle": authority},
    )
    owner.start()
    try:
        candidates = request(
            engine.ipc_socket_path,
            "candidates",
            "retry-candidates",
        )
        assert candidates.ok
        assert candidates.result["candidates"][0]["id"] == "T-1"
        assert candidates.result["candidates"][0]["kind"] == "interrupted"
        if interactive:
            monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
            monkeypatch.setattr("builtins.input", lambda _prompt: "1")
            argv = ["devlegate", "--env", str(engine.env_file), "retry"]
        else:
            argv = [
                "devlegate",
                "--env",
                str(engine.env_file),
                "retry",
                "T-1",
            ]
        monkeypatch.setattr(sys, "argv", argv)
        assert cli.main() == 0
        assert "retry accepted: T-1" in capsys.readouterr().out
        assert recovered.wait(5)
        assert candidate_response == ["T-1"]
    finally:
        stop_event.set()
        engine.wake()
        if owner is not None:
            owner.join(timeout=3)
        server.stop()
        authority.close()
    assert owner is None or not owner.is_alive()

@pytest.mark.parametrize(
    "payload",
    [{}, {"ticket_id": ""}, {"ticket_id": None}, {"ticket_id": "T-1", "extra": 1}],
)
def test_retry_mutation_payload_is_strict(payload):
    class FakeEngine:
        def submit_retry(self, _ticket_id):
            pytest.fail("invalid retry payload was queued")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("retry", payload))

def test_force_retry_mutation_forwards_force_only_when_requested():
    calls = []

    class FakeEngine:
        def submit_retry(self, ticket_id, *, request_id, force=False):
            calls.append((ticket_id, force, request_id))
            return {"accepted": True, "ticket_id": ticket_id}

    result = dispatch_mutation(
        FakeEngine(), _request("retry", {"ticket_id": "T-1", "force": True})
    )
    assert result == {"accepted": True, "ticket_id": "T-1"}
    assert calls == [("T-1", True, "id")]

@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"ticket_id": "T-1"},
        {"ticket_id": "", "execution_id": "execution-1"},
        {"ticket_id": "T-1", "execution_id": ""},
        {"ticket_id": "T-1", "execution_id": "execution-1", "extra": 1},
    ],
)
def test_drop_mutation_payload_is_strict(payload):
    class FakeEngine:
        def submit_drop(self, _ticket_id, _execution_id, *, request_id):
            pytest.fail(f"invalid drop payload was queued: {request_id}")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("drop", payload))

def test_drop_mutation_binds_ticket_and_execution_id():
    class FakeEngine:
        def submit_drop(self, ticket_id, execution_id, *, request_id):
            return {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
                "request_id": request_id,
            }

    result = dispatch_mutation(
        FakeEngine(),
        _request("drop", {"ticket_id": "T-1", "execution_id": "execution-1"}),
    )
    assert result == {
        "accepted": True,
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "request_id": "id",
    }

def test_matching_live_worker_rejects_retry_without_queueing(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(engine, engine.state_dir)
    identity = engine._state["worker_identity"]
    engine._save_state(
        "agent_running",
        execution_stage="worker-running",
        worker_identity=identity,
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor.observe_worker_identity", lambda _: "matching-live"
    )

    engine.begin_hosted_owner()
    try:
        with pytest.raises(DevlegateError, match="worker is already running"):
            engine._validate_operator_admission(
                OperatorCommand("live-worker", "retry", "", "T-1")
            )
    finally:
        engine.end_hosted_owner()
    assert not engine._operator_command_pending()

def test_recover_mutation_forwards_exact_identity_to_owner():
    calls = []

    class FakeEngine:
        def submit_recover(self, ticket_id, execution_id, observed_head, *, request_id):
            calls.append((ticket_id, execution_id, observed_head, request_id))
            return {
                "accepted": True,
                "ticket_id": ticket_id,
                "execution_id": execution_id,
                "observed_head": observed_head,
            }

    result = dispatch_mutation(
        FakeEngine(),
        _request(
            "recover",
            {
                "ticket_id": "T-1",
                "execution_id": "execution-1",
                "observed_head": "a" * 40,
            },
        ),
    )

    assert result["accepted"] is True
    assert calls == [("T-1", "execution-1", "a" * 40, "id")]

@pytest.mark.parametrize(
    "payload",
    [
        {"ticket_id": "T-1", "execution_id": "execution-1", "observed_head": ""},
    ],
)
def test_recover_mutation_rejects_non_exact_identity(payload):
    class FakeEngine:
        def submit_recover(self, *_args, **_kwargs):
            pytest.fail("invalid recovery identity was queued")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("recover", payload))

@pytest.mark.parametrize(
    "payload",
    [
        {"onto": "B"},
        {"ticket_id": "T-1", "onto": "B", "extra": "nope"},
        {"ticket_id": "", "onto": "B"},
        {"ticket_id": "T-1", "onto": ""},
    ],
)
def test_reconcile_mutation_payload_is_strict(payload):
    class FakeEngine:
        def submit_reconcile_update_base(self, *_args, **_kwargs):
            pytest.fail("invalid reconciliation payload was queued")

    with pytest.raises(IPCProtocolError):
        dispatch_mutation(FakeEngine(), _request("reconcile-update-base", payload))

def test_reconcile_resume_mutation_is_strict_and_owner_routed():
    calls = []

    class FakeEngine:
        def submit_reconcile_resume(self, ticket_id, *, request_id):
            calls.append((ticket_id, request_id))
            return {"accepted": True, "ticket_id": ticket_id}

    result = dispatch_mutation(
        FakeEngine(), _request("reconcile-resume", {"ticket_id": "T-1"})
    )
    assert result == {"accepted": True, "ticket_id": "T-1"}
    assert calls == [("T-1", "id")]
    with pytest.raises(IPCProtocolError):
        dispatch_mutation(
            FakeEngine(),
            _request("reconcile-resume", {"ticket_id": "T-1", "onto": "B"}),
        )

def test_reconcile_resume_admission_persists_receipt_with_resolving_state(
    tmp_path, monkeypatch
):
    engine, _state = make_engine(tmp_path, monkeypatch)
    engine._state["reconciliation"] = {
        "status": "pending",
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "original_base": "a" * 40,
        "observed_product": "b" * 40,
        "worker_checkpoint": "c" * 40,
        "product_remote_head": "a" * 40,
        "control_head": "d" * 40,
        "execution_branch": "devlegate/work/T-1",
        "execution_path": "/tmp/execution",
        "evidence_ref": "refs/devlegate/reconciliation/T-1/execution-1",
    }
    command = SimpleNamespace(
        request_id="resume-request",
        method="reconcile-resume",
        fingerprint=_reconcile_resume_request_fingerprint("T-1"),
        ticket_id="T-1",
        onto=None,
    )
    engine._record_operator_admission(command)
    restarted = ServiceEngine(engine.env_file)
    assert restarted._state["mutable_receipts"]["resume-request"]["accepted"] is True
    assert restarted._state["reconciliation"]["status"] == "resolving"
    assert restarted._state["reconciliation"]["resolution"] == "resume"
    assert restarted.submit_reconcile_resume("T-1", request_id="resume-request") == {
        "accepted": True,
        "ticket_id": "T-1",
    }
    assert restarted._state["reconciliation"]["status"] == "resolving"
