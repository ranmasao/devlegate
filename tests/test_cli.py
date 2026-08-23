import os
import pty
import shutil
import subprocess
import sys
import termios
from pathlib import Path

import pytest

from devlegate.cli import Devlegate, _preserve_terminal, build_parser, main


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
    (seed / "kanban/backlog/.gitkeep").parent.mkdir(parents=True)
    (seed / "kanban/backlog/.gitkeep").touch()
    (seed / "kanban/done/.gitkeep").parent.mkdir(parents=True)
    (seed / "kanban/done/.gitkeep").touch()
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
        "import json\n"
        "import os\n"
        "import subprocess\n"
        "import sys\n"
        "from pathlib import Path\n"
        "Path(os.environ['FAKE_MARKER']).open('a').write('run\\n')\n"
        "if os.environ.get('FAKE_PROMPT'):\n"
        "    Path(os.environ['FAKE_PROMPT']).write_text(sys.argv[-1])\n"
        "if os.environ.get('FAKE_STDIN'):\n"
        "    Path(os.environ['FAKE_STDIN']).write_text(\n"
        "        f'{sys.stdin.isatty()}:{sys.stdin.read(1)!r}'\n"
        "    )\n"
        "mode = os.environ.get('FAKE_MODE', 'success')\n"
        "output_mode = os.environ.get('FAKE_OUTPUT_MODE')\n"
        "payload = ('before\\n\\x1b[?1049h\\ninside\\n'\n"
        "           '\\x1b[1;24r\\nafter\\n\\x1b[?1h\\n')\n"
        "if output_mode == 'stdout':\n"
        "    event = {'type': 'text', 'part': {'text': payload}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'tool':\n"
        "    event = {'type': 'tool_use', 'part': {'tool': 'bash',\n"
        "        'state': {'status': 'completed', 'output': payload}}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'tool-error':\n"
        "    event = {'type': 'tool_use', 'part': {'tool': 'bash',\n"
        "        'state': {'status': 'error', 'error': payload}}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'tool-ordinary':\n"
        "    output = 'collecting tests...\\ntest_a PASSED\\ntest_b FAILED'\n"
        "    event = {'type': 'tool_use', 'part': {'tool': 'bash',\n"
        "        'state': {'status': 'completed', 'output': output}}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'error-event':\n"
        "    event = {'type': 'error', 'error': {'name': 'APIError',\n"
        "        'data': {'message': payload}}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'stderr':\n"
        "    print(payload, file=sys.stderr, end='', flush=True)\n"
        "elif output_mode == 'ordinary':\n"
        "    event = {'type': 'text', 'part': {'text':\n"
        "        'Starting worker\\nProgress: compiling ' + chr(0x2603)}}\n"
        "    print(json.dumps(event), flush=True)\n"
        "elif output_mode == 'malformed':\n"
        "    print('not-json', flush=True)\n"
        "if mode == 'fail':\n"
        "    sys.exit(7)\n"
        "if mode == 'fail-dirty':\n"
        "    Path('agent-output.txt').write_text('unfinished\\n')\n"
        "    sys.exit(7)\n"
        "if mode == 'observe':\n"
        "    sys.exit(0)\n"
        "if mode == 'dirty':\n"
        "    Path('agent-output.txt').write_text('uncommitted\\n')\n"
        "    sys.exit(0)\n"
        "if mode == 'blocked':\n"
        "    sys.exit(0)\n"
        "ticket = next((p for p in Path('kanban/todo').iterdir()\n"
        "               if p.is_file() and p.name != '.gitkeep'), None)\n"
        "if ticket is not None:\n"
        "    Path('kanban/review').mkdir(parents=True, exist_ok=True)\n"
        "    ticket.rename(Path('kanban/review') / ticket.name)\n"
        "    subprocess.run(['git', 'add', '-A'], check=True, capture_output=True)\n"
        "    subprocess.run(\n"
        "        ['git', 'commit', '-m', 'agent work'],\n"
        "        check=True, capture_output=True\n"
        "    )\n"
        "if mode == 'no-push':\n"
        "    sys.exit(0)\n"
        "subprocess.run(\n"
        "    ['git', 'push', 'origin', 'main'], check=True, capture_output=True\n"
        ")\n"
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
    remote_file = publisher / "remote.txt"
    remote_file.write_text(
        remote_file.read_text() + "remote\n" if remote_file.exists() else "remote\n"
    )
    if ticket:
        (publisher / "kanban/todo").mkdir(parents=True, exist_ok=True)
        (publisher / "kanban/todo/ticket.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Implement ticket"\n'
            "---\nimplement\n"
        )
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "remote update")
    git(publisher, "push")


def replace_remote_tickets(fixture, tickets):
    publisher = fixture["publisher"]
    todo = publisher / "kanban/todo"
    for path in todo.glob("*.md"):
        path.unlink()
    for ticket_id, title, body, depends_on in tickets:
        lines = [
            "---",
            '"type": "devlegate.ticket"',
            f'"title": "{title}"',
        ]
        if depends_on:
            lines.append('"depends_on":')
            lines.extend(f'  - "{dependency}"' for dependency in depends_on)
        lines.extend(["---", body])
        (todo / f"{ticket_id}.md").write_text("\n".join(lines) + "\n")
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "replace tickets")
    git(publisher, "push")


def run_devlegate(fixture, *args, env_file=None, mode="success", output_mode=None):
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
        FAKE_STDIN=str(fixture["tmp"] / "stdin-observation.txt"),
    )
    if output_mode:
        environment["FAKE_OUTPUT_MODE"] = output_mode
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


def test_terminal_state_is_restored_after_worker_changes_pty(capsys, monkeypatch):
    master, slave = pty.openpty()
    stream = os.fdopen(os.dup(slave), "r")
    monkeypatch.setattr(sys, "stdin", stream)
    original = termios.tcgetattr(slave)
    changed = list(original)
    changed[1] ^= termios.ONLCR

    try:
        with _preserve_terminal():
            termios.tcsetattr(slave, termios.TCSANOW, changed)
            assert termios.tcgetattr(slave) == changed
        assert termios.tcgetattr(slave) == original
    finally:
        stream.close()
        os.close(master)
        os.close(slave)
    capsys.readouterr()


def test_tool_output_control_sequences_are_rendered_as_inert_text(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    direct = run_devlegate(git_fixture, mode="observe", output_mode="tool")

    assert direct.returncode == 0
    output = direct.stdout + direct.stderr
    assert "OpenCode tool: bash" in output
    assert "before" in output
    assert "inside" in output
    assert "after" in output
    assert "\\x1b[?1049h" in output
    assert "\\x1b[1;24r" in output
    assert "\\x1b[?1h" in output
    assert "\x1b" not in output


def test_worker_control_sequences_on_stderr_are_rendered_as_inert_text(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    result = run_devlegate(git_fixture, mode="observe", output_mode="stderr")

    assert result.returncode == 0
    assert "before" in result.stderr
    assert "\\x1b[?1049h" in result.stderr
    assert "\x1b" not in result.stderr


def test_failed_tool_output_is_visible_and_inert(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    result = run_devlegate(git_fixture, mode="observe", output_mode="tool-error")

    assert result.returncode == 0
    assert "OpenCode tool: bash" in result.stdout
    assert "OpenCode tool failed: before" in result.stdout
    assert "\\x1b[?1049h" in result.stdout
    assert "\x1b" not in result.stdout


def test_successful_tool_output_remains_readable(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    result = run_devlegate(
        git_fixture, mode="observe", output_mode="tool-ordinary"
    )

    assert result.returncode == 0
    assert "OpenCode tool: bash" in result.stdout
    assert "collecting tests...\ntest_a PASSED\ntest_b FAILED" in result.stdout


def test_open_code_error_event_message_is_visible_and_inert(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    result = run_devlegate(git_fixture, mode="observe", output_mode="error-event")

    assert result.returncode == 0
    assert "OpenCode error: before" in result.stdout
    assert "\\x1b[?1049h" in result.stdout
    assert "\x1b" not in result.stdout


def test_worker_ordinary_output_remains_readable_and_stdin_is_headless(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    result = run_devlegate(git_fixture, mode="observe", output_mode="ordinary")

    assert result.returncode == 0
    assert "Starting worker" in result.stdout
    assert "Progress: compiling" in result.stdout
    assert chr(0x2603) in result.stdout
    assert (git_fixture["tmp"] / "stdin-observation.txt").read_text() == "False:''"


def test_malformed_worker_output_fails_closed(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    result = run_devlegate(git_fixture, mode="observe", output_mode="malformed")

    assert result.returncode == 1
    assert "OpenCode protocol error: invalid JSON event on stdout" in result.stdout
    assert "not-json" not in result.stdout
    assert "not-json" not in result.stderr


def test_dispatches_one_sorted_ticket_without_orchestration_metadata(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture,
        [
            ("ED-18", "Second", "second body", []),
            ("ED-17", "First", "first body", ["ED-10"]),
        ],
    )
    done = git_fixture["publisher"] / "kanban/done"
    (done / "ED-10.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n'
        "---\ndone body\n"
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "add done dependency")
    git(git_fixture["publisher"], "push")
    result = run_devlegate(git_fixture, mode="observe")

    assert result.returncode == 0
    prompt = git_fixture["prompt_capture"].read_text()
    assert "Assigned ticket ID: ED-17" in prompt
    assert "first body" in prompt
    assert "second body" not in prompt
    assert '"type": "devlegate.ticket"' not in prompt
    assert '"depends_on"' not in prompt
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_blocked_todo_graph_does_not_start_worker(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture, [("ED-11", "Blocked", "blocked body", ["ED-10"])]
    )
    review = git_fixture["publisher"] / "kanban/review/ED-10.md"
    review.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Under review"\n'
        "---\nreview body\n"
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "add review dependency")
    git(git_fixture["publisher"], "push")

    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert not git_fixture["marker"].exists()
    assert "blocked by unfinished dependencies" in result.stdout


def test_recovery_keeps_persisted_ticket_identity(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture,
        [
            ("ED-17", "First", "first body", []),
            ("ED-18", "Second", "second body", []),
        ],
    )
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "observe")
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ED-18",
    )

    assert Devlegate(config).run_once() == 0
    prompt = git_fixture["prompt_capture"].read_text()
    assert "Assigned ticket ID: ED-18" in prompt
    assert "second body" in prompt
    assert "first body" not in prompt


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


def test_synchronized_checkout_still_detects_new_todo_generation(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")

    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_changed_todo_fingerprint_starts_new_generation(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    first = run_devlegate(git_fixture)
    assert first.returncode == 0

    todo = git_fixture["working"] / "kanban/todo"
    (todo / "new-ticket.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "New ticket"\n'
        "---\nnew work\n"
    )
    git(git_fixture["working"], "add", ".")
    git(git_fixture["working"], "commit", "-m", "local todo update")
    git(git_fixture["working"], "push", "origin", "main")

    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]


def test_local_ahead_changed_todo_generation_preserves_heads(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    assert run_devlegate(git_fixture, mode="blocked").returncode == 0
    remote_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()

    ticket = git_fixture["working"] / "kanban/todo/ticket.md"
    ticket.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Implement ticket"\n'
        "---\nchanged local work\n"
    )
    git(git_fixture["working"], "add", "kanban/todo/ticket.md")
    git(git_fixture["working"], "commit", "-m", "local todo update")
    local_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()

    result = run_devlegate(git_fixture, mode="blocked")

    assert result.returncode == 1
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == local_head
    assert git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip() == remote_head
    state_file = next((git_fixture["tmp"] / "state").glob("*.json"))
    state = state_file.read_text()
    assert f'"local_head": "{local_head}"' in state
    assert f'"remote_head": "{remote_head}"' in state


def test_local_ahead_agent_pending_restart_executes_once(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    assert run_devlegate(git_fixture, mode="blocked").returncode == 0
    config = git_fixture["tmp"] / "config.env"
    remote_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    ticket = git_fixture["working"] / "kanban/todo/ticket.md"
    ticket.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Implement ticket"\n'
        "---\nchanged local work\n"
    )
    git(git_fixture["working"], "add", "kanban/todo/ticket.md")
    git(git_fixture["working"], "commit", "-m", "local todo update")
    local_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()

    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "blocked")
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=local_head,
        remote_head=remote_head,
        changed_paths="",
        selected_ticket_id="ticket",
    )

    assert Devlegate(config).run_once() == 1
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == local_head
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
        == remote_head
    )


def test_unchanged_blocked_todo_is_not_redispatched(git_fixture):
    add_remote_revision(git_fixture, ticket=True)

    first = run_devlegate(git_fixture, mode="blocked")
    second = run_devlegate(git_fixture, mode="blocked")

    assert first.returncode == 0
    assert second.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert "unchanged todo is already handled" in second.stdout


def test_remote_revision_reconsiders_unchanged_blocked_todo_once(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    assert run_devlegate(git_fixture, mode="blocked").returncode == 0
    add_remote_revision(git_fixture)

    result = run_devlegate(git_fixture, mode="blocked")

    assert result.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]


def test_dirty_diagnostics_include_paths_and_classes(git_fixture):
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")
    (git_fixture["working"] / "staged.txt").write_text("staged\n")
    git(git_fixture["working"], "add", "staged.txt")
    (git_fixture["working"] / "untracked.txt").write_text("untracked\n")

    result = run_devlegate(git_fixture)

    assert result.returncode == 1
    assert " M tracked.txt" in result.stdout
    assert "A  staged.txt" in result.stdout
    assert "?? untracked.txt" in result.stdout
    assert "tracked modified: 1" in result.stdout
    assert "staged: 1" in result.stdout
    assert "untracked: 1" in result.stdout


def test_dirty_diagnostics_report_conflicted_paths(git_fixture):
    publisher = git_fixture["publisher"]
    (publisher / "tracked.txt").write_text("remote change\n")
    git(publisher, "add", "tracked.txt")
    git(publisher, "commit", "-m", "remote conflicting update")
    git(publisher, "push")
    (git_fixture["working"] / "tracked.txt").write_text("local change\n")
    git(git_fixture["working"], "add", "tracked.txt")
    git(git_fixture["working"], "commit", "-m", "local conflicting update")
    git(git_fixture["working"], "fetch", "origin", "main")
    subprocess.run(
        ["git", "merge", "origin/main"],
        cwd=git_fixture["working"],
        text=True,
        capture_output=True,
        check=False,
    )

    result = run_devlegate(git_fixture)

    assert result.returncode == 1
    assert "UU tracked.txt" in result.stdout
    assert "conflicted: 1" in result.stdout


def test_dirty_diagnostics_are_suppressed_until_state_changes(
    git_fixture, monkeypatch, capsys
):
    config = git_fixture["tmp"] / "config.env"
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")

    assert devlegate.run_once() == 1
    first = capsys.readouterr().out
    assert devlegate.run_once() == 1
    second = capsys.readouterr().out
    (git_fixture["working"] / "other.txt").write_text("other\n")
    assert devlegate.run_once() == 1
    changed = capsys.readouterr().out
    (git_fixture["working"] / "tracked.txt").write_text("initial\n")
    (git_fixture["working"] / "other.txt").unlink()
    assert devlegate.run_once() == 0
    clean = capsys.readouterr().out

    assert "working tree became dirty" in first
    assert "working tree became dirty" not in second
    assert "other.txt" in changed
    assert "working tree is clean again" in clean


def test_unchanged_dirty_polls_do_not_repeat_refusal(capsys, git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("POLL_INTERVAL", "0")
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")
    devlegate = Devlegate(config)

    assert devlegate.run_once() == 1
    first = capsys.readouterr().out
    assert devlegate.run_once() == 1
    second = capsys.readouterr().out

    assert "refusing to pull or start the agent" in first
    assert "refusing to pull or start the agent" not in second


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
    assert "local HEAD:" in result.stdout
    assert "remote HEAD:" in result.stdout
    assert "merge base:" in result.stdout
    assert "local ahead:" in result.stdout
    assert "local behind:" in result.stdout


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
    assert "same assigned ticket" in prompt
    assert not (git_fixture["working"] / "kanban/todo/ticket.md").exists()


def test_clean_recovery_pending_still_runs_one_recovery(git_fixture, monkeypatch):
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
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    monkeypatch.setenv("FAKE_MODE", "fail")

    assert Devlegate(config).run_once() == 7
    monkeypatch.setenv("FAKE_MODE", "success")
    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]
    assert (git_fixture["working"] / "kanban/review/ticket.md").exists()


def test_interrupted_recovery_with_dirty_tree_requires_manual_intervention(
    git_fixture, monkeypatch
):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    (git_fixture["working"] / "unfinished.txt").write_text("unfinished\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    devlegate = Devlegate(config)
    devlegate._save_state("recovery_running")
    restarted = Devlegate(config)

    assert restarted.run_once() == 1
    assert not git_fixture["marker"].exists()
    state_file = next((config.parent / "state").glob("*.json"))
    assert '"phase": "recovery_failed"' in state_file.read_text()


def test_interrupted_recovery_with_clean_tree_clears_state(
    git_fixture, monkeypatch
):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    devlegate = Devlegate(config)
    devlegate._save_state("recovery_running")
    restarted = Devlegate(config)

    assert restarted.run_once() == 0
    assert not git_fixture["marker"].exists()
    state_file = next((config.parent / "state").glob("*.json"))
    assert '"phase": "idle"' in state_file.read_text()


def test_interrupted_recovery_is_never_retried(
    git_fixture, monkeypatch
):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    (git_fixture["working"] / "unfinished.txt").write_text("unfinished\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    devlegate = Devlegate(config)
    devlegate._save_state("recovery_running")
    restarted = Devlegate(config)

    assert restarted.run_once() == 1
    assert restarted.run_once() == 1
    assert not git_fixture["marker"].exists()


def test_failed_recovery_is_not_retried_and_manual_cleanup_resumes(
    git_fixture, monkeypatch
):
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
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    monkeypatch.setenv("FAKE_MODE", "fail-dirty")

    assert Devlegate(config).run_once() == 7
    assert Devlegate(config).run_once() == 7
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]
    state_files = list((config.parent / "state").glob("*.json"))
    assert '"phase": "recovery_failed"' in state_files[0].read_text()

    (git_fixture["working"] / "agent-output.txt").unlink()
    add_remote_revision(git_fixture)
    monkeypatch.setenv("FAKE_MODE", "success")
    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run", "run"]


def test_pending_revision_is_resumed_before_new_remote_revision(
    git_fixture, monkeypatch, capsys
):
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
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    old_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(git_fixture["working"], "fetch", "origin", "main")
    revision_a = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    changed_paths = git(
        git_fixture["working"], "diff", "--name-only", old_head, revision_a
    ).stdout
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=revision_a,
        remote_head=revision_a,
        changed_paths=changed_paths,
        selected_ticket_id="ticket",
    )
    add_remote_revision(git_fixture)
    revision_b = git(git_fixture["publisher"], "rev-parse", "HEAD").stdout.strip()

    monkeypatch.setenv("FAKE_MODE", "observe")
    assert Devlegate(config).run_once() == 0
    output = capsys.readouterr().out
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == revision_b
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
        == revision_b
    )
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert "no remote changes" not in output


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
        selected_ticket_id="ticket",
    )

    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert (git_fixture["working"] / "kanban/review/ticket.md").exists()


def test_merge_pending_at_old_head_performs_persisted_merge(
    git_fixture, monkeypatch
):
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
    target_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    changed_paths = git(
        git_fixture["working"], "diff", "--name-only", old_head, target_head
    ).stdout
    devlegate._save_state(
        "merge_pending",
        local_head=old_head,
        remote_head=target_head,
        changed_paths=changed_paths,
        selected_ticket_id="ticket",
    )

    assert devlegate.run_once() == 0
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() != old_head
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


def test_default_state_directory_uses_xdg(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setenv("XDG_STATE_HOME", str(git_fixture["tmp"] / "xdg-state"))
    assert Devlegate(config).state_dir == git_fixture["tmp"] / "xdg-state/devlegate"


def test_default_state_directory_uses_home_without_xdg(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.delenv("XDG_STATE_HOME", raising=False)
    monkeypatch.setattr(
        "devlegate.cli.Path.home", lambda: git_fixture["tmp"] / "home"
    )
    assert (
        Devlegate(config).state_dir
        == git_fixture["tmp"] / "home/.local/state/devlegate"
    )


def test_external_flock_executable_is_not_required(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    real_which = shutil.which
    monkeypatch.setattr(
        "devlegate.cli.shutil.which",
        lambda name: None if name == "flock" else real_which(name),
    )
    assert Devlegate(config).current_branch == "main"


def test_env_example_uses_optional_prompt_paths():
    content = Path(__file__).parents[1].joinpath(".env.example").read_text()
    assert "AGENT_PROMPT_FILE=/path/to/agent-prompt.txt" not in content
    assert "# AGENT_PROMPT_FILE=" in content


def test_check_is_read_only_and_reports_ready(git_fixture):
    add_remote_revision(git_fixture)
    result = run_devlegate(git_fixture, "--check")

    assert result.returncode == 0
    assert "Devlegate 0.2.4 preflight" in result.stdout
    assert "Ready." in result.stdout
    assert not (git_fixture["working"] / "remote.txt").exists()
    assert not git_fixture["marker"].exists()
    assert "known remote HEAD:" in result.stdout
    assert "todo files: 0" in result.stdout
    assert "todo fingerprint:" in result.stdout
    assert "Ticket storage:" in result.stdout
    assert "Runnable tickets: 0" in result.stdout
    assert "work generation differs from persisted: True" in result.stdout
    assert "--check does not fetch" in result.stdout


def test_check_reports_dirty_details(git_fixture):
    (git_fixture["working"] / "tracked.txt").write_text("changed\n")

    result = run_devlegate(git_fixture, "--check")

    assert result.returncode == 1
    assert "working tree clean" in result.stdout
    assert "Dirty working tree details:" in result.stdout
    assert " M tracked.txt" in result.stdout


def test_check_reports_invalid_configuration(git_fixture):
    config = git_fixture["tmp"] / "invalid.env"
    config.write_text("REMOTE_BRANCH=main\n")

    result = run_devlegate(git_fixture, "--check", env_file=config)

    assert result.returncode == 1
    assert "FAIL  configuration" in result.stdout
    assert "OPENCODE_MODEL is required" in result.stdout
