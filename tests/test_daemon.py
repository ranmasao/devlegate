import signal
import threading

import pytest
from test_control_plane import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli
import devlegate.daemon as daemon
from devlegate.cli import DevlegateError
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), config, state


def test_daemon_command_constructs_one_service_engine(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

    def host(engine):
        calls.append(("host", engine))
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
    )
    monkeypatch.chdir(working)

    assert cli.main() == 0
    assert len(calls) == 2
    assert calls[0] == ("init", config, False)
    assert calls[1][0] == "host"
    assert isinstance(calls[1][1], FakeServiceEngine)


@pytest.mark.parametrize(
    ("signum", "expected_kind", "expected_status"),
    [
        (signal.SIGINT, "operator_abort", 130),
        (signal.SIGTERM, "service_shutdown", 0),
    ],
)
def test_daemon_host_signal_handler_only_sets_stop_intent(
    monkeypatch, signum, expected_kind, expected_status
):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_event):
            installed[signum](signum, None)
            assert stop_event.is_set()
            assert stop_event.kind == expected_kind
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    assert daemon.run_daemon(FakeServiceEngine()) == expected_status
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}


@pytest.mark.parametrize(
    "signals", [(signal.SIGTERM, signal.SIGINT), (signal.SIGINT, signal.SIGTERM)]
)
def test_daemon_host_operator_abort_precedes_service_shutdown(monkeypatch, signals):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_intent):
            for signum in signals:
                installed[signum](signum, None)
            assert stop_intent.kind == "operator_abort"
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    assert daemon.run_daemon(FakeServiceEngine()) == 130


def test_daemon_cli_remains_foreground_until_host_returns(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result = []

    class FakeServiceEngine:
        def __init__(self, _env_file, *, read_only=False):
            assert not read_only

    def host(_engine):
        entered.set()
        assert release.wait(10)
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
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

    monkeypatch.setattr(engine, "run_once", iteration)
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


def test_daemon_stop_already_requested_does_not_admit_iteration(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    stop_event.set()
    calls = []
    monkeypatch.setattr(engine, "run_once", lambda: calls.append(True))
    monkeypatch.setattr(
        "devlegate.runtime._git",
        lambda *_args, **_kwargs: pytest.fail("unexpected observation"),
    )

    assert engine.serve(stop_event) == 0
    assert calls == []


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
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: calls.append(True))

    assert engine.serve(stop_event) == 0
    assert calls == []
    assert engine._state["phase"] == "idle"


def test_stop_before_merge_commit_does_not_fast_forward(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
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
    publisher = tmp_path / "seed"
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


def test_persisted_merge_pending_drains_even_when_stop_already_set(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    publisher = tmp_path / "seed"
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


def test_existing_agent_pending_is_preserved_on_stop(tmp_path, monkeypatch):
    engine, _config, state = make_engine(tmp_path, monkeypatch)
    workspace, _control = persist_agent_running(engine, state)
    engine._save_state("agent_pending")
    before = dict(engine._state)
    calls = []
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: calls.append(True))
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
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: pytest.fail("worker"))
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
    monkeypatch.setattr(engine, "_run_worker", lambda *_args: pytest.fail("worker"))
    assert engine.serve(stop_event) == 0
    assert prepared and prepared[0].path.is_dir()
    assert engine._state["phase"] == "agent_pending"


def test_stop_after_agent_running_commit_still_drains_attempt(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()
    original_save = engine._save_state
    worker_calls = []

    def save_state(phase, **fields):
        original_save(phase, **fields)
        if (
            phase == "agent_running"
            and fields.get("execution_stage") == "pre-checkpoint"
        ):
            stop_event.set()

    def worker(_workspace, _prompt):
        worker_calls.append(True)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0
    assert worker_calls == [True]
    assert engine._state["phase"] == "idle"


def test_precheckpoint_integrity_failure_with_stop_keeps_retry_semantics(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt):
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule changed")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        engine.run_once(stop_event)
    assert engine._state["phase"] == "idle"
    assert engine._state["failed_executions"]["T-1"]["interrupted"] is True


def test_later_stage_failure_with_stop_remains_ambiguous(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    stop_event = threading.Event()

    def worker(_workspace, _prompt):
        stop_event.set()
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    monkeypatch.setattr(
        engine,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        engine.run_once(stop_event)
    assert engine._state["phase"] == "agent_running"
    assert engine._state["execution_stage"] == "publishing"
    assert engine._state.get("failed_executions", {}) == {}


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

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_save_state", save_state)
    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0

    control = next((state / "worktrees").glob("*/control"))
    assert engine._state["phase"] == "idle"
    assert (control / "kanban/review/T-1.md").is_file()
    assert engine.service_snapshot().worker_running is False


def test_stop_before_accepted_integration_leaves_ticket_accepted(
    tmp_path, monkeypatch
):
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
    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.run_once() == 0
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

    def worker(workspace, _prompt):
        calls.append(workspace.ticket_id)
        stop_event.set()
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_event) == 0
    assert calls == ["T-1"]
    assert (control / "kanban/todo/T-2.md").is_file()


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

    monkeypatch.setattr(engine, "run_once", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)

    second = ServiceEngine(config)
    before = dict(second._state)
    with pytest.raises(DevlegateError, match="another devlegate instance"):
        second.run(once=True)
    assert second.status().phase == "idle"
    assert second.plan().action == "run-worker"
    assert second._state == before

    release.set()
    thread.join(10)
    assert result == [0]
    assert state.exists()


def test_daemon_stop_during_worker_finishes_attempt_without_next_ticket(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    worker_entered = threading.Event()
    worker_release = threading.Event()
    stop_event = threading.Event()
    worker_calls = []
    result = []

    def worker(_workspace, _prompt):
        worker_calls.append(True)
        worker_entered.set()
        assert worker_release.wait(10)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
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


@pytest.mark.parametrize("kind", ["operator_abort", "service_shutdown"])
def test_controlled_worker_interruption_is_persisted_and_preserves_workspace(
    tmp_path, monkeypatch, kind
):
    engine, config, _state = make_engine(tmp_path, monkeypatch)
    stop_intent = daemon.ShutdownIntent()
    workspace_path = []

    def worker(workspace, _prompt):
        workspace_path.append(workspace.path)
        (workspace.path / "partial-work.txt").write_text("preserve\n")
        stop_intent.request(kind)
        return WorkerRunResult(-2, None, None, None, kind)

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert engine.serve(stop_intent) == 0
    assert engine._state["phase"] == "idle"
    assert workspace_path[0].joinpath("partial-work.txt").read_text() == "preserve\n"
    metadata = engine._state["failed_executions"]["T-1"]
    assert metadata["interruption_kind"] == kind
    assert metadata["reason"] == "interrupted: " + kind.replace("_", " ")

    fresh = ServiceEngine(config)
    failure = fresh.status_view().failed_executions[0]
    assert failure.interruption_kind == kind


def test_daemon_has_no_unix_daemonization_path():
    source = open(daemon.__file__, encoding="ascii").read()
    assert "os.fork" not in source
    assert "setsid" not in source
