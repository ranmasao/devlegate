import json

import pytest
from test_cli import git, publish_control, ticket

from devlegate.cli import Devlegate, DevlegateError


def read_only(fixture, monkeypatch, name):
    config = fixture["tmp"] / f"{name}.env"
    config.write_text(f"REMOTE_BRANCH=main\nSTATE_DIR={fixture['state']}\n")
    monkeypatch.chdir(fixture["working"])
    return Devlegate(config, read_only=True)


def commit_control(fixture, files, message):
    for relative, content in files.items():
        path = fixture["control"] / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(fixture["control"], "add", "-A")
    git(fixture["control"], "commit", "-m", message)


def test_snapshot_retries_when_runtime_state_changes(git_fixture, monkeypatch):
    devlegate = read_only(git_fixture, monkeypatch, "state-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-state-before" and not changed:
            devlegate.state_dir.mkdir(parents=True, exist_ok=True)
            devlegate._state_file.write_text(json.dumps({"phase": "idle", "n": 1}))
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0
    assert changed


def test_snapshot_retries_when_code_head_changes(git_fixture, monkeypatch):
    devlegate = read_only(git_fixture, monkeypatch, "code-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-git-before" and not changed:
            path = git_fixture["working"] / "race.txt"
            path.write_text("changed\n")
            git(git_fixture["working"], "add", "race.txt")
            git(git_fixture["working"], "commit", "-m", "code race")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0


def test_snapshot_retries_when_control_head_changes(git_fixture, monkeypatch):
    devlegate = read_only(git_fixture, monkeypatch, "control-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-git-before" and not changed:
            commit_control(
                git_fixture, {"control-race.txt": "changed\n"}, "control race"
            )
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0


def test_snapshot_retries_when_ticket_moves_and_changes(git_fixture, monkeypatch):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket()})
    devlegate = read_only(git_fixture, monkeypatch, "ticket-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            path = git_fixture["control"] / "kanban/todo/T-1.md"
            path.rename(git_fixture["control"] / "kanban/review/T-1.md")
            git(git_fixture["control"], "add", "-A")
            git(git_fixture["control"], "commit", "-m", "ticket move")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0
    assert changed


def test_snapshot_retries_when_ticket_contents_change(git_fixture, monkeypatch):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket()})
    devlegate = read_only(git_fixture, monkeypatch, "content-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            path = git_fixture["control"] / "kanban/todo/T-1.md"
            path.write_text(ticket("Changed"))
            git(git_fixture["control"], "add", "-A")
            git(git_fixture["control"], "commit", "-m", "ticket edit")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 0
    assert changed


def test_snapshot_retries_when_workflow_directory_kind_changes(
    git_fixture, monkeypatch
):
    devlegate = read_only(git_fixture, monkeypatch, "directory-race")
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            todo = git_fixture["control"] / "kanban/todo"
            (todo / ".gitkeep").unlink()
            todo.rmdir()
            todo.write_text("not a directory\n")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    assert devlegate.status() == 1
    assert changed


def test_snapshot_fails_after_continuous_instability(git_fixture, monkeypatch):
    devlegate = read_only(git_fixture, monkeypatch, "unstable")
    count = 0

    def hook(point):
        nonlocal count
        if point == "after-state-before":
            count += 1
            devlegate.state_dir.mkdir(parents=True, exist_ok=True)
            devlegate._state_file.write_text(json.dumps({"phase": "idle", "n": count}))

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)
    with pytest.raises(DevlegateError, match="project state changed"):
        devlegate.status()
