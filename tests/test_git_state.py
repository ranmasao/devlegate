import json
import sqlite3

import pytest
from test_cli import git, invoke, publish_control, ticket

from devlegate.cli import Devlegate, DevlegateError


def code_update(fixture, name="remote.txt", content="remote\n"):
    path = fixture["publisher"] / name
    path.write_text(content)
    git(fixture["publisher"], "add", name)
    git(fixture["publisher"], "commit", "-m", "code update")
    git(fixture["publisher"], "push", "origin", "HEAD:main")


def state_payload(fixture):
    database = next(fixture["state"].glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    payload = connection.execute(
        "SELECT payload FROM runtime_state WHERE id = 1"
    ).fetchone()[0]
    connection.close()
    return json.loads(payload)


def test_code_sync_completes_before_invalid_control_workflow(git_fixture):
    code_update(git_fixture)
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )

    result = invoke(git_fixture, "run", "--once")

    assert result.returncode == 1
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    assert "invalid ticket" in result.stdout
    assert state_payload(git_fixture)["phase"] != "merge_pending"
    second = invoke(git_fixture, "run", "--once")
    assert "resumed after completed merge" not in second.stdout


def test_invalid_workflow_repeated_run_has_no_stale_merge_recovery(git_fixture):
    code_update(git_fixture)
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )

    first = invoke(git_fixture, "run", "--once")
    second = invoke(git_fixture, "run", "--once")

    assert first.returncode == second.returncode == 1
    assert "resumed after completed merge" not in second.stdout
    assert "superseding pending revision" not in second.stdout
    assert state_payload(git_fixture)["phase"] != "merge_pending"


def test_descendant_control_revision_repairs_invalid_workflow(git_fixture):
    publish_control(
        git_fixture, "invalid workflow", {"kanban/todo/T-1.md": "bad\n"}, sync=False
    )
    assert invoke(git_fixture, "run", "--once").returncode == 1
    publish_control(
        git_fixture,
        "repair workflow",
        {"kanban/todo/T-1.md": ticket("Repaired")},
        sync=False,
    )

    result = invoke(git_fixture, "run", "--once")

    assert result.returncode == 1
    assert "execution failed" in result.stdout
    assert "superseding pending revision" not in result.stdout
    assert state_payload(git_fixture)["phase"] == "idle"


def test_interrupted_pre_sync_merge_pending_recovers(git_fixture, monkeypatch):
    code_update(git_fixture)
    target = git(git_fixture["publisher"], "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    assert devlegate.run_once() == 0
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == target
    assert state_payload(git_fixture)["phase"] == "idle"


def test_post_sync_crash_state_is_cleared_without_second_merge(
    git_fixture, monkeypatch
):
    code_update(git_fixture)
    target = git(git_fixture["publisher"], "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    result = devlegate.run_once()

    assert result == 0
    assert state_payload(git_fixture)["phase"] == "idle"


def test_merge_pending_divergence_fails_closed(git_fixture, monkeypatch):
    code_update(git_fixture)
    target = git(git_fixture["publisher"], "rev-parse", "HEAD").stdout.strip()
    old = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    (git_fixture["working"] / "local.txt").write_text("diverged\n")
    git(git_fixture["working"], "add", "local.txt")
    git(git_fixture["working"], "commit", "-m", "divergent local update")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    devlegate._save_state(
        "merge_pending",
        local_head=old,
        remote_head=target,
        changed_paths="remote.txt\n",
        control_head=git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip(),
    )

    with pytest.raises(DevlegateError, match="does not match the local HEAD"):
        devlegate.run_once()
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() != target


def test_agent_running_fails_closed_without_dispatch(
    git_fixture, monkeypatch, capsys
):
    publish_control(
        git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Bound")}
    )
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch="devlegate/work/T-1",
        execution_path=str(devlegate.execution_worktree_root / "work" / "T-1"),
        execution_id="attempt-1",
        execution_remote_head=None,
    )

    restarted = Devlegate(git_fixture["config"])
    calls = []
    monkeypatch.setattr(restarted, "_run_worker", lambda *_args: calls.append(True))
    with pytest.raises(DevlegateError, match="interrupted execution is ambiguous"):
        restarted.run_once()
    assert calls == []
    assert capsys.readouterr().out == ""
    assert state_payload(git_fixture)["phase"] == "agent_running"


def test_unresolved_agent_phase_survives_repeated_runs(
    git_fixture, monkeypatch
):
    publish_control(
        git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Bound")}
    )
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch="devlegate/work/T-1",
        execution_path=str(devlegate.execution_worktree_root / "work" / "T-1"),
        execution_id="attempt-1",
        execution_remote_head=None,
    )
    for _ in range(2):
        with pytest.raises(DevlegateError, match="interrupted execution is ambiguous"):
            devlegate.run_once()
    assert state_payload(git_fixture)["phase"] == "agent_running"
