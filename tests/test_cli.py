import hashlib
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path

import pytest

from devlegate.cli import (
    Devlegate,
    DevlegateError,
    _has_todo_files,
    _todo_fingerprint,
    build_parser,
    main,
)


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True,
        capture_output=True, check=True
    )


@pytest.fixture
def git_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    publisher = tmp_path / "publisher"
    state = tmp_path / "state"

    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "tracked.txt").write_text("initial\n")
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
    for name in ("backlog", "todo", "review", "accepted", "done"):
        (seed / "kanban" / name).mkdir(parents=True)
        (seed / "kanban" / name / ".gitkeep").touch()
    git(seed, "add", ".")
    git(seed, "commit", "-m", "control")
    git(seed, "push", "origin", "devlegate/control")

    git(tmp_path, "clone", "-b", "main", bare, working)
    git(tmp_path, "clone", "-b", "main", bare, publisher)
    for repo in (working, publisher):
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")

    key = hashlib.sha256(str(working.resolve()).encode()).hexdigest()
    control = state / "worktrees" / key / "control"
    git(working, "fetch", "origin", "devlegate/control")
    git(working, "worktree", "add", "--track", "-b", "devlegate/control",
        control, "origin/devlegate/control")
    publisher_control = tmp_path / "publisher-control"
    git(publisher, "fetch", "origin", "devlegate/control")
    git(publisher, "worktree", "add", "--track", "-b", "publisher-control",
        publisher_control, "origin/devlegate/control")

    config = tmp_path / "devlegate.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\nPOLL_INTERVAL=0\n"
        f"STATE_DIR={state}\n"
    )
    return {
        "bare": bare, "working": working, "publisher": publisher,
        "control": control, "publisher_control": publisher_control,
        "config": config, "state": state, "tmp": tmp_path,
    }


def invoke(fixture, *args, env_file=None):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    return subprocess.run(
        [sys.executable, "-m", "devlegate", *args, "--env",
         env_file or fixture["config"]], cwd=fixture["working"], env=environment,
        text=True, capture_output=True
    )


def publish_control(fixture, message, files, *, sync=True):
    for relative, content in files.items():
        path = fixture["publisher_control"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(fixture["publisher_control"], "add", ".")
    git(fixture["publisher_control"], "commit", "-m", message)
    git(fixture["publisher_control"], "push", "origin", "HEAD:devlegate/control")
    if sync:
        git(fixture["control"], "fetch", "origin", "devlegate/control")
        git(fixture["control"], "merge", "--ff-only", "origin/devlegate/control")


def ticket(title="Ticket", body="work", depends=None):
    lines = ["---", '"type": "devlegate.ticket"', f'"title": "{title}"']
    if depends:
        lines.append('"depends_on":')
        lines.extend(f'  - "{item}"' for item in depends)
    return "\n".join(lines + ["---", body, ""])


def test_help_and_parser_expose_phase1_commands(monkeypatch, capsys):
    parser = build_parser()
    assert parser.parse_args(["run", "--once"]).once
    assert parser.parse_args(["control", "init"]).command == "control"
    assert parser.parse_args(["status", "--json"]).json
    monkeypatch.setattr("sys.argv", ["devlegate", "--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert (
        "{init,render,run,retry,check,status,plan,control}"
        in capsys.readouterr().out
    )
    assert build_parser().parse_args(["retry", "T-1"]).ticket_id == "T-1"


def test_check_rejects_missing_project_context(git_fixture):
    working = git_fixture["working"]
    config = git_fixture["config"]
    (working / ".devlegate/project.md").unlink()

    result = invoke(git_fixture, "check", env_file=config)

    assert result.returncode == 1
    assert "project context manifest is missing" in result.stdout


def test_removed_top_level_forms_are_rejected(git_fixture):
    for args in ((), ("--once",), ("--check",)):
        result = invoke(git_fixture, *args)
        assert result.returncode == 2


def test_control_init_attaches_existing_orphan_branch_and_is_idempotent(git_fixture):
    assert not git_fixture["control"].exists() or git_fixture["control"].is_dir()
    assert invoke(git_fixture, "control", "init").returncode == 0
    assert git_fixture["control"].is_dir()
    assert (
        git(git_fixture["control"], "symbolic-ref", "--short", "HEAD")
        .stdout.strip()
        == "devlegate/control"
    )
    assert invoke(git_fixture, "control", "init").returncode == 0


def test_init_and_render_use_effective_configuration_without_control_mutation(
    git_fixture,
):
    config = git_fixture["tmp"] / "render.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=automation/state\n"
        "BACKLOG_PATH=workflow/waiting\nTODO_PATH=workflow/ready\n"
        "REVIEW_PATH=workflow/inspection\nDONE_PATH=workflow/accepted\n"
    )
    result = invoke(git_fixture, "init", env_file=config)
    assert result.returncode == 0
    assert not (git_fixture["working"] / ".env").exists()
    assert "rendered 2" in result.stdout
    architect = (
        git_fixture["working"] / "skills/architect/SKILL.md"
    )
    reviewer = (
        git_fixture["working"] / "skills/reviewer/SKILL.md"
    )
    assert "automation/state" in architect.read_text()
    assert "workflow/inspection" in reviewer.read_text()
    assert invoke(git_fixture, "render", "--check", env_file=config).returncode == 0


def test_check_rejects_invalid_poll_interval_without_creating_state(git_fixture):
    config = git_fixture["tmp"] / "invalid-poll.env"
    state = git_fixture["tmp"] / "invalid-poll-state"
    config.write_text(
        "REMOTE_BRANCH=main\nOPENCODE_BIN=true\nOPENCODE_MODEL=fake\n"
        "POLL_INTERVAL=banana\n"
        f"STATE_DIR={state}\n"
    )

    result = invoke(git_fixture, "check", env_file=config)

    assert result.returncode == 1
    assert "poll interval" in result.stdout
    assert "Not ready." in result.stdout
    assert not state.exists()


def test_check_distinguishes_missing_configured_remote_from_stale_ref(git_fixture):
    git(git_fixture["working"], "remote", "remove", "origin")

    result = invoke(git_fixture, "check")

    assert result.returncode == 1
    assert "FAIL  configured remote: origin" in result.stdout
    assert "known remote HEAD:" in result.stdout
    assert "Not ready." in result.stdout


def test_check_reports_local_product_branch_ahead(git_fixture):
    (git_fixture["working"] / "local.txt").write_text("local\n")
    git(git_fixture["working"], "add", "local.txt")
    git(git_fixture["working"], "commit", "-m", "local")

    result = invoke(git_fixture, "check")

    assert "ahead/behind: 1 0" in result.stdout


def test_check_reports_local_product_branch_behind(git_fixture):
    publisher = git_fixture["publisher"]
    (publisher / "remote.txt").write_text("remote\n")
    git(publisher, "add", "remote.txt")
    git(publisher, "commit", "-m", "remote")
    git(publisher, "push", "origin", "main")
    git(git_fixture["working"], "fetch", "origin", "main")

    result = invoke(git_fixture, "check")

    assert "ahead/behind: 0 1" in result.stdout


def test_check_reports_diverged_product_branch(git_fixture):
    (git_fixture["working"] / "local.txt").write_text("local\n")
    git(git_fixture["working"], "add", "local.txt")
    git(git_fixture["working"], "commit", "-m", "local")
    publisher = git_fixture["publisher"]
    (publisher / "remote.txt").write_text("remote\n")
    git(publisher, "add", "remote.txt")
    git(publisher, "commit", "-m", "remote")
    git(publisher, "push", "origin", "main")
    git(git_fixture["working"], "fetch", "origin", "main")

    result = invoke(git_fixture, "check")

    assert "ahead/behind: 1 1" in result.stdout


def test_init_outside_git_does_not_seed_project_files(tmp_path):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "init"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "not a git repository" in result.stderr
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".devlegate").exists()


def test_init_from_repository_subdirectory_does_not_seed_project_files(git_fixture):
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "init"],
        cwd=subdirectory,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "run devlegate from repository root" in result.stderr
    assert not (subdirectory / ".env").exists()
    assert not (subdirectory / ".devlegate").exists()


def test_init_seeds_missing_project_env_from_package(git_fixture):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    env = git_fixture["working"] / ".env"
    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "init"],
        cwd=git_fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0
    assert b"OPENCODE_MODEL=" in env.read_bytes()
    before = env.read_bytes()
    assert subprocess.run(
        [sys.executable, "-m", "devlegate", "init"],
        cwd=git_fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    ).returncode == 0
    assert env.read_bytes() == before


def test_read_only_commands_do_not_create_state_or_fetch(git_fixture):
    state = git_fixture["tmp"] / "read-only-state"
    config = git_fixture["tmp"] / "read-only.env"
    config.write_text(f"REMOTE_BRANCH=main\nSTATE_DIR={state}\n")
    before = git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
    result = invoke(git_fixture, "plan", "--json", env_file=config)
    assert result.returncode == 0
    assert not state.exists()
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
        == before
    )


def test_status_reports_nested_code_and_control_observations(git_fixture):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Control")})
    result = invoke(git_fixture, "status", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["observation"]["code"]["branch"] == "main"
    assert payload["observation"]["control"]["branch"] == "devlegate/control"
    assert payload["tickets"]["next"]["id"] == "T-1"


def test_missing_control_blocks_without_product_mutation(git_fixture):
    control = git_fixture["control"]
    git(git_fixture["working"], "worktree", "remove", "--force", control)
    before = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    result = invoke(git_fixture, "run", "--once")
    assert result.returncode == 1
    assert "control worktree is missing" in result.stdout
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == before


def test_product_workflow_copy_fails_closed(git_fixture):
    assert invoke(git_fixture, "control", "init").returncode == 0
    (git_fixture["working"] / "kanban/todo").mkdir(parents=True)
    result = invoke(git_fixture, "plan", "--json")
    assert "product checkout" in json.loads(result.stdout)["reason"]


def test_run_once_persists_idle_and_control_head(git_fixture):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket()})
    result = invoke(git_fixture, "run", "--once")
    assert result.returncode == 1
    assert "execution failed" in result.stdout
    state = next(git_fixture["state"].glob("*.json"))
    payload = json.loads(state.read_text())
    assert payload["phase"] == "idle"
    assert payload["handled_control_head"]
    assert "selected_ticket_id" not in payload


def test_persisted_execution_requires_control_revision_identity(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"])
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    with pytest.raises(DevlegateError, match="control revision identity"):
        devlegate._save_state(
            "agent_pending", local_head=code_head, remote_head=code_head,
            changed_paths="", selected_ticket_id="T-1", selected_ticket_body="work",
        )
    devlegate._save_state(
        "agent_pending", local_head=code_head, remote_head=code_head,
        changed_paths="", control_head=control_head,
        selected_ticket_id="T-1", selected_ticket_body="work",
    )
    assert devlegate._state["control_head"] == control_head


def test_status_retries_when_control_workflow_changes_during_snapshot(
    git_fixture, monkeypatch, capsys
):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket()})
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(git_fixture["config"], read_only=True)
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            path = git_fixture["control"] / "kanban/todo/T-1.md"
            path.write_text(ticket("Changed"))
            git(git_fixture["control"], "add", ".")
            git(git_fixture["control"], "commit", "-m", "snapshot change")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0
    assert changed
    assert "Changed" in capsys.readouterr().out


def test_plan_selects_deterministic_ticket_from_control(git_fixture):
    publish_control(git_fixture, "tickets", {
        "kanban/todo/ED-22.md": ticket("Later"),
        "kanban/todo/ED-17.md": ticket("Earlier"),
    })
    payload = json.loads(invoke(git_fixture, "plan", "--json").stdout)
    assert payload["action"] == "run-worker"
    assert payload["ticket"]["id"] == "ED-17"
    assert payload["observation"]["control"]["branch"] == "devlegate/control"


def test_ticket_validation_and_dependency_blockers(git_fixture):
    publish_control(git_fixture, "invalid", {"kanban/todo/ED-1.md": "not a ticket\n"})
    payload = json.loads(invoke(git_fixture, "plan", "--json").stdout)
    assert payload["action"] == "blocked"
    assert "invalid ticket" in payload["reason"]

    (git_fixture["control"] / "kanban/todo/ED-1.md").unlink()
    git(git_fixture["control"], "add", "-u")
    git(git_fixture["control"], "commit", "-m", "remove invalid ticket")


def test_missing_dependency_is_blocked(git_fixture):
    publish_control(git_fixture, "blocked", {
        "kanban/todo/ED-2.md": ticket("Waiting", depends=["ED-404"]),
    })
    payload = json.loads(invoke(git_fixture, "plan", "--json").stdout)
    assert payload["action"] == "blocked"
    assert "missing dependency" in payload["reason"]


def test_dirty_control_worktree_is_refused(git_fixture):
    (git_fixture["control"] / "dirty.txt").write_text("dirty\n")
    payload = json.loads(invoke(git_fixture, "plan", "--json").stdout)
    assert payload["action"] == "blocked"
    assert "dirty" in payload["reason"]


def test_control_divergence_is_observed_and_blocked(git_fixture):
    (git_fixture["control"] / "local.txt").write_text("local\n")
    git(git_fixture["control"], "add", ".")
    git(git_fixture["control"], "commit", "-m", "local control change")
    publish_control(git_fixture, "remote", {"remote.txt": "remote\n"}, sync=False)
    result = invoke(git_fixture, "run", "--once")
    assert result.returncode == 1
    assert "control branch cannot be fast-forwarded" in result.stdout


def test_dual_heads_are_distinct_and_workflow_is_control_only(git_fixture):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket()})
    code_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    control_head = git(git_fixture["control"], "rev-parse", "HEAD").stdout.strip()
    assert code_head != control_head
    assert not (git_fixture["working"] / "kanban").exists()
    assert (git_fixture["control"] / "kanban/todo/T-1.md").is_file()


def test_todo_fingerprint_ignores_noncanonical_artifacts(tmp_path):
    todo = tmp_path / "kanban/todo"
    todo.mkdir(parents=True)
    ticket_path = todo / "T-1.md"
    ticket_path.write_text(ticket())
    first = _todo_fingerprint(tmp_path, "kanban/todo")
    (todo / ".T-1.md.swp").write_text("temporary\n")
    assert _todo_fingerprint(tmp_path, "kanban/todo") == first
    assert _has_todo_files(tmp_path, "kanban/todo")


def test_dirty_diagnostics_include_classes(git_fixture):
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")
    (git_fixture["working"] / "staged.txt").write_text("staged\n")
    git(git_fixture["working"], "add", "staged.txt")
    result = invoke(git_fixture, "check")
    assert result.returncode == 1
    assert " M tracked.txt" in result.stdout
    assert "A  staged.txt" in result.stdout


def test_non_fast_forward_code_update_is_refused(git_fixture):
    (git_fixture["working"] / "local.txt").write_text("local\n")
    git(git_fixture["working"], "add", ".")
    git(git_fixture["working"], "commit", "-m", "local")
    (git_fixture["publisher"] / "remote.txt").write_text("remote\n")
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "remote")
    git(git_fixture["publisher"], "push", "origin", "main")
    result = invoke(git_fixture, "run", "--once")
    assert result.returncode == 1
    assert "cannot fast-forward" in result.stdout


def test_control_init_rejects_wrong_branch(git_fixture):
    assert invoke(git_fixture, "control", "init").returncode == 0
    git(git_fixture["control"], "switch", "-c", "wrong-control")
    result = invoke(git_fixture, "control", "init")
    assert result.returncode == 1
    assert "wrong branch" in result.stderr


def test_terminal_state_helpers_remain_importable():
    assert callable(Devlegate)
    assert issubclass(DevlegateError, Exception)


def test_retry_without_ticket_rejects_non_tty(monkeypatch, tmp_path):
    devlegate = object.__new__(Devlegate)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr(os, "isatty", lambda _fd: False)

    with pytest.raises(DevlegateError, match="interactive retry requires a terminal"):
        devlegate.retry()


def test_interactive_retry_selects_only_requested_candidate(monkeypatch, capsys):
    devlegate = object.__new__(Devlegate)
    candidates = (("T-1", "one", "first"), ("T-2", "two", "second"))
    selected = []
    monkeypatch.setattr(devlegate, "_retry_candidates", lambda: candidates)
    monkeypatch.setattr(devlegate, "_lock", lambda: nullcontext())
    monkeypatch.setattr(
        devlegate,
        "run_once",
        lambda: selected.append(devlegate._retry_ticket_id) or 0,
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "2")

    assert devlegate.retry() == 0
    assert selected == ["T-2"]
    output = capsys.readouterr().out
    assert "1) T-1" in output
    assert "2) T-2" in output


def test_interactive_retry_requires_explicit_enter_for_single_candidate(monkeypatch):
    devlegate = object.__new__(Devlegate)
    selected = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(devlegate, "_lock", lambda: nullcontext())
    monkeypatch.setattr(
        devlegate,
        "run_once",
        lambda: selected.append(devlegate._retry_ticket_id) or 0,
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "")

    assert devlegate.retry() == 0
    assert selected == ["T-1"]


def test_interactive_retry_rejects_invalid_selection_without_running(monkeypatch):
    devlegate = object.__new__(Devlegate)
    ran = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(devlegate, "run_once", lambda: ran.append(True) or 0)
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: "9")

    with pytest.raises(DevlegateError, match="invalid retry selection"):
        devlegate.retry()
    assert ran == []


def test_failed_execution_state_requires_product_head():
    state = {
        "phase": "idle",
        "failed_executions": {
            "T-1": {
                "execution_id": "attempt-1",
                "remote_head": "remote",
                "control_head": "control",
                "todo_fingerprint": "todo",
                "reason": "worker failed",
            }
        },
    }

    with pytest.raises(DevlegateError, match="failed execution metadata is invalid"):
        Devlegate._validate_state_invariant(state)
