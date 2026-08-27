import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from devlegate.cli import Devlegate, DevlegateError
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    parse_worktree_porcelain,
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )


def control_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "product.txt").write_text("product\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "product")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(seed, "switch", "--orphan", "devlegate/control")
    (seed / "product.txt").unlink(missing_ok=True)
    for state in ("backlog", "todo", "review", "done"):
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
    )
    assert devlegate._state["control_head"] == control_head


def test_run_worker_gate_leaves_idle_state(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    state_file = next(state.glob("*.json"))
    payload = json.loads(state_file.read_text())
    assert payload["phase"] == "idle"
    assert "selected_ticket_id" not in payload
    assert payload["handled_control_head"]
    assert "worker dispatch is gated" in result.stdout


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
    assert git(control, "rev-parse", "HEAD").stdout.strip() == control_head
    assert not (execution / "kanban").exists()


def test_run_reuses_and_recreates_execution_worktree_from_durable_branch(
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
    checkpoint = git(execution, "rev-parse", "HEAD").stdout.strip()
    git(working, "worktree", "remove", execution)

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    assert execution.is_dir()
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint
    assert (execution / "implementation.txt").read_text() == "checkpoint\n"


def test_dirty_execution_worktree_is_preserved(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "run", "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    marker = execution / "uncommitted.txt"
    marker.write_text("unique worker work\n")

    assert invoke(working, "run", "--once", config=config).returncode == 1
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

    result = invoke(working, "run", "--once", config=config)

    assert result.returncode == 1
    assert execution.is_dir()
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == checkpoint
