# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Single-argument reset lifecycle on disposable real Git repositories."""

import os
import signal
import subprocess
import sys
import threading
import time

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration
from test_clean_preview import owner as owner  # noqa: F401

from devlegate.execution_containment import DurableDeterministicContainmentProvider
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.runtime import DevlegateError, WorkflowBlockedError
from devlegate.ticket_reset import admit, advance, validate
from devlegate.tickets import TicketError
from devlegate.worker_egress import WorkerRunResult
from devlegate.worker_supervisor import (
    _capture_worker_identity,
    _linux_boot_id,
    _linux_process_start_time,
    _run_opencode,
)


@pytest.mark.parametrize("state", ["backlog", "todo", "review"])
def test_reset_requeues_exact_blob_and_discards_rework(owner, state, monkeypatch):
    source = owner.control_worktree / owner.review_path / "T-1.md"
    blob = git(owner.control_worktree, "rev-parse",
               f"HEAD:{owner.review_path}/T-1.md").stdout.strip()
    if state != "review":
        target = owner.control_worktree / owner._workflow_paths()[state] / "T-1.md"
        target.parent.mkdir(exist_ok=True)
        git(owner.control_worktree, "mv", str(source), str(target))
        git(owner.control_worktree, "commit", "-m", "source state fixture")
        git(owner.control_worktree, "push", "origin", owner.control_branch)
    feedback = owner.control_worktree / "reviews" / "T-1"
    feedback.mkdir(parents=True, exist_ok=True)
    (feedback / f"{owner._state['execution_id']}.md").write_text(
        "PAST_FEEDBACK_MUST_NOT_CONTINUE: preserve the previous implementation.\n",
    )
    git(owner.control_worktree, "add", "reviews")
    git(owner.control_worktree, "commit", "-m", "historical feedback fixture")
    git(owner.control_worktree, "push", "origin", owner.control_branch)
    reports_before = git(owner.control_worktree, "ls-tree", "-r", "HEAD",
                         "executions", "reviews").stdout
    old_execution = owner._state["execution_id"]
    with owner._lock():
        ack = admit(owner, "T-1")
        assert admit(owner, "T-1") == ack
        advance(owner)
    assert owner._state["ticket_reset"]["status"] == "queued"
    assert git(owner.control_worktree, "rev-parse",
               f"HEAD:{owner.todo_path}/T-1.md").stdout.strip() == blob
    assert git(owner.control_worktree, "ls-tree", "-r", "HEAD",
               "executions", "reviews").stdout == reports_before
    restored = Devlegate(owner.env_file)
    assert admit(restored, "T-1")["operation_id"] == ack["operation_id"]
    captured = []
    original = owner._workers.run
    (owner.repo / "current-product.txt").write_text("current product\n")
    git(owner.repo, "add", "current-product.txt")
    git(owner.repo, "commit", "-m", "current product fixture")
    git(owner.repo, "push", "origin", "main")
    current_product = git(owner.repo, "rev-parse", "HEAD").stdout.strip()
    owner.env_file.write_text(
        owner.env_file.read_text() + "\nOPENCODE_MODEL=fresh-model\n",
    )

    def worker(workspace, prompt, **kwargs):
        assert not (workspace.path / "implementation.txt").exists()
        captured.append((workspace, prompt, kwargs["execution_id"]))
        return original(workspace, prompt, **kwargs)

    monkeypatch.setattr(owner._workers, "run", worker)
    assert run_test_iteration(owner) == 0
    assert len(captured) == 1
    assert captured[0][2] != old_execution
    assert "PAST_FEEDBACK_MUST_NOT_CONTINUE" not in captured[0][1]
    assert captured[0][0].head == current_product
    assert owner._workers.opencode_model == "fresh-model"
    assert owner._state["execution_resource_binding"]["generation"] != (
        owner._state["ticket_reset"]["binding"]["generation"]
    )


@pytest.mark.parametrize("state", ["accepted", "done", "abandoned"])
def test_reset_forbidden_workflow_does_not_admit(owner, state):
    source = owner.control_worktree / owner.review_path / "T-1.md"
    target = owner.control_worktree / owner._workflow_paths()[state] / "T-1.md"
    target.parent.mkdir(exist_ok=True)
    git(owner.control_worktree, "mv", str(source), str(target))
    git(owner.control_worktree, "commit", "-m", "terminal fixture")
    git(owner.control_worktree, "push", "origin", owner.control_branch)
    with pytest.raises(TicketError, match="workflow prohibits"):
        admit(owner, "T-1")
    assert "ticket_reset" not in owner._state


def test_reset_dirty_generation_is_blocked_and_resumable(owner):
    path = owner.recovery_workspace_manager("T-1").path
    (path / "untracked").write_text("preserve me\n")
    ack = admit(owner, "T-1")
    with pytest.raises((ExecutionWorkspaceError, ValueError, OSError)):
        advance(owner)
    assert path.exists()
    assert admit(owner, "T-1")["status"] == "blocked"
    restored = Devlegate(owner.env_file)
    assert admit(restored, "T-1")["operation_id"] == ack["operation_id"]


@pytest.mark.parametrize("retirement_proved", [True, False])
def test_live_reset_cancels_before_checkpoint_and_restarts_fresh(
    tmp_path, monkeypatch, retirement_proved,
):
    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    engine = Devlegate(config)
    second = engine.control_worktree / engine.todo_path / "T-2.md"
    second.write_text(
        (engine.control_worktree / engine.todo_path / "T-1.md").read_text(),
    )
    git(engine.control_worktree, "add", ".")
    git(engine.control_worktree, "commit", "-m", "unrelated ticket fixture")
    git(engine.control_worktree, "push", "origin", engine.control_branch)
    marker = tmp_path / "live"
    runs, results, errors = [], [], []

    def worker(workspace, prompt, **kwargs):
        runs.append((workspace, prompt, kwargs["execution_id"]))
        result = _run_opencode(
            [sys.executable, "-c",
             "import pathlib,sys,time; pathlib.Path(sys.argv[1]).write_text('live'); "
             "time.sleep(60)", str(marker)],
            prompt, execution_id=kwargs["execution_id"],
            worker_identity_handler=kwargs["identity_handler"],
            interruption_handler=kwargs["interruption_handler"],
            owner_poll=kwargs["owner_poll"], stop_request=kwargs["stop_request"],
            containment=engine._workers.containment_provider.create(
                kwargs["execution_id"],
            ),
        )
        return WorkerRunResult(
            result.process_returncode, result.transport_error, None, None,
            result.interruption_kind,
            result.worker_group_retired and retirement_proved, True,
        )

    monkeypatch.setattr(engine._workers, "run", worker)

    def owner_thread(target=engine):
        try:
            results.append(run_test_iteration(target))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=owner_thread)
    thread.start()
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    with pytest.raises(DevlegateError, match="unknown or ambiguous"):
        engine.submit_reset("UNKNOWN", request_id="unrelated-ticket")
    with pytest.raises(DevlegateError, match="another ticket's worker"):
        engine.submit_reset("T-2", request_id="known-unrelated-ticket")
    assert thread.is_alive()
    ack = engine.submit_reset("T-1", request_id="transport-only")
    thread.join(10)
    assert not thread.is_alive()
    if not retirement_proved:
        assert errors and "retirement is unproven" in str(errors[0])
        restored = Devlegate(config)
        path = restored.recovery_workspace_manager("T-1").path
        with pytest.raises(WorkflowBlockedError, match="retirement cannot be proved"):
            run_test_iteration(restored)
        assert path.exists()
        assert admit(restored, "T-1")["status"] == "blocked"
        assert restored._state["ticket_reset"]["operation_id"] == ack["operation_id"]
        return
    assert not errors
    assert results == [0]
    assert ack["status"] == "stopping-worker"
    assert engine._state["execution_stage"] == "post-worker"
    assert engine._state["worker_identity"] is None
    assert not (engine.control_worktree / "executions" / "T-1").exists()
    assert engine._state["ticket_reset"]["prior_execution"]["execution_id"] == (
        runs[0][2]
    )
    restarted = Devlegate(config)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["ticket_reset"]["status"] == "queued"
    monkeypatch.setattr(restarted._workers, "run", worker)
    marker.unlink()
    fresh_thread = threading.Thread(target=owner_thread, args=(restarted,))
    fresh_thread.start()
    deadline = time.monotonic() + 10
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert marker.exists()
    duplicate = restarted.submit_reset("T-1", request_id="lost-ack-retry")
    assert duplicate["operation_id"] == ack["operation_id"]
    assert duplicate["status"] == "started"
    assert fresh_thread.is_alive()
    config.write_text(config.read_text() + "\nOPENCODE_MODEL=more-capable\n")
    changed = restarted.submit_reset("T-1", request_id="new-configuration")
    assert changed["operation_id"] != ack["operation_id"]
    fresh_thread.join(10)
    assert not fresh_thread.is_alive()
    assert not errors


def test_pristine_todo_reset_is_a_read_only_scheduling_nudge(tmp_path, monkeypatch):
    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    engine = Devlegate(config)
    before = engine._runtime_store.observe()
    assert admit(engine, "T-1")["status"] == "already-fresh"
    assert admit(engine, "T-1")["status"] == "already-fresh"
    assert engine._runtime_store.observe() == before
    assert engine._service_wake.is_set()


@pytest.mark.parametrize("committed", [False, True])
def test_reset_lost_admission_ack_replays_without_external_id(
    owner, monkeypatch, committed,
):
    original = owner._runtime_store._commit

    def crash(connection):
        if committed:
            original(connection)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(owner._runtime_store, "_commit", crash)
    with pytest.raises(DevlegateError):
        admit(owner, "T-1")
    assert owner._state_write_uncertain
    restored = Devlegate(owner.env_file)
    ack = admit(restored, "T-1")
    operation = ack["operation_id"]
    assert admit(restored, "T-1")["operation_id"] == operation
    assert run_test_iteration(restored) == 0
    assert restored._state["ticket_reset"]["status"] == "queued"


@pytest.mark.parametrize("field", ["blob", "operation_id", "binding", "prior_reports"])
def test_reset_admission_identity_is_immutable(owner, field):
    admit(owner, "T-1")
    owner._state["ticket_reset"][field] = "corrupt"
    with pytest.raises(ValueError):
        validate(owner._state)


@pytest.mark.parametrize("effect", ["worktree", "local", "remote"])
@pytest.mark.parametrize("after", [False, True])
def test_reset_effect_ack_loss_replays_exact_namespace(
    owner, monkeypatch, effect, after,
):
    import devlegate.clean_reset as retirement

    ack = admit(owner, "T-1")
    original = retirement.observe_git
    interrupted = []

    def git_fault(repo, *args, **kwargs):
        selected = (
            effect == "worktree" and args[:2] == ("worktree", "remove")
            or effect == "local" and "update-ref" in args and "-d" in args
            or effect == "remote" and "push" in args and any(
                arg.startswith(":refs/heads/devlegate/work/") for arg in args
            )
        )
        if selected and not interrupted:
            interrupted.append(True)
            if after:
                original(repo, *args, **kwargs)
            raise OSError("lost Git acknowledgement")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(retirement, "observe_git", git_fault)
    with pytest.raises(OSError, match="lost Git acknowledgement"):
        advance(owner)
    assert interrupted
    assert admit(owner, "T-1")["status"] == "blocked"
    restored = Devlegate(owner.env_file)
    assert admit(restored, "T-1")["operation_id"] == ack["operation_id"]
    assert run_test_iteration(restored) == 0
    assert restored._state["ticket_reset"]["status"] == "queued"


@pytest.mark.parametrize(
    "replacement", ["worktree", "ref", "missing-generation", "lock"],
)
def test_reset_never_adopts_replaced_or_unbound_resources(owner, replacement):
    ack = admit(owner, "T-1")
    manager = owner.recovery_workspace_manager("T-1")
    ref = f"refs/heads/{manager.branch}"
    head = git(owner.repo, "rev-parse", ref).stdout.strip()
    if replacement == "worktree":
        git(owner.repo, "worktree", "remove", str(manager.path))
        git(owner.repo, "worktree", "add", str(manager.path), manager.branch)
    elif replacement == "ref":
        git(owner.repo, "update-ref", "-d", ref)
        git(owner.repo, "update-ref", ref, head)
    elif replacement == "lock":
        git(owner.repo, "worktree", "lock", str(manager.path))
    else:
        generation = owner._state["ticket_reset"]["binding"]["generation"]
        directory = owner.execution_worktree_root / "bindings" / generation
        directory.rename(directory.with_name(generation + "-preserved"))
    with pytest.raises((ExecutionWorkspaceError, ValueError, OSError)):
        advance(owner)
    assert manager.path.exists()
    assert git(owner.repo, "rev-parse", ref).stdout.strip() == head
    assert admit(owner, "T-1")["operation_id"] == ack["operation_id"]
    assert admit(owner, "T-1")["status"] == "blocked"


def test_reset_competing_transport_requests_share_one_owner_intent(owner):
    results, errors = [], []
    owner.begin_hosted_owner()

    def client(request_id):
        try:
            results.append(owner.submit_reset("T-1", request_id=request_id))
        except Exception as error:
            errors.append(error)

    threads = [threading.Thread(target=client, args=(str(i),)) for i in range(2)]
    for thread in threads:
        thread.start()
    assert owner._service_wake.wait(2)
    command, _scheduler = owner._claim_owner_work(True)
    owner._admit_operator_command(command)
    for thread in threads:
        thread.join(2)
        assert not thread.is_alive()
    owner._release_operator_command()
    owner.end_hosted_owner()
    assert not errors
    assert len(results) == 2
    assert results[0]["operation_id"] == results[1]["operation_id"]


def test_reset_launch_failure_retries_pending_fresh_admission(owner, monkeypatch):
    admit(owner, "T-1")
    advance(owner)
    original = owner._workers.run
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: WorkerRunResult(
        -1, "launch failed", None, None, process_started=False,
    ))
    assert run_test_iteration(owner) == 0
    assert owner._state["phase"] == "agent_pending"
    assert admit(owner, "T-1")["status"] == "blocked"
    pending_id = owner._state["execution_id"]
    monkeypatch.setattr(owner._workers, "run", original)
    assert run_test_iteration(owner) == 0
    assert owner._state["execution_id"] == pending_id
    assert owner._state["ticket_reset"]["status"] == "started"
    assert owner._state["ticket_reset"]["blocker"] is None


def test_reset_requeue_preserves_raw_ticket_blob(tmp_path, monkeypatch):
    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    engine = Devlegate(config)
    source = engine.control_worktree / engine.todo_path / "T-1.md"
    target = engine.control_worktree / engine.backlog_path / "T-1.md"
    target.parent.mkdir(exist_ok=True)
    git(engine.control_worktree, "mv", str(source), str(target))
    target.write_bytes(target.read_bytes().replace(b"\n", b"\r\n"))
    git(engine.control_worktree, "add", ".")
    git(engine.control_worktree, "commit", "-m", "raw ticket fixture")
    git(engine.control_worktree, "push", "origin", engine.control_branch)
    blob = git(engine.control_worktree, "rev-parse",
               f"HEAD:{engine.backlog_path}/T-1.md").stdout.strip()
    admit(engine, "T-1")
    advance(engine)
    assert git(engine.control_worktree, "rev-parse",
               f"HEAD:{engine.todo_path}/T-1.md").stdout.strip() == blob


@pytest.mark.parametrize("owner", ["submodule", "nested"], indirect=True)
@pytest.mark.parametrize("dirty", [False, True])
def test_reset_recursive_submodule_safety(owner, dirty):
    manager = owner.recovery_workspace_manager("T-1")
    if dirty:
        dependency = manager.path / "dependency"
        target = (dependency / "nested"
                  if (dependency / "nested").exists() else dependency)
        (target / "data").write_text("preserve dirty submodule\n")
    admit(owner, "T-1")
    if dirty:
        with pytest.raises(ExecutionWorkspaceError):
            advance(owner)
        assert manager.path.exists()
        assert admit(owner, "T-1")["status"] == "blocked"
    else:
        advance(owner)
        assert not manager.path.exists()
        assert owner._state["ticket_reset"]["status"] == "queued"


def test_restart_during_cancellation_retires_live_managed_group(tmp_path, monkeypatch):
    """A durable process fixture models recovery of the kernel lifetime boundary."""
    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    root = tmp_path / "lifetimes"

    class RecoverableProvider(DurableDeterministicContainmentProvider):
        def launch_evidence(self, execution_id):
            metadata = self.root.stat()
            pid = int((self.root / f"execution-{execution_id}.pid").read_text())
            return {
                "root": str(self.root), "inode": [metadata.st_dev, metadata.st_ino],
                "boot": _linux_boot_id(), "pid": pid,
                "start": _linux_process_start_time(pid),
                "containment": f"deterministic:{execution_id}",
            }

        def recover_launch(self, execution_id, evidence, *, pid, terminate):
            assert evidence == self.launch_evidence(execution_id)
            assert pid == evidence["pid"] and terminate
            os.killpg(pid, signal.SIGTERM)
            time.sleep(0.1)
            os.killpg(pid, signal.SIGKILL)
            deadline = time.monotonic() + 5
            while self.observe(evidence["containment"]) != "absent":
                if time.monotonic() >= deadline:
                    raise RuntimeError("fixture group retirement remains unproven")
                time.sleep(0.01)
            return "absent"

    class OwnerLost(Exception):
        pass

    engine = Devlegate(config)
    provider = RecoverableProvider(root)
    engine._workers.containment_provider = provider
    running, acknowledgements = [], []
    marker = tmp_path / "descendant"

    def interrupted_owner(_workspace, _prompt, **kwargs):
        execution_id = kwargs["execution_id"]
        boundary = provider.create(execution_id)
        script = (
            "import pathlib,signal,subprocess,sys,time; "
            "signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "child=subprocess.Popen([sys.executable, '-c', "
            "'import signal,time; signal.signal(signal.SIGTERM, signal.SIG_IGN); "
            "time.sleep(60)']); "
            "pathlib.Path(sys.argv[1]).write_text(str(child.pid)); time.sleep(60)"
        )
        process = boundary.spawn(
            [sys.executable, "-c", script, str(marker)], start_new_session=True,
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        running.append((boundary, process))
        kwargs["identity_handler"](_capture_worker_identity(
            process, execution_id, boundary.identity,
        ))
        boundary.acknowledge_identity()
        deadline = time.monotonic() + 5
        while not marker.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert marker.exists()
        acknowledgements.append(admit(engine, "T-1"))
        # Lose the owner after durable admission but before any stop/effect.
        raise OwnerLost

    monkeypatch.setattr(engine._workers, "run", interrupted_owner)
    try:
        with pytest.raises(OwnerLost):
            run_test_iteration(engine)
        assert provider.observe(running[0][0].identity) == "matching-live"
        restored = Devlegate(config)
        restored._workers.containment_provider = RecoverableProvider(root)
        assert run_test_iteration(restored) == 0
        assert restored._state["ticket_reset"]["status"] == "queued"
        assert restored._state["ticket_reset"]["operation_id"] == (
            acknowledgements[0]["operation_id"]
        )
        assert provider.observe(running[0][0].identity) == "absent"
        with pytest.raises(ProcessLookupError):
            os.kill(int(marker.read_text()), 0)
        assert not (restored.control_worktree / "executions" / "T-1").exists()
    finally:
        for boundary, process in running:
            boundary.force_terminate()
            process.wait(timeout=5)
