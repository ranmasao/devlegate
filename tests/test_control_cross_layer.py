# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Cross-layer control probes; stronger service companions remain authoritative."""

from dataclasses import replace

from _control_support import *  # noqa: F403,F405
from devlegate.git import resolve_commit
from devlegate import reconciliation_transactions as transactions

def test_product_workflow_copy_fails_closed(tmp_path):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    (working / "kanban/todo").mkdir(parents=True)
    result = invoke(working, "plan", "--json", config=config)
    assert result.returncode == 0
    assert "product checkout" in json.loads(result.stdout)["reason"]

def test_unrelated_local_parent_blocks_lifecycle_reset(tmp_path, monkeypatch):
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
    report = ExecutionReport.from_dict(devlegate._state["pending_execution_report"])
    control = next((state / "worktrees").glob("*/control"))
    (control / "manual.txt").write_text("unrelated\n")
    git(control, "add", "manual.txt")
    git(control, "commit", "-m", "unrelated local commit")
    manual = git(control, "rev-parse", "HEAD").stdout.strip()
    Devlegate._apply_lifecycle_once(devlegate, report)
    lifecycle = git(control, "rev-parse", "HEAD").stdout.strip()

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
    remote_descendant = git(architect, "rev-parse", "HEAD").stdout.strip()
    git(architect, "push", "origin", "HEAD:refs/heads/devlegate/control")

    recovered = Devlegate(config)
    with pytest.raises(
        DevlegateError, match="parent is not on the fresh remote lineage"
    ):
        run_test_iteration(recovered)
    assert git(control, "rev-parse", "HEAD").stdout.strip() == lifecycle
    assert manual in git(control, "log", "--format=%H").stdout.splitlines()
    remote_head = git(
        tmp_path / "remote.git", "rev-parse", "refs/heads/devlegate/control"
    ).stdout.strip()
    assert remote_head == remote_descendant
    assert recovered._state["phase"] == "agent_running"

def test_accepted_ticket_is_fast_forward_integrated_and_completed(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    review.write_text(
        review.read_text() + "\n<!-- devlegate: curator-note -->\nLooks good.\n"
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "append curator review note")
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept implementation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert run_test_iteration(devlegate) == 0

    assert (working / "implementation.txt").is_file()
    assert not (control / "kanban/accepted/T-1.md").exists()
    assert (control / "kanban/done/T-1.md").is_file()
    integration_message = git(control, "log", "-1", "--format=%B").stdout
    assert git(control, "log", "-1", "--pretty=%s").stdout.strip() == (
        "T-1: Control ticket (integrated)"
    )
    assert "Worker result:\nimplemented" in integration_message
    assert "Devlegate-Execution:" in integration_message
    assert "Devlegate integrate T-1" not in integration_message
    assert (
        git(working, "rev-parse", "HEAD").stdout.strip()
        == git(working, "rev-parse", "origin/main").stdout.strip()
    )
    execution_path = devlegate.execution_worktree_root / "work" / "T-1"
    assert not execution_path.exists()
    assert not any(
        item.get("branch") == "devlegate/work/T-1"
        for item in ExecutionWorkspaceManager(
            devlegate.repo, devlegate.execution_worktree_root, "T-1"
        )._registrations().values()
    )
    assert resolve_commit(devlegate.repo, "refs/heads/devlegate/work/T-1") is None
    assert git(
        working, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
    ).stdout == ""


def test_done_cleanup_recovery_after_process_loss_before_state_clear(
    tmp_path, monkeypatch
):
    """Replay clears the integration dependency before retiring its resources."""
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    review.write_text(review.read_text() + "\n<!-- devlegate: curator-note -->\n")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept review")
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept implementation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    original_save = engine._save_state

    def crash_before_clear(phase, **kwargs):
        if kwargs.get("accepted_integration") is None and kwargs.get(
            "handled_control_head"
        ):
            raise RuntimeError("simulated process loss after cleanup")
        return original_save(phase, **kwargs)

    monkeypatch.setattr(engine, "_save_state", crash_before_clear)
    with pytest.raises(RuntimeError, match="simulated process loss"):
        run_test_iteration(engine)

    assert engine._state.get("accepted_integration", {}).get("ticket_id") == "T-1"
    execution_path = engine.execution_worktree_root / "work" / "T-1"
    assert execution_path.is_dir()
    assert resolve_commit(engine.repo, "refs/heads/devlegate/work/T-1") is not None
    assert git(
        working, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
    ).stdout

    restarted = Devlegate(config)
    assert restarted._state.get("accepted_integration", {}).get("ticket_id") == "T-1"
    assert run_test_iteration(restarted) == 0
    assert restarted._state.get("accepted_integration") is None
    assert (working / "implementation.txt").is_file()
    assert (control / "kanban/done/T-1.md").is_file()
    assert not execution_path.exists()
    assert resolve_commit(engine.repo, "refs/heads/devlegate/work/T-1") is None
    assert (
        git(working, "ls-remote", "origin", "refs/heads/devlegate/work/T-1").stdout
        == ""
    )


def test_hosted_cleanup_recovers_after_published_done_before_retirement(
    tmp_path, monkeypatch
):
    """Startup discovery retires resources left after a post-done interruption."""
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    config.write_text(config.read_text() + "POLL_INTERVAL=0\n")
    monkeypatch.chdir(working)
    engine = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    review.write_text(review.read_text() + "\n<!-- devlegate: curator-note -->\n")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept review")
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept implementation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    def interrupt_before_retirement(*_args):
        raise RuntimeError("simulated process loss before workspace retirement")

    monkeypatch.setattr(
        engine, "_retire_integrated_workspace", interrupt_before_retirement
    )
    with pytest.raises(RuntimeError, match="simulated process loss"):
        run_test_iteration(engine)

    execution_path = engine.execution_worktree_root / "work" / "T-1"
    assert execution_path.is_dir()
    assert resolve_commit(engine.repo, "refs/heads/devlegate/work/T-1") is not None
    assert git(
        working, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
    ).stdout
    assert (control / "kanban/done/T-1.md").is_file()

    with LiveService(working, config) as service:
        service.wait_for(
            lambda: (
                not execution_path.exists()
                and resolve_commit(
                    working, "refs/heads/devlegate/work/T-1"
                )
                is None
                and not git(
                    working,
                    "ls-remote",
                    "origin",
                    "refs/heads/devlegate/work/T-1",
                ).stdout
            )
        )

    assert ExecutionReportStore(control).list("T-1")
    assert (working / "implementation.txt").is_file()


def test_unclassified_review_edit_after_admission_fails_closed(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    review.write_text(review.read_text() + "\nChanged acceptance criteria.\n")
    git(control, "add", "-A")
    git(control, "commit", "-m", "alter review criteria")
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept altered implementation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    with pytest.raises(DevlegateError, match="review content changed after admission"):
        run_test_iteration(devlegate)


def test_accepted_ticket_uses_newest_review_generation_at_same_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    first = ExecutionReportStore(control).list("T-1")[0]

    review.rename(control / "kanban/todo/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "retry review generation")
    retry_parent = git(control, "rev-parse", "HEAD").stdout.strip()

    second = replace(first, execution_id="retry-generation", control_head=retry_parent)
    ExecutionReportStore(control).write(second)
    (control / "kanban/todo/T-1.md").rename(review)
    git(control, "add", "-A")
    git(control, "commit", "-m", "publish retry generation")

    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept retry generation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert run_test_iteration(devlegate) == 0
    message = git(control, "log", "-1", "--format=%B").stdout
    assert "Devlegate-Execution: retry-generation" in message

def test_integrated_ticket_does_not_suppress_next_runnable_ticket(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []

    def worker(workspace, _prompt, **_kwargs):
        calls.append(workspace.ticket_id)
        if workspace.ticket_id == "T-1":
            (workspace.path / "implementation.txt").write_text("worker change\n")
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "implemented", (), ()), None
            )
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    git(control, "add", "kanban/todo/T-2.md")
    git(control, "commit", "-m", "add independent ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert run_test_iteration(devlegate) == 0
    review = control / "kanban/review/T-1.md"
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept first ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert run_test_iteration(devlegate) == 0
    assert (control / "kanban/done/T-1.md").is_file()
    assert run_test_iteration(devlegate) == 1
    assert calls == ["T-1", "T-2"]

def test_unchanged_empty_generation_remains_a_noop(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").unlink()
    git(control, "add", "-A")
    git(control, "commit", "-m", "remove runnable ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []
    monkeypatch.setattr(
        devlegate._workers, "run", lambda *_args, **_kwargs: calls.append(True)
    )

    assert run_test_iteration(devlegate) == 0
    assert run_test_iteration(devlegate) == 0
    assert calls == []

def test_persisted_agent_running_still_refuses_ordinary_restart(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)

    with pytest.raises(DevlegateError, match="unsafe execution stage"):
        run_test_iteration(devlegate)
    assert devlegate._state["phase"] == "agent_running"
    assert devlegate._state["execution_interruption_kind"] == "process_loss"

def test_check_reports_stranded_runtime_state_not_ready(tmp_path, monkeypatch, capsys):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)

    assert devlegate.check() == 1
    output = capsys.readouterr().out
    assert "FAIL  runtime state" in output
    assert "mutation-owner reconciliation is required" in output
    assert "\nReady." not in output

def test_check_reports_active_runtime_when_mutation_owner_is_live(
    tmp_path, monkeypatch, capsys
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)

    with devlegate._lock():
        monkeypatch.setattr(
            runtime.fcntl,
            "flock",
            lambda *_args: pytest.fail("check attempted to acquire mutation lock"),
        )
        assert devlegate.check() == 0
    output = capsys.readouterr().out
    assert "OK    runtime state" in output
    assert "mutation owner is active" in output
    assert "\nReady." in output

def test_mutation_owner_probe_reports_unlocked_without_acquiring(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    monkeypatch.setattr(
        runtime.fcntl,
        "flock",
        lambda *_args: pytest.fail("probe attempted to acquire mutation lock"),
    )

    assert devlegate._mutation_owner_live() is False

@pytest.mark.parametrize(
    ("claim", "process_returncode", "egress_error", "conclusion"),
    [
        (WorkerClaim("completed", "done", (), ()), 0, None, "completed"),
        (None, 1, "worker failed", "failed"),
    ],
)
def test_zero_delta_stale_conclusions_are_historical_only(
    tmp_path, monkeypatch, claim, process_returncode, egress_error, conclusion
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
            process_returncode, None, claim, egress_error
        )

    monkeypatch.setattr(engine._workers, "run", stale_worker)
    assert run_test_iteration(engine) == 0
    pending = engine._state["automatic_retry"]
    control = next((state / "worktrees").glob("*/control"))
    ticket = control / "kanban/todo/T-1.md"
    assert ticket.is_file()
    assert not (control / "kanban/review/T-1.md").exists()
    old_report = ExecutionReportStore(control).read(
        "T-1", pending["stale_execution_id"]
    )
    assert old_report.result.conclusion == conclusion
    assert "T-1" not in engine._state.get("failed_executions", {})

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
    fresh_report = ExecutionReportStore(control).read("T-1", fresh_ids[0])
    assert fresh_report.code_base_head != old_report.code_base_head
    assert fresh_report.result.conclusion == "blocked"
    assert "T-1" not in restarted._state.get("failed_executions", {})

def test_zero_delta_remote_only_descendant_is_safely_retried(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    alternate = tmp_path / "remote-advance"

    def worker(_workspace, _prompt, **_kwargs):
        git(working, "worktree", "add", "--detach", alternate, base)
        git(alternate, "config", "user.email", "test@example.com")
        git(alternate, "config", "user.name", "Test User")
        (alternate / "remote-change.txt").write_text("product B\n")
        git(alternate, "add", "remote-change.txt")
        git(alternate, "commit", "-m", "advance remote product")
        git(alternate, "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    pending = engine._state["automatic_retry"]
    assert git(working, "rev-parse", "HEAD").stdout.strip() == base
    assert pending["product_head"] != base

    restarted = Devlegate(config)
    fresh_bases = []

    def fresh_worker(_workspace, _prompt, **_kwargs):
        fresh_bases.append(restarted._state["execution_base_head"])
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "rechecked", (), ()), None
        )

    monkeypatch.setattr(restarted._workers, "run", fresh_worker)
    assert run_test_iteration(restarted) == 0
    assert fresh_bases == [pending["product_head"]]

def test_product_observation_does_not_use_stale_remote_tracking_ref(
    tmp_path, monkeypatch
):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    assert git(working, "rev-parse", "origin/main").stdout.strip() == base
    git(working, "config", "remote.origin.url", str(tmp_path / "offline-remote"))

    observation = engine._observe_product_generation(base)

    assert observation["classification"] == "unobservable"
    assert observation["remote_head"] == ""
    assert observation["stable"] is False

    (working / "local-change.txt").write_text("local change\n")
    git(working, "add", "local-change.txt")
    git(working, "commit", "-m", "local product change")
    ambiguous = engine._observe_product_generation(base)
    assert ambiguous["classification"] == "unsafe-local"
    assert ambiguous["remote_head"] == ""

def test_zero_delta_divergent_product_history_remains_reconciliation(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    alternate = tmp_path / "alternate"

    def worker(_workspace, _prompt, **_kwargs):
        remote_url = git(working, "remote", "get-url", "origin").stdout.strip()
        git(working, "clone", "--no-local", remote_url, alternate)
        git(alternate, "switch", "--orphan", "divergent")
        git(alternate, "config", "user.email", "test@example.com")
        git(alternate, "config", "user.name", "Test User")
        (alternate / "divergent.txt").write_text("divergent\n")
        git(alternate, "add", "divergent.txt")
        git(alternate, "commit", "-m", "divergent product")
        divergent = git(alternate, "rev-parse", "HEAD").stdout.strip()
        git(alternate, "push", "--force", "origin", f"{divergent}:main")
        return WorkerRunResult(
            0, None, WorkerClaim("blocked", "evidence absent", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 1
    assert engine._state["reconciliation"]["status"] == "pending"
    assert engine._state.get("automatic_retry") is None

@pytest.mark.parametrize("published_before_restart", [False, True])
def test_resolving_reconciliation_restart_recovers_retained_report(
    tmp_path, monkeypatch, published_before_restart
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
    original_base = reconciliation["original_base"]
    checkpoint = reconciliation["worker_checkpoint"]
    execution = next((state / "worktrees").glob("*/work/T-1"))
    if published_before_restart:
        git(
            execution,
            "push",
            "origin",
            f"{checkpoint}:refs/heads/{reconciliation['execution_branch']}",
        )
    else:
        git(
            execution,
            "push",
            "origin",
            f"{original_base}:refs/heads/{reconciliation['execution_branch']}",
        )
    reconciliation["status"] = "resolving"
    reconciliation["resolution"] = "resume"
    reconciliation["execution_remote_head"] = original_base
    devlegate._save_state("idle", reconciliation=reconciliation)
    (working / "dirty-product.txt").unlink()

    restarted = Devlegate(config)
    monkeypatch.setattr(
        restarted._workers, "run", lambda *_args, **_kwargs: pytest.fail("worker reran")
    )
    assert run_test_iteration(restarted) == 0
    assert restarted._state["reconciliation"]["status"] == "resolved"
    assert restarted._state["reconciliation"]["resolution"] == "resume"
    assert (
        git(
            execution,
            "ls-remote",
            "origin",
            f"refs/heads/{reconciliation['execution_branch']}",
        ).stdout.split()[0]
        == checkpoint
    )
    assert (
        next((state / "worktrees").glob("*/control")) / "kanban/review/T-1.md"
    ).is_file()

@pytest.mark.parametrize(
    "point", ["rebase", "observation", "lineage", "linear", "pin", "resolution"]
)
def test_update_base_interruption_keeps_base_and_replays(tmp_path, monkeypatch, point):
    devlegate, _working, state, execution, reconciliation, _first, _second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    original_base = reconciliation["original_base"]

    def interrupt(*args, **kwargs):
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        if point == "rebase":
            original = devlegate.reconciliation_git

            def rebase(*args, **kwargs):
                result = original(*args, **kwargs)
                if args[1:2] == ("rebase",):
                    interrupt()
                return result

            patch.setattr(devlegate, "reconciliation_git", rebase)
        elif point == "observation":
            original = devlegate._execution_workspace_for_recovery

            def observe(*args, **kwargs):
                if kwargs.get("base_head") == target:
                    interrupt()
                return original(*args, **kwargs)

            patch.setattr(devlegate, "_execution_workspace_for_recovery", observe)
        elif point == "lineage":
            patch.setattr(transactions, "authorize_workspace_rewrite", interrupt)
        elif point == "linear":
            patch.setattr(transactions, "_linear", interrupt)
        elif point == "pin":
            patch.setattr(devlegate, "reconciliation_pin_rewrite_evidence", interrupt)
        else:
            patch.setattr(devlegate, "reconciliation_todo_fingerprint", interrupt)
        with pytest.raises(RuntimeError, match="injected interruption"):
            devlegate.reconcile_update_base("T-1", target)

    durable = state_payload(state)
    assert durable["execution_base_head"] == original_base
    assert devlegate._state["execution_base_head"] == original_base
    assert durable["reconciliation"] == devlegate._state["reconciliation"]
    assert durable["reconciliation"]["rewrite_stage"] == "local-pending"
    if point == "resolution":
        reference = durable["reconciliation"]["rewritten_evidence_ref"]
        pinned = git(execution, "rev-parse", reference).stdout.strip()
        assert pinned == git(execution, "rev-parse", "HEAD").stdout.strip()
        monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:00+0000")
        with monkeypatch.context() as patch:
            patch.setattr(devlegate, "reconciliation_todo_fingerprint", interrupt)
            with pytest.raises(RuntimeError, match="injected interruption"):
                devlegate.reconcile_update_base("T-1", target)
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == pinned
        assert state_payload(state)["reconciliation"] == durable["reconciliation"]
    assert devlegate.reconcile_update_base("T-1", target) == 0
    assert devlegate._state["execution_base_head"] == target
    assert state_payload(state)["execution_base_head"] == target
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert git(execution, "rev-parse", "HEAD~2").stdout.strip() == target
    if point == "resolution":
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == pinned


def test_local_replay_refuses_conflicting_pinned_evidence(tmp_path, monkeypatch):
    devlegate, _working, state, execution, reconciliation, _first, second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )

    def interrupt():
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        patch.setattr(devlegate, "reconciliation_todo_fingerprint", interrupt)
        with pytest.raises(RuntimeError, match="injected interruption"):
            devlegate.reconcile_update_base("T-1", target)
    pending = state_payload(state)["reconciliation"]
    reference = pending["rewritten_evidence_ref"]
    rewritten = git(execution, "rev-parse", "HEAD").stdout.strip()
    git(execution, "update-ref", reference, second, rewritten)
    with pytest.raises(DevlegateError, match="rewrite evidence is invalid"):
        devlegate.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", reference).stdout.strip() == second
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == rewritten
    assert state_payload(state)["reconciliation"] == pending
    assert devlegate._state["execution_base_head"] == reconciliation["original_base"]


@pytest.mark.parametrize(
    "point", ["remote_wal", "pin", "after_pin", "publish", "resolution"]
)
def test_published_update_base_interruption_keeps_base_and_replays(
    tmp_path, monkeypatch, point
):
    devlegate, _working, state, execution, reconciliation, _first, second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    git(
        execution, "push", "origin",
        f"{second}:refs/heads/{reconciliation['execution_branch']}",
    )
    devlegate._save_state(
        "idle", reconciliation={**reconciliation, "execution_remote_head": second}
    )

    def interrupt(*args, **kwargs):
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        if point == "remote_wal":
            original = devlegate._save_state

            def save(*args, **kwargs):
                stage = kwargs.get("reconciliation", {}).get("rewrite_stage")
                if stage == "remote-pending":
                    interrupt()
                return original(*args, **kwargs)

            patch.setattr(devlegate, "_save_state", save)
        elif point == "pin":
            patch.setattr(devlegate, "reconciliation_pin_rewrite_evidence", interrupt)
        elif point == "after_pin":
            original = devlegate.reconciliation_pin_rewrite_evidence

            def pin(*args, **kwargs):
                original(*args, **kwargs)
                interrupt()

            patch.setattr(devlegate, "reconciliation_pin_rewrite_evidence", pin)
        elif point == "publish":
            original = devlegate.reconciliation_publish_rewrite

            def publish(*args, **kwargs):
                original(*args, **kwargs)
                interrupt()

            patch.setattr(devlegate, "reconciliation_publish_rewrite", publish)
        else:
            patch.setattr(devlegate, "reconciliation_todo_fingerprint", interrupt)
        with pytest.raises(RuntimeError, match="injected interruption"):
            devlegate.reconcile_update_base("T-1", target, rewrite_published=True)

    durable = state_payload(state)
    assert durable["execution_base_head"] == reconciliation["original_base"]
    assert devlegate._state["execution_base_head"] == durable["execution_base_head"]
    assert devlegate._state["reconciliation"] == durable["reconciliation"]
    assert durable["reconciliation"]["rewrite_stage"] == (
        "local-pending" if point == "remote_wal" else "remote-pending"
    )
    if point in {"after_pin", "publish", "resolution"}:
        pinned = git(
            execution, "rev-parse", durable["reconciliation"]["rewritten_evidence_ref"]
        ).stdout.strip()
        assert pinned == git(execution, "rev-parse", "HEAD").stdout.strip()
        monkeypatch.setenv("GIT_COMMITTER_DATE", "2030-01-01T00:00:00+0000")
    assert devlegate.reconcile_update_base("T-1", target, rewrite_published=True) == 0
    if devlegate._state["reconciliation"]["status"] != "resolved":
        assert (
            devlegate.reconcile_update_base("T-1", target, rewrite_published=True) == 0
        )
    assert devlegate._state["execution_base_head"] == target
    assert state_payload(state)["execution_base_head"] == target
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert git(execution, "rev-parse", "HEAD~2").stdout.strip() == target
    if point in {"after_pin", "publish", "resolution"}:
        assert git(execution, "rev-parse", "HEAD").stdout.strip() == pinned


def test_update_base_recovers_local_pending_after_rebase_before_next_wal_stage(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "worker.txt").write_text("worker\n")
        (working / "operator.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = dict(devlegate._state["reconciliation"])
    original_base = reconciliation["original_base"]
    execution = next((state / "worktrees").glob("*/work/T-1"))

    (working / "target.txt").write_text("target\n")
    git(working, "add", "target.txt", "operator.txt")
    git(working, "commit", "-m", "advance product")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    git(execution, "rebase", "--onto", target, original_base)
    operation = {
        **reconciliation,
        "rewrite_authorized": True,
        "rewrite_target": target,
        "rewrite_count": 1,
        "rewrite_stage": "local-pending",
        "rewrite_expected_remote": None,
        "displaced_evidence_ref": reconciliation["evidence_ref"],
        "rewritten_evidence_ref": transactions.rewritten_evidence_ref(
            "T-1", reconciliation["execution_id"]
        ),
    }
    devlegate._save_state(
        "idle",
        execution_base_head=original_base,
        execution_start_head=original_base,
        reconciliation=operation,
    )

    restarted = Devlegate(config)
    assert restarted.reconcile_update_base("T-1", target) == 0
    assert restarted._state["reconciliation"]["status"] == "resolved"
    assert restarted._state["reconciliation"]["effective_base"] == target
    assert git(execution, "rev-parse", "HEAD^").stdout.strip() == target

def test_update_base_published_rewrite_fails_closed_on_lease_drift(
    tmp_path, monkeypatch
):
    devlegate, working, _state, execution, reconciliation, _first, second, target = (
        _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch)
    )
    git(
        execution,
        "push",
        "origin",
        f"{second}:refs/heads/{reconciliation['execution_branch']}",
    )
    operation = {**reconciliation, "execution_remote_head": second}
    devlegate._save_state("idle", reconciliation=operation)
    drift = git(execution, "rev-parse", "HEAD^").stdout.strip()
    original_git = runtime._git
    moved = False

    def drift_before_lease(repo, *args, **kwargs):
        nonlocal moved
        if not moved and args[:1] == ("push",) and any(
            str(argument).startswith("--force-with-lease=") for argument in args
        ):
            git(
                execution,
                "push",
                "--force",
                "origin",
                f"{drift}:refs/heads/{reconciliation['execution_branch']}",
            )
            moved = True
        return original_git(repo, *args, **kwargs)

    monkeypatch.setattr(runtime, "_git", drift_before_lease)
    with pytest.raises(
        DevlegateError, match="stale info|remote changed during publication"
    ):
        devlegate.reconcile_update_base("T-1", target, rewrite_published=True)
    remote = git(
        execution,
        "ls-remote",
        "origin",
        f"refs/heads/{reconciliation['execution_branch']}",
    ).stdout.split()[0]
    assert remote == drift
    assert devlegate._state["reconciliation"].get("rewrite_stage") == "remote-pending"

@pytest.mark.parametrize("published_checkpoint", ["first", "second"])
def test_update_base_rewrites_exact_published_lineage_only_with_authorization(
    tmp_path, monkeypatch, published_checkpoint
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
    first = git(execution, "rev-parse", "HEAD^").stdout.strip()
    published = first if published_checkpoint == "first" else second
    git(
        execution,
        "push",
        "origin",
        f"{published}:refs/heads/{reconciliation['execution_branch']}",
    )
    git(working, "add", "dirty-product.txt")
    git(working, "commit", "-m", "advance product")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    git(execution, "update-ref", reconciliation["evidence_ref"], second)
    operation = {
        **reconciliation,
        "worker_checkpoint": second,
        "execution_remote_head": published,
        "execution_report": {
            **reconciliation["execution_report"],
            "workspace_head": second,
        },
    }
    devlegate._save_state("idle", reconciliation=operation)

    with pytest.raises(DevlegateError, match="--rewrite-published"):
        devlegate.reconcile_update_base("T-1", target)
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == second

    assert devlegate.reconcile_update_base("T-1", target, rewrite_published=True) == 0
    resolved = devlegate._state["reconciliation"]
    assert resolved["status"] == "resolved"
    assert resolved["effective_base"] == target
    assert resolved["worker_checkpoint"] != second
    assert (
        git(
            execution,
            "ls-remote",
            "origin",
            f"refs/heads/{reconciliation['execution_branch']}",
        ).stdout.split()[0]
        == resolved["worker_checkpoint"]
    )
    assert (
        git(execution, "rev-parse", reconciliation["evidence_ref"]).stdout.strip()
        == second
    )
    assert (
        git(execution, "rev-parse", resolved["evidence_ref"]).stdout.strip()
        == resolved["worker_checkpoint"]
    )

def test_engine_dirty_product_can_become_valid_update_base_target(
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
    evidence = reconciliation["evidence_ref"]
    (working / "product-b.txt").write_text("product B\n")
    git(working, "add", "uncommitted-product.txt", "product-b.txt")
    git(working, "commit", "-m", "commit product work")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()

    assert devlegate.reconcile_update_base("T-1", target) == 0
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert devlegate._state["resume_required"]["status"] == "required"
    assert devlegate._state["reconciliation"]["resolution"] == "update-base"
    assert devlegate._state["reconciliation"]["effective_base"] == target
    assert (
        git(working, "rev-parse", evidence).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert git(execution, "rev-parse", "HEAD^").stdout.strip() == target

def test_engine_still_dirty_product_blocks_update_base_without_rewrite(
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
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="product checkout is dirty"):
        devlegate.reconcile_update_base("T-1", reconciliation["original_base"])
    assert devlegate._state["reconciliation"]["status"] == "pending"
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint

def test_engine_detached_product_can_be_restored_for_update_base(tmp_path, monkeypatch):
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
    evidence = reconciliation["evidence_ref"]
    git(working, "switch", "main")
    (working / "restored-product.txt").write_text("product B\n")
    git(working, "add", "restored-product.txt")
    git(working, "commit", "-m", "restore product branch")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()

    assert devlegate.reconcile_update_base("T-1", target) == 0
    assert devlegate._state["reconciliation"]["status"] == "resolved"
    assert devlegate._state["resume_required"]["status"] == "required"
    assert (
        git(working, "rev-parse", evidence).stdout.strip()
        == reconciliation["worker_checkpoint"]
    )

def test_engine_still_detached_product_blocks_update_base_without_rewrite(
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
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="wrong branch"):
        devlegate.reconcile_update_base("T-1", reconciliation["original_base"])
    assert devlegate._state["reconciliation"]["status"] == "pending"
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint

def test_locked_agent_running_cannot_be_recovered(tmp_path, monkeypatch):
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

    with devlegate._lock():
        with pytest.raises(DevlegateError, match="another devlegate instance"):
            devlegate.retry("T-1")

    assert devlegate._state == before
    assert calls == []

def test_incomplete_report_enters_review_and_preserves_remaining_work(
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
        (workspace.path / "partial.txt").write_text("partial\n")
        return WorkerRunResult(
            0, None, WorkerClaim("incomplete", "more remains", ("finish",), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 0
    assert run_test_iteration(devlegate) == 0
    assert calls == 1
    control = next((state / "worktrees").glob("*/control"))
    assert not (control / "kanban/todo/T-1.md").exists()
    assert (control / "kanban/review/T-1.md").is_file()
    report = next((control / "executions/T-1").glob("*.json"))
    assert json.loads(report.read_text())["remaining"] == ["finish"]

def test_unchanged_prepared_generation_is_quiet(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    first = invoke(working, "--once", config=config)
    second = invoke(working, "--once", config=config)

    assert first.returncode == 1
    assert second.returncode == 0
    assert "execution workspace" not in second.stdout

def test_selected_stale_registration_repair_preserves_unrelated_registration(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    unrelated = tmp_path / "unrelated"
    git(working, "worktree", "add", "--detach", unrelated, "HEAD")
    shutil.rmtree(execution)
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    manager.prepare(git(working, "rev-parse", "HEAD").stdout.strip())
    registrations = manager._registrations()
    assert unrelated.resolve() in registrations

def test_first_creation_remains_bound_to_planned_product_head_if_product_moves(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    key = next(state.glob("worktrees/*"))
    manager = ExecutionWorkspaceManager(working, key, "T-1")
    original_validate = manager._validate_base

    def move_product(planned):
        original_validate(planned)
        (working / "product-race.txt").write_text("moved\n")
        git(working, "add", "product-race.txt")
        git(working, "commit", "-m", "move product")

    monkeypatch.setattr(manager, "_validate_base", move_product)
    workspace = manager.prepare(base)

    assert workspace.base_head == base
    assert workspace.head == base
