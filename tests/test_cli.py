import os
import subprocess
import sys
from pathlib import Path

import pytest

from devlegate.cli import Devlegate, build_parser, main


@pytest.fixture
def git_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    publisher = tmp_path / "publisher"
    fake = tmp_path / "fake-opencode"
    marker = tmp_path / "agent-runs"

    git(bare.parent, "init", "--bare", bare)
    git(seed.parent, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "kanban/todo/.gitkeep").parent.mkdir(parents=True)
    (seed / "kanban/todo/.gitkeep").touch()
    (seed / "kanban/review/.gitkeep").parent.mkdir(parents=True)
    (seed / "kanban/review/.gitkeep").touch()
    (seed / "tracked.txt").write_text("initial\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "initial")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", bare, working)
    git(tmp_path, "clone", "-b", "main", bare, publisher)
    for repo in (working, publisher):
        git(repo, "config", "user.email", "test@example.com")
        git(repo, "config", "user.name", "Test User")

    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['FAKE_MARKER']).open('a').write('run\\n')\n"
        "if os.environ.get('FAKE_PROMPT'):\n"
        "    Path(os.environ['FAKE_PROMPT']).write_text(sys.argv[-1])\n"
        "mode = os.environ.get('FAKE_MODE', 'success')\n"
        "if mode == 'fail':\n"
        "    sys.exit(7)\n"
        "if mode == 'fail-dirty':\n"
        "    Path('agent-output.txt').write_text('unfinished\\n')\n"
        "    sys.exit(7)\n"
        "if mode == 'dirty':\n"
        "    Path('agent-output.txt').write_text('uncommitted\\n')\n"
        "    sys.exit(0)\n"
        "ticket = next((p for p in Path('kanban/todo').iterdir()\n"
        "               if p.is_file() and p.name != '.gitkeep'), None)\n"
        "if ticket is not None:\n"
        "    Path('kanban/review').mkdir(parents=True, exist_ok=True)\n"
        "    ticket.rename(Path('kanban/review') / ticket.name)\n"
        "    subprocess.run(['git', 'add', '-A'], check=True)\n"
        "    subprocess.run(['git', 'commit', '-m', 'agent work'], check=True)\n"
        "if mode == 'no-push':\n"
        "    sys.exit(0)\n"
        "subprocess.run(['git', 'push', 'origin', 'main'], check=True)\n"
    )
    fake.chmod(0o755)
    return {
        "bare": bare,
        "working": working,
        "publisher": publisher,
        "fake": fake,
        "marker": marker,
        "prompt_capture": tmp_path / 'captured-prompt.txt',
        "tmp": tmp_path,
    }


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True, capture_output=True, check=True
    )


def add_remote_revision(fixture, *, ticket=False):
    publisher = fixture["publisher"]
    (publisher / "remote.txt").write_text("remote\n")
    if ticket:
        (publisher / "kanban/todo").mkdir(parents=True, exist_ok=True)
        (publisher / "kanban/todo/ticket.md").write_text("implement\n")
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "remote update")
    git(publisher, "push")


def run_devlegate(fixture, *args, env_file=None, mode="success"):
    config = env_file or fixture["tmp"] / "config.env"
    if not config.exists():
        config.write_text(
            f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
            f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
            f"AGENT_PROMPT_FILE={fixture['tmp'] / 'prompt.txt'}\n"
            f"OPENCODE_BIN={fixture['fake']}\nOPENCODE_MODEL=fake\n"
            f"STATE_DIR={fixture['tmp'] / 'state'}\n"
        )
    (fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    environment = os.environ.copy()
    environment.update(
        FAKE_MARKER=str(fixture["marker"]),
        FAKE_MODE=mode,
        FAKE_PROMPT=str(fixture["prompt_capture"]),
    )
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "devlegate", "--once", *args, "--env", config],
        cwd=fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )


def test_help(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "--help"])
    try:
        main()
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert "usage: devlegate" in output


def test_version(capsys):
    parser = build_parser()
    try:
        parser.parse_args(["--version"])
    except SystemExit as error:
        assert error.code == 0

    output = capsys.readouterr().out
    assert output.startswith("devlegate ")


def test_no_remote_change_does_not_start_agent(git_fixture):
    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert not git_fixture["marker"].exists()
    assert "no remote changes" in result.stdout


def test_new_revision_without_tickets_fast_forwards_without_agent(git_fixture):
    add_remote_revision(git_fixture)

    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert (git_fixture["working"] / "remote.txt").exists()
    assert not git_fixture["marker"].exists()
    assert "no actionable ticket files" in result.stdout


def test_ticket_starts_agent_and_next_poll_does_not_repeat(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    first = run_devlegate(git_fixture)
    second = run_devlegate(git_fixture)

    assert first.returncode == 0
    assert second.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert not (git_fixture["working"] / "kanban/todo/ticket.md").exists()
    assert (git_fixture["working"] / "kanban/review/ticket.md").exists()
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout == git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout
    assert "no remote changes" in second.stdout


def test_dirty_worktree_refuses_execution(git_fixture):
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")

    result = run_devlegate(git_fixture)

    assert result.returncode == 1
    assert not git_fixture["marker"].exists()
    assert "working tree is dirty" in result.stdout


def test_non_fast_forward_update_is_refused(git_fixture):
    (git_fixture["working"] / "local.txt").write_text("local\n")
    git(git_fixture["working"], "add", ".")
    git(git_fixture["working"], "commit", "-m", "local update")
    add_remote_revision(git_fixture)

    result = run_devlegate(git_fixture)

    assert result.returncode == 1
    assert not git_fixture["marker"].exists()
    assert "cannot fast-forward" in result.stdout


@pytest.mark.parametrize(
    ("mode", "message"),
    [
        ("fail", "agent exited with status 7"),
        ("dirty", "left uncommitted repository changes"),
        ("no-push", "expected it to commit and push"),
    ],
)
def test_agent_failures_are_visible(git_fixture, mode, message):
    add_remote_revision(git_fixture, ticket=True)

    result = run_devlegate(git_fixture, mode=mode)

    expected_status = 7 if mode == "fail" else 1
    assert result.returncode == expected_status
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert message in result.stdout


def test_failed_agent_changes_are_recovered_on_next_poll(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
        f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text(
        "Todo=${TODO_DIRECTORY}\nReview={{REVIEW_DIRECTORY}}\n"
    )
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    monkeypatch.setenv("FAKE_MODE", "fail-dirty")
    devlegate = Devlegate(config)

    assert devlegate.run_once() == 7
    assert (git_fixture["working"] / "agent-output.txt").exists()

    monkeypatch.setenv("FAKE_MODE", "success")
    assert Devlegate(config).run_once() == 0
    prompt = git_fixture["prompt_capture"].read_text()
    assert f"Todo={git_fixture['working'] / 'kanban/todo'}" in prompt
    assert f"Review={git_fixture['working'] / 'kanban/review'}" in prompt
    assert "Do not start implementing todo tickets from scratch" in prompt
    assert not (git_fixture["working"] / "kanban/todo/ticket.md").exists()


def test_completed_merge_is_resumed_before_agent_start(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
        f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "success")
    devlegate = Devlegate(config)
    old_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(git_fixture["working"], "fetch", "origin", "main")
    remote_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    changed_paths = git(
        git_fixture["working"], "diff", "--name-only", old_head, remote_head
    ).stdout
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    devlegate._save_state(
        "merge_pending",
        local_head=old_head,
        remote_head=remote_head,
        changed_paths=changed_paths,
    )

    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert (git_fixture["working"] / "kanban/review/ticket.md").exists()


def test_once_and_explicit_env_file(git_fixture):
    env_file = git_fixture["tmp"] / "custom.env"
    add_remote_revision(git_fixture)

    result = run_devlegate(git_fixture, "--once", env_file=env_file)

    assert result.returncode == 0
    assert (git_fixture["working"] / "remote.txt").exists()


def test_watch_is_rejected(git_fixture):
    result = run_devlegate(git_fixture, "--watch")

    assert result.returncode == 2
    assert "unrecognized arguments: --watch" in result.stderr
