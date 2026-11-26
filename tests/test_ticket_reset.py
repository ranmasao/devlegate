# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Single-argument reset lifecycle on disposable real Git repositories."""

import copy
import json
import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import replace
from pathlib import Path

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration
from test_clean_preview import owner as owner  # noqa: F401

from devlegate.blackboard_store import BlackboardStore
from devlegate.execution_containment import DurableDeterministicContainmentProvider
from devlegate.execution_result import ExecutionReportStore
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.inspection import inspect_object
from devlegate.ipc_server import UnixIPCServer
from devlegate.resource_binding import UnprovenResource
from devlegate.runtime import DevlegateError, WorkflowBlockedError
from devlegate.ticket_reset import admit, advance, validate
from devlegate.tickets import TicketError
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from devlegate.worker_supervisor import (
    _capture_worker_identity,
    _linux_boot_id,
    _linux_process_start_time,
    _run_opencode,
)


def test_reset_lifecycle_journal_tracks_persisted_transitions(owner, monkeypatch):
    import devlegate.ticket_reset as reset

    entries = []

    def record(message):
        state, _revision = owner._runtime_store.observe()
        entries.append((message, copy.deepcopy(state["ticket_reset"])))

    monkeypatch.setattr(reset, "service_log", record)
    ack = admit(owner, "T-1")
    assert admit(owner, "T-1") == ack
    advance(owner)
    assert run_test_iteration(owner) == 0
    stages = [
        "stopping-worker", "retiring-old-generation", "requeue", "queued", "started",
    ]
    assert [state["status"] for _, state in entries] == stages
    for (message, state), stage in zip(entries, stages, strict=True):
        assert f"ticket_id=T-1 operation_id={ack['operation_id']}" in message
        assert f"stage={stage}" in message
        if stage == "started":
            assert f"execution_id={state['fresh_execution_id']}" in message
    assert "admitted" in entries[0][0]
    assert "worker retirement proven" in entries[1][0]
    assert "old generation retirement proven" in entries[2][0]
    assert "requeue published and activated" in entries[3][0]
    assert "fresh execution started" in entries[4][0]
    restored = Devlegate(owner.env_file)
    assert restored._state["ticket_reset"]["status"] == "started"
    assert len(entries) == 5


def test_reset_blocked_journal_is_private_deduplicated_and_recovers(owner, monkeypatch):
    import devlegate.ticket_reset as reset

    entries = []
    monkeypatch.setattr(reset, "service_log", entries.append)
    ack = admit(owner, "T-1")
    original = reset._binding

    def fail(*_args):
        raise ValueError("secret=PASSWORD /private/path\nmalicious journal line")

    monkeypatch.setattr(reset, "_binding", fail)
    for _ in range(2):
        with pytest.raises(ValueError, match="secret=PASSWORD"):
            advance(owner)
    blocked = [line for line in entries if " blocked " in line]
    assert len(blocked) == 1
    assert f"ticket_id=T-1 operation_id={ack['operation_id']}" in blocked[0]
    assert "stage=retiring-old-generation" in blocked[0]
    assert "PASSWORD" not in blocked[0] and "/private" not in blocked[0]
    assert "\n" not in blocked[0]
    assert not any("stage=requeue" in line for line in entries)
    monkeypatch.setattr(reset, "_binding", original)
    restored = Devlegate(owner.env_file)
    advance(restored)
    assert sum("stage=retiring-old-generation" in line for line in entries) == 2
    assert any("stage=requeue" in line for line in entries)
    assert any("stage=queued" in line for line in entries)


def test_reset_publication_failure_does_not_log_queue(owner, monkeypatch):
    import devlegate.ticket_reset as reset

    entries = []
    monkeypatch.setattr(reset, "service_log", entries.append)
    admit(owner, "T-1")
    original = owner._publish_abandonment_transaction

    def fail(_transaction):
        raise OSError("private publication failure")

    monkeypatch.setattr(owner, "_publish_abandonment_transaction", fail)
    with pytest.raises(OSError):
        advance(owner)
    assert owner._state["ticket_reset"]["status"] == "requeue"
    assert any("stage=requeue blocked" in line for line in entries)
    assert not any("stage=queued" in line for line in entries)
    monkeypatch.setattr(owner, "_publish_abandonment_transaction", original)
    advance(owner)
    assert sum("stage=requeue old generation" in line for line in entries) == 1
    assert sum("stage=queued" in line for line in entries) == 1


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


def test_reset_stopped_dirty_generation_restarts_without_old_content(
    owner, monkeypatch,
):
    board = BlackboardStore(owner.state_dir, owner._state_key, "T-1")
    original_note = board.save(
        "Historical inventory", "pinned inputs", "text",
        execution=owner._state["execution_id"],
    )
    path = owner.recovery_workspace_manager("T-1").path
    (path / "implementation.txt").write_text("discard tracked edits\n")
    (path / "untracked").write_text("discard new source\n")
    (path / ".gitignore").write_text("ignored\n")
    (path / "ignored").write_text("discard ignored source\n")
    git(path, "update-index", "--assume-unchanged", "implementation.txt")
    outside = path.parent / "preserved"
    outside.write_text("outside generation\n")
    (path / "link").symlink_to(outside)
    admit(owner, "T-1")
    advance(owner)
    assert not path.exists()
    assert outside.read_text() == "outside generation\n"
    restored = Devlegate(owner.env_file)
    original = owner._workers.run

    def worker(workspace, prompt, **kwargs):
        for name in ("implementation.txt", "untracked", "ignored", "link"):
            assert not (workspace.path / name).exists()
        assert git(workspace.path, "status", "--porcelain").stdout == ""
        assert "Historical inventory" not in prompt
        assert "pinned inputs" not in prompt
        return original(workspace, prompt, **kwargs)

    monkeypatch.setattr(restored._workers, "run", worker)
    assert run_test_iteration(restored) == 0
    assert board.read(original_note["id"], None)["content"] == "pinned inputs"


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
        tracked = git(workspace.path, "ls-files").stdout.splitlines()[0]
        result = _run_opencode(
            [sys.executable, "-c",
             "import pathlib,sys,time; root=pathlib.Path(sys.argv[2]); "
             "(root/sys.argv[3]).write_text('OLD_TRACKED_CODE'); "
             "(root/'old-source.py').write_text('OLD_UNTRACKED_CODE'); "
             "pathlib.Path(sys.argv[1]).write_text('live'); time.sleep(60)",
             str(marker), str(workspace.path), tracked],
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
    old_path = runs[0][0].path
    assert (old_path / "old-source.py").exists()
    blob = git(engine.control_worktree, "rev-parse",
               f"HEAD:{engine.todo_path}/T-1.md").stdout.strip()
    with pytest.raises(DevlegateError, match="unknown or ambiguous"):
        engine.submit_reset("UNKNOWN", request_id="unrelated-ticket")
    with pytest.raises(DevlegateError, match="another ticket's worker"):
        engine.submit_reset("T-2", request_id="known-unrelated-ticket")
    assert thread.is_alive()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        response = invoke(repo, "reset", "T-1", "--json", config=config)
        assert response.returncode == 0, response.stderr
        ack = json.loads(response.stdout)
    finally:
        server.stop()
    thread.join(10)
    assert not thread.is_alive()
    if not retirement_proved:
        assert errors and "retirement is unproven" in str(errors[0])
        restored = Devlegate(config)
        assert admit(restored, "T-1")["reason"] == "worker retirement is unproven"
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
    assert not old_path.exists()
    assert git(restarted.control_worktree, "rev-parse",
               f"HEAD:{restarted.todo_path}/T-1.md").stdout.strip() == blob
    (repo / "current-product.txt").write_text("current product\n")
    git(repo, "add", "current-product.txt")
    git(repo, "commit", "-m", "current product after reset")
    git(repo, "push", "origin", "main")
    current = git(repo, "rev-parse", "HEAD").stdout.strip()
    config.write_text(config.read_text() + "\nOPENCODE_MODEL=fresh-model\n")

    def fresh_worker(workspace, prompt, **kwargs):
        assert kwargs["execution_id"] != runs[0][2]
        assert workspace.head == current
        assert restarted._workers.opencode_model == "fresh-model"
        assert git(workspace.path, "status", "--porcelain").stdout == ""
        assert not (workspace.path / "old-source.py").exists()
        assert "OLD_TRACKED_CODE" not in prompt
        assert "OLD_UNTRACKED_CODE" not in prompt
        assert restarted._state["execution_resource_binding"]["generation"] != (
            restarted._state["ticket_reset"]["binding"]["generation"]
        )
        return worker(workspace, prompt, **kwargs)

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
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
    import devlegate.ticket_reset as reset

    entries = []
    monkeypatch.setattr(reset, "service_log", entries.append)
    original = owner._runtime_store._commit

    def crash(connection):
        if committed:
            original(connection)
        raise OSError("lost acknowledgement")

    monkeypatch.setattr(owner._runtime_store, "_commit", crash)
    with pytest.raises(DevlegateError):
        admit(owner, "T-1")
    assert not entries
    assert owner._state_write_uncertain
    restored = Devlegate(owner.env_file)
    ack = admit(restored, "T-1")
    assert sum(" admitted" in line for line in entries) == (0 if committed else 1)
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

    path = owner.recovery_workspace_manager("T-1").path
    (path / "implementation.txt").write_text("discard tracked changes on replay\n")
    (path / "new-source").write_text("discard untracked changes on replay\n")
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


@pytest.mark.parametrize("unsafe", ["nested-repo", "bare-repo", "fifo", "admin-lock"])
def test_dirty_discard_does_not_override_safety_uncertainty(owner, unsafe):
    from devlegate.resource_binding import ResourceBinding

    manager = owner.recovery_workspace_manager("T-1")
    binding = ResourceBinding.for_execution(manager, owner._state["execution_id"])
    (manager.path / "implementation.txt").write_text("ordinary dirty source\n")
    if unsafe in {"nested-repo", "bare-repo"}:
        foreign = manager.path / "foreign"
        foreign.mkdir()
        git(foreign, "init", *(("--bare",) if unsafe == "bare-repo" else ()))
    elif unsafe == "fifo":
        os.mkfifo(manager.path / "pipe")
    else:
        (binding._admin() / "index.lock").write_text("held\n")
    admit(owner, "T-1")
    with pytest.raises((ExecutionWorkspaceError, ValueError)):
        advance(owner)
    assert manager.path.exists()
    assert (manager.path / "implementation.txt").read_text() == (
        "ordinary dirty source\n"
    )
    assert admit(owner, "T-1")["status"] == "blocked"


@pytest.mark.parametrize("owner", ["nested"], indirect=True)
@pytest.mark.parametrize("unsafe", ["lock", "foreign-admin", "head", "symlink"])
def test_dirty_submodule_discard_requires_proved_administration(
    owner, unsafe, tmp_path,
):
    manager = owner.recovery_workspace_manager("T-1")
    module = manager.path / "dependency" / "nested"
    (module / "data").write_text("ordinary dirty dependency\n")
    if unsafe == "lock":
        admin = git(module, "rev-parse", "--absolute-git-dir").stdout.strip()
        (Path(admin) / "index.lock").write_text("held\n")
    elif unsafe == "foreign-admin":
        foreign = tmp_path / "foreign"
        git(tmp_path, "clone", str(module), str(foreign))
        (module / ".git").write_text(f"gitdir: {foreign / '.git'}\n")
        git(foreign, "config", "core.worktree", str(module))
    elif unsafe == "head":
        git(module, "add", "data")
        git(module, "-c", "user.name=Fixture", "-c", "user.email=f@example.com",
            "commit", "-m", "unreceipted dependency commit")
    else:
        moved = module.with_name("preserved")
        module.rename(moved)
        module.symlink_to(moved, target_is_directory=True)
    admit(owner, "T-1")
    with pytest.raises((ExecutionWorkspaceError, ValueError)):
        advance(owner)
    assert manager.path.exists()
    assert admit(owner, "T-1")["status"] == "blocked"


def test_reset_launch_failure_retries_pending_fresh_admission(owner, monkeypatch):
    import devlegate.ticket_reset as reset

    entries = []
    monkeypatch.setattr(reset, "service_log", entries.append)
    admit(owner, "T-1")
    advance(owner)
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: WorkerRunResult(
        -1, "launch failed", None, None, process_started=False,
    ))
    assert run_test_iteration(owner) == 0
    assert owner._state["phase"] == "agent_pending"
    assert admit(owner, "T-1")["status"] == "blocked"
    assert any("stage=queued blocked" in line for line in entries)
    assert not any("stage=started" in line for line in entries)
    pending_id = owner._state["execution_id"]
    pending = copy.deepcopy(owner._state["ticket_reset"])
    owner.env_file.write_text(owner.env_file.read_text() + "\nOPENCODE_MODEL=changed\n")
    restored = Devlegate(owner.env_file)
    assert admit(restored, "T-1") == admit(owner, "T-1")

    def fast_exit(workspace, _prompt, **kwargs):
        assert kwargs["execution_id"] == pending_id
        assert restored._state["ticket_reset"] == pending
        assert restored._workers.opencode_model == pending["fresh_configuration"][
            "opencode_model"
        ]
        (workspace.path / "fast-exit").write_text("new contribution\n")
        # A started-and-retired lifetime, with no identity callback.
        return WorkerRunResult(0, None, WorkerClaim("completed", "fast", (), ()), None)

    monkeypatch.setattr(restored._workers, "run", fast_exit)
    assert run_test_iteration(restored) == 0
    assert restored._state["ticket_reset"] == {
        **pending, "status": "started", "blocker": None,
    }
    assert sum("stage=started" in line for line in entries) == 1


@pytest.mark.parametrize("field", ["fresh_execution_id", "fresh_generation"])
def test_bound_queue_refuses_mismatched_pending_admission(owner, monkeypatch, field):
    admit(owner, "T-1")
    advance(owner)
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: WorkerRunResult(
        -1, "launch failed", None, None, process_started=False,
    ))
    assert run_test_iteration(owner) == 0
    queued = owner._state["ticket_reset"]
    mismatch = {**queued, field: "f" * len(queued[field])}
    owner._save_state("agent_pending", ticket_reset=mismatch)
    restored = Devlegate(owner.env_file)
    monkeypatch.setattr(
        restored._workers, "run", lambda *_a, **_k: pytest.fail("launch"),
    )
    before = restored._runtime_store.observe()
    with pytest.raises(WorkflowBlockedError, match="execution or generation no longer"):
        run_test_iteration(restored)
    assert restored._runtime_store.observe() == before


def test_unbound_queue_resumes_proven_preparation_boundary(owner, monkeypatch):
    ack = admit(owner, "T-1")
    advance(owner)
    queued = copy.deepcopy(owner._state["ticket_reset"])
    save = owner._save_state

    class OwnerLost(Exception):
        pass

    def lose_owner(phase, **fields):
        if phase == "agent_pending" and "ticket_reset" in fields:
            # Model interruption with the pending execution saved separately
            # from the reset binding, before any workspace preparation.
            fields.pop("ticket_reset")
            save(phase, **fields)
            raise OwnerLost
        save(phase, **fields)

    monkeypatch.setattr(owner, "_save_state", lose_owner)
    with pytest.raises(OwnerLost):
        run_test_iteration(owner)
    restored = Devlegate(owner.env_file)
    assert restored._state["ticket_reset"] == queued
    pending_id = restored._state["execution_id"]
    generation = restored._state["execution_resource_binding"]["generation"]
    monkeypatch.setattr(restored._workers, "run", owner._workers.run)
    assert run_test_iteration(restored) == 0
    completed = restored._state["ticket_reset"]
    assert completed["status"] == "started"
    assert completed["operation_id"] == ack["operation_id"]
    assert completed["fresh_execution_id"] == pending_id
    assert completed["fresh_generation"] == generation


@pytest.mark.parametrize("retired", [False, True])
@pytest.mark.parametrize("field", ["fresh_execution_id", "fresh_generation"])
def test_mismatched_queue_cannot_record_worker_progress(
    owner, monkeypatch, field, retired,
):
    import devlegate.ticket_reset as reset

    entries = []
    monkeypatch.setattr(reset, "service_log", entries.append)
    admit(owner, "T-1")
    advance(owner)
    observed = []

    def worker(_workspace, prompt, **kwargs):
        queued = owner._state["ticket_reset"]
        mismatch = {**queued, field: "f" * len(queued[field])}
        owner._save_state("agent_running", ticket_reset=mismatch)
        observed.append(mismatch)
        if not retired:
            run = _run_opencode(
                [sys.executable, "-c", "import sys; sys.stdin.read()"], prompt,
                execution_id=kwargs["execution_id"],
                worker_identity_handler=kwargs["identity_handler"],
            )
            assert owner._state["worker_identity"] is not None
            return replace(run, worker_group_retired=False)
        return WorkerRunResult(0, None, None, None)

    monkeypatch.setattr(owner._workers, "run", worker)
    message = "cannot advance" if retired else "retirement is unproven"
    with pytest.raises(DevlegateError, match=message):
        run_test_iteration(owner)
    assert owner._runtime_store.observe()[0]["ticket_reset"] == observed[0]
    assert not any("blocked" in line or "stage=started" in line for line in entries)


def test_queued_uncertain_launch_holds_same_operation_after_restart(owner, monkeypatch):
    ack = admit(owner, "T-1")
    advance(owner)
    assert owner._state["ticket_reset"]["fresh_execution_id"] is None
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: WorkerRunResult(
        -1, "launch outcome unknown", None, None,
        worker_group_retired=False, process_started=False,
    ))
    with pytest.raises(DevlegateError, match="retirement is unproven"):
        run_test_iteration(owner)
    restored = Devlegate(owner.env_file)
    before = copy.deepcopy(restored._state["ticket_reset"])
    assert before["fresh_execution_id"] == restored._state["execution_id"]
    assert before["fresh_generation"] == (
        restored._state["execution_resource_binding"]["generation"]
    )
    blocked = admit(restored, "T-1")
    assert blocked["operation_id"] == ack["operation_id"]
    assert blocked["stage"] == "queued" and blocked["status"] == "blocked"
    assert blocked["reason"] == "worker retirement is unproven"
    monkeypatch.setattr(
        restored._workers, "run", lambda *_a, **_k: pytest.fail("launch"),
    )
    with pytest.raises(DevlegateError):
        run_test_iteration(restored)
    assert restored._state["ticket_reset"] == before
    assert restored._state["execution_stage"] == "worker-launch"


def test_stale_started_blocker_is_read_only_history(owner):
    store = ExecutionReportStore(owner.control_worktree)
    [discarded] = store.list("T-1")
    admit(owner, "T-1")
    advance(owner)
    assert run_test_iteration(owner) == 0
    completed = owner._state["ticket_reset"]
    stale = {**completed, "blocker": "worker retirement is unproven"}
    owner._save_state(owner._state["phase"], ticket_reset=stale)
    restored = Devlegate(owner.env_file)
    before = restored._runtime_store.observe()
    with restored._lock():
        status = restored.status_view()
        for kind, ticket in (("ticket", "T-1"), ("service", None)):
            view = inspect_object(restored, kind, ticket)["observation"]["reset"]
            assert view["status"] == "started" and view["reason"] is None
            assert view["fresh_execution_id"] == completed["fresh_execution_id"]
    assert "reset" not in status.plan.reason
    assert restored._state["reset_boundaries"]["T-1"] == [discarded.execution_id]
    assert restored._latest_reviewed_rework_report([discarded]) is None
    assert restored._runtime_store.observe() == before


@pytest.mark.parametrize("scenario", ["ordinary", "same-ticket", "different-ticket"])
def test_worker_retirement_refusal_is_independent_of_reset_history(
    tmp_path, monkeypatch, scenario,
):
    import devlegate.ticket_reset as reset

    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    engine = Devlegate(config)
    provider = DurableDeterministicContainmentProvider(tmp_path / "lifetimes")
    entries, completed, runs = [], [], []
    monkeypatch.setattr(reset, "service_log", entries.append)

    if scenario != "ordinary":
        source = engine.control_worktree / engine.todo_path / "T-1.md"
        target = engine.control_worktree / engine.backlog_path / source.name
        target.parent.mkdir(exist_ok=True)
        git(engine.control_worktree, "mv", str(source), str(target))
        git(engine.control_worktree, "commit", "-m", "backlog fixture")
        git(engine.control_worktree, "push", "origin", engine.control_branch)
        admit(engine, "T-1")
        advance(engine)

    def install_failure(owner):
        owner._workers.containment_provider = provider

        def worker(workspace, prompt, **kwargs):
            execution_id = kwargs["execution_id"]

            def record_identity(identity):
                kwargs["identity_handler"](identity)
                record = owner._state.get("ticket_reset")
                if record is not None and not completed:
                    assert record["status"] == "started"
                    assert admit(owner, workspace.ticket_id)["operation_id"] == (
                        record["operation_id"]
                    )
                    completed.append(copy.deepcopy(record))

            runs.append(execution_id)
            result = _run_opencode(
                [sys.executable, "-c", "import sys; sys.stdin.read()"], prompt,
                execution_id=execution_id,
                worker_identity_handler=record_identity,
                containment=provider.create(execution_id),
            )
            assert result.worker_group_retired
            # Lose the supervisor's proof. Restart can independently observe
            # absence later, but this owner must retain its exact worker binding.
            return replace(result, worker_group_retired=False)

        monkeypatch.setattr(owner._workers, "run", worker)

    def assert_refusal(owner, ticket_id):
        install_failure(owner)
        with pytest.raises(DevlegateError, match="worker retirement is unproven"):
            run_test_iteration(owner)
        state = owner._runtime_store.observe()[0]
        assert state["execution_ticket_id"] == ticket_id
        assert state["execution_id"] == runs[-1]
        assert state["execution_stage"] == "worker-running"
        identity = state["worker_identity"]
        binding = owner._execution_generation_binding()
        assert identity == binding.operation_receipt("worker")["identity"]
        assert identity["containment_id"] == f"deterministic:{runs[-1]}"
        with pytest.raises(UnprovenResource):
            binding.require_worker_retirement()
        assert binding.manager.path.exists()
        assert not ExecutionReportStore(owner.control_worktree).list(ticket_id)
        assert state.get("ticket_reset") == (completed[0] if completed else None)
        assert not any("blocked" in message for message in entries)

    if scenario == "different-ticket":
        def successful_fresh(workspace, _prompt, **_kwargs):
            (workspace.path / "implementation").write_text("fresh source\n")
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "fresh implementation", (), ()), None,
            )

        monkeypatch.setattr(engine._workers, "run", successful_fresh)
        assert run_test_iteration(engine) == 0
        completed.append(copy.deepcopy(engine._state["ticket_reset"]))
        assert completed[0]["status"] == "started"
        control = engine.control_worktree
        (control / engine.todo_path / "T-2.md").write_text(
            (control / engine.review_path / "T-1.md").read_text(),
        )
        backlog = control / engine.backlog_path
        backlog.mkdir(exist_ok=True)
        git(control, "mv", str(control / engine.review_path / "T-1.md"), str(backlog))
        git(control, "add", ".")
        git(control, "commit", "-m", "next ticket")
        git(control, "push", "origin", engine.control_branch)
        restored = Devlegate(config)
        assert_refusal(restored, "T-2")
    else:
        assert_refusal(engine, "T-1")
        restored = Devlegate(config)
        assert_refusal(restored, "T-1")
        assert runs[0] != runs[1]
    if completed:
        assert restored._state["execution_id"] != completed[0]["fresh_execution_id"]
        assert restored._state["execution_resource_binding"]["generation"] != (
            completed[0]["fresh_generation"]
        )


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
def test_reset_recursive_submodule_safety(owner, dirty, monkeypatch):
    manager = owner.recovery_workspace_manager("T-1")
    old_execution = owner._state["execution_id"]
    if dirty:
        dependency = manager.path / "dependency"
        target = (dependency / "nested"
                  if (dependency / "nested").exists() else dependency)
        (target / "data").write_text("discard dirty submodule\n")
        (target / "new-source").write_text("discard untracked submodule source\n")
    admit(owner, "T-1")
    advance(owner)
    assert not manager.path.exists()
    assert owner._state["ticket_reset"]["status"] == "queued"
    original = owner._workers.run

    def fresh(workspace, prompt, **kwargs):
        assert kwargs["execution_id"] != old_execution
        dependency = workspace.path / "dependency"
        target = (dependency / "nested"
                  if (dependency / "nested").exists() else dependency)
        assert (target / "data").read_text() == "dependency\n"
        assert not (target / "new-source").exists()
        assert git(workspace.path, "status", "--porcelain").stdout == ""
        return original(workspace, prompt, **kwargs)

    monkeypatch.setattr(owner._workers, "run", fresh)
    assert run_test_iteration(owner) == 0


@pytest.mark.parametrize("owner", ["named"], indirect=True)
@pytest.mark.parametrize("dirty", [False, True])
def test_reset_named_submodule_retirement(owner, dirty, monkeypatch):
    from devlegate.resource_binding import ResourceBinding

    manager = owner.recovery_workspace_manager("T-1")
    binding = ResourceBinding.for_execution(manager, owner._state["execution_id"])
    admin = binding._admin()
    module = manager.path / "src/devlegate/_vendor/nanoyaml"
    assert Path(git(module, "rev-parse", "--absolute-git-dir").stdout.strip()) == (
        admin / "modules/nanoyaml"
    )
    unrelated = manager.path.parent / "unrelated"
    unrelated.write_text("preserved\n")
    if dirty:
        (module / "data").write_text("dirty\n")
        (module / "new-source").write_text("untracked\n")
        (manager.path / ".gitmodules").write_text("modified configuration\n")
    admit(owner, "T-1")
    advance(owner)
    assert not manager.path.exists()
    assert not admin.exists()
    assert str(binding.intent["ref"]) not in git(
        owner.repo, "for-each-ref", "--format=%(refname)", str(binding.intent["ref"]),
    ).stdout.splitlines()
    assert unrelated.read_text() == "preserved\n"
    assert owner._state["ticket_reset"]["status"] == "queued"
    assert run_test_iteration(owner) == 0


@pytest.mark.parametrize("configuration", [
    '[submodule "other"]\n path = elsewhere\n',
    '[submodule "one"]\n path = dependency\n'
    '[submodule "two"]\n path = dependency\n',
    '[submodule "one"]\n path = dependency\n path = elsewhere\n',
    '[submodule "../escape"]\n path = dependency\n',
])
def test_reset_rejects_unproved_committed_submodule_mapping(tmp_path, configuration):
    from devlegate.clean_reset import _submodule_names

    git(tmp_path, "init")
    (tmp_path / ".gitmodules").write_text(configuration)
    git(tmp_path, "add", ".gitmodules")
    git(tmp_path, "-c", "user.name=Fixture", "-c", "user.email=f@example.com",
        "commit", "-m", "configuration")
    with pytest.raises(ValueError):
        _submodule_names(tmp_path, [Path("dependency")])


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

    def interrupted_owner(workspace, _prompt, **kwargs):
        (workspace.path / "README.md").write_text("interrupted dirty source\n")
        (workspace.path / "new-source.py").write_text("interrupted new source\n")
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
