# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Execution workspace, checkpoint, branch, and publication semantics."""

from _control_support import *  # noqa: F403,F405

def test_dirty_control_worktree_blocks_run_before_product_sync(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    (control / "uncommitted.txt").write_text("dirty\n")
    before = git(working, "rev-parse", "HEAD").stdout.strip()
    result = invoke(working, "--once", config=config)
    assert result.returncode == 1
    assert "control working tree is dirty" in result.stdout
    assert git(working, "rev-parse", "HEAD").stdout.strip() == before

def test_once_prepares_exact_product_execution_workspace_without_cross_plane_changes(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    operator_head = git(working, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    execution = (
        state / "worktrees" / next(state.glob("worktrees/*")).name / "work" / "T-1"
    )
    assert execution.is_dir()
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == operator_head
    assert git(execution, "symbolic-ref", "--short", "HEAD").stdout.strip() == (
        "devlegate/work/T-1"
    )
    assert git(working, "rev-parse", "HEAD").stdout.strip() == operator_head
    assert git(control, "rev-parse", "HEAD").stdout.strip() != control_head
    assert not (execution / "kanban").exists()

def test_unexpected_execution_remote_creation_blocks_publication(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        git(workspace.path, "push", "origin", "HEAD:refs/heads/devlegate/work/T-1")
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    with pytest.raises(DevlegateError, match="remote changed"):
        run_test_iteration(devlegate)

    execution = next((state / "worktrees").glob("*/work/T-1"))
    control = next((state / "worktrees").glob("*/control"))
    assert (execution / "implementation.txt").exists()
    assert git(execution, "log", "-1", "--pretty=%s").stdout.strip() == (
        "T-1: Control ticket"
    )
    message = git(execution, "log", "-1", "--format=%B").stdout
    assert "Worker result:\ndone" in message
    assert "Devlegate-Execution:" in message
    assert (control / "kanban/todo/T-1.md").exists()
    assert not (control / "kanban/review/T-1.md").exists()
    assert not list((control / "executions").glob("T-1/*.json"))

@pytest.mark.parametrize("expected_absent", [False, True])
def test_execution_publication_lease_rejects_intervening_remote_generation(
    tmp_path, monkeypatch, expected_absent
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    manager = ExecutionWorkspaceManager(
        devlegate.repo, devlegate.execution_worktree_root, "T-1"
    )
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    workspace = manager.prepare(base)
    (workspace.path / "implementation.txt").write_text("predecessor\n")
    git(workspace.path, "add", "implementation.txt")
    git(workspace.path, "commit", "-m", "predecessor")
    predecessor = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    if not expected_absent:
        git(
            workspace.path,
            "push",
            "origin",
            f"{predecessor}:refs/heads/{manager.branch}",
        )
    expected = None if expected_absent else predecessor
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
        devlegate._publish_execution_branch(workspace, checkpoint, expected)
    assert raced
    assert (
        git(
            workspace.path, "ls-remote", "origin", f"refs/heads/{manager.branch}"
        ).stdout.split()[0]
        == external
    )

def test_zero_delta_product_drift_preserves_old_report_and_admits_fresh_execution(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()

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
    assert pending["status"] == "pending_admission"
    assert pending["original_base"] == base
    assert pending["worker_checkpoint"] == base
    assert engine._state.get("reconciliation") is None

    control = next((state / "worktrees").glob("*/control"))
    old_id = pending["stale_execution_id"]
    old_report = ExecutionReportStore(control).read("T-1", old_id)
    assert old_report.code_base_head == base
    assert old_report.workspace_head == base
    assert old_report.result.conclusion == "blocked"

    restarted = Devlegate(config)
    fresh_ids = []

    def fresh_worker(_workspace, _prompt, **_kwargs):
        fresh_ids.append(restarted._state["execution_id"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
    assert run_test_iteration(restarted) == 0
    assert fresh_ids == [pending["fresh_execution_id"]]
    assert fresh_ids[0] != old_id
    fresh_report = ExecutionReportStore(control).read("T-1", fresh_ids[0])
    product_b = git(working, "rev-parse", "HEAD").stdout.strip()
    assert product_b != base
    assert fresh_report.code_base_head == product_b
    assert fresh_report.workspace_head == product_b
    assert restarted._state.get("reconciliation") is None
    assert restarted._state["last_automatic_retry"]["status"] == "completed"

def test_post_checkpoint_unobservable_remote_retains_execution_binding(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()

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

    monkeypatch.setattr(engine, "_observe_product_generation", unavailable)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda workspace, *_args, **_kwargs: (
            (workspace.path / "worker-progress.txt").write_text("worker progress\n"),
            WorkerRunResult(
                0, None, WorkerClaim("blocked", "connectivity lost", (), ()), None
            ),
        )[-1],
    )
    assert run_test_iteration(engine) == 1
    retained = state_payload(state)
    assert retained["execution_stage"] == "post-checkpoint"
    checkpoint = retained["pending_execution_report"]["workspace_head"]
    assert checkpoint != base
    assert retained["execution_id"]
    assert "reconciliation" not in retained
    execution_id = retained["execution_id"]

    restarted = Devlegate(config)
    monkeypatch.setattr(restarted, "_observe_product_generation", unavailable)
    with pytest.raises(
        WorkflowBlockedError, match="product remote could not be observed"
    ):
        run_test_iteration(restarted)
    recovered = state_payload(state)
    assert recovered["execution_id"] == execution_id
    assert recovered["pending_execution_report"] == retained["pending_execution_report"]
    assert "reconciliation" not in recovered

def test_zero_delta_ticket_state_change_skips_fresh_execution(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: (
            (working / "product-change.txt").write_text("product B\n"),
            git(working, "add", "product-change.txt"),
            git(working, "commit", "-m", "advance product"),
            git(working, "push", "origin", "HEAD:main"),
            WorkerRunResult(
                0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
            ),
        )[-1],
    )
    assert run_test_iteration(engine) == 0
    pending = engine._state["automatic_retry"]
    control = next((state / "worktrees").glob("*/control"))
    todo = control / "kanban/todo/T-1.md"
    todo.rename(control / "kanban/review/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "review ticket externally")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    restarted = Devlegate(config)
    monkeypatch.setattr(
        restarted._workers,
        "run",
        lambda *_args, **_kwargs: pytest.fail("obsolete retry was admitted"),
    )
    assert run_test_iteration(restarted) == 0
    assert restarted._state.get("automatic_retry") is None
    assert restarted._state["last_automatic_retry"]["status"] == "not_admitted"
    assert restarted._state["last_automatic_retry"]["stale_execution_id"] == (
        pending["stale_execution_id"]
    )

def test_update_base_transplants_multiple_unpublished_checkpoints(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "first.txt").write_text("first\n")
        (working / "dirty-product.txt").write_text("product\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = dict(devlegate._state["reconciliation"])
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "second.txt").write_text("second\n")
    git(execution, "add", "second.txt")
    git(execution, "commit", "-m", "second checkpoint")
    second = git(execution, "rev-parse", "HEAD").stdout.strip()
    git(working, "add", "dirty-product.txt")
    git(working, "commit", "-m", "advance product")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "update-ref", reconciliation["evidence_ref"], second)
    operation = {
        **reconciliation,
        "worker_checkpoint": second,
        "execution_report": {
            **reconciliation["execution_report"],
            "workspace_head": second,
        },
    }
    devlegate._save_state("idle", reconciliation=operation)

    assert devlegate.reconcile_update_base("T-1", target) == 0
    rewritten = git(
        execution, "rev-list", "--reverse", f"{target}..HEAD"
    ).stdout.splitlines()
    assert len(rewritten) == 2
    assert git(execution, "rev-parse", f"{rewritten[0]}^").stdout.strip() == target

def test_update_base_rejects_published_execution_outside_lineage(
    tmp_path, monkeypatch
):
    devlegate, working, _state, execution, reconciliation, _first, second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    tree = git(execution, "rev-parse", f"{second}^{{tree}}").stdout.strip()
    outside = subprocess.run(
        ["git", "commit-tree", tree, "-p", second],
        cwd=execution,
        input="outside execution\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    git(
        execution,
        "push",
        "origin",
        f"{outside}:refs/heads/{reconciliation['execution_branch']}",
    )
    operation = {**reconciliation, "execution_remote_head": outside}
    devlegate._save_state("idle", reconciliation=operation)
    with pytest.raises(DevlegateError, match="outside the proven execution lineage"):
        devlegate.reconcile_update_base("T-1", target, rewrite_published=True)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == second
    assert git(working, "rev-parse", "HEAD").stdout.strip() == target
    assert second != outside

def test_update_base_rejects_merge_execution_lineage(tmp_path, monkeypatch):
    devlegate, _working, _state, execution, reconciliation, first, _second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    (execution / "side.txt").write_text("side\n")
    git(execution, "add", "side.txt")
    git(execution, "commit", "-m", "side checkpoint")
    side = git(execution, "rev-parse", "HEAD").stdout.strip()
    tree = git(execution, "rev-parse", f"{side}^{{tree}}").stdout.strip()
    merge = subprocess.run(
        ["git", "commit-tree", tree, "-p", first, "-p", side],
        cwd=execution,
        input="merge checkpoint\n",
        text=True,
        capture_output=True,
        check=True,
    ).stdout.strip()
    git(execution, "reset", "--hard", merge)
    git(execution, "update-ref", reconciliation["evidence_ref"], merge)
    operation = {
        **reconciliation,
        "worker_checkpoint": merge,
        "execution_report": {
            **reconciliation["execution_report"],
            "workspace_head": merge,
        },
    }
    devlegate._save_state("idle", reconciliation=operation)
    with pytest.raises(DevlegateError, match="merge, side branch, or unrelated"):
        devlegate.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == merge

def test_update_base_conflict_restores_multi_checkpoint_workspace(
    tmp_path, monkeypatch
):
    devlegate, working, _state, execution, reconciliation, _first, second, _target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    (working / "first.txt").write_text("target\n")
    git(working, "add", "first.txt")
    git(working, "commit", "-m", "conflicting product target")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="manual/advanced reconciliation"):
        devlegate.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == second
    assert not git(execution, "status", "--porcelain").stdout
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == second
    )

def test_update_base_rejects_dropped_execution_commit(tmp_path, monkeypatch):
    devlegate, working, _state, execution, reconciliation, first, second, _target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    (working / "first.txt").write_text("first\n")
    git(working, "add", "first.txt")
    git(working, "commit", "-m", "product contains first checkpoint")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="reconciled execution lineage is invalid"):
        devlegate.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == second
    assert git(execution, "rev-parse", "HEAD^").stdout.strip() == first

def test_product_checkout_mutation_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        (working / "product.txt").write_text("unexpected\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (working / "product.txt").read_text() == "unexpected\n"
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )
    assert (control / "kanban/todo/T-1.md").exists()
    assert not (control / "kanban/review/T-1.md").exists()
    assert reconciliation["product_dirty"] is True

def test_product_checkout_head_mutation_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        (working / "product.txt").write_text("unexpected history\n")
        git(working, "add", "product.txt")
        git(working, "commit", "-m", "unexpected product commit")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert git(working, "status", "--porcelain").stdout == ""
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )
    assert (control / "kanban/todo/T-1.md").exists()
    assert reconciliation["product_target_eligible"] is True

def test_product_checkout_branch_switch_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt, **_kwargs):
        git(working, "switch", "-c", "unexpected-product-branch")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1

    control = next((state / "worktrees").glob("*/control"))
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert (
        git(
            next((state / "worktrees").glob("*/work/T-1")),
            "rev-parse",
            reconciliation["evidence_ref"],
        ).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )
    assert (control / "kanban/todo/T-1.md").exists()

def test_product_status_observation_failure_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    worker_finished = False
    original_git = runtime._git

    def git_with_failed_status(repo, *args, check=True):
        if worker_finished and args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="cannot inspect checkout"
            )
        return original_git(repo, *args, check=check)

    def worker(_workspace, _prompt, **_kwargs):
        nonlocal worker_finished
        worker_finished = True
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(runtime, "_git", git_with_failed_status)
    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    reconciliation = devlegate._state["reconciliation"]
    assert reconciliation["status"] == "pending"
    assert reconciliation["product_target_eligible"] is False
    assert (control / "kanban/todo/T-1.md").exists()
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )

def test_same_ticket_lineage_reuses_published_branch_for_later_attempt(
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
        (workspace.path / f"attempt-{calls}.txt").write_text("checkpoint\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    control = next((state / "worktrees").glob("*/control"))
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "return ticket for rework")
    git(control, "push", "origin", "devlegate/control")

    assert run_test_iteration(devlegate) == 0
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert calls == 2
    assert (execution / "attempt-1.txt").exists()
    assert (execution / "attempt-2.txt").exists()
    assert git(execution, "log", "--format=%s").stdout.splitlines().count(
        "T-1: Control ticket"
    ) == 2
    assert len(list((control / "executions/T-1").glob("*.json"))) == 2

def test_unchanged_generation_does_not_recreate_missing_execution_worktree(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "checkpoint")
    git(working, "worktree", "remove", execution)

    result = invoke(working, "--once", config=config)

    assert result.returncode == 0
    assert not execution.exists()

def test_dirty_execution_worktree_is_preserved(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    marker = execution / "uncommitted.txt"
    marker.write_text("unique worker work\n")

    assert invoke(working, "--once", config=config).returncode == 0
    assert marker.read_text() == "unique worker work\n"

def test_execution_path_conflict_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next((state / "worktrees").glob("*"))
    expected = key / "work" / "T-1"
    expected.mkdir(parents=True)
    (expected / "do-not-delete").write_text("foreign\n")

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    assert "not a registered execution worktree" in result.stderr
    assert (expected / "do-not-delete").read_text() == "foreign\n"

def test_read_only_plan_does_not_materialize_execution_workspace(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0

    result = invoke(working, "plan", "--json", config=config)

    assert result.returncode == 0
    assert not list((state / "worktrees").glob("*/work/*"))

def test_worktree_porcelain_parser_rejects_ambiguous_records():
    output = "\n".join(
        [
            "worktree /tmp/repo",
            "HEAD " + "a" * 40,
            "branch refs/heads/main",
            "branch refs/heads/other",
            "",
        ]
    )
    with pytest.raises(ExecutionWorkspaceError, match="duplicate"):
        parse_worktree_porcelain(output)

def test_stale_execution_registration_is_repaired_without_global_prune(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    shutil.rmtree(execution)

    manager = ExecutionWorkspaceManager(
        working,
        state / "worktrees" / next(state.glob("worktrees/*")).name,
        "T-1",
    )
    workspace = manager.prepare(checkpoint)

    assert workspace.path == execution
    assert execution.is_dir()
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint

def test_product_advance_does_not_rebind_execution_base(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "execution checkpoint")
    execution_head = git(execution, "rev-parse", "HEAD").stdout.strip()
    original_base = state_payload(state)["execution_base_head"]
    product = control_publisher(tmp_path)
    git(product, "switch", "main")
    (product / "product-update.txt").write_text("product\n")
    git(product, "add", "product-update.txt")
    git(product, "commit", "-m", "product update")
    git(product, "push", "origin", "main")

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_base_head"] == original_base
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == execution_head
    assert git(working, "rev-parse", "HEAD").stdout.strip() != execution_head

def test_recreate_after_product_advance_uses_execution_head(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "execution checkpoint")
    product = control_publisher(tmp_path)
    git(product, "switch", "main")
    (product / "product-update.txt").write_text("product\n")
    git(product, "add", "product-update.txt")
    git(product, "commit", "-m", "product update")
    git(product, "push", "origin", "main")
    assert invoke(working, "--once", config=config).returncode == 1
    shutil.rmtree(execution)
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persisted_state = state_payload(state)
    devlegate._save_state(
        "agent_pending",
        local_head=persisted_state["local_head"],
        remote_head=persisted_state["remote_head"],
        changed_paths="",
        control_head=persisted_state["control_head"],
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=persisted_state["execution_base_head"],
        execution_control_head=persisted_state["execution_control_head"],
        execution_branch="devlegate/work/T-1",
        execution_path=str(execution),
    )

    assert invoke(working, "--once", config=config).returncode == 1
    assert not execution.exists()
    assert state_payload(state)["reconciliation"]["status"] == "pending"

def test_unrelated_detached_worktree_does_not_break_preparation(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    unrelated = tmp_path / "unrelated"
    git(working, "worktree", "add", "--detach", unrelated, "HEAD")

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    assert next((state / "worktrees").glob("*/work/T-1"), None) is not None

def test_expected_detached_execution_worktree_fails_closed(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "checkout", "--detach")
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = state_payload(state)["execution_base_head"]
    manager = ExecutionWorkspaceManager(
        working,
        state / "worktrees" / next(state.glob("worktrees/*")).name,
        "T-1",
    )

    with pytest.raises(ExecutionWorkspaceError, match="detached"):
        manager.prepare(base)
    assert execution.is_dir()
    assert devlegate._state["execution_base_head"] == base

def test_expected_execution_worktree_on_wrong_branch_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "switch", "-c", "wrong-execution")
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    with pytest.raises(ExecutionWorkspaceError, match="on branch wrong-execution"):
        manager.prepare(git(working, "rev-parse", "HEAD").stdout.strip())

def test_execution_branch_attached_to_unexpected_worktree_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    branch = git(execution, "rev-parse", "HEAD").stdout.strip()
    git(working, "worktree", "remove", execution)
    elsewhere = tmp_path / "elsewhere"
    git(working, "worktree", "add", elsewhere, "devlegate/work/T-1")
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    with pytest.raises(ExecutionWorkspaceError, match="unexpected worktree"):
        manager.prepare(branch)

def test_existing_execution_branch_unrelated_to_base_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    orphan = tmp_path / "orphan"
    git(working, "worktree", "add", "--detach", orphan, base)
    git(orphan, "switch", "--orphan", "unrelated")
    (orphan / "unrelated.txt").write_text("unrelated\n")
    git(orphan, "add", ".")
    git(orphan, "commit", "-m", "unrelated")
    unrelated = git(orphan, "rev-parse", "HEAD").stdout.strip()
    git(working, "worktree", "remove", orphan)
    git(working, "branch", "devlegate/work/T-1", unrelated)
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    with pytest.raises(ExecutionWorkspaceError, match="unrelated"):
        manager.prepare(base)

def test_execution_leaf_symlink_fails_closed_without_following_target(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next(state.glob("worktrees/*"))
    target = tmp_path / "leaf-target"
    target.mkdir()
    (key / "work").mkdir()
    (key / "work" / "T-1").symlink_to(target, target_is_directory=True)
    manager = ExecutionWorkspaceManager(working, key, "T-1")

    with pytest.raises(ExecutionWorkspaceError, match="is a symlink"):
        manager.prepare(git(working, "rev-parse", "HEAD").stdout.strip())
    assert target.is_dir()

def test_managed_execution_work_root_symlink_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next(state.glob("worktrees/*"))
    target = tmp_path / "work-target"
    target.mkdir()
    (key / "work").symlink_to(target, target_is_directory=True)
    manager = ExecutionWorkspaceManager(working, key, "T-1")

    with pytest.raises(ExecutionWorkspaceError, match="symlinked workspace root"):
        manager.prepare(git(working, "rev-parse", "HEAD").stdout.strip())

def test_existing_branch_and_worktree_are_rediscovered_after_finalization_boundary(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    base = git(execution, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    rediscovered = manager.prepare(base)

    assert rediscovered.path == execution
    assert rediscovered.head == base

def test_branch_creation_race_reobserves_exact_valid_topology(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next(state.glob("worktrees/*"))
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    real_git = execution_workspace._git
    raced = False

    def race(repo, *args, **kwargs):
        nonlocal raced
        result = real_git(repo, *args, **kwargs)
        if args[:2] == ("branch", "devlegate/work/T-1") and not raced:
            raced = True
            return subprocess.CompletedProcess(result.args, 1, "", "already exists")
        return result

    monkeypatch.setattr(execution_workspace, "_git", race)
    workspace = ExecutionWorkspaceManager(working, key, "T-1").prepare(base)

    assert raced
    assert workspace.head == base

def test_worktree_creation_race_reobserves_exact_valid_topology(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next(state.glob("worktrees/*"))
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    real_git = execution_workspace._git
    raced = False

    def race(repo, *args, **kwargs):
        nonlocal raced
        result = real_git(repo, *args, **kwargs)
        if args[:3] == ("worktree", "add", str(key / "work" / "T-1")) and not raced:
            raced = True
            return subprocess.CompletedProcess(result.args, 1, "", "already exists")
        return result

    monkeypatch.setattr(execution_workspace, "_git", race)
    workspace = ExecutionWorkspaceManager(working, key, "T-1").prepare(base)

    assert raced
    assert workspace.path.is_dir()
    assert workspace.head == base

@pytest.mark.parametrize(
    "command",
    [("status",), ("status", "--json"), ("plan",), ("plan", "--json"), ("check",)],
)
def test_read_only_commands_do_not_materialize_or_rebind_execution(command, tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    before = state_payload(state)
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(working, "worktree", "remove", execution)

    result = invoke(working, *command, config=config)

    assert result.returncode in {0, 1}
    assert state_payload(state) == before
    assert not execution.exists()

def test_pending_workspace_plan_is_reusable_only_for_exact_binding(
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
    _persist_pending_execution(devlegate, state, workspace, base, "current")

    plan = devlegate.plan_view()

    assert plan.action == "run-worker"
    assert plan.bound is True
    assert devlegate.status_view().plan.action == plan.action
    assert (
        devlegate._inspect_bound_execution_workspace(devlegate._state).classification
        == "REUSABLE"
    )

def test_unattributable_pending_branch_is_unsafe_and_untouched(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    orphan = tmp_path / "orphan"
    git(working, "worktree", "add", "--detach", orphan, base)
    git(orphan, "switch", "--orphan", "unowned")
    (orphan / "unowned.txt").write_text("unowned\n")
    git(orphan, "add", "unowned.txt")
    git(orphan, "commit", "-m", "unowned branch")
    unrelated = git(orphan, "rev-parse", "HEAD").stdout.strip()
    git(working, "worktree", "remove", "--force", orphan)
    git(working, "branch", "devlegate/work/T-1", unrelated)
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager
    _persist_pending_execution(devlegate, state, workspace, base, "current")

    plan = devlegate.plan_view()

    assert plan.action == "blocked"
    assert "unsafe" in plan.reason
    assert devlegate.status_view().plan.reason == plan.reason
    assert git(working, "rev-parse", "devlegate/work/T-1").stdout.strip() == unrelated

def test_prior_generation_is_repaired_with_evidence_and_same_execution_id(
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
        calls.append(True)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    monkeypatch.setattr(
        devlegate,
        "_sync_control",
        lambda: (git(control, "rev-parse", "HEAD").stdout.strip(),) * 2,
    )

    assert devlegate.plan_view().action == "blocked"
    assert "recoverable" in devlegate.plan_view().reason
    assert [
        report.workspace_head
        for report in ExecutionReportStore(control).list("T-1")
    ] == [old_head]
    effects = []
    real_git = runtime._git

    def observe_effect(repo, *args, **kwargs):
        if args[:2] == (
            "update-ref",
            "refs/devlegate/recovery/workspace/T-1/old-generation",
        ):
            effects.append("evidence")
        elif args[:2] == ("update-ref", "refs/heads/devlegate/work/T-1"):
            effects.append("branch")
        return real_git(repo, *args, **kwargs)

    monkeypatch.setattr(runtime, "_git", observe_effect)
    devlegate._repair_bound_execution_workspace()
    assert devlegate.plan_view().action == "run-worker"
    assert effects.index("evidence") < effects.index("branch")

    evidence = "refs/devlegate/recovery/workspace/T-1/old-generation"
    assert git(working, "rev-parse", evidence).stdout.strip() == old_head
    assert git(working, "rev-parse", "devlegate/work/T-1").stdout.strip() == new_base
    assert devlegate._state["execution_id"] == "current"

def test_pending_workspace_with_wrong_registered_worktree_is_not_runnable(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    root = state / "worktrees" / next(state.glob("worktrees/*")).name
    manager = ExecutionWorkspaceManager(working, root, "T-1")
    workspace = manager.prepare(base)
    wrong_path = tmp_path / "wrong-worktree"
    git(working, "worktree", "move", workspace.path, wrong_path)
    _persist_pending_execution(devlegate, state, workspace, base, "current")

    inspection = devlegate._inspect_bound_execution_workspace(devlegate._state)

    assert inspection.classification == "UNSAFE"
    assert devlegate.plan_view().action == "blocked"
    assert "unsafe" in devlegate.plan_view().reason

def test_automatic_repair_refuses_branch_drift_before_cas(tmp_path, monkeypatch):
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
    devlegate = Devlegate(config)
    _persist_pending_execution(devlegate, state, workspace, new_base, "current")

    original = ExecutionWorkspaceManager._validate_existing
    drifted = []

    def drift(registration_manager, registration, base_head):
        workspace = original(registration_manager, registration, base_head)
        if not drifted:
            drifted.append(True)
            git(
                working,
                "update-ref",
                "refs/heads/devlegate/work/T-1",
                new_base,
                old_head,
            )
        return workspace

    monkeypatch.setattr(ExecutionWorkspaceManager, "_validate_existing", drift)
    with pytest.raises(DevlegateError, match="changed"):
        devlegate._repair_bound_execution_workspace()
    assert git(working, "rev-parse", "devlegate/work/T-1").stdout.strip() == new_base
    assert devlegate.plan_view().action == "blocked"
