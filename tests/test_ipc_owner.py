# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real owner-thread handoff, mutation admission, and receipt semantics."""

import os
import shlex
import signal
import subprocess
from pathlib import Path

from _ipc_support import (
    DevlegateError,
    IPCProtocolError,
    OperatorCommand,
    RetryCandidate,
    ServiceEngine,
    UnixIPCServer,
    WorkerClaim,
    WorkerRunResult,
    _request,
    cli,
    daemon,
    dispatch_mutation,
    git,
    json,
    make_engine,
    persist_agent_running,
    pytest,
    request,
    run_test_iteration,
    socket,
    sys,
    threading,
    time,
)
from _ipc_support import running_server as running_server

import devlegate.runtime as runtime
from devlegate.execution_containment import DeterministicContainmentProvider
from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.runtime_store import RuntimeStoreError


@pytest.mark.parametrize("pause_at", ["validation", "receipt"])
def test_timeout_waiting_for_owner_lock_returns_committed_admission(
    tmp_path, monkeypatch, short_state_dir, pause_at
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine._interactive_retry_candidates = lambda: (
        RetryCandidate("T-1", "Ticket", "failed", "failed"),
    )
    monkeypatch.setattr(runtime, "OPERATOR_ADMISSION_TIMEOUT", 0.05)
    queued = threading.Event()
    timed_out = threading.Event()
    entered = threading.Event()
    release = threading.Event()
    result = []

    def wake():
        command = engine._operator_command
        if command is None:
            return
        original_wait = command.admission_event.wait

        def wait(timeout):
            value = original_wait(timeout)
            if not value:
                timed_out.set()
            return value

        command.admission_event.wait = wait
        queued.set()

    monkeypatch.setattr(engine, "wake", wake)
    original_validate = engine._validate_operator_admission
    original_record = engine._record_operator_admission

    def validate(command):
        if pause_at == "validation":
            entered.set()
            assert release.wait(5)
        original_validate(command)

    def record(command):
        original_record(command)
        if pause_at == "receipt":
            entered.set()
            assert release.wait(5)

    monkeypatch.setattr(engine, "_validate_operator_admission", validate)
    monkeypatch.setattr(engine, "_record_operator_admission", record)
    client = threading.Thread(
        target=lambda: result.append(engine.submit_retry("T-1", request_id="race"))
    )
    client.start()
    assert queued.wait(2)
    command, scheduler = engine._claim_owner_work(True)
    assert command is not None and not scheduler
    owner = threading.Thread(target=engine._admit_operator_command, args=(command,))
    owner.start()
    drain_started = threading.Event()
    drain_finished = threading.Event()
    drain_results = []
    server = UnixIPCServer(
        engine, engine.ipc_socket_path,
        lifecycle=lambda intent, rid, force: engine.request_lifecycle(
            intent, rid, force=force,
        ),
    )
    server.start()

    def drain():
        started = time.monotonic()
        drain_started.set()
        response = request(
            engine.ipc_socket_path, "drain-race", "stop", {"force": True},
        )
        drain_results.append((response.result, time.monotonic() - started))
        drain_finished.set()

    drainer = threading.Thread(target=drain)
    try:
        assert entered.wait(2)
        assert timed_out.wait(2)
        drainer.start()
        assert drain_started.wait(2)
        assert not drain_finished.wait(0.1)
    finally:
        release.set()
        owner.join(5)
        client.join(5)
        if drainer.ident is not None:
            drainer.join(5)
        server.stop()
    assert not owner.is_alive() and not client.is_alive() and not drainer.is_alive()
    assert drain_finished.is_set()
    (force, elapsed), = drain_results
    assert force["request_id"] == "drain-race" and force["force"] is True
    print(f"operator {pause_at} lock: force admission {elapsed:.3f}s")
    assert result == [{"accepted": True, "ticket_id": "T-1"}]
    assert engine._mutable_receipt("race")["accepted"] is True
    restarted = ServiceEngine(engine.env_file)
    assert restarted.submit_retry("T-1", request_id="race") == result[0]
    with pytest.raises(DevlegateError, match="request id collision"):
        restarted.submit_retry("T-2", request_id="race")


def test_unclaimed_timeout_cancels_queue(tmp_path, monkeypatch, short_state_dir):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    monkeypatch.setattr(runtime, "OPERATOR_ADMISSION_TIMEOUT", 0.01)
    with pytest.raises(DevlegateError, match="request was not admitted"):
        engine.submit_retry("T-1", request_id="queued")
    assert engine._mutable_receipt("queued") is None
    assert engine._claim_owner_work(True) == (None, True)
    engine._release_scheduler_iteration()


def test_claimed_timeout_before_owner_admission_is_unknown(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    monkeypatch.setattr(runtime, "OPERATOR_ADMISSION_TIMEOUT", 0.05)
    queued = threading.Event()
    monkeypatch.setattr(engine, "wake", queued.set)
    errors = []

    def submit():
        try:
            engine.submit_retry("T-1", request_id="claimed")
        except DevlegateError as error:
            errors.append(str(error))

    client = threading.Thread(target=submit)
    client.start()
    assert queued.wait(2)
    command, _ = engine._claim_owner_work(True)
    client.join(2)
    assert not client.is_alive()
    assert errors == [
        "service admission outcome is unknown; retry with the same request id"
    ]
    assert not command.cancelled.is_set()
    assert engine._mutable_receipt("claimed") is None
    engine._release_operator_command()


def test_terminal_stop_survives_late_repeat_and_sigterm(
    tmp_path, monkeypatch, short_state_dir
):
    engine, _state = make_engine(tmp_path, monkeypatch, short_state_dir)
    original_write = daemon.write_lifecycle_receipt
    repeat_entered = threading.Event()
    completion_written = threading.Event()
    release_repeat = threading.Event()
    client_result = []
    helper_threads = []
    original_request = engine.request_lifecycle

    def gated_request(intent, request_id, *, force=False):
        result = original_request(intent, request_id, force=force)
        if request_id == "repeat":
            repeat_entered.set()
            assert release_repeat.wait(timeout=5)
        return result

    def gated_write(locator, payload):
        original_write(locator, payload)
        if payload.get("action") == "stop" and payload.get("state") == "completed":
            os.kill(os.getpid(), signal.SIGTERM)
            completion_written.set()

    monkeypatch.setattr(daemon, "write_lifecycle_receipt", gated_write)
    monkeypatch.setattr(engine, "request_lifecycle", gated_request)
    receipt_path = engine._locator.state_dir / "lifecycle" / (
        f"{engine._locator.state_key}.json"
    )

    def host(service_host, stop_intent, **kwargs):
        client_result.append(request(engine.ipc_socket_path, "first", "stop"))
        client = threading.Thread(
            target=lambda: client_result.append(
                request(engine.ipc_socket_path, "repeat", "stop")
            )
        )

        def release_after_completion():
            try:
                assert completion_written.wait(timeout=5)
            finally:
                release_repeat.set()

        watcher = threading.Thread(target=release_after_completion)
        helper_threads.extend((client, watcher))
        client.start()
        watcher.start()
        assert repeat_entered.wait(timeout=5)
        return 0

    monkeypatch.setattr(daemon.ServiceHost, "_serve_engine", host)
    try:
        assert daemon.run_service(engine) == 0
    finally:
        release_repeat.set()
        for helper in helper_threads:
            helper.join(timeout=5)

    assert all(not helper.is_alive() for helper in helper_threads)
    results = [response.result for response in client_result]
    assert [result["request_id"] for result in results] == ["first", "first"]
    assert results[0]["instance_id"] == results[1]["instance_id"]
    assert json.loads(receipt_path.read_text())["state"] == "completed"


@pytest.mark.parametrize("committed", [False, True])
def test_force_write_failure_never_grants_signal_permission(
    tmp_path, monkeypatch, short_state_dir, committed,
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    persist_agent_running(
        engine, short_state_dir, execution_id="held-execution", record_start=True,
    )
    engine._save_state("agent_running", execution_stage="worker-running")
    before = engine._runtime_store.load()
    replace = engine._runtime_store.replace

    def fail_replace(value):
        assert value["manual_retry_required"]["request_id"] == "graceful"
        if committed:
            replace(value)
        raise RuntimeStoreError("injected uncertain cancellation")

    monkeypatch.setattr(engine._runtime_store, "replace", fail_replace)
    intent = daemon.ShutdownIntent()
    with engine._stop_context(intent):
        engine.request_lifecycle("stop", "graceful")
        with pytest.raises(DevlegateError, match="uncertain cancellation"):
            engine.request_lifecycle("stop", "force", force=True)
        with pytest.raises(DevlegateError, match="outcome uncertain"):
            engine.request_lifecycle("stop", "force-repeat", force=True)
        assert not intent.is_set()
        assert engine.lifecycle_status_payload()["force"] is False
        with pytest.raises(DevlegateError, match="already accepted"):
            engine.request_lifecycle("restart", "conflict", force=True)
        with pytest.raises(DevlegateError, match="outcome uncertain"):
            engine.require_lifecycle_retirement()
    after = engine._runtime_store.load()
    assert after["execution_id"] == before["execution_id"]
    assert (after.get("manual_retry_required") is not None) is committed


def test_force_admitted_during_scheduler_git_but_cannot_finish_before_git(
    tmp_path, monkeypatch, short_state_dir,
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    fifo = tmp_path / "scheduler-input"
    os.mkfifo(fifo)
    entered = threading.Event()
    evidence = []

    def iteration():
        entered.set()
        result = engine._git_runtime(engine.repo, "hash-object", str(fifo))
        evidence.append(result.stdout.strip())
        return 0

    monkeypatch.setattr(engine, "run_iteration", iteration)

    def client():
        assert entered.wait(5)
        # Open blocks until the real Git child has opened its input.
        with fifo.open("wb") as pipe:
            started = time.monotonic()
            graceful = request(engine.ipc_socket_path, "git-stop", "stop").result
            forced = request(
                engine.ipc_socket_path, "git-force", "stop", {"force": True},
            ).result
            evidence.append((graceful, forced, time.monotonic() - started))
            assert engine._locator.daemon_authority_present()
            assert not (engine._locator.state_dir / "lifecycle").exists()
            pipe.write(b"scheduler Git still owns its input\n")

    thread = threading.Thread(target=client)
    thread.start()
    assert daemon.run_service(engine) == 0
    thread.join(5)
    assert not thread.is_alive()
    (graceful, forced, elapsed), git_result = evidence
    assert graceful["request_id"] == forced["request_id"] == "git-stop"
    assert forced["force"] is True
    assert len(git_result) == 40
    assert not engine._locator.daemon_authority_present()
    print(f"scheduler Git: graceful + force admission {elapsed:.3f}s before Git exit")


def test_force_during_git_publication_preserves_unknown_effect_for_exact_replay(
    tmp_path, monkeypatch, short_state_dir,
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    remote = git(engine.repo, "remote", "get-url", "origin").stdout.strip()
    hook = Path(remote) / "hooks" / "post-receive"
    effect = tmp_path / "publication-entered"
    release = tmp_path / "publication-release"
    hook.write_text(
        "#!/bin/sh\nwhile read old new ref; do\n"
        "case $ref in refs/devlegate/publication/*)\n"
        f"touch {shlex.quote(str(effect))}\n"
        f"while ! test -e {shlex.quote(str(release))}; do sleep .01; done;;\n"
        "esac\ndone\n"
    )
    hook.chmod(0o755)

    def worker(workspace, *_args, **_kwargs):
        (workspace.path / "effect.txt").write_text("one exact checkpoint\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(engine._workers, "run", worker)
    run_git = engine._git_runtime

    def lose_observation(repo, *args, **kwargs):
        if release.exists() and args[0] == "ls-remote" and any(
            arg.startswith("refs/devlegate/publication/") for arg in args
        ):
            return subprocess.CompletedProcess(args, 1, "", "lost observation")
        return run_git(repo, *args, **kwargs)

    monkeypatch.setattr(engine, "_git_runtime", lose_observation)
    admitted = []

    def client():
        try:
            deadline = time.monotonic() + 20
            while not effect.exists() and time.monotonic() < deadline:
                time.sleep(.01)
            assert effect.exists()
            started = time.monotonic()
            stop = request(engine.ipc_socket_path, "publish-stop", "stop").result
            force = request(
                engine.ipc_socket_path, "publish-force", "stop", {"force": True},
            ).result
            admitted.append((stop, force, time.monotonic() - started))
            assert engine._locator.daemon_authority_present()
        finally:
            release.touch()

    thread = threading.Thread(target=client)
    thread.start()
    assert daemon.run_service(engine) == 0
    thread.join(5)
    (stop, force, elapsed), = admitted
    assert stop["request_id"] == force["request_id"] == "publish-stop"
    assert force["force"] is True
    wal = engine._runtime_store.load()
    assert wal["execution_stage"] == "publishing"
    assert not wal.get("manual_retry_required")
    ref = f"refs/heads/{wal['execution_branch']}"
    published = git(engine.repo, "ls-remote", "origin", ref).stdout.split()[0]
    outcome = cli.read_lifecycle_receipt(
        engine._locator, instance_id=stop["instance_id"], request_id=stop["request_id"],
    )
    assert outcome["state"] == "completed"

    recovered = ServiceEngine(engine.env_file)
    monkeypatch.setattr(
        recovered._workers, "run", lambda *_: pytest.fail("worker reran"),
    )
    recover_git = recovered._git_runtime
    pushes = []

    def trace_replay(repo, *args, **kwargs):
        if args[0] == "push":
            pushes.append(args)
        return recover_git(repo, *args, **kwargs)

    monkeypatch.setattr(recovered, "_git_runtime", trace_replay)
    assert run_test_iteration(recovered) == 0
    assert recovered._runtime_store.load()["phase"] == "idle"
    assert all(ref not in arg for args in pushes for arg in args)
    assert git(engine.repo, "ls-remote", "origin", ref).stdout.split()[0] == published
    print(f"stalled publication: graceful + force admission {elapsed:.3f}s")


@pytest.mark.parametrize("empty", [None, False])
def test_stop_preserves_worker_wal_when_leader_exits_without_containment_proof(
    tmp_path, monkeypatch, short_state_dir, empty,
):
    engine, _ = make_engine(tmp_path, monkeypatch, short_state_dir)
    launch = engine._workers.containment_provider.create

    def containment(execution_id):
        boundary = launch(execution_id)
        monkeypatch.setattr(boundary, "wait_empty", lambda _timeout: empty)
        return boundary

    monkeypatch.setattr(
        DeterministicContainmentProvider, "create", lambda _, eid: containment(eid),
    )
    record_identity = engine._record_worker_identity
    response = []

    def record(identity, **kwargs):
        record_identity(identity, **kwargs)
        response.append(request(engine.ipc_socket_path, "contained-stop", "stop"))

    monkeypatch.setattr(engine, "_record_worker_identity", record)
    worker = tmp_path / "short-worker"
    worker.write_text("#!/bin/sh\nsleep .3\nexit 1\n")
    worker.chmod(0o755)
    monkeypatch.setattr(engine._workers, "opencode_bin", str(worker))
    with pytest.raises(DevlegateError, match="retirement is unproven"):
        daemon.run_service(engine)
    assert response[0].result["request_id"] == "contained-stop"
    state = engine._runtime_store.load()
    assert state["execution_stage"] == "worker-running"
    assert state["worker_identity"] is not None
    assert engine._workers.active_count == 0
    with pytest.raises(DevlegateError, match="stop blocked: .*retirement is unproven"):
        cli._wait_for_service_stop(
            engine._locator, "contained-stop", response[0].result["instance_id"],
        )


def test_unknown_owner_command_fails_closed(running_server):
    engine, _state, _server = running_server
    command = OperatorCommand("unknown", "unknown", "fingerprint", "T-1")

    with pytest.raises(DevlegateError, match="unsupported operator command: unknown"):
        engine._dispatch_operator_command(command, None)

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
    for method in ("status_view", "retry_candidates_view"):
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
            for method in ("status", "retry-candidates")
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

def test_workspace_ls_uses_live_owner_without_receipt_or_git_mutation(
    tmp_path, monkeypatch
):
    engine, state = make_engine(tmp_path, monkeypatch)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(
            0, None, WorkerClaim("completed", "not integrated", (), ()), None
        ),
    )
    assert run_test_iteration(engine) == 0
    execution = next((state / "worktrees").glob("*/work/T-1"))
    before = dict(engine._state.get("mutable_receipts", {}))
    manager = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    )
    registration_before = manager._registrations()

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
            "workspace-ls-request",
            "workspace-ls",
        )
        assert response.ok
        assert response.result["dry_run"] is True
    finally:
        stop_event.set()
        engine.wake()
        owner.join(timeout=5)
        server.stop()
        authority.close()

    assert not owner.is_alive()
    assert execution.is_dir()
    assert engine._state.get("mutable_receipts", {}) == before
    assert manager._registrations() == registration_before
