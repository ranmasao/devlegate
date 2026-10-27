# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Daemon tests outside host, lifecycle, and worker-loss ownership."""

from _daemon_support import *  # noqa: F403,F405
from _control_support import fresh_control_fixture


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


@pytest.mark.parametrize("advance_remote", [False, True])
def test_control_sync_trace_covers_runtime_observations(
    tmp_path, monkeypatch, advance_remote
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    if advance_remote:
        publisher = control_publisher(tmp_path)
        git(publisher, "switch", "devlegate/control")
        (publisher / "trace-marker").write_text("trace\n")
        git(publisher, "add", "trace-marker")
        git(publisher, "commit", "-m", "trace control update")
        git(
            publisher,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )

    original_git = runtime._git
    trace: list[tuple[str, ...]] = []

    def traced_git(repo, *args, check=True):
        trace.append(tuple(args))
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", traced_git)
    before = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
    local_head, remote_head = engine._sync_control()
    commands = [
        ("merge-base", "--is-ancestor")
        if args[:2] == ("merge-base", "--is-ancestor")
        else args
        for args in trace
    ]

    assert remote_head == local_head
    fetch = ("fetch", "--prune", "origin", "devlegate/control")
    remote_ref = "refs/remotes/origin/devlegate/control"
    assert commands.count(fetch) == 1
    fetch_index = commands.index(fetch)
    assert ("status", "--porcelain") in commands[:fetch_index]
    assert ("show-ref", "--verify", "--quiet", remote_ref) in commands[
        fetch_index + 1 :
    ]
    assert ("show-ref", "--verify", remote_ref) in commands[fetch_index + 1 :]
    assert ("rev-parse", "--verify", f"{remote_ref}^{{commit}}") in commands[
        fetch_index + 1 :
    ]
    assert ("rev-parse", "--verify", "HEAD^{commit}") in commands[fetch_index + 1 :]
    assert not any(
        args[0] in {"push", "update-ref"} or args[:2] == ("worktree", "add")
        for args in commands
    )
    if advance_remote:
        assert local_head != before
        merge = ("merge", "--ff-only", "origin/devlegate/control")
        assert commands.count(merge) == 1
        assert commands.index(("merge-base", "--is-ancestor")) < commands.index(merge)
    else:
        assert local_head == before
        assert ("merge", "--ff-only", "origin/devlegate/control") not in commands


def test_control_init_attach_trace_covers_full_runtime_path(tmp_path, monkeypatch):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    control = engine.control_worktree
    git(engine.repo, "worktree", "remove", "--force", control)
    git(engine.repo, "branch", "-D", engine.control_branch)

    original_git = runtime._git
    trace: list[tuple[str, ...]] = []

    def traced_git(repo, *args, check=True):
        trace.append(tuple(args))
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", traced_git)
    result = engine.control_init_result()

    assert result["result"] == "attached_existing_remote"
    assert trace == [
        ("ls-remote", "origin", "refs/heads/devlegate/control"),
        ("fetch", "origin", "devlegate/control"),
        ("rev-parse", "--verify", "origin/devlegate/control^{commit}"),
        (
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/devlegate/control",
        ),
        (
            "worktree",
            "add",
            "--track",
            "-b",
            "devlegate/control",
            str(control),
            "origin/devlegate/control",
        ),
    ]


def test_control_init_bootstrap_trace_covers_full_runtime_path(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)

    original_git = runtime._git
    trace: list[tuple[str, ...]] = []

    def traced_git(repo, *args, check=True):
        trace.append(tuple(args))
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", traced_git)
    result = engine.control_init_result()
    control_head = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()

    assert result["result"] == "initialized_new_control_plane"
    branch_ref = "refs/heads/devlegate/control"
    worktree_add = (
        "worktree", "add", "--orphan", "-b", "devlegate/control",
        str(engine.control_worktree),
    )
    push = (
        "push", "--force-with-lease=refs/heads/devlegate/control:", "origin",
        f"{control_head}:{branch_ref}",
    )
    assert trace.count(worktree_add) == 1
    assert trace.count(("add", "--all")) == 1
    assert trace.count(("commit", "-m", "Initialize Devlegate control plane")) == 1
    assert trace.count(push) == 1
    assert trace.count(("fetch", "origin", "devlegate/control")) == 1
    assert trace.index(("ls-remote", "origin", branch_ref)) < trace.index(
        worktree_add
    )
    assert ("show-ref", "--verify", "--quiet", branch_ref) in trace[
        : trace.index(worktree_add)
    ]
    assert trace.index(
        ("commit", "-m", "Initialize Devlegate control plane")
    ) < trace.index(push)
    assert ("show-ref", "--verify", branch_ref) in trace[:trace.index(push)]
    assert trace.index(push) < trace.index(("fetch", "origin", "devlegate/control"))


def test_control_init_is_idempotent_and_preserves_product_checkout(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    product_branch = git(working, "branch", "--show-current").stdout.strip()
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    first = engine.control_init_result()
    second = engine.control_init_result()

    assert first["result"] == "initialized_new_control_plane"
    assert second["result"] == "already_attached"
    assert git(working, "branch", "--show-current").stdout.strip() == product_branch
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_rejects_local_only_control_ref_without_product_mutation(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    git(working, "branch", "devlegate/control", "HEAD")
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(
        DevlegateError, match="exists while the remote branch is absent"
    ):
        engine.control_init_result()

    assert not engine.control_worktree.exists()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_transport_failure_is_observation_error_without_mutation(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    git(working, "remote", "set-url", "origin", tmp_path / "unavailable.git")
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(DevlegateError, match="cannot observe remote ref"):
        engine.control_init_result()

    assert not engine.control_worktree.exists()
    assert (
        runtime._git(
            working,
            "show-ref",
            "--verify",
            "--quiet",
            "refs/heads/devlegate/control",
            check=False,
        ).returncode
        == 1
    )
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_resumes_registered_unborn_control_worktree(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True)
    git(
        working,
        "worktree",
        "add",
        "--orphan",
        "-b",
        engine.control_branch,
        engine.control_worktree,
    )
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    result = engine.control_init_result()

    assert result["result"] == "initialized_new_control_plane"
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_retries_rejected_publication_with_absent_ref_lease(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    hook = tmp_path / "remote.git/hooks/pre-receive"
    hook.write_text("#!/bin/sh\nexit 1\n")
    hook.chmod(0o755)
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(DevlegateError, match="cannot publish initial control branch"):
        engine.control_init_result()

    assert engine.control_worktree.is_dir()
    assert (
        runtime._git(
            working,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
            check=False,
        ).stdout
        == ""
    )
    hook.unlink()
    result = engine.control_init_result()

    assert result["result"] == "initialized_new_control_plane"
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_rejects_incomplete_committed_partial_skeleton(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True)
    git(
        working,
        "worktree",
        "add",
        "--orphan",
        "-b",
        engine.control_branch,
        engine.control_worktree,
    )
    incomplete = engine.control_worktree / "workflow/backlog"
    incomplete.mkdir(parents=True)
    (incomplete / ".gitkeep").touch()
    git(engine.control_worktree, "add", "--all")
    git(engine.control_worktree, "commit", "-m", "incomplete control")

    with pytest.raises(DevlegateError, match="complete workflow skeleton"):
        engine.control_init_result()

    assert (
        runtime._git(
            working,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
            check=False,
        ).stdout
        == ""
    )


def test_control_init_does_not_overwrite_ref_created_after_absence_observation(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    bare = tmp_path / "remote.git"
    competing_head = git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    original_git = runtime._git
    inserted = False

    def racing_git(repo, *args, check=True):
        nonlocal inserted
        if args[:1] == ("push",) and not inserted:
            inserted = True
            git(
                bare,
                "update-ref",
                "refs/heads/devlegate/control",
                competing_head,
                "",
            )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", racing_git)
    with pytest.raises(DevlegateError, match="cannot publish initial control branch"):
        engine.control_init_result()

    assert inserted
    assert (
        git(bare, "rev-parse", "refs/heads/devlegate/control").stdout.strip()
        == competing_head
    )
    assert git(working, "rev-parse", "HEAD").stdout.strip() == competing_head
    assert git(working, "status", "--porcelain").stdout == ""


def test_control_init_detects_remote_replacement_during_refresh(
    tmp_path, monkeypatch
):
    working, config, _state = fresh_control_fixture(tmp_path)
    bare = tmp_path / "remote.git"
    competing_head = git(bare, "rev-parse", "refs/heads/main").stdout.strip()
    monkeypatch.chdir(working)
    engine = ServiceEngine(config)
    original_git = runtime._git
    replaced = False

    def racing_git(repo, *args, check=True):
        nonlocal replaced
        if args[:1] == ("fetch",) and not replaced:
            replaced = True
            git(
                bare,
                "update-ref",
                "refs/heads/devlegate/control",
                competing_head,
            )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", racing_git)
    with pytest.raises(DevlegateError, match="changed during refresh"):
        engine.control_init_result()

    assert replaced
    assert (
        git(bare, "rev-parse", "refs/heads/devlegate/control").stdout.strip()
        == competing_head
    )
    assert git(working, "rev-parse", "HEAD").stdout.strip() == competing_head
    assert git(working, "status", "--porcelain").stdout == ""


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

def test_merge_pending_control_merge_is_not_retried_then_fresh_syncs(
    tmp_path, monkeypatch
):
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
    assert merge_calls == 1
    assert engine._state["phase"] == "merge_pending"
    assert git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip() == control

    monkeypatch.setattr(runtime, "_git", original_git)
    fresh_control, fresh_remote = engine._sync_control()
    assert fresh_control == target
    assert fresh_remote == target
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
    assert fetch_calls == 1

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
