import hashlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

import devlegate.cli as cli
import devlegate.execution_workspace as execution_workspace
import devlegate.runtime as runtime
from devlegate.cli import Devlegate, DevlegateError
from devlegate.execution_result import ExecutionReport, ExecutionReportStore
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
    parse_worktree_porcelain,
)
from devlegate.runtime import _capture_worker_identity, _todo_fingerprint
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def state_payload(state_dir):
    database = next(state_dir.glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    payload = connection.execute(
        "SELECT payload FROM runtime_state WHERE id = 1"
    ).fetchone()[0]
    connection.close()
    return json.loads(payload)


def persist_agent_running(
    devlegate,
    state,
    execution_id="interrupted-old",
    *,
    checkpointed=False,
    record_start=False,
    publish=False,
):
    control = next((state / "worktrees").glob("*/control"))
    code_head = git(devlegate.repo, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    todo_fingerprint, _count = _todo_fingerprint(control, devlegate.todo_path)
    manager = ExecutionWorkspaceManager(
        devlegate.repo, devlegate.execution_worktree_root, "T-1"
    )
    workspace = manager.prepare(code_head)
    if checkpointed:
        (workspace.path / "prior-checkpoint.txt").write_text("prior checkpoint\n")
        git(workspace.path, "add", "prior-checkpoint.txt")
        git(workspace.path, "commit", "-m", "prior checkpoint")
        if publish:
            git(
                workspace.path,
                "push",
                "origin",
                f"HEAD:refs/heads/{workspace.branch}",
            )
        workspace = manager.prepare(code_head)
    execution_start_head = workspace.head if record_start else None
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        handled_remote_head=code_head,
        handled_control_head=control_head,
        handled_todo_fingerprint=todo_fingerprint,
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        execution_id=execution_id,
        execution_remote_head=workspace.head if publish else None,
        worker_identity={
            "execution_id": execution_id,
            "pid": 1,
            "pgid": 1,
            "sid": 1,
            "boot_id": "test-prior-boot",
            "start_time": 0,
        },
        **(
            {"execution_start_head": execution_start_head}
            if record_start
            else {}
        ),
    )
    return workspace, control


def control_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "product.txt").write_text("product\n")
    (seed / "README.md").write_text("project context\n")
    (seed / ".devlegate").mkdir()
    (seed / ".devlegate/project.md").write_text(
        '---\n"type": "devlegate.project"\n"common":\n  - "README.md"\n---\n'
    )
    git(seed, "add", ".")
    git(seed, "commit", "-m", "product")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(seed, "switch", "--orphan", "devlegate/control")
    (seed / "product.txt").unlink(missing_ok=True)
    for state in ("backlog", "todo", "review", "accepted", "done"):
        (seed / "kanban" / state).mkdir(parents=True)
        (seed / "kanban" / state / ".gitkeep").touch()
    (seed / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Control ticket"\n'
        "---\nwork\n"
    )
    git(seed, "add", ".")
    git(seed, "commit", "-m", "control workflow")
    git(seed, "push", "origin", "devlegate/control")
    git(tmp_path, "clone", "-b", "main", bare, working)
    git(working, "config", "user.email", "test@example.com")
    git(working, "config", "user.name", "Test User")
    config = tmp_path / "devlegate.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={tmp_path / 'state'}\n"
    )
    return working, config, tmp_path / "state"


def fresh_control_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "product.txt").write_text("product\n")
    (seed / "README.md").write_text("project context\n")
    (seed / ".devlegate").mkdir()
    (seed / ".devlegate/project.md").write_text(
        '---\n"type": "devlegate.project"\n"common":\n  - "README.md"\n---\n'
    )
    git(seed, "add", ".")
    git(seed, "commit", "-m", "product")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", bare, working)
    git(working, "config", "user.email", "test@example.com")
    git(working, "config", "user.name", "Test User")
    state = tmp_path / "state"
    config = tmp_path / "devlegate.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "BACKLOG_PATH=workflow/backlog\nTODO_PATH=workflow/todo\n"
        "REVIEW_PATH=workflow/review\nDONE_PATH=workflow/done\n"
        "ACCEPTED_PATH=workflow/accepted\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={state}\n"
    )
    return working, config, state


def invoke(working, *args, config):
    return subprocess.run(
        [sys.executable, "-m", "devlegate", *args, "--env", config],
        cwd=working,
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        text=True,
        capture_output=True,
    )


def test_control_init_is_explicit_idempotent_and_nested_observation(tmp_path):
    working, config, state = control_fixture(tmp_path)

    missing = invoke(working, "plan", "--json", config=config)
    assert missing.returncode == 0
    assert json.loads(missing.stdout)["action"] == "blocked"
    assert not (state / "worktrees").exists()

    initialized = invoke(working, "control", "init", config=config)
    assert initialized.returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    assert control.is_dir()
    assert (control / "kanban/todo/T-1.md").is_file()
    assert invoke(working, "control", "init", config=config).returncode == 0

    planned = invoke(working, "plan", "--json", config=config)
    payload = json.loads(planned.stdout)
    assert payload["observation"]["code"]["branch"] == "main"
    assert payload["observation"]["control"]["branch"] == "devlegate/control"
    assert payload["ticket"] is not None, payload
    assert payload["ticket"]["id"] == "T-1"


def test_control_init_bootstraps_empty_orphan_control_plane(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    initialized = invoke(working, "control", "init", config=config)

    assert initialized.returncode == 0, initialized.stderr
    control = next((state / "worktrees").glob("*/control"))
    assert git(control, "symbolic-ref", "--short", "HEAD").stdout.strip() == (
        "devlegate/control"
    )
    assert len(git(control, "rev-list", "--parents", "-n1", "HEAD").stdout.split()) == 1
    for state_name in ("backlog", "todo", "review", "accepted", "done"):
        assert (control / "workflow" / state_name / ".gitkeep").is_file()
    assert not (working / "workflow").exists()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "ls-remote", "origin", "refs/heads/devlegate/control").stdout
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert json.loads(invoke(working, "plan", "--json", config=config).stdout)[
        "action"
    ] == "none"
    assert invoke(working, "check", config=config).returncode == 0
    assert invoke(working, "status", "--json", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 0


def test_control_init_does_not_treat_remote_observation_failure_as_absence(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    config.write_text(
        config.read_text().replace(
            "REMOTE_BRANCH=main", "REMOTE_NAME=missing\nREMOTE_BRANCH=main"
        )
    )

    result = invoke(working, "control", "init", config=config)

    assert result.returncode == 1
    assert "cannot observe control branch" in result.stderr
    assert not state.exists()
    assert not subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/devlegate/control"],
        cwd=working,
        text=True,
        capture_output=True,
    ).stdout


def test_control_init_rejects_ambiguous_local_control_state(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    key = hashlib.sha256(str(working.resolve()).encode()).hexdigest()
    control_path = state / "worktrees" / key / "control"
    control_path.parent.mkdir(parents=True)
    control_path.write_text("foreign\n")

    result = invoke(working, "control", "init", config=config)

    assert result.returncode == 1
    assert "expected control worktree path already exists" in result.stderr
    assert control_path.read_text() == "foreign\n"


def test_product_workflow_copy_fails_closed(tmp_path):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    (working / "kanban/todo").mkdir(parents=True)
    result = invoke(working, "plan", "--json", config=config)
    assert result.returncode == 0
    assert "product checkout" in json.loads(result.stdout)["reason"]


def test_dependency_state_change_on_control_changes_plan(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/review/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n'
        "---\ndependency\n"
    )
    (control / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Waiting"\n'
        '"depends_on":\n  - "D-1"\n---\nwork\n'
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "add dependency workflow")
    blocked = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert blocked["action"] == "none"

    (control / "kanban/review/D-1.md").rename(control / "kanban/done/D-1.md")
    git(control, "add", ".")
    git(control, "commit", "-m", "complete dependency")
    runnable = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert runnable["action"] == "run-worker"
    assert runnable["ticket"]["id"] == "T-1"


def test_dirty_control_worktree_blocks_run_before_product_sync(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    (control / "uncommitted.txt").write_text("dirty\n")
    before = git(working, "rev-parse", "HEAD").stdout.strip()
    result = invoke(working, "run", "--once", config=config)
    assert result.returncode == 1
    assert "control working tree is dirty" in result.stdout
    assert git(working, "rev-parse", "HEAD").stdout.strip() == before


def test_runtime_rejects_foreign_control_checkout(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    git(working, "worktree", "remove", "--force", control)
    control.mkdir(parents=True)
    git(control, "init", "-b", "devlegate/control")
    result = invoke(working, "plan", "--json", config=config)
    assert result.returncode == 0
    assert "registered Git worktree" in json.loads(result.stdout)["reason"]
    shutil.rmtree(control)


def test_check_is_read_only_and_missing_control_does_not_create_state(tmp_path):
    working, config, state = control_fixture(tmp_path)
    result = invoke(working, "check", config=config)

    assert result.returncode == 1
    assert "control worktree" in result.stdout
    assert not state.exists()

    status = invoke(working, "status", "--json", config=config)
    assert status.returncode == 1
    status_payload = json.loads(status.stdout)
    assert status_payload["observation"]["control"] is None
    assert "control worktree is missing" in status_payload["plan"]["reason"]


def test_run_validates_control_before_product_fast_forward(tmp_path):
    working, config, _state = control_fixture(tmp_path)
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "new-product.txt").write_text("new\n")
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "product update")
    git(publisher, "push", "origin", "main")
    before = git(working, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    assert git(working, "rev-parse", "HEAD").stdout.strip() == before
    assert not (working / "new-product.txt").exists()
    assert "control worktree is missing" in result.stdout


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


def test_run_worker_completes_one_lifecycle_attempt(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0

    result = invoke(working, "run", "--once", config=config)

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

    def worker(_workspace, _prompt):
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        devlegate.run_once()
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
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("lifecycle recovery launched a worker"),
    )
    assert recovered.run_once() == 1
    assert recovered._state["phase"] == "idle"
    assert (control / "kanban/todo/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()
    lifecycle = git(control, "rev-parse", "HEAD").stdout.strip()
    assert git(control, "rev-parse", f"{lifecycle}^").stdout.strip() == descendant
    report_path = next((control / "executions/T-1").glob("*.json"))
    report = json.loads(report_path.read_text())
    assert report["control_head"] == original_control


def test_legacy_lifecycle_commit_replays_on_control_descendant_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    monkeypatch.setattr(
        devlegate,
        "_run_worker",
        lambda _workspace, _prompt: WorkerRunResult(1, None, None, None),
    )
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        devlegate.run_once()
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
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("legacy lifecycle recovery launched a worker"),
    )
    assert recovered.run_once() == 1
    assert recovered._state["phase"] == "idle"
    assert (control / "kanban/todo/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()
    assert ExecutionReportStore(control).read("T-1", report.execution_id) == report


def test_unrelated_local_parent_blocks_lifecycle_reset(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    monkeypatch.setattr(
        devlegate,
        "_run_worker",
        lambda _workspace, _prompt: WorkerRunResult(1, None, None, None),
    )
    monkeypatch.setattr(
        devlegate,
        "_apply_execution_lifecycle",
        lambda _report: (_ for _ in ()).throw(
            DevlegateError("simulated lifecycle interruption")
        ),
    )
    with pytest.raises(DevlegateError, match="simulated lifecycle interruption"):
        devlegate.run_once()
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
        recovered.run_once()
    assert git(control, "rev-parse", "HEAD").stdout.strip() == lifecycle
    assert manual in git(control, "log", "--format=%H").stdout.splitlines()
    remote_head = git(
        tmp_path / "remote.git", "rev-parse", "refs/heads/devlegate/control"
    ).stdout.strip()
    assert remote_head == remote_descendant
    assert recovered._state["phase"] == "agent_running"


def test_execution_start_head_is_persisted_before_worker(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base_head = git(working, "rev-parse", "HEAD").stdout.strip()

    def worker(workspace, _prompt):
        assert workspace.head == base_head
        assert devlegate._state["execution_base_head"] == base_head
        assert devlegate._state["execution_start_head"] == base_head
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 1
    assert state_payload(state)["phase"] == "idle"


def test_control_init_rejects_wrong_or_unregistered_existing_path(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    git(control, "switch", "-c", "wrong-control")
    wrong = invoke(working, "control", "init", config=config)
    assert wrong.returncode == 1
    assert "wrong branch" in wrong.stderr

    git(control, "switch", "devlegate/control")
    git(working, "worktree", "remove", "--force", control)
    control.mkdir(parents=True)
    unregistered = invoke(working, "control", "init", config=config)
    assert unregistered.returncode == 1
    assert "registered Git worktree" in unregistered.stderr


def test_run_prepares_exact_product_execution_workspace_without_cross_plane_changes(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    operator_head = git(working, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "run", "--once", config=config)

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
    assert "Devlegate checkpoint" not in git(
        workspace.path, "log", "-1", "--pretty=%s"
    ).stdout
    assert not (control / "executions").exists()


def test_worker_created_commit_cannot_reach_lifecycle_or_publication(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "worker-commit.txt").write_text("worker history\n")
        git(workspace.path, "add", "worker-commit.txt")
        git(workspace.path, "commit", "-m", "worker-owned commit")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        devlegate.run_once()

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


def test_unexpected_execution_remote_creation_blocks_publication(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        git(workspace.path, "push", "origin", "HEAD:refs/heads/devlegate/work/T-1")
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="remote changed"):
        devlegate.run_once()

    execution = next((state / "worktrees").glob("*/work/T-1"))
    control = next((state / "worktrees").glob("*/control"))
    assert (execution / "implementation.txt").exists()
    assert git(execution, "log", "-1", "--pretty=%s").stdout.strip().startswith(
        "Devlegate checkpoint T-1 "
    )
    assert (control / "kanban/todo/T-1.md").exists()
    assert not (control / "kanban/review/T-1.md").exists()
    assert not list((control / "executions").glob("T-1/*.json"))


def test_completed_worker_is_checkpointed_published_and_submitted_to_review(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt):
        nonlocal calls
        calls += 1
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 0
    assert calls == 1
    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (execution / "implementation.txt").read_text() == "worker change\n"
    assert git(execution, "log", "-1", "--pretty=%s").stdout.strip().startswith(
        "Devlegate checkpoint T-1 "
    )
    remote_control = git(
        control, "ls-remote", "origin", "refs/heads/devlegate/control"
    ).stdout.split()[0]
    assert remote_control == git(control, "rev-parse", "HEAD").stdout.strip()
    assert not (control / "kanban/todo/T-1.md").exists()
    assert (control / "kanban/review/T-1.md").is_file()
    reports = list((control / "executions/T-1").glob("*.json"))
    assert len(reports) == 1
    assert json.loads(reports[0].read_text())["conclusion"] == "completed"
    assert git(working, "rev-parse", "HEAD").stdout.strip() == git(
        working, "rev-parse", "origin/main"
    ).stdout.strip()
    assert not (working / "implementation.txt").exists()


def test_checkpointing_recovery_recreates_missing_checkpoint_without_worker(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    original_checkpoint = ExecutionWorkspaceManager.checkpoint
    failed = False

    def fail_once(manager, workspace, execution_id):
        nonlocal failed
        if not failed:
            failed = True
            raise ExecutionWorkspaceError("simulated checkpoint crash")
        return original_checkpoint(manager, workspace, execution_id)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(ExecutionWorkspaceManager, "checkpoint", fail_once)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        devlegate.run_once()
    assert devlegate._state["execution_stage"] == "checkpointing"
    assert devlegate._state["pending_execution_report"]["workspace_head"] is None

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("checkpoint recovery reran worker"),
    )
    assert recovered.run_once() == 0
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

    def worker(workspace, _prompt):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

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

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(devlegate, "_save_state", crash_before_post)
    with pytest.raises(RuntimeError, match="simulated state crash"):
        devlegate.run_once()
    assert crashed
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint_count = int(
        git(
            execution,
            "rev-list",
            "--count",
            "--all",
            "--grep",
            "Devlegate checkpoint",
        ).stdout
    )
    assert checkpoint_count == 1

    if dirty_after_checkpoint:
        (execution / "late-change.txt").write_text("must remain\n")
        recovered = Devlegate(config)
        monkeypatch.setattr(
            recovered,
            "_run_worker",
            lambda *_args: pytest.fail("dirty checkpoint recovery reran worker"),
        )
        with pytest.raises(DevlegateError, match="ambiguous checkpoint history"):
            recovered.run_once()
        assert (execution / "late-change.txt").read_text() == "must remain\n"
        assert not git(
            execution, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
        ).stdout.strip()
        return

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("checkpoint recovery reran worker"),
    )
    assert recovered.run_once() == 0
    assert int(
        git(
            execution,
            "rev-list",
            "--count",
            "--all",
            "--grep",
            "Devlegate checkpoint",
        ).stdout
    ) == 1


def test_publishing_recovery_pushes_checkpoint_without_worker(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(
        devlegate,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("simulated publication crash")
        ),
    )
    with pytest.raises(DevlegateError, match="publication failed"):
        devlegate.run_once()
    assert devlegate._state["execution_stage"] == "publishing"

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("publication recovery reran worker"),
    )
    assert recovered.run_once() == 0
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert git(
        execution,
        "ls-remote",
        "origin",
        "refs/heads/devlegate/work/T-1",
    ).stdout.split()[0] == git(execution, "rev-parse", "HEAD").stdout.strip()


def test_checkpoint_recovery_refuses_stale_product_before_side_effects(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    original_checkpoint = ExecutionWorkspaceManager.checkpoint
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "checkpoint",
        lambda *_args: (_ for _ in ()).throw(
            ExecutionWorkspaceError("simulated checkpoint crash")
        ),
    )
    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="checkpoint failed"):
        devlegate.run_once()
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
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("stale recovery launched worker"),
    )
    with pytest.raises(
        DevlegateError, match="product checkout or remote changed"
    ):
        recovered.run_once()
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

    def worker(workspace, _prompt):
        (workspace.path / "partial.txt").write_text("preserve\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

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

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(devlegate, "_save_state", crash_after_publication)
    with pytest.raises(RuntimeError, match="simulated state crash"):
        devlegate.run_once()
    assert crashed
    assert devlegate._state["execution_stage"] == "publishing"

    recovered = Devlegate(config)
    monkeypatch.setattr(
        recovered,
        "_run_worker",
        lambda *_args: pytest.fail("post-publication recovery reran worker"),
    )
    assert recovered.run_once() == 0
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/review/T-1.md").is_file()


def test_accepted_ticket_is_fast_forward_integrated_and_completed(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker change\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 0
    control = next((state / "worktrees").glob("*/control"))
    review = control / "kanban/review/T-1.md"
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept implementation")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert devlegate.run_once() == 0

    assert (working / "implementation.txt").is_file()
    assert not (control / "kanban/accepted/T-1.md").exists()
    assert (control / "kanban/done/T-1.md").is_file()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == git(
        working, "rev-parse", "origin/main"
    ).stdout.strip()


def test_integrated_ticket_does_not_suppress_next_runnable_ticket(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []

    def worker(workspace, _prompt):
        calls.append(workspace.ticket_id)
        if workspace.ticket_id == "T-1":
            (workspace.path / "implementation.txt").write_text("worker change\n")
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "implemented", (), ()), None
            )
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    git(control, "add", "kanban/todo/T-2.md")
    git(control, "commit", "-m", "add independent ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert devlegate.run_once() == 0
    review = control / "kanban/review/T-1.md"
    review.rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept first ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert devlegate.run_once() == 0
    assert (control / "kanban/done/T-1.md").is_file()
    assert devlegate.run_once() == 1
    assert calls == ["T-1", "T-2"]


def test_independent_ticket_remains_blocked_by_review_barrier(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []
    monkeypatch.setattr(
        devlegate,
        "_run_worker",
        lambda workspace, _prompt: (
            calls.append(workspace.ticket_id)
            or WorkerRunResult(
                0, None, WorkerClaim("completed", "implemented", (), ()), None
            )
        ),
    )
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    git(control, "add", "kanban/todo/T-2.md")
    git(control, "commit", "-m", "add independent ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert devlegate.run_once() == 0
    plan = devlegate.plan_view()
    assert calls == ["T-1"]
    assert plan.action == "none"
    assert "waiting for review" in plan.reason
    assert (control / "kanban/review/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()


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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

    assert devlegate.run_once() == 0
    assert devlegate.run_once() == 0
    assert calls == []


def test_review_and_accepted_states_enforce_serial_planning(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    (control / "kanban/todo/T-1.md").rename(control / "kanban/review/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "send ticket to review")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    review = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert review["action"] == "none"
    assert "waiting for review" in review["reason"]

    (control / "kanban/review/T-1.md").rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    accepted = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert accepted["action"] == "integrate"
    assert accepted["ticket"]["id"] == "T-1"


def test_failed_execution_is_suppressed_until_explicit_retry(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt):
        nonlocal calls
        calls += 1
        if calls == 2:
            (workspace.path / "retry.txt").write_text("retried\n")
            return WorkerRunResult(
                0, None, WorkerClaim("completed", "done", (), ()), None
            )
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 1
    assert devlegate.run_once() == 0
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

    def worker(workspace, _prompt):
        (workspace.path / "useful-change.txt").write_text("keep this\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(
        ExecutionWorkspaceManager,
        "verify_submodules",
        lambda _manager, _workspace: (_ for _ in ()).throw(
            ExecutionWorkspaceError("submodule T is dirty")
        ),
    )

    with pytest.raises(DevlegateError, match="post-worker execution integrity failed"):
        devlegate.run_once()

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


def test_persisted_agent_running_still_refuses_ordinary_restart(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)

    with pytest.raises(DevlegateError, match="unsafe execution stage"):
        devlegate.run_once()
    assert devlegate._state["phase"] == "agent_running"
    assert devlegate._state["execution_interruption_kind"] == "process_loss"


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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

    with pytest.raises(DevlegateError, match="ownership cannot be proven absent"):
        devlegate.retry("T-1")
    assert calls == []


@pytest.mark.skipif(os.name != "posix", reason="requires POSIX process groups")
def test_startup_reconciles_absent_worker_as_process_loss(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(
        devlegate, state, record_start=True
    )
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
        recovered,
        "_run_worker",
        lambda workspace, prompt: (
            prompts.append((workspace.path, prompt)),
            WorkerRunResult(1, None, None, None),
        )[1],
    )
    assert recovered.run_once() == 1
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
    monkeypatch.setattr(recovered, "_run_worker", lambda *_args: calls.append(True))

    with pytest.raises(DevlegateError, match="unsafe execution stage worker-launch"):
        recovered.run_once()
    assert recovered._state["phase"] == "agent_running"
    assert recovered._state["execution_interruption_kind"] == "process_loss"
    assert calls == []


def test_startup_post_worker_normalizes_without_worker_launch(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    workspace, _control = persist_agent_running(
        devlegate, state, record_start=True
    )
    (workspace.path / "partial.txt").write_text("preserve\n")
    devlegate._save_state(
        "agent_running", execution_stage="post-worker", worker_identity=None
    )
    recovered = Devlegate(config)
    prompts = []
    monkeypatch.setattr(
        recovered,
        "_run_worker",
        lambda workspace, prompt: (
            prompts.append((workspace.path, prompt)),
            WorkerRunResult(1, None, None, None),
        )[1],
    )

    assert recovered.run_once() == 1
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
            devlegate, "_run_worker", lambda *_args: calls.append(True)
        )

        with pytest.raises(DevlegateError, match="worker is still alive"):
            devlegate.retry("T-1")
        assert calls == []
        assert helper.poll() is None
    finally:
        os.killpg(os.getpgid(helper.pid), signal.SIGKILL)
        helper.wait()


def test_worker_identity_observer_rejects_pid_reuse_without_signaling(monkeypatch):
    identity = runtime.WorkerProcessIdentity(
        "execution-1", 123, 123, 123, "boot-1", 10
    )
    signals = []
    monkeypatch.setattr(runtime, "_linux_boot_id", lambda: "boot-1")
    monkeypatch.setattr(runtime, "_linux_process_start_time", lambda _pid: 11)
    monkeypatch.setattr(runtime, "_worker_group_exists", lambda _pgid: False)
    monkeypatch.setattr(runtime.os, "killpg", lambda *args: signals.append(args))

    assert runtime.observe_worker_identity(identity) == "absent"
    assert signals == []


def test_worker_identity_observer_blocks_when_leader_is_gone_but_group_survives(
    monkeypatch,
):
    identity = runtime.WorkerProcessIdentity(
        "execution-1", 123, 456, 456, "boot-1", 10
    )
    monkeypatch.setattr(runtime, "_linux_boot_id", lambda: "boot-1")
    monkeypatch.setattr(
        runtime,
        "_linux_process_start_time",
        lambda _pid: (_ for _ in ()).throw(FileNotFoundError),
    )
    monkeypatch.setattr(runtime, "_worker_group_exists", lambda _pgid: True)

    assert runtime.observe_worker_identity(identity) == "indeterminate"


def test_worker_identity_observer_treats_boot_change_as_absent_without_group_probe(
    monkeypatch,
):
    identity = runtime.WorkerProcessIdentity(
        "execution-1", 123, 123, 123, "old-boot", 10
    )
    probes = []
    monkeypatch.setattr(runtime, "_linux_boot_id", lambda: "new-boot")
    monkeypatch.setattr(
        runtime, "_worker_group_exists", lambda pgid: probes.append(pgid) or True
    )

    assert runtime.observe_worker_identity(identity) == "absent"
    assert probes == []


def test_locked_agent_running_cannot_be_recovered(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    before = dict(devlegate._state)
    calls = []
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

    with devlegate._lock():
        with pytest.raises(DevlegateError, match="another devlegate instance"):
            devlegate.retry("T-1")

    assert devlegate._state == before
    assert calls == []


def test_interrupted_recovery_refreshes_stale_product_remote(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    before = dict(devlegate._state)
    calls = []
    publisher = tmp_path / "seed"
    git(publisher, "switch", "main")
    (publisher / "remote-advance.txt").write_text("advanced\n")
    git(publisher, "add", "remote-advance.txt")
    git(publisher, "commit", "-m", "advance product remote")
    git(publisher, "push", "origin", "HEAD:main")
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

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

    def worker(current_workspace, _prompt):
        seen.append(devlegate._state["execution_id"])
        assert (
            current_workspace.path / "useful-change.txt"
        ).read_text() == "keep this\n"
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.retry("T-1") == 1

    assert seen and seen[0] != old_id
    assert devlegate._state["phase"] == "idle"
    assert (workspace.path / "useful-change.txt").is_file()
    assert devlegate._state["interrupted_execution_id"] == old_id
    assert (
        devlegate._state["failed_executions"]["T-1"]["interrupted_execution_id"]
        == old_id
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


def test_interactive_retry_selection_recovers_agent_running(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    persist_agent_running(devlegate, state)
    seen = []
    monkeypatch.setattr(
        devlegate,
        "_run_worker",
        lambda *_args: seen.append(devlegate._state["execution_id"])
        or WorkerRunResult(1, None, None, None),
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
        devlegate, "_run_worker", lambda *_args: WorkerRunResult(1, None, None, None)
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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: pytest.fail("worker"))

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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

    with pytest.raises(DevlegateError, match="does not match the execution start HEAD"):
        devlegate.retry("T-1")

    assert devlegate._state == before
    assert calls == []


def test_publication_failure_remains_ambiguous_not_retryable(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt):
        (workspace.path / "change.txt").write_text("checkpointed\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    monkeypatch.setattr(
        devlegate,
        "_publish_execution_branch",
        lambda *_args: (_ for _ in ()).throw(
            DevlegateError("publication outcome is unknown")
        ),
    )

    with pytest.raises(DevlegateError, match="execution branch publication failed"):
        devlegate.run_once()

    assert devlegate._state["phase"] == "agent_running"
    assert devlegate._state["execution_stage"] == "publishing"
    assert devlegate._state.get("failed_executions", {}) == {}
    with pytest.raises(DevlegateError, match="publication outcome is unknown"):
        devlegate.run_once()
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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

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
    monkeypatch.setattr(devlegate, "_run_worker", lambda *_args: calls.append(True))

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
        devlegate,
        "_run_worker",
        lambda _workspace, _prompt: WorkerRunResult(1, None, None, None),
    )
    assert devlegate.run_once() == 1

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
    assert "execution: failed  retryable: no" in devlegate._render_status_text(stale)
    assert stale.as_dict()["failed_executions"][0]["retryable"] is False
    assert devlegate._retry_candidates() == ()


def test_clean_local_product_advance_invalidates_retry(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(_workspace, _prompt):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 1
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
        devlegate,
        "_run_worker",
        lambda _workspace, _prompt: WorkerRunResult(1, None, None, None),
    )
    assert devlegate.run_once() == 1
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

    def worker(_workspace, _prompt):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 1
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

    def worker(_workspace, _prompt):
        nonlocal calls
        calls += 1
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 1
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


def test_product_checkout_mutation_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt):
        (working / "product.txt").write_text("unexpected\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="execution isolation cannot be proven"):
        devlegate.run_once()

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert (working / "product.txt").read_text() == "unexpected\n"
    assert not list((control / "executions/T-1").glob("*.json"))
    assert (control / "kanban/todo/T-1.md").exists()
    assert not (control / "kanban/review/T-1.md").exists()
    assert not git(execution, "log", "-1", "--pretty=%s").stdout.startswith(
        "Devlegate checkpoint"
    )


def test_product_checkout_head_mutation_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt):
        (working / "product.txt").write_text("unexpected history\n")
        git(working, "add", "product.txt")
        git(working, "commit", "-m", "unexpected product commit")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="execution isolation cannot be proven"):
        devlegate.run_once()

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert git(working, "status", "--porcelain").stdout == ""
    assert not list((control / "executions/T-1").glob("*.json"))
    assert (control / "kanban/todo/T-1.md").exists()
    assert not git(execution, "log", "-1", "--pretty=%s").stdout.startswith(
        "Devlegate checkpoint"
    )


def test_product_checkout_branch_switch_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(_workspace, _prompt):
        git(working, "switch", "-c", "unexpected-product-branch")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="execution isolation cannot be proven"):
        devlegate.run_once()

    control = next((state / "worktrees").glob("*/control"))
    assert not list((control / "executions/T-1").glob("*.json"))
    assert (control / "kanban/todo/T-1.md").exists()


def test_product_status_observation_failure_stops_lifecycle_before_checkpoint(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    worker_finished = False
    original_git = cli._git

    def git_with_failed_status(repo, *args, check=True):
        if worker_finished and args == ("status", "--porcelain"):
            return subprocess.CompletedProcess(
                ["git", *args], 1, stdout="", stderr="cannot inspect checkout"
            )
        return original_git(repo, *args, check=check)

    def worker(_workspace, _prompt):
        nonlocal worker_finished
        worker_finished = True
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(cli, "_git", git_with_failed_status)
    monkeypatch.setattr(devlegate, "_run_worker", worker)
    with pytest.raises(DevlegateError, match="cannot verify product checkout"):
        devlegate.run_once()

    control = next((state / "worktrees").glob("*/control"))
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert not list((control / "executions/T-1").glob("*.json"))
    assert (control / "kanban/todo/T-1.md").exists()
    assert not git(execution, "log", "-1", "--pretty=%s").stdout.startswith(
        "Devlegate checkpoint"
    )


def test_incomplete_report_preserves_todo_and_prevents_immediate_redispatch(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt):
        nonlocal calls
        calls += 1
        (workspace.path / "partial.txt").write_text("partial\n")
        return WorkerRunResult(
            0, None, WorkerClaim("incomplete", "more remains", ("finish",), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 0
    assert devlegate.run_once() == 0
    assert calls == 1
    control = next((state / "worktrees").glob("*/control"))
    assert (control / "kanban/todo/T-1.md").is_file()
    assert not (control / "kanban/review/T-1.md").exists()


def test_same_ticket_lineage_reuses_published_branch_for_later_attempt(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = 0

    def worker(workspace, _prompt):
        nonlocal calls
        calls += 1
        (workspace.path / f"attempt-{calls}.txt").write_text("checkpoint\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        )

    monkeypatch.setattr(devlegate, "_run_worker", worker)
    assert devlegate.run_once() == 0
    control = next((state / "worktrees").glob("*/control"))
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "return ticket for rework")
    git(control, "push", "origin", "devlegate/control")

    assert devlegate.run_once() == 0
    execution = next((state / "worktrees").glob("*/work/T-1"))
    assert calls == 2
    assert (execution / "attempt-1.txt").exists()
    assert (execution / "attempt-2.txt").exists()
    assert git(execution, "log", "--oneline").stdout.count("Devlegate checkpoint") == 2
    assert len(list((control / "executions/T-1").glob("*.json"))) == 2


def test_unchanged_generation_does_not_recreate_missing_execution_worktree(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "checkpoint")
    git(working, "worktree", "remove", execution)

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 0
    assert not execution.exists()


def test_dirty_execution_worktree_is_preserved(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    marker = execution / "uncommitted.txt"
    marker.write_text("unique worker work\n")

    assert invoke(working, "run", "--once", config=config).returncode == 0
    assert marker.read_text() == "unique worker work\n"


def test_execution_path_conflict_fails_closed(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    key = next((state / "worktrees").glob("*"))
    expected = key / "work" / "T-1"
    expected.mkdir(parents=True)
    (expected / "do-not-delete").write_text("foreign\n")

    result = invoke(working, "run", "--once", config=config)

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
    assert invoke(working, "run", "--once", config=config).returncode == 1
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
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "execution checkpoint")
    execution_head = git(execution, "rev-parse", "HEAD").stdout.strip()
    original_base = state_payload(state)["execution_base_head"]
    product = tmp_path / "seed"
    git(product, "switch", "main")
    (product / "product-update.txt").write_text("product\n")
    git(product, "add", "product-update.txt")
    git(product, "commit", "-m", "product update")
    git(product, "push", "origin", "main")

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_base_head"] == original_base
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == execution_head
    assert git(working, "rev-parse", "HEAD").stdout.strip() != execution_head


def test_new_control_generation_refreshes_attempt_authority(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    original_base = state_payload(state)["execution_base_head"]
    control = next((state / "worktrees").glob("*/control"))
    (control / "unrelated.md").write_text("architect note\n")
    git(control, "add", "unrelated.md")
    git(control, "commit", "-m", "unrelated control update")
    git(control, "push", "origin", "devlegate/control")
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_base_head"] == original_base
    final_control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    assert payload["execution_control_head"] == final_control_head
    assert final_control_head != control_head
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == original_base


def test_bound_pending_generation_preserves_authority_and_rejects_stale_control(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    original = state_payload(state)
    control = next((state / "worktrees").glob("*/control"))
    (control / "authority-race.md").write_text("changed\n")
    git(control, "add", ".")
    git(control, "commit", "-m", "advance control")
    git(control, "push", "origin", "devlegate/control")
    current_control = git(control, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    execution_path = (
        state / "worktrees" / next(state.glob("worktrees/*")).name / "work" / "T-1"
    )
    devlegate._save_state(
        "agent_pending",
        local_head=original["local_head"],
        remote_head=original["remote_head"],
        changed_paths=original["changed_paths"],
        control_head=original["handled_control_head"],
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=original["execution_base_head"],
        execution_control_head=original["handled_control_head"],
        execution_branch="devlegate/work/T-1",
        execution_path=str(execution_path),
    )

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_control_head"] == original["handled_control_head"]
    assert payload["execution_control_head"] != current_control
    assert "stale execution" in result.stderr


def test_recreate_after_product_advance_uses_execution_head(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "implementation.txt").write_text("checkpoint\n")
    git(execution, "add", "implementation.txt")
    git(execution, "commit", "-m", "execution checkpoint")
    execution_head = git(execution, "rev-parse", "HEAD").stdout.strip()
    product = tmp_path / "seed"
    git(product, "switch", "main")
    (product / "product-update.txt").write_text("product\n")
    git(product, "add", "product-update.txt")
    git(product, "commit", "-m", "product update")
    git(product, "push", "origin", "main")
    assert invoke(working, "run", "--once", config=config).returncode == 1
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

    assert invoke(working, "run", "--once", config=config).returncode == 1
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == execution_head


def test_unchanged_prepared_generation_is_quiet(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    first = invoke(working, "run", "--once", config=config)
    second = invoke(working, "run", "--once", config=config)

    assert first.returncode == 1
    assert second.returncode == 0
    assert "execution workspace" not in second.stdout


def test_unrelated_detached_worktree_does_not_break_preparation(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    unrelated = tmp_path / "unrelated"
    git(working, "worktree", "add", "--detach", unrelated, "HEAD")

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    assert next((state / "worktrees").glob("*/work/T-1"), None) is not None


def test_expected_detached_execution_worktree_fails_closed(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
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
    assert invoke(working, "run", "--once", config=config).returncode == 1
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
    assert invoke(working, "run", "--once", config=config).returncode == 1
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


def test_selected_stale_registration_repair_preserves_unrelated_registration(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
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


def test_branch_exists_worktree_missing_resumes_from_branch(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    shutil.rmtree(execution)
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )

    resumed = manager.prepare(checkpoint)

    assert resumed.path == execution
    assert resumed.head == checkpoint


def test_existing_branch_and_worktree_are_rediscovered_after_finalization_boundary(
    tmp_path,
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
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


@pytest.mark.parametrize(
    "command",
    [("status",), ("status", "--json"), ("plan",), ("plan", "--json"), ("check",)],
)
def test_read_only_commands_do_not_materialize_or_rebind_execution(command, tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    before = state_payload(state)
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(working, "worktree", "remove", execution)

    result = invoke(working, *command, config=config)

    assert result.returncode in {0, 1}
    assert state_payload(state) == before
    assert not execution.exists()
