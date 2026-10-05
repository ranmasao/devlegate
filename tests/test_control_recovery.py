# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Retry, reconciliation, recovery, and worker-identity engine semantics."""

from _control_support import *  # noqa: F403,F405

def test_accepted_integration_state_requires_idle_and_exact_identity():
    complete = {
        "ticket_id": "T-1",
        "checkpoint": "a" * 40,
        "control_head": "b" * 40,
    }
    with pytest.raises(DevlegateError, match="invalid accepted integration state"):
        runtime.ServiceEngine._validate_state_invariant(
            {"phase": "idle", "accepted_integration": {"ticket_id": "T-1"}}
        )
    with pytest.raises(DevlegateError, match="only valid while idle"):
        runtime.ServiceEngine._validate_state_invariant(
            {"phase": "agent_running", "accepted_integration": complete}
        )

@pytest.mark.parametrize(
    ("stable", "execution_remote_head", "expected_class"),
    [
        (True, None, "resume"),
        (False, None, "update-base"),
        (
            False,
            "published-checkpoint",
            "update-base with published-lineage rewrite",
        ),
    ],
)
def test_reconcile_auto_classifies_and_dispatches_from_durable_class(
    tmp_path, monkeypatch, stable, execution_remote_head, expected_class
):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = runtime.ServiceEngine(config)
    current_head = git(working, "rev-parse", "HEAD").stdout.strip()
    engine._state["reconciliation"] = {
        "status": "pending",
        "ticket_id": "T-1",
        "original_base": current_head,
        "execution_remote_head": execution_remote_head,
    }
    monkeypatch.setattr(
        engine,
        "_observe_product_generation",
        lambda _original_base: {
            "target_eligible": True,
            "local_head": current_head,
            "remote_head": current_head,
            "stable": stable,
        },
    )
    command = runtime.OperatorCommand(
        "request-1", "reconcile-auto", "fingerprint", "T-1", onto=current_head[:7]
    )
    engine._validate_reconcile_auto_admission(command)
    assert command.resolution_class == expected_class
    assert command.onto == current_head

    engine._state["reconciliation"]["resolution_class"] = command.resolution_class
    dispatched = []
    monkeypatch.setattr(
        engine,
        "_reconcile_resume_owned",
        lambda ticket_id: dispatched.append((ticket_id, "resume")) or 0,
    )
    monkeypatch.setattr(
        engine,
        "_reconcile_update_base_owned",
        lambda ticket_id, target, **kwargs: dispatched.append(
            (ticket_id, target, kwargs["rewrite_published"])
        )
        or 0,
    )
    engine._dispatch_operator_command(command, None)
    expected = ("T-1", "resume") if expected_class == "resume" else (
        "T-1",
        current_head,
        True,
    )
    assert dispatched == [expected]

def test_reconcile_assertion_rejects_short_hex_prefix(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = runtime.ServiceEngine(config)
    with pytest.raises(DevlegateError, match="at least 4 characters"):
        engine._canonical_reconcile_assertion("abc", "a" * 40)

def test_reconcile_assertion_rejects_ambiguous_commit_prefix(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    base = git(working, "rev-parse", "HEAD").stdout.strip()

    tree = git(working, "rev-parse", "HEAD^{tree}").stdout.strip()
    parent = base
    for index in range(2048):
        message = f"ambiguous prefix {index}\n"
        commit = subprocess.run(
            ["git", "commit-tree", tree, "-p", parent, "-m", message.rstrip()],
            cwd=working,
            text=True,
            capture_output=True,
            check=True,
        )
        parent = commit.stdout.strip()
    subprocess.run(
        ["git", "update-ref", "refs/heads/ambiguous-prefixes", parent],
        cwd=working,
        check=True,
    )

    reachable = git(working, "rev-list", "--all").stdout.splitlines()
    prefixes = {}
    for commit in reachable:
        prefix = commit[:4]
        prefixes.setdefault(prefix, []).append(commit)
    ambiguous = next(
        prefix for prefix, matches in prefixes.items() if len(matches) > 1
    )

    engine = runtime.ServiceEngine(config)
    with pytest.raises(DevlegateError, match="not a valid commit"):
        engine._canonical_reconcile_assertion(ambiguous, base)

def test_reconcile_auto_receipt_replays_canonical_prefix_after_restart(
    tmp_path, monkeypatch
):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = runtime.ServiceEngine(config)
    original_base = git(working, "rev-parse", "HEAD").stdout.strip()
    prefix = original_base[:7]
    engine._save_state(
        "idle",
        reconciliation={
            "status": "pending",
            "ticket_id": "T-1",
            "execution_id": "execution-1",
            "original_base": original_base,
            "observed_product": original_base,
            "worker_checkpoint": original_base,
            "product_remote_head": original_base,
            "control_head": original_base,
            "execution_branch": "devlegate/execution/T-1",
            "execution_path": str(working),
            "evidence_ref": "evidence-1",
        },
    )
    command = runtime.OperatorCommand(
        "reconcile-replay",
        "reconcile-auto",
        runtime._reconcile_auto_request_fingerprint("T-1", prefix),
        "T-1",
        onto=prefix,
    )
    engine._admit_operator_command(command)
    receipt = engine._state["mutable_receipts"]["reconcile-replay"]
    assert receipt["onto"] == original_base
    assert len(receipt["onto"]) == 40
    assert receipt["resolution_class"] == "resume"

    (working / "product-moved.txt").write_text("product moved\n")
    git(working, "add", "product-moved.txt")
    git(working, "commit", "-m", "move product after admission")
    git(working, "push", "origin", "HEAD:main")

    restarted = runtime.ServiceEngine(config)
    monkeypatch.setattr(
        restarted,
        "_observe_product_generation",
        lambda _original: pytest.fail("replayed receipt was reclassified"),
    )
    assert restarted.submit_reconcile_auto(
        "T-1", prefix, request_id="reconcile-replay"
    ) == {
        "accepted": True,
        "ticket_id": "T-1",
        "onto": original_base,
        "resolution_class": "resume",
    }
    assert restarted._operator_command is None

def test_worker_gate_clears_bound_state_and_bound_state_requires_control_head(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    head = git(working, "rev-parse", "HEAD").stdout.strip()
    control_head = git(
        next((state / "worktrees").glob("*/control")), "rev-parse", "HEAD"
    ).stdout.strip()

    with pytest.raises(DevlegateError, match="control revision identity"):
        devlegate._save_state(
            "agent_pending",
            local_head=head,
            remote_head=head,
            changed_paths="",
            selected_ticket_id="T-1",
            selected_ticket_body="work",
        )
    devlegate._save_state(
        "agent_pending",
        local_head=head,
        remote_head=head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work",
        execution_ticket_id="T-1",
        execution_base_head=head,
        execution_control_head=control_head,
        execution_branch="devlegate/work/T-1",
        execution_path=str(devlegate.execution_worktree_root / "work" / "T-1"),
        execution_id="attempt-1",
        execution_remote_head=None,
    )
    assert devlegate._state["control_head"] == control_head

def test_once_worker_completes_one_lifecycle_attempt(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["phase"] == "idle"
    assert "selected_ticket_id" not in payload
    assert payload["handled_control_head"]
    assert "execution failed" in result.stdout
    control = next((state / "worktrees").glob("*/control"))
    assert list((control / "executions/T-1").glob("*.json"))

def test_lifecycle_reconciles_compatible_control_descendant_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        run_test_iteration(devlegate)
    payload = state_payload(state)
    original_control = payload["execution_control_head"]

    architect = tmp_path / "architect"
    git(tmp_path, "clone", tmp_path / "remote.git", architect)
    git(architect, "config", "user.email", "test@example.com")
    git(architect, "config", "user.name", "Test User")
    git(architect, "switch", "-c", "architect-control", "origin/devlegate/control")
    (architect / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Future"\n---\nfuture\n'
    )
    git(architect, "add", "-A")
    git(architect, "commit", "-m", "add future ticket")
    descendant = git(architect, "rev-parse", "HEAD").stdout.strip()
    git(architect, "push", "origin", "HEAD:refs/heads/devlegate/control")
    control = next((state / "worktrees").glob("*/control"))

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("lifecycle recovery launched a worker"),
    )
    assert run_test_iteration(recovered) == 1
    assert recovered._state["phase"] == "idle"
    assert (control / "kanban/todo/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()
    lifecycle = git(control, "rev-parse", "HEAD").stdout.strip()
    assert git(control, "rev-parse", f"{lifecycle}^").stdout.strip() == descendant
    report_path = next((control / "executions/T-1").glob("*.json"))
    report = json.loads(report_path.read_text())
    assert report["control_head"] == original_control

def test_lifecycle_recovery_replays_after_fast_forwarded_validation_failure(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        run_test_iteration(devlegate)
    original_control = devlegate._state["execution_control_head"]

    architect = tmp_path / "architect"
    git(tmp_path, "clone", tmp_path / "remote.git", architect)
    git(architect, "config", "user.email", "test@example.com")
    git(architect, "config", "user.name", "Test User")
    git(architect, "switch", "-c", "architect-control", "origin/devlegate/control")
    (architect / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Future"\n---\nfuture\n'
    )
    git(architect, "add", "-A")
    git(architect, "commit", "-m", "add future ticket")
    descendant = git(architect, "rev-parse", "HEAD").stdout.strip()
    git(architect, "push", "origin", "HEAD:refs/heads/devlegate/control")

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered,
        "_apply_lifecycle_once",
        lambda _report, **_kwargs: (_ for _ in ()).throw(
            WorkflowBlockedError("simulated validation failure")
        ),
    )
    with pytest.raises(WorkflowBlockedError, match="simulated validation failure"):
        run_test_iteration(recovered)
    control = next((state / "worktrees").glob("*/control"))
    assert git(control, "rev-parse", "HEAD").stdout.strip() == descendant

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("lifecycle recovery launched a worker"),
    )
    assert run_test_iteration(recovered) == 1
    assert recovered._state["phase"] == "idle"
    lifecycle = git(control, "rev-parse", "HEAD").stdout.strip()
    assert git(control, "rev-parse", f"{lifecycle}^").stdout.strip() == descendant
    report_path = next((control / "executions/T-1").glob("*.json"))
    assert json.loads(report_path.read_text())["control_head"] == original_control

def test_legacy_lifecycle_commit_replays_on_control_descendant_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        run_test_iteration(devlegate)
    pending = devlegate._state["pending_execution_report"]
    report = ExecutionReport.from_dict(pending)
    original_apply = Devlegate._apply_lifecycle_once
    original_apply(devlegate, report)
    legacy_state = dict(devlegate._state)
    legacy_state.pop("pending_execution_report")
    devlegate._runtime_store.replace(legacy_state)

    architect = tmp_path / "architect"
    git(tmp_path, "clone", tmp_path / "remote.git", architect)
    git(architect, "config", "user.email", "test@example.com")
    git(architect, "config", "user.name", "Test User")
    git(architect, "switch", "-c", "architect-control", "origin/devlegate/control")
    (architect / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Future"\n---\nfuture\n'
    )
    git(architect, "add", "-A")
    git(architect, "commit", "-m", "add future ticket")
    git(architect, "push", "origin", "HEAD:refs/heads/devlegate/control")

    control = next((state / "worktrees").glob("*/control"))
    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("legacy lifecycle recovery launched a worker"),
    )
    assert run_test_iteration(recovered) == 1
    assert recovered._state["phase"] == "idle"
    assert (control / "kanban/todo/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()
    assert ExecutionReportStore(control).read("T-1", report.execution_id) == report

def test_execution_start_head_is_persisted_before_worker(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base_head = git(working, "rev-parse", "HEAD").stdout.strip()

    def worker(workspace, _prompt, **_kwargs):
        assert workspace.head == base_head
        assert devlegate._state["execution_base_head"] == base_head
        assert devlegate._state["execution_start_head"] == base_head
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    assert state_payload(state)["phase"] == "idle"

def test_checkpoint_rejects_worker_created_history_and_preserves_it(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    (workspace.path / "worker-commit.txt").write_text("worker history\n")
    git(workspace.path, "add", "worker-commit.txt")
    git(workspace.path, "commit", "-m", "worker-owned commit")
    worker_head = git(workspace.path, "rev-parse", "HEAD").stdout.strip()

    with pytest.raises(ExecutionWorkspaceError, match="history changed"):
        manager.checkpoint(workspace, "attempt-1")

    assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == worker_head
    assert (workspace.path / "worker-commit.txt").read_text() == "worker history\n"
    assert (
        "Devlegate checkpoint"
        not in git(workspace.path, "log", "-1", "--pretty=%s").stdout
    )
    assert not (control / "executions").exists()

def test_worker_created_commit_cannot_reach_lifecycle_or_publication(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "worker-commit.txt").write_text("worker history\n")
        git(workspace.path, "add", "worker-commit.txt")
        git(workspace.path, "commit", "-m", "worker-owned commit")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        run_test_iteration(devlegate)

    execution = next((state / "worktrees").glob("*/work/T-1"))
    control = next((state / "worktrees").glob("*/control"))
    assert git(execution, "log", "-1", "--pretty=%s").stdout.strip() == (
        "worker-owned commit"
    )
    assert not (control / "kanban/review/T-1.md").exists()
    assert (control / "kanban/todo/T-1.md").exists()
    assert not list((control / "executions").glob("T-1/*.json"))
    assert not git(
        control, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
    ).stdout.strip()

def test_reconcile_resume_race_stays_unresolved_before_lifecycle(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    control = next((state / "worktrees").glob("*/control"))
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        devlegate.repo, devlegate.execution_worktree_root, "T-1"
    )
    workspace = manager.prepare(base)
    (workspace.path / "predecessor.txt").write_text("E\n")
    git(workspace.path, "add", "predecessor.txt")
    git(workspace.path, "commit", "-m", "predecessor")
    predecessor = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "origin",
        f"{predecessor}:refs/heads/{manager.branch}",
    )
    tree = git(workspace.path, "rev-parse", f"{predecessor}^{{tree}}").stdout.strip()
    external = subprocess.run(
        [
            "git",
            "-C",
            str(workspace.path),
            "commit-tree",
            tree,
            "-p",
            predecessor,
        ],
        input="external\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    git(workspace.path, "reset", "--hard", external)
    (workspace.path / "implementation.txt").write_text("worker\n")
    git(workspace.path, "add", "implementation.txt")
    git(workspace.path, "commit", "-m", "checkpoint")
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    report = build_execution_report(
        execution_id="execution-1",
        ticket_id="T-1",
        code_base_head=base,
        control_head=control_head,
        execution_branch=manager.branch,
        execution_path=str(manager.path),
        workspace_head=checkpoint,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        ),
    )
    evidence_ref = "refs/devlegate/reconciliation/T-1/execution-1"
    git(workspace.path, "update-ref", evidence_ref, checkpoint)
    reconciliation = {
        "status": "pending",
        "reason": "product-observation-unsafe",
        "resolution": None,
        "ticket_id": "T-1",
        "execution_id": "execution-1",
        "original_base": base,
        "observed_product": base,
        "worker_checkpoint": checkpoint,
        "execution_remote_head": predecessor,
        "product_remote_head": base,
        "control_head": control_head,
        "execution_branch": manager.branch,
        "execution_path": str(manager.path),
        "evidence_ref": evidence_ref,
        "execution_report": report.as_dict(),
    }
    devlegate._save_state(
        "idle",
        local_head=base,
        remote_head=base,
        execution_ticket_id="T-1",
        execution_base_head=base,
        execution_control_head=control_head,
        execution_branch=manager.branch,
        execution_path=str(manager.path),
        execution_id="execution-1",
        execution_remote_head=predecessor,
        reconciliation=reconciliation,
    )
    original_git = runtime._git
    raced = False

    def racing_git(repo, *args, check=True):
        nonlocal raced
        if (
            not raced
            and args[:1] == ("push",)
            and any(f"refs/heads/{manager.branch}" in arg for arg in args)
        ):
            raced = True
            git(
                workspace.path,
                "push",
                "origin",
                f"{external}:refs/heads/{manager.branch}",
            )
        return original_git(repo, *args, check=check)

    monkeypatch.setattr(runtime, "_git", racing_git)
    with pytest.raises(DevlegateError, match="remote changed during publication"):
        devlegate.reconcile_resume("T-1")
    assert raced
    assert devlegate._state["reconciliation"]["status"] == "resolving"
    assert not (control / "kanban/review/T-1.md").exists()
    assert (
        git(
            workspace.path, "ls-remote", "origin", f"refs/heads/{manager.branch}"
        ).stdout.split()[0]
        == external
    )

def test_completed_worker_is_checkpointed_published_and_submitted_to_review(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    assert calls == 1
    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (execution / "implementation.txt").read_text() == "worker change\n"
    assert git(execution, "log", "-1", "--pretty=%s").stdout.strip() == (
        "T-1: Control ticket"
    )
    message = git(execution, "log", "-1", "--format=%B").stdout
    assert "Worker result:\nimplemented" in message
    assert "Devlegate-Execution:" in message
    remote_control = git(
        control, "ls-remote", "origin", "refs/heads/devlegate/control"
    ).stdout.split()[0]
    assert remote_control == git(control, "rev-parse", "HEAD").stdout.strip()
    control_message = git(control, "log", "-1", "--format=%B").stdout
    assert git(control, "log", "-1", "--pretty=%s").stdout.strip() == (
        "T-1: Control ticket (completed)"
    )
    assert "Worker result:\nimplemented" in control_message
    assert "Devlegate-Execution:" in control_message
    assert not (control / "kanban/todo/T-1.md").exists()
    assert (control / "kanban/review/T-1.md").is_file()
    reports = list((control / "executions/T-1").glob("*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["conclusion"] == "completed"
    assert (
        git(working, "rev-parse", "HEAD").stdout.strip()
        == git(working, "rev-parse", "origin/main").stdout.strip()
    )
    assert not (working / "implementation.txt").exists()

def test_checkpointing_recovery_recreates_missing_checkpoint_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    original_checkpoint = ExecutionWorkspaceManager.checkpoint
    failed = False

    def fail_once(manager, workspace, execution_id, **kwargs):
        nonlocal failed
        if not failed:
            failed = True
            raise ExecutionWorkspaceError("simulated checkpoint crash")
        return original_checkpoint(manager, workspace, execution_id, **kwargs)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(ExecutionWorkspaceManager, "checkpoint", fail_once)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        run_test_iteration(devlegate)
    assert devlegate._state["execution_stage"] == "checkpointing"
    assert devlegate._state["pending_execution_report"]["workspace_head"] is None

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("checkpoint recovery reran worker"),
    )
    assert run_test_iteration(recovered) == 0
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (execution / "partial.txt").read_text() == "preserve\n"

@pytest.mark.parametrize("dirty_after_checkpoint", [False, True])
def test_checkpointing_recovery_recognizes_existing_exact_checkpoint(
    tmp_path, monkeypatch, dirty_after_checkpoint
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    original_save = devlegate._save_state
    crashed = False

    def crash_before_post(phase, **fields):
        nonlocal crashed
        if (
            phase == "agent_running"
            and fields.get("execution_stage") == "post-checkpoint"
        ):
            crashed = True
            raise RuntimeError("simulated state crash")
        original_save(phase, **fields)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(devlegate, "_save_state", crash_before_post)
    with pytest.raises(RuntimeError, match="simulated state crash"):
        run_test_iteration(devlegate)
    assert crashed
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint_count = git(
        execution, "log", "--all", "--format=%s"
    ).stdout.splitlines().count("T-1: Control ticket")
    assert checkpoint_count == 1

    if dirty_after_checkpoint:
        (execution / "late-change.txt").write_text("must remain\n")
        recovered = Devlegate(config)
        monkeypatch.setattr(
            recovered._workers,
            "run",
            lambda *_args: pytest.fail("dirty checkpoint recovery reran worker"),
        )
        with pytest.raises(DevlegateError, match="ambiguous checkpoint history"):
            run_test_iteration(recovered)
        assert (execution / "late-change.txt").read_text() == "must remain\n"
        assert not git(
            execution, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
        ).stdout.strip()
        return

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("checkpoint recovery reran worker"),
    )
    assert run_test_iteration(recovered) == 0
    assert git(execution, "log", "--all", "--format=%s").stdout.splitlines().count(
        "T-1: Control ticket"
    ) == 1

def test_publishing_recovery_pushes_checkpoint_without_worker(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("simulated publication crash")
        ),
    )
    with pytest.raises(DevlegateError, match="publication failed"):
        run_test_iteration(devlegate)
    assert devlegate._state["execution_stage"] == "publishing"

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("publication recovery reran worker"),
    )
    assert run_test_iteration(recovered) == 0
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (
        git(
            execution,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/work/T-1",
        ).stdout.split()[0]
        == git(execution, "rev-parse", "HEAD").stdout.strip()
    )

def test_service_recovers_publication_failure_without_rerunning_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    devlegate.poll_interval = "1"
    worker_calls = 0
    publication_calls = 0
    first_publication = threading.Event()
    second_publication = threading.Event()

    def worker(workspace, _prompt, **_kwargs):
        nonlocal worker_calls
        worker_calls += 1
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    original_publish = devlegate._publish_execution_branch

    def publish(*args):
        nonlocal publication_calls
        publication_calls += 1
        if publication_calls == 1:
            first_publication.set()
            raise DevlegateError("simulated publication outage")
        if publication_calls == 2:
            second_publication.set()
        return original_publish(*args)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(devlegate, "_publish_execution_branch", publish)
    stop_event = threading.Event()
    thread = threading.Thread(target=lambda: devlegate.serve(stop_event), daemon=True)
    thread.start()

    assert first_publication.wait(10)
    devlegate.wake()
    assert second_publication.wait(10)
    stop_event.set()
    devlegate.wake()
    thread.join(timeout=5)

    assert not thread.is_alive()
    assert worker_calls == 1
    assert publication_calls == 2
    assert devlegate._state["phase"] == "idle"
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()

def test_checkpoint_recovery_refuses_stale_product_before_side_effects(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    original_checkpoint = ExecutionWorkspaceManager.checkpoint
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "checkpoint",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            ExecutionWorkspaceError("simulated checkpoint crash")
        ),
    )
    monkeypatch.setattr(devlegate._workers, "run", worker)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        run_test_iteration(devlegate)
    monkeypatch.setattr(ExecutionWorkspaceManager, "checkpoint", original_checkpoint)

    publisher = tmp_path / "publisher"
    git(tmp_path, "clone", "-b", "main", tmp_path / "remote.git", publisher)
    git(publisher, "config", "user.email", "test@example.com")
    git(publisher, "config", "user.name", "Test User")
    (publisher / "product-change.txt").write_text("advanced\n")
    git(publisher, "add", "product-change.txt")
    git(publisher, "commit", "-m", "advance product")
    git(publisher, "push", "origin", "HEAD:main")

    execution = next((state / "worktrees").glob("*/work/T-1"))
    before = git(execution, "rev-parse", "HEAD").stdout.strip()
    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("stale recovery launched worker"),
    )
    with pytest.raises(DevlegateError, match="product checkout or remote changed"):
        run_test_iteration(recovered)
    assert recovered._state["execution_stage"] == "checkpointing"
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == before
    assert not git(
        execution, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
    ).stdout.strip()

def test_post_publication_recovery_replays_lifecycle_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    original_save = devlegate._save_state
    crashed = False

    def crash_after_publication(phase, **fields):
        nonlocal crashed
        if (
            phase == "agent_running"
            and fields.get("execution_stage") == "post-publication"
        ):
            crashed = True
            raise RuntimeError("simulated state crash")
        original_save(phase, **fields)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(devlegate, "_save_state", crash_after_publication)
    with pytest.raises(RuntimeError, match="simulated state crash"):
        run_test_iteration(devlegate)
    assert crashed
    assert devlegate._state["execution_stage"] == "publishing"

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda *_args: pytest.fail("post-publication recovery reran worker"),
    )
    assert run_test_iteration(recovered) == 0
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()

def test_failed_execution_is_suppressed_until_explicit_retry(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace.path / "retry.txt").write_text("retried\n")
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "done", (), ()), None
            )
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    assert run_test_iteration(devlegate) == 0
    assert calls == 1
    assert devlegate.retry("T-1") == 0
    assert calls == 2

    control = next((state / "worktrees").glob("*/control"))
    reports = list((control / "executions/T-1").glob("*.json"))
    assert len(reports) == 2
    assert (control / "kanban/review/T-1.md").exists()
    assert not (control / "kanban/todo/T-1.md").exists()

def test_post_worker_integrity_failure_is_persisted_without_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    control = next((state / "worktrees").glob("*/control"))

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "useful-change.txt").write_text("keep this\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule T is dirty")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        run_test_iteration(devlegate)

    assert devlegate._state["phase"] == "idle"
    failure = devlegate._collect_status_attempt(
        allow_workflow_blocked=True
    ).failed_executions[0]
    assert failure.ticket_id == "T-1"
    assert failure.retryable
    assert "post-worker dependency integrity failure" in failure.reason
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (execution / "useful-change.txt").read_text() == "keep this\n"
    assert not (control / "kanban/review/T-1.md").exists()
    assert list((control / "executions/T-1").glob("*.json")) == []
    assert git(execution, "log", "--oneline").stdout.count("Devlegate checkpoint") == 0

@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process identity")
def test_check_does_not_treat_orphan_worker_as_runtime_owner(
    tmp_path, monkeypatch, capsys
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(devlegate, state)
    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        identity = _capture_worker_identity(helper, devlegate._state["execution_id"])
        devlegate._save_state(
            "agent_running",
            execution_stage="worker-running",
            worker_identity=identity.as_dict(),
        )
        assert devlegate.check() == 1
        output = capsys.readouterr().out
        assert "mutation-owner reconciliation is required" in output
        assert "\nReady." not in output
        assert workspace.path.is_dir()
    finally:
        helper.kill()
        helper.wait(timeout=10)

def test_engine_reconciliation_and_update_base_resumes(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    observed = []

    def worker(workspace, prompt, **_kwargs):
        observed.append((workspace.path, prompt, devlegate._state["execution_id"]))
        (workspace.path / "implementation.txt").write_text("worker work\n")
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert reconciliation["original_base"] != reconciliation["observed_product"]
    assert reconciliation["worker_checkpoint"]
    status = devlegate.status_view()
    assert status.reconciliation["original_base"] == reconciliation["original_base"]
    assert (
        status.reconciliation["worker_checkpoint"]
        == reconciliation["worker_checkpoint"]
    )
    assert not git(
        next((state / "worktrees").glob("*/work/T-1")),
        "ls-remote",
        "origin",
        "refs/heads/devlegate/work/T-1",
    ).stdout.strip()
    old_id = reconciliation["execution_id"]
    evidence = reconciliation["evidence_ref"]
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (
        git(execution, "rev-parse", evidence).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )

    git(working, "pull", "--ff-only", "origin", "main")
    assert (
        devlegate.reconcile_update_base("T-1", reconciliation["observed_product"]) == 0
    )
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert devlegate._state["resume_required"]["status"] == "required"
    assert git(execution, "rev-parse", "HEAD^").returncode == 0

    resumed = Devlegate(config)
    resumed_ids = []
    prompts = []

    def resumed_worker(workspace, prompt, **_kwargs):
        resumed_ids.append(resumed._state["execution_id"])
        prompts.append(prompt)
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "validated", (), ()), None
        )

    monkeypatch.setattr(resumed._workers, "run", resumed_worker)
    assert run_test_iteration(resumed) == 0
    assert resumed_ids[0] != old_id
    assert "Continue the existing implementation" in prompts[0]
    assert (execution / "implementation.txt").read_text() == "worker work\n"
    assert (
        next((state / "worktrees").glob("*/control")) / "kanban/review/T-1.md"
    ).is_file()

def test_zero_delta_retry_state_rejects_unknown_or_invalid_status():
    with pytest.raises(DevlegateError, match="automatic retry status"):
        runtime.ServiceEngine._validate_state_invariant(
            {"phase": "idle", "automatic_retry": {"status": "unknown"}}
        )

def test_zero_delta_checkpoint_recovery_observes_drift_after_restart(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    alternate = tmp_path / "checkpoint-remote"

    def crash_after_checkpoint(_admitted_head):
        git(working, "worktree", "add", "--detach", alternate, base)
        git(alternate, "config", "user.email", "test@example.com")
        git(alternate, "config", "user.name", "Test User")
        (alternate / "remote-change.txt").write_text("product B\n")
        git(alternate, "add", "remote-change.txt")
        git(alternate, "commit", "-m", "advance remote product")
        git(alternate, "push", "origin", "HEAD:main")
        git(working, "worktree", "remove", "--force", alternate)
        raise RuntimeError("simulated stop after checkpoint")

    monkeypatch.setattr(engine, "_observe_product_generation", crash_after_checkpoint)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        ),
    )
    with pytest.raises(RuntimeError, match="after checkpoint"):
        run_test_iteration(engine)
    assert engine._state["execution_stage"] == "post-checkpoint"
    assert engine._state.get("automatic_retry") is None
    assert engine._state["pending_execution_report"]["workspace_head"] == base

    restarted = Devlegate(config)
    fresh_ids = []

    def fresh_worker(_workspace, _prompt, **_kwargs):
        fresh_ids.append(restarted._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["automatic_retry"]["status"] == "pending_admission"
    assert fresh_ids == []
    assert run_test_iteration(restarted) == 0
    assert len(fresh_ids) == 1

@pytest.mark.parametrize("product_changed", [False, True])
def test_post_checkpoint_unobservable_remote_resumes_after_observation_returns(
    tmp_path, monkeypatch, product_changed
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    original_observer = engine._observe_product_generation

    def unavailable(_admitted_head):
        return {
            "classification": "unobservable",
            "stable": False,
            "branch": "main",
            "local_head": base,
            "remote_head": "",
            "dirty": False,
            "target_eligible": False,
            "reason": "product remote could not be observed",
        }

    def worker(workspace, *_args, **_kwargs):
        (workspace.path / "worker-progress.txt").write_text("worker progress\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "checkpointed work", (), ()), None
        )

    monkeypatch.setattr(engine, "_observe_product_generation", unavailable)
    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    retained = state_payload(state)
    checkpoint = retained["pending_execution_report"]["workspace_head"]
    execution_id = retained["execution_id"]
    assert checkpoint != base
    assert retained["execution_stage"] == "post-checkpoint"
    assert "reconciliation" not in retained

    changed_head = base
    if product_changed:
        alternate = tmp_path / "restored-product"
        git(working, "worktree", "add", "--detach", alternate, base)
        git(alternate, "config", "user.email", "test@example.com")
        git(alternate, "config", "user.name", "Test User")
        (alternate / "restored-product.txt").write_text("product B\n")
        git(alternate, "add", "restored-product.txt")
        git(alternate, "commit", "-m", "advance restored product")
        git(alternate, "push", "origin", "HEAD:main")
        changed_head = git(alternate, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setattr(engine, "_observe_product_generation", original_observer)
    if not product_changed:
        assert run_test_iteration(engine) == 0
        recovered = state_payload(state)
        assert recovered["execution_id"] == execution_id
        assert recovered.get("reconciliation") is None
        assert recovered["phase"] == "idle"
        control = next((state / "worktrees").glob("*/control"))
        assert not (control / "kanban/todo/T-1.md").exists()
    else:
        with pytest.raises(WorkflowBlockedError, match="reconciliation required"):
            run_test_iteration(engine)
        recovered = state_payload(state)
        reconciliation = recovered["reconciliation"]
        assert recovered["execution_id"] == execution_id
        assert reconciliation["worker_checkpoint"] == checkpoint
        assert reconciliation["execution_report"]["execution_id"] == execution_id
        assert reconciliation["product_remote_head"] == changed_head
        assert reconciliation["product_remote_head"]
        assert reconciliation["original_base"] == base

def test_zero_delta_without_product_drift_does_not_retry(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    calls = []

    def worker(_workspace, _prompt, **_kwargs):
        calls.append(engine._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    assert len(calls) == 1
    assert engine._state.get("automatic_retry") is None
    assert engine._state.get("reconciliation") is None
    control = next((state / "worktrees").glob("*/control"))
    assert len(list((control / "executions/T-1").glob("*.json"))) == 1

def test_zero_delta_retry_replay_does_not_double_admit(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    pending = engine._state["automatic_retry"]
    restarted = Devlegate(config)
    calls = []

    def fresh_worker(_workspace, _prompt, **_kwargs):
        calls.append(restarted._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
    assert run_test_iteration(restarted) == 0
    assert calls == [pending["fresh_execution_id"]]

    replay = Devlegate(config)
    monkeypatch.setattr(
        replay._workers,
        "run",
        lambda *_args, **_kwargs: pytest.fail("replay admitted a duplicate"),
    )
    assert run_test_iteration(replay) == 0
    assert replay._state["last_automatic_retry"]["fresh_execution_id"] == calls[0]

def test_zero_delta_retry_replays_after_historical_report_persistence(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    original_retire = engine._retire_zero_delta_execution

    def crash_before_retirement(report):
        monkeypatch.setattr(
            engine, "_retire_zero_delta_execution", original_retire
        )
        raise RuntimeError("simulated crash before retirement")

    monkeypatch.setattr(engine, "_retire_zero_delta_execution", crash_before_retirement)
    with pytest.raises(RuntimeError, match="before retirement"):
        run_test_iteration(engine)
    pending = engine._state["automatic_retry"]

    restarted = Devlegate(config)
    ids = []

    def fresh_worker(_workspace, _prompt, **_kwargs):
        ids.append(restarted._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
    assert run_test_iteration(restarted) == 0
    assert ids == []
    assert run_test_iteration(restarted) == 0
    assert len(ids) == 1
    assert ids[0] != pending["stale_execution_id"]
    control = next((state / "worktrees").glob("*/control"))
    assert len(list((control / "executions/T-1").glob("*.json"))) == 2

def test_zero_delta_retry_reuses_admitted_id_after_worker_launch_crash(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)

    def stale_worker(_workspace, _prompt, **_kwargs):
        (working / "product-change.txt").write_text("product B\n")
        git(working, "add", "product-change.txt")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", stale_worker)
    assert run_test_iteration(engine) == 0
    pending = engine._state["automatic_retry"]

    restarted = Devlegate(config)
    monkeypatch.setattr(
        restarted._workers, "run", lambda *_args, **_kwargs: (_ for _ in ()).throw(
            WorkerAdmissionClosed()
        )
    )
    assert run_test_iteration(restarted) == 0
    assert restarted._state["automatic_retry"]["status"] == "admitted"
    assert restarted._state["automatic_retry"]["fresh_execution_id"] == (
        pending["fresh_execution_id"]
    )

    replay = Devlegate(config)
    ids = []

    def resumed_worker(_workspace, _prompt, **_kwargs):
        ids.append(replay._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(replay._workers, "run", resumed_worker)
    assert run_test_iteration(replay) == 0
    assert ids == [pending["fresh_execution_id"]]

def test_engine_reconciliation_resume_reuses_retained_report_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []

    def worker(workspace, _prompt, **_kwargs):
        calls.append(True)
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    checkpoint = reconciliation["worker_checkpoint"]
    assert reconciliation["execution_report"]["workspace_head"] == checkpoint
    (working / "dirty-product.txt").unlink()
    assert devlegate.reconcile_resume("T-1") == 0
    assert calls == [True]
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert devlegate._state["reconciliation"]["resolution"] == "resume"

def test_engine_legacy_reconciliation_resume_publishes_and_schedules_resume(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = dict(devlegate._state["reconciliation"])
    old_id = reconciliation["execution_id"]
    checkpoint = reconciliation["worker_checkpoint"]
    reconciliation.pop("execution_report")
    devlegate._save_state("idle", reconciliation=reconciliation)
    (working / "dirty-product.txt").unlink()
    assert devlegate.reconcile_resume("T-1") == 0
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert devlegate._state["resume_required"] == {
        "ticket_id": "T-1",
        "status": "required",
    }
    resumed_ids = []

    def resumed_worker(workspace, prompt, **_kwargs):
        resumed_ids.append(devlegate._state["execution_id"])
        assert "Continue the existing implementation" in prompt
        assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == checkpoint
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "validated", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", resumed_worker)
    assert run_test_iteration(devlegate) == 0
    assert resumed_ids and resumed_ids[0] != old_id

@pytest.mark.parametrize(
    "mutation",
    [
        "divergent remote",
        "changed evidence",
        "dirty workspace",
        "changed workspace head",
        "changed control",
        "wrong product local head",
        "wrong product remote head",
        "dirty product",
    ],
)
def test_reconcile_resume_rejects_unsafe_state(tmp_path, monkeypatch, mutation):
    devlegate, working, state = _pending_resume_fixture(tmp_path, monkeypatch)
    reconciliation = devlegate._state["reconciliation"]
    execution = next((state / "worktrees").glob("*/work/T-1"))
    expected_remote = ""
    if mutation == "divergent remote":
        git(
            execution,
            "push",
            "origin",
            f"{reconciliation['original_base']}:refs/heads/{reconciliation['execution_branch']}",
        )
        expected_remote = reconciliation["original_base"]
    elif mutation == "changed evidence":
        git(
            working,
            "update-ref",
            reconciliation["evidence_ref"],
            reconciliation["original_base"],
        )
    elif mutation == "dirty workspace":
        (execution / "unexpected.txt").write_text("dirty\n")
    elif mutation == "changed workspace head":
        git(execution, "reset", "--hard", reconciliation["original_base"])
    elif mutation == "changed control":
        control = next((state / "worktrees").glob("*/control"))
        (control / "changed.txt").write_text("changed\n")
        git(control, "add", "changed.txt")
        git(control, "commit", "-m", "foreign change")
    elif mutation == "wrong product local head":
        (working / "product.txt").write_text("changed\n")
        git(working, "add", "product.txt")
        git(working, "commit", "-m", "foreign product change")
    elif mutation == "wrong product remote head":
        changed = git(working, "rev-parse", "HEAD^{tree}").stdout.strip()
        moved = subprocess.run(
            [
                "git",
                "-C",
                str(working),
                "commit-tree",
                changed,
                "-p",
                reconciliation["original_base"],
            ],
            input="remote change\n",
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        git(working, "push", "origin", f"{moved}:refs/heads/main")
    elif mutation == "dirty product":
        (working / "dirty-again.txt").write_text("dirty\n")
    with pytest.raises(DevlegateError):
        devlegate.reconcile_resume("T-1")
    assert devlegate._state["reconciliation"]["status"] != "resolved"
    observed_remote = git(
        execution,
        "ls-remote",
        "origin",
        f"refs/heads/{reconciliation['execution_branch']}",
    ).stdout.split()
    assert (observed_remote[0] if observed_remote else "") == expected_remote

@pytest.mark.parametrize(
    "field",
    [
        "ticket_id",
        "execution_id",
        "code_base_head",
        "control_head",
        "execution_branch",
        "execution_path",
        "workspace_head",
    ],
)
def test_reconcile_resume_rejects_retained_report_binding_mismatch(
    tmp_path, monkeypatch, field
):
    devlegate, _working, _state = _pending_resume_fixture(tmp_path, monkeypatch)
    payload = copy.deepcopy(devlegate._state)
    report = payload["reconciliation"]["execution_report"]
    report[field] = "mismatch"
    with pytest.raises(DevlegateError, match="retained reconciliation report"):
        runtime.ServiceEngine._validate_state_invariant(payload)

def test_reconcile_resume_rejects_malformed_retained_report(tmp_path, monkeypatch):
    devlegate, _working, _state = _pending_resume_fixture(tmp_path, monkeypatch)
    payload = copy.deepcopy(devlegate._state)
    payload["reconciliation"]["execution_report"] = {"invalid": True}
    with pytest.raises(DevlegateError, match="retained reconciliation report"):
        runtime.ServiceEngine._validate_state_invariant(payload)

def test_engine_reconcile_update_base_conflict_preserves_original_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    (working / "product-seed.txt").write_text("seed\n")
    git(working, "add", "product-seed.txt")
    git(working, "commit", "-m", "seed product base")
    git(working, "push", "origin", "HEAD:main")
    publisher = tmp_path / "publisher"
    git(tmp_path, "clone", "-b", "main", tmp_path / "remote.git", publisher)
    git(publisher, "config", "user.email", "test@example.com")
    git(publisher, "config", "user.name", "Test User")
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "shared.txt").write_text("worker\n")
        parent = git(working, "rev-parse", "HEAD^").stdout.strip()
        git(publisher, "reset", "--hard", parent)
        (publisher / "shared.txt").write_text("product\n")
        git(publisher, "add", "shared.txt")
        git(publisher, "commit", "-m", "advance product")
        git(publisher, "push", "--force", "origin", "HEAD:main")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    execution = next((state / "worktrees").glob("*/work/T-1"))
    original_head = git(execution, "rev-parse", "HEAD").stdout.strip()
    original_parent = git(
        execution, "rev-parse", f"{reconciliation['original_base']}^"
    ).stdout.strip()
    target_parent = git(
        working, "rev-parse", f"{reconciliation['observed_product']}^"
    ).stdout.strip()
    assert target_parent == original_parent
    git(working, "reset", "--hard", reconciliation["observed_product"])

    with pytest.raises(DevlegateError, match="manual/advanced reconciliation"):
        devlegate.reconcile_update_base("T-1", reconciliation["observed_product"])
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == original_head
    assert not git(execution, "status", "--porcelain").stdout
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == original_head
    )
    assert devlegate._state["reconciliation"]["status"] == "pending"

def test_engine_reconcile_update_base_same_parent_replacement_succeeds(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    (working / "product-seed.txt").write_text("seed\n")
    git(working, "add", "product-seed.txt")
    git(working, "commit", "-m", "seed product base")
    git(working, "push", "origin", "HEAD:main")
    publisher = tmp_path / "publisher"
    git(tmp_path, "clone", "-b", "main", tmp_path / "remote.git", publisher)
    git(publisher, "config", "user.email", "test@example.com")
    git(publisher, "config", "user.name", "Test User")
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "worker.txt").write_text("worker\n")
        parent = git(working, "rev-parse", "HEAD^").stdout.strip()
        git(publisher, "reset", "--hard", parent)
        (publisher / "product.txt").write_text("replacement\n")
        git(publisher, "add", "product.txt")
        git(publisher, "commit", "-m", "replace product tip")
        git(publisher, "push", "--force", "origin", "HEAD:main")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    evidence = reconciliation["evidence_ref"]
    execution = next((state / "worktrees").glob("*/work/T-1"))
    original_checkpoint = reconciliation["worker_checkpoint"]
    target = reconciliation["observed_product"]
    assert git(
        execution, "rev-parse", f"{reconciliation['original_base']}^"
    ).stdout.strip() == git(working, "rev-parse", f"{target}^").stdout.strip()
    git(working, "reset", "--hard", target)

    assert devlegate.reconcile_update_base("T-1", target) == 0
    resolved = devlegate._state["reconciliation"]
    assert resolved["status"] == "resolved"
    assert resolved["resolution"] == "update-base"
    assert resolved["effective_base"] == target
    assert git(execution, "rev-parse", "HEAD^").stdout.strip() == target
    assert git(working, "rev-parse", evidence).stdout.strip() == original_checkpoint
    assert resolved["evidence_ref"] != evidence
    assert git(execution, "rev-parse", resolved["evidence_ref"]).stdout.strip() == git(
        execution, "rev-parse", "HEAD"
    ).stdout.strip()

def test_dirty_product_after_worker_is_reconciliation_pending_without_mutation(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "uncommitted-product.txt").write_text("operator work\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert reconciliation["product_dirty"] is True
    assert reconciliation["product_target_eligible"] is False
    assert (working / "uncommitted-product.txt").read_text() == "operator work\n"
    assert not git(working, "status", "--porcelain").stdout == ""
    assert not git(
        next((state / "worktrees").glob("*/work/T-1")),
        "ls-remote",
        "origin",
        "refs/heads/devlegate/work/T-1",
    ).stdout.strip()

def test_wrong_product_branch_after_worker_preserves_checkpoint_and_branch(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        git(working, "switch", "--detach", base)
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert reconciliation["product_target_eligible"] is False
    assert git(working, "rev-parse", "--abbrev-ref", "HEAD").stdout.strip() == "HEAD"
    assert not git(
        next((state / "worktrees").glob("*/work/T-1")),
        "ls-remote",
        "origin",
        "refs/heads/devlegate/work/T-1",
    ).stdout.strip()

def test_stranded_agent_running_without_worker_identity_refuses_retry(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    devlegate._save_state("agent_running", worker_identity=None)
    calls = []
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="ownership cannot be proven absent"):
        devlegate.retry("T-1")
    assert calls == []

@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_startup_reconciles_absent_worker_as_process_loss(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(devlegate, state, record_start=True)
    (workspace.path / "partial.txt").write_text("preserve\n")
    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        identity = _capture_worker_identity(helper, devlegate._state["execution_id"])
        devlegate._save_state(
            "agent_running",
            execution_stage="worker-running",
            worker_identity=identity.as_dict(),
        )
        os.killpg(os.getpgid(helper.pid), signal.SIGKILL)
        helper.wait(timeout=10)
    finally:
        if helper.poll() is None:
            os.killpg(os.getpgid(helper.pid), signal.SIGKILL)
            helper.wait(timeout=10)

    recovered = Devlegate(config)
    old_execution_id = recovered._state["execution_id"]
    prompts = []
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda workspace, prompt, **_kwargs: (
            prompts.append((workspace.path, prompt)),
            WorkerRunResult(1, None, None, None),
        )[1],
    )
    assert run_test_iteration(recovered) == 1
    assert recovered._state["phase"] == "idle"
    metadata = recovered._state["failed_executions"]["T-1"]
    assert metadata["execution_id"] != old_execution_id
    assert metadata["interrupted_execution_id"] == old_execution_id
    assert len(prompts) == 1
    assert "Continue the existing implementation" in prompts[0][1]
    assert (workspace.path / "partial.txt").read_text() == "preserve\n"

def test_startup_launch_window_remains_blocked_without_identity(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state, record_start=True)
    devlegate._save_state(
        "agent_running", execution_stage="worker-launch", worker_identity=None
    )
    recovered = Devlegate(config)
    calls = []
    monkeypatch.setattr(
        recovered._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="unsafe execution stage worker-launch"):
        run_test_iteration(recovered)
    assert recovered._state["phase"] == "agent_running"
    assert recovered._state["execution_interruption_kind"] == "process_loss"
    assert calls == []

def test_startup_post_worker_normalizes_without_worker_launch(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(devlegate, state, record_start=True)
    (workspace.path / "partial.txt").write_text("preserve\n")
    devlegate._save_state(
        "agent_running", execution_stage="post-worker", worker_identity=None
    )
    recovered = Devlegate(config)
    prompts = []
    monkeypatch.setattr(
        recovered._workers,
        "run",
        lambda workspace, prompt, **_kwargs: (
            prompts.append((workspace.path, prompt)),
            WorkerRunResult(1, None, None, None),
        )[1],
    )

    assert run_test_iteration(recovered) == 1
    assert recovered._state["phase"] == "idle"
    assert len(prompts) == 1
    assert "Continue the existing implementation" in prompts[0][1]
    assert (workspace.path / "partial.txt").read_text() == "preserve\n"

def test_matching_live_worker_identity_refuses_retry_without_signaling(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    helper = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(60)"],
        start_new_session=True,
    )
    try:
        identity = _capture_worker_identity(helper, devlegate._state["execution_id"])
        devlegate._save_state("agent_running", worker_identity=identity.as_dict())
        calls = []
        monkeypatch.setattr(
            devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
        )

        with pytest.raises(DevlegateError, match="worker is still alive"):
            devlegate.retry("T-1")
        assert calls == []
        assert helper.poll() is None
    finally:
        os.killpg(os.getpgid(helper.pid), signal.SIGKILL)
        helper.wait()

def test_worker_identity_observer_rejects_pid_reuse_without_signaling(monkeypatch):
    identity = WorkerProcessIdentity("execution-1", 123, 123, 123, "boot-1", 10)
    signals = []
    monkeypatch.setattr("devlegate.worker_supervisor._linux_boot_id", lambda: "boot-1")
    monkeypatch.setattr(
        "devlegate.worker_supervisor._linux_process_start_time", lambda _pid: 11
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor._worker_group_exists", lambda _pgid: False
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor.os.killpg", lambda *args: signals.append(args)
    )

    assert observe_worker_identity(identity) == "absent"
    assert signals == []

def test_worker_identity_observer_blocks_when_leader_is_gone_but_group_survives(
    monkeypatch,
):
    identity = WorkerProcessIdentity("execution-1", 123, 456, 456, "boot-1", 10)
    monkeypatch.setattr("devlegate.worker_supervisor._linux_boot_id", lambda: "boot-1")
    monkeypatch.setattr(
        "devlegate.worker_supervisor._linux_process_start_time",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError),
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor._worker_group_exists", lambda _pgid: True
    )

    assert observe_worker_identity(identity) == "indeterminate"

def test_worker_identity_observer_treats_boot_change_as_absent_without_group_probe(
    monkeypatch,
):
    identity = WorkerProcessIdentity("execution-1", 123, 123, 123, "old-boot", 10)
    probes = []
    monkeypatch.setattr(
        "devlegate.worker_supervisor._linux_boot_id", lambda: "new-boot"
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor._worker_group_exists",
        lambda pgid: probes.append(pgid) or True,
    )

    assert observe_worker_identity(identity) == "absent"
    assert probes == []

def test_interrupted_recovery_refreshes_stale_product_remote(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    before = dict(devlegate._state)
    calls = []
    publisher = control_publisher(tmp_path)
    git(publisher, "switch", "main")
    (publisher / "remote-advance.txt").write_text("advanced\n")
    git(publisher, "add", "remote-advance.txt")
    git(publisher, "commit", "-m", "advance product remote")
    git(publisher, "push", "origin", "HEAD:main")
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="product remote generation changed"):
        devlegate.retry("T-1")

    assert devlegate._state == before
    assert devlegate._state["phase"] == "agent_running"
    assert calls == []

def test_explicit_retry_recovers_agent_running_with_fresh_identity(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(devlegate, state)
    (workspace.path / "useful-change.txt").write_text("keep this\n")
    old_id = devlegate._state["execution_id"]
    seen = []

    def worker(current_workspace, _prompt, **_kwargs):
        seen.append(devlegate._state["execution_id"])
        assert (
            current_workspace.path / "useful-change.txt"
        ).read_text() == "keep this\n"
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert devlegate.retry("T-1") == 1

    assert seen and seen[0] != old_id
    assert devlegate._state["phase"] == "idle"
    assert (workspace.path / "useful-change.txt").is_file()
    assert devlegate._state["interrupted_execution_id"] == old_id
    assert (
        devlegate._state["failed_executions"]["T-1"]["interrupted_execution_id"]
        == old_id
    )
    assert any(
        failure.ticket_id == "T-1"
        for failure in devlegate.status_view().failed_executions
    )

def test_active_retry_projection_hides_superseded_failure(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    _workspace, control = persist_agent_running(devlegate, state)
    code_head = git(devlegate.repo, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    todo_fingerprint, _count = _todo_fingerprint(control, devlegate.todo_path)
    devlegate._save_state(
        "agent_running",
        failed_executions={
            "T-1": {
                "execution_id": "interrupted-old",
                "product_head": code_head,
                "remote_head": code_head,
                "control_head": control_head,
                "todo_fingerprint": todo_fingerprint,
                "reason": "previous attempt failed",
                "report_unavailable": True,
            },
            "T-99": {
                "execution_id": "unrelated-failure",
                "product_head": code_head,
                "remote_head": code_head,
                "control_head": control_head,
                "todo_fingerprint": todo_fingerprint,
                "reason": "unrelated attempt failed",
                "report_unavailable": True,
            },
        },
    )
    monkeypatch.setattr(
        "devlegate.worker_supervisor.observe_worker_identity", lambda _: "absent"
    )
    observed = []

    def stop_during_prepare(plan):
        snapshot = devlegate.status_view()
        observed.append(snapshot)
        raise ExecutionWorkspaceError("test stops the resumed attempt")

    monkeypatch.setattr(devlegate, "_prepare_execution_workspace", stop_during_prepare)

    with pytest.raises(DevlegateError, match="test stops"):
        devlegate.retry("T-1")

    assert observed
    assert [failure.ticket_id for failure in observed[0].failed_executions] == ["T-99"]
    assert devlegate._state["failed_executions"]["T-1"]["execution_id"] == (
        "interrupted-old"
    )

def test_interactive_retry_lists_recoverable_agent_running_without_side_effects(
    tmp_path, monkeypatch, capsys
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    state_before = (next(state.glob("*.sqlite3"))).read_bytes()
    worktrees_before = git(devlegate.repo, "worktree", "list", "--porcelain").stdout
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert devlegate.retry() == 0
    assert "T-1" in capsys.readouterr().out
    assert (next(state.glob("*.sqlite3"))).read_bytes() == state_before
    assert git(devlegate.repo, "worktree", "list", "--porcelain").stdout == (
        worktrees_before
    )
    assert devlegate._state["phase"] == "agent_running"

def test_interactive_retry_selection_recovers_agent_running(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    seen = []
    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda *_args, **_kwargs: (
            seen.append(devlegate._state["execution_id"])
            or WorkerRunResult(1, None, None, None)
        ),
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")

    assert devlegate.retry() == 1
    assert seen
    assert devlegate._state["phase"] == "idle"

def test_explicit_retry_recovers_legacy_published_worktree_at_remote_head(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(
        devlegate, state, checkpointed=True, publish=True
    )
    assert "execution_start_head" not in devlegate._state
    assert devlegate._state["execution_remote_head"] == workspace.head
    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )

    assert devlegate.retry("T-1") == 1
    assert devlegate._state["phase"] == "idle"

def test_explicit_retry_rejects_legacy_worktree_when_remote_advanced(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(
        devlegate, state, checkpointed=True, publish=True
    )
    recorded_remote_head = workspace.head
    before = dict(devlegate._state)
    (workspace.path / "remote-advance.txt").write_text("remote advance\n")
    git(workspace.path, "add", "remote-advance.txt")
    git(workspace.path, "commit", "-m", "remote advance")
    git(workspace.path, "push", "origin", f"HEAD:refs/heads/{workspace.branch}")
    git(workspace.path, "reset", "--hard", recorded_remote_head)
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker")
    )

    with pytest.raises(DevlegateError, match="remote HEAD changed"):
        devlegate.retry("T-1")

    assert devlegate._state == before

def test_explicit_retry_rejects_worktree_advanced_after_execution_start(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(
        devlegate, state, checkpointed=True, record_start=True
    )
    before = dict(devlegate._state)
    (workspace.path / "advanced.txt").write_text("advanced\n")
    git(workspace.path, "add", "advanced.txt")
    git(workspace.path, "commit", "-m", "advanced after start")
    calls = []
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="does not match the execution start HEAD"):
        devlegate.retry("T-1")

    assert devlegate._state == before
    assert calls == []

def test_publication_failure_remains_ambiguous_not_retryable(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "change.txt").write_text("checkpointed\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        run_test_iteration(devlegate)

    assert devlegate._state["phase"] == "agent_running"
    assert devlegate._state["execution_stage"] == "publishing"
    assert devlegate._state.get("failed_executions", {}) == {}
    with pytest.raises(DevlegateError, match="publication outcome is unknown"):
        run_test_iteration(devlegate)
    assert devlegate._state["execution_stage"] == "publishing"
    with pytest.raises(DevlegateError, match="ambiguous post-worker stage"):
        devlegate.retry("T-1")

def test_explicit_retry_rejects_mismatched_agent_running_ticket(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    before = dict(devlegate._state)
    calls = []
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="does not match"):
        devlegate.retry("T-2")

    assert devlegate._state == before
    assert calls == []

def test_explicit_retry_rejects_unsafe_agent_running_workspace(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(devlegate, state)
    before = dict(devlegate._state)
    calls = []
    (workspace.path / "unsafe.txt").write_text("uncommitted dependency change\n")
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule T is dirty")
        ),
    )
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    with pytest.raises(DevlegateError, match="cannot safely recover"):
        devlegate.retry("T-1")

    assert devlegate._state == before
    assert calls == []
    assert (
        workspace.path / "unsafe.txt"
    ).read_text() == "uncommitted dependency change\n"

def test_stale_failed_execution_remains_visible_but_not_retryable(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(devlegate) == 1

    current = devlegate._collect_status_attempt(allow_workflow_blocked=True)
    assert current.failed_executions[0].retryable

    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Changed"\n---\nwork\n'
    )
    stale = devlegate._collect_status_attempt(allow_workflow_blocked=True)
    assert len(stale.failed_executions) == 1
    assert not stale.failed_executions[0].retryable
    assert "todo workflow changed" in (
        stale.failed_executions[0].nonretryable_reason or ""
    )
    text = devlegate._render_status_text(stale)
    assert "Retryable" in text
    assert "no" in text
    assert "todo workflow changed since the failure" in text
    assert stale.as_dict()["failed_executions"][0]["retryable"] is False
    assert devlegate._retry_candidates() == ()

def test_clean_local_product_advance_invalidates_retry(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(_workspace, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    (working / "local.txt").write_text("local advance\n")
    git(working, "add", "local.txt")
    git(working, "commit", "-m", "local advance")

    snapshot = devlegate._collect_status_attempt(allow_workflow_blocked=True)
    failure = snapshot.failed_executions[0]
    assert not failure.retryable
    assert "product HEAD changed" in (failure.nonretryable_reason or "")
    assert devlegate._retry_candidates() == ()
    with pytest.raises(DevlegateError, match="not currently retryable"):
        devlegate.retry("T-1")
    assert calls == 1

def test_dirty_product_blocks_current_retryability(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda _workspace, _prompt, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(devlegate) == 1
    (working / "dirty.txt").write_text("uncommitted\n")

    snapshot = devlegate._collect_status_attempt(allow_workflow_blocked=True)
    failure = snapshot.failed_executions[0]
    assert not failure.retryable
    assert failure.nonretryable_reason == "code or control working tree is dirty"
    assert devlegate._retry_candidates() == ()

@pytest.mark.parametrize(
    ("barrier_state", "expected_action"),
    (("review", "none"), ("accepted", "integrate")),
)
def test_retry_cannot_bypass_serial_barrier(
    tmp_path, monkeypatch, barrier_state, expected_action
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(_workspace, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    control = next((state / "worktrees").glob("*/control"))
    (control / f"kanban/{barrier_state}/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Barrier"\n---\nbarrier\n'
    )
    git(control, "add", ".")
    git(control, "commit", "-m", f"add {barrier_state} barrier")
    metadata = dict(devlegate._state["failed_executions"]["T-1"])
    metadata["control_head"] = git(control, "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "idle",
        failed_executions={"T-1": metadata},
    )

    snapshot = devlegate._collect_status_attempt(allow_workflow_blocked=True)
    assert snapshot.plan.action == expected_action
    assert not snapshot.failed_executions[0].retryable
    assert devlegate._retry_candidates() == ()
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="not currently retryable"):
        devlegate.retry("T-1")
    assert calls == 1
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert (control / f"kanban/{barrier_state}/T-2.md").exists()

def test_retry_cannot_replace_persisted_bound_execution(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(_workspace, _prompt, **_kwargs):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    persisted = dict(devlegate._state)
    devlegate._save_state(
        "agent_pending",
        local_head=persisted["local_head"],
        remote_head=persisted["remote_head"],
        changed_paths=persisted["changed_paths"],
        control_head=persisted["control_head"],
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=persisted["execution_base_head"],
        execution_control_head=persisted["execution_control_head"],
        execution_branch=persisted["execution_branch"],
        execution_path=persisted["execution_path"],
        execution_id=persisted["execution_id"],
        execution_remote_head=persisted["execution_remote_head"],
    )

    with pytest.raises(DevlegateError, match="not currently retryable"):
        devlegate.retry("T-1")
    assert calls == 1
    assert devlegate._state["phase"] == "agent_pending"

@pytest.mark.parametrize(
    ("outcome", "remaining", "questions"),
    [
        ("completed", (), ()),
        ("incomplete", ("finish",), ()),
        ("blocked", (), ("Which API?",)),
    ],
)
def test_every_valid_worker_claim_enters_review_with_claim_fields(
    tmp_path, monkeypatch, outcome, remaining, questions
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        return WorkerRunResult(
            0,
            None,
            WorkerClaim(outcome, "semantic result", remaining, questions),
            None,
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0

    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()
    payload = json.loads(next((control / "executions/T-1").glob("*.json")).read_text())
    assert payload["conclusion"] == outcome
    assert payload["remaining"] == list(remaining)
    assert payload["questions"] == list(questions)

def test_branch_exists_worktree_missing_resumes_from_branch(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    shutil.rmtree(execution)
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    resumed = manager.prepare(checkpoint)

    assert resumed.path == execution
    assert resumed.head == checkpoint

def test_operator_recovery_requires_exact_observed_head_and_preserves_identity(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head="f" * 40
    )
    old_head = workspace.head

    with pytest.raises(DevlegateError, match="authorized HEAD"):
        devlegate._validate_recover_admission("T-1", "current", "0" * 40)
    assert git(working, "rev-parse", "devlegate/work/T-1").stdout.strip() == old_head

    devlegate._recover_unsafe_bound_execution_workspace("T-1", "current", old_head)
    assert git(working, "rev-parse", "devlegate/work/T-1").stdout.strip() == base
    assert (
        git(
            working,
            "rev-parse",
            "refs/devlegate/recovery/operator/T-1/current",
        ).stdout.strip()
        == old_head
    )

def test_operator_recovery_keeps_current_remote_predecessor_for_publication(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    old_base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(old_base)
    git(workspace.path, "config", "user.email", "test@example.com")
    git(workspace.path, "config", "user.name", "Test User")
    (workspace.path / "old.txt").write_text("old\n")
    git(workspace.path, "add", "old.txt")
    git(workspace.path, "commit", "-m", "old generation")
    old_head = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(workspace.path, "push", "origin", f"{old_head}:refs/heads/{workspace.branch}")

    control = next((state / "worktrees").glob("*/control"))
    _write_prior_execution_receipt(
        control,
        execution_id="old-generation",
        base=old_base,
        path=workspace.path,
        head=old_head,
    )
    git(control, "add", "executions/T-1")
    git(control, "commit", "-m", "retain prior execution receipt")
    git(control, "push", "origin", "devlegate/control")
    (working / "new.txt").write_text("new\n")
    git(working, "add", "new.txt")
    git(working, "commit", "-m", "new admitted base")
    new_base = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "push", "origin", "HEAD:main")

    devlegate = Devlegate(config)
    _persist_pending_execution(
        devlegate, state, workspace, new_base, "current", start_head=old_head
    )
    devlegate._save_state("agent_pending", execution_remote_head=old_head)
    assert devlegate.plan_view().action == "blocked"

    devlegate._recover_unsafe_bound_execution_workspace("T-1", "current", old_head)

    assert devlegate._state["execution_id"] == "current"
    assert devlegate._state["execution_remote_head"] == new_base
    assert "execution_stage" not in devlegate._state
    assert "execution_start_head" not in devlegate._state
    assert devlegate.plan_view().action == "run-worker"

    worker_ids = []

    def worker(recovered_workspace, _prompt, **_kwargs):
        worker_ids.append(devlegate._state["execution_id"])
        (recovered_workspace.path / "new.txt").write_text("worker\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_sync_control",
        lambda: (git(control, "rev-parse", "HEAD").stdout.strip(),) * 2,
    )

    assert run_test_iteration(devlegate) == 0
    assert worker_ids == ["current"]
    execution_remote = git(
        working, "ls-remote", "origin", f"refs/heads/{manager.branch}"
    ).stdout.split()[0]
    assert execution_remote == git(manager.path, "rev-parse", "HEAD").stdout.strip()

def test_operator_recovery_replays_after_local_reconstruction_boundary(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head="f" * 40
    )
    observed = workspace.head
    devlegate._save_state(
        "agent_pending",
        operator_recovery={
            "ticket_id": "T-1",
            "execution_id": "current",
            "observed_head": observed,
        },
    )
    original_prepare = ExecutionWorkspaceManager.prepare

    def crash_after_reconstruction(self, planned_base):
        original_prepare(self, planned_base)
        raise RuntimeError("simulated recovery crash")

    monkeypatch.setattr(
        ExecutionWorkspaceManager, "prepare", crash_after_reconstruction
    )
    with pytest.raises(RuntimeError, match="simulated recovery crash"):
        devlegate.run_iteration()

    assert git(working, "rev-parse", workspace.branch).stdout.strip() == base
    assert devlegate._state["operator_recovery"]["observed_head"] == observed

    restarted = Devlegate(config)
    assert restarted._state["execution_id"] == "current"
    monkeypatch.setattr(ExecutionWorkspaceManager, "prepare", original_prepare)
    assert run_test_iteration(restarted) == 0
    assert restarted._state["phase"] == "agent_pending"
    assert "operator_recovery" not in restarted._state
    assert restarted.plan_view().action == "run-worker"

@pytest.mark.parametrize(
    ("ticket_id", "execution_id", "observed_head", "expected_error"),
    [
        ("wrong-ticket", "current", "old", "requested ticket"),
        ("T-1", "wrong-execution", "old", "requested execution"),
        ("T-1", "current", "0" * 40, "authorized HEAD"),
    ],
)
def test_public_recover_owner_admission_is_identity_bound(
    tmp_path,
    monkeypatch,
    ticket_id,
    execution_id,
    observed_head,
    expected_error,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head="f" * 40
    )
    errors = []

    def submit():
        try:
            devlegate.submit_recover(
                ticket_id, execution_id, observed_head, request_id="recover-boundary"
            )
        except DevlegateError as error:
            errors.append(error)

    thread = threading.Thread(target=submit)
    thread.start()
    deadline = time.monotonic() + 2
    while (
        not devlegate._operator_command_pending()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    command = devlegate._take_operator_command()
    assert command is not None
    with pytest.raises(DevlegateError, match=expected_error):
        devlegate._admit_operator_command(command)
    thread.join(timeout=2)
    assert len(errors) == 1

def test_automatic_repair_launches_one_worker_with_same_execution_id(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    old_base = git(working, "rev-parse", "HEAD").stdout.strip()
    root = state / "worktrees" / next(state.glob("worktrees/*")).name
    manager = ExecutionWorkspaceManager(working, root, "T-1")
    workspace = manager.prepare(old_base)
    git(workspace.path, "config", "user.email", "test@example.com")
    git(workspace.path, "config", "user.name", "Test User")
    (workspace.path / "old.txt").write_text("old\n")
    git(workspace.path, "add", "old.txt")
    git(workspace.path, "commit", "-m", "old generation")
    old_head = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    control = next((state / "worktrees").glob("*/control"))
    _write_prior_execution_receipt(
        control,
        execution_id="old-generation",
        base=old_base,
        path=workspace.path,
        head=old_head,
    )
    git(control, "add", "executions/T-1")
    git(control, "commit", "-m", "retain prior execution receipt")
    git(control, "push", "origin", "devlegate/control")
    (working / "new.txt").write_text("new\n")
    git(working, "add", "new.txt")
    git(working, "commit", "-m", "new admitted base")
    new_base = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "push", "origin", "HEAD:main")
    devlegate = Devlegate(config)
    _persist_pending_execution(
        devlegate, state, workspace, new_base, "current", start_head=new_base
    )
    calls = []

    def worker(_workspace, _prompt, **_kwargs):
        calls.append(devlegate._state["execution_id"])
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_sync_control",
        lambda: (git(control, "rev-parse", "HEAD").stdout.strip(),) * 2,
    )

    assert run_test_iteration(devlegate) in {0, 1}
    assert calls == ["current"]
    assert devlegate._state["execution_id"] == "current"

@pytest.mark.parametrize("boundary", ["evidence", "remote", "normalization"])
def test_operator_recovery_replays_each_durable_prefix(
    tmp_path, monkeypatch, boundary
):
    (
        working,
        config,
        state,
        devlegate,
        manager,
        old_head,
        base,
    ) = _operator_recovery_fixture(tmp_path, monkeypatch, remote=boundary == "remote")
    real_git = runtime._git
    crashed = False

    def crash_after_effect(repo, *args, **kwargs):
        nonlocal crashed
        result = real_git(repo, *args, **kwargs)
        if not crashed and (
            (boundary == "evidence" and args[:2] == (
                "update-ref",
                "refs/devlegate/recovery/operator/T-1/current",
            ))
            or (
                boundary == "remote"
                and any(
                    str(argument).startswith("--force-with-lease") for argument in args
                )
            )
        ):
            crashed = True
            raise RuntimeError("simulated recovery crash")
        return result

    monkeypatch.setattr(runtime, "_git", crash_after_effect)
    if boundary == "normalization":
        original_save = devlegate._save_state

        def crash_after_normalization(phase, **kwargs):
            result = original_save(phase, **kwargs)
            if kwargs.get("clear_pre_worker_generation"):
                raise RuntimeError("simulated recovery crash")
            return result

        monkeypatch.setattr(devlegate, "_save_state", crash_after_normalization)

    with pytest.raises(RuntimeError, match="simulated recovery crash"):
        devlegate.run_iteration()
    assert crashed or boundary in {"remote", "normalization"}

    restarted = Devlegate(config)
    assert restarted._state["execution_id"] == "current"
    monkeypatch.setattr(runtime, "_git", real_git)
    if boundary != "normalization":
        run_test_iteration(restarted)

    assert git(working, "rev-parse", manager.branch).stdout.strip() == base
    assert (
        git(
            working,
            "rev-parse",
            "refs/devlegate/recovery/operator/T-1/current",
        ).stdout.strip()
        == old_head
    )
    assert restarted._state["execution_id"] == "current"
    assert "operator_recovery" not in restarted._state
    assert (
        restarted._inspect_bound_execution_workspace(restarted._state).classification
        == "REUSABLE"
    )

def test_operator_recovery_rejects_remote_drift_after_observation(
    tmp_path, monkeypatch
):
    (
        working,
        _config,
        _state,
        devlegate,
        manager,
        old_head,
        base,
    ) = _operator_recovery_fixture(tmp_path, monkeypatch, remote=True)
    drift_path = tmp_path / "drift-worktree"
    git(working, "worktree", "add", "--detach", drift_path, base)
    git(drift_path, "config", "user.email", "test@example.com")
    git(drift_path, "config", "user.name", "Test User")
    (drift_path / "drift.txt").write_text("drift\n")
    git(drift_path, "add", "drift.txt")
    git(drift_path, "commit", "-m", "unexpected remote generation")
    drift_head = git(drift_path, "rev-parse", "HEAD").stdout.strip()
    git(working, "worktree", "remove", "--force", drift_path)
    observed = 0
    real_observe = devlegate._execution_remote_head

    def drift_after_observe(branch):
        nonlocal observed
        value = real_observe(branch)
        observed += 1
        if observed == 2:
            git(
                working,
                "push",
                "--force",
                "origin",
                f"{drift_head}:refs/heads/{branch}",
            )
        return value

    monkeypatch.setattr(devlegate, "_execution_remote_head", drift_after_observe)
    with pytest.raises(DevlegateError, match="changed before recovery CAS"):
        devlegate.run_iteration()

    remote = git(
        working, "ls-remote", "origin", f"refs/heads/{manager.branch}"
    ).stdout.split()[0]
    assert remote == drift_head
    assert (
        git(
            working,
            "rev-parse",
            "refs/devlegate/recovery/operator/T-1/current",
        ).stdout.strip()
        == old_head
    )
    assert devlegate._state["execution_id"] == "current"
    assert devlegate._state["execution_base_head"] == base
    assert devlegate._state.get("execution_remote_head") != drift_head

    monkeypatch.setattr(devlegate, "_execution_remote_head", real_observe)
    with pytest.raises(DevlegateError, match="conflicting identity"):
        devlegate._recover_unsafe_bound_execution_workspace(
            "T-1", "current", old_head
        )
    assert devlegate._state.get("execution_remote_head") != drift_head

def test_live_service_keeps_unsafe_recovery_authority_reachable(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head="f" * 40
    )
    observed = workspace.head

    with LiveService(working, config) as service:
        service.wait_ready()
        time.sleep(1.2)
        assert service.process is not None and service.process.poll() is None
        status = service.cli("status", "--json")
        assert status.returncode in {0, 1}
        status_payload = json.loads(status.stdout)
        assert status_payload["execution"]["phase"] == "agent_pending"
        assert status_payload["service"]["state"] == "running"
        assert status_payload["execution"]["state"] == "recovery-required"
        blocked_plan = service.cli("plan", "--json")
        assert blocked_plan.returncode in {0, 1}
        assert json.loads(blocked_plan.stdout)["action"] == "blocked"
        recovered = service.cli(
            "recover", "T-1", "current", "--observed-head", observed
        )
        assert recovered.returncode == 0, recovered.stderr
        assert "recovery accepted: T-1" in recovered.stdout
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            payload = state_payload(state)
            if "operator_recovery" not in payload:
                break
            time.sleep(0.05)
        assert "operator_recovery" not in payload
        assert payload["execution_id"] == "current"
        resumed = service.cli("plan", "--json")
        assert resumed.returncode == 0, resumed.stderr
        assert json.loads(resumed.stdout)["action"] == "run-worker"
        resumed_status = service.cli("status", "--json")
        assert resumed_status.returncode in {0, 1}
        resumed_payload = json.loads(resumed_status.stdout)
        assert resumed_payload["plan"]["action"] == "run-worker"
        assert resumed_payload["execution"]["state"] != "recovery-required"
        assert service.process is not None and service.process.poll() is None
