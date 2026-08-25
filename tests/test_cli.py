import json
import os
import pty
import shutil
import subprocess
import sys
import termios
from pathlib import Path

import pytest

from devlegate.cli import (
    Devlegate,
    DevlegateError,
    _has_todo_files,
    _preserve_terminal,
    _todo_fingerprint,
    build_parser,
    main,
)


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
        "import re\n"
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
        "               if p.is_file() and re.fullmatch(\n"
        "                   r'[A-Za-z0-9][A-Za-z0-9_-]*\\.md', p.name)), None)\n"
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


def run_status(fixture, *args, env_file=None):
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
    environment["PYTHONPATH"] = str(Path(__file__).parents[1] / "src")
    return subprocess.run(
        [sys.executable, "-m", "devlegate", "status", *args, "--env", config],
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
    assert "{run,check,status}" in output


def test_command_parser_exposes_run_check_and_status():
    parser = build_parser()

    assert parser.parse_args(["run"]).command == "run"
    assert parser.parse_args(["run", "--once"]).once
    assert parser.parse_args(["check"]).command == "check"
    status = parser.parse_args(["status", "--json"])
    assert status.command == "status"
    assert status.json


def test_status_text_reports_one_observed_snapshot(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")

    result = run_status(git_fixture)

    assert result.returncode == 0
    assert "Execution:" in result.stdout
    assert "phase: idle" in result.stdout
    assert "Repository:" in result.stdout
    assert "known remote HEAD:" in result.stdout
    assert "Tickets:" in result.stdout
    assert "todo: 1" in result.stdout
    assert "Runnable:" in result.stdout
    assert "ticket  Implement ticket" in result.stdout
    assert "Next:" in result.stdout


def test_status_json_matches_observed_ticket_projection(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")

    result = run_status(git_fixture, "--json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["execution"] == {
        "bound_ticket": None,
        "phase": "idle",
        "persisted_body": False,
    }
    assert document["tickets"]["counts"]["todo"] == 1
    assert [item["id"] for item in document["tickets"]["runnable"]] == ["ticket"]
    assert document["tickets"]["next"]["id"] == "ticket"
    assert "implement" not in result.stdout


def test_status_json_reports_direct_blockers_and_review(git_fixture):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture, [("ED-11", "Blocked", "blocked", ["ED-10"])]
    )
    review = git_fixture["publisher"] / "kanban/review/ED-10.md"
    review.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Review"\n---\nreview\n'
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "add review blocker")
    git(git_fixture["publisher"], "push")
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")

    result = run_status(git_fixture, "--json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["tickets"]["blocked"] == [
        {
            "blocked_by": [{"id": "ED-10", "state": "review"}],
            "id": "ED-11",
            "title": "Blocked",
        }
    ]
    assert document["tickets"]["review"] == [
        {"id": "ED-10", "title": "Review"}
    ]
    assert document["tickets"]["next"] is None


def test_status_does_not_fetch_remote_tracking_ref(git_fixture):
    add_remote_revision(git_fixture)
    git(git_fixture["working"], "fetch", "origin", "main")
    local_remote_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    add_remote_revision(git_fixture)

    result = run_status(git_fixture, "--json")

    assert result.returncode == 0
    document = json.loads(result.stdout)
    assert document["repository"]["remote_head"] == local_remote_head


def test_status_does_not_wait_for_runtime_lock(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    runner = Devlegate(config)
    lock = runner._lock()
    try:
        result = run_status(git_fixture)
    finally:
        lock.close()

    assert result.returncode == 0


def test_status_retries_when_persisted_state_changes(git_fixture, monkeypatch, capsys):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config, read_only=True)
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-state-before" and not changed:
            devlegate.state_dir.mkdir(parents=True, exist_ok=True)
            devlegate._state_file.write_text(
                json.dumps(
                    {
                        "phase": "agent_pending",
                        "local_head": head,
                        "remote_head": head,
                        "changed_paths": "",
                        "selected_ticket_id": "ED-17",
                        "selected_ticket_body": "bound body",
                    }
                )
            )
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)

    assert devlegate.status() == 0
    assert "phase: agent_pending" in capsys.readouterr().out


def test_status_retries_when_head_changes_during_collection(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config, read_only=True)
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-git-before" and not changed:
            (git_fixture["working"] / "status-head-change.txt").write_text("B\n")
            git(git_fixture["working"], "add", "status-head-change.txt")
            git(git_fixture["working"], "commit", "-m", "status head change")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)

    assert devlegate.status() == 0


def test_status_retries_when_ticket_moves_during_collection(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config, read_only=True)
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            git(
                git_fixture["working"],
                "mv",
                "kanban/todo/ticket.md",
                "kanban/review/ticket.md",
            )
            git(git_fixture["working"], "commit", "-m", "status ticket move")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)

    assert devlegate.status() == 0
    assert (git_fixture["working"] / "kanban/review/ticket.md").is_file()


def test_status_retries_when_ticket_contents_change(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    git(git_fixture["working"], "fetch", "origin", "main")
    git(git_fixture["working"], "merge", "--ff-only", "origin/main")
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config, read_only=True)
    changed = False

    def hook(point):
        nonlocal changed
        if point == "after-workflow-before" and not changed:
            ticket = git_fixture["working"] / "kanban/todo/ticket.md"
            ticket.write_text(ticket.read_text().replace("implement", "edited"))
            git(git_fixture["working"], "add", str(ticket))
            git(git_fixture["working"], "commit", "-m", "status ticket edit")
            changed = True

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)

    assert devlegate.status() == 0
    assert "edited" in (git_fixture["working"] / "kanban/todo/ticket.md").read_text()


def test_status_fails_after_bounded_instability(git_fixture, monkeypatch):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config, read_only=True)
    counter = 0

    def hook(point):
        nonlocal counter
        if point == "after-state-before":
            counter += 1
            devlegate.state_dir.mkdir(parents=True, exist_ok=True)
            devlegate._state_file.write_text(
                json.dumps({"phase": "idle", "counter": counter})
            )

    monkeypatch.setattr(devlegate, "_status_snapshot_hook", hook)

    with pytest.raises(
        DevlegateError,
        match="project state changed while status snapshot was being collected",
    ):
        devlegate.status()


def test_status_reports_stable_malformed_ticket(git_fixture):
    todo = git_fixture["working"] / "kanban/todo"
    (todo / "ED-17.md").write_text("not a ticket\n")

    result = run_status(git_fixture)

    assert result.returncode == 1
    assert "invalid ticket" in result.stderr


def test_noncanonical_todo_artifacts_do_not_change_fingerprint_or_presence(tmp_path):
    todo = tmp_path / "kanban/todo"
    todo.mkdir(parents=True)
    canonical = todo / "ED-17.md"
    canonical.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Ticket"\n---\nbody\n'
    )
    first = _todo_fingerprint(tmp_path, "kanban/todo")
    assert _has_todo_files(tmp_path, "kanban/todo")

    temporary = todo / ".ED-17.md.swp"
    temporary.write_text("one\n")
    second = _todo_fingerprint(tmp_path, "kanban/todo")
    temporary.write_text("two\n")
    third = _todo_fingerprint(tmp_path, "kanban/todo")
    temporary.unlink()
    fourth = _todo_fingerprint(tmp_path, "kanban/todo")

    assert first == second == third == fourth
    canonical.write_text(canonical.read_text() + "changed\n")
    assert _todo_fingerprint(tmp_path, "kanban/todo") != first


def test_non_ticket_only_todo_has_no_work(tmp_path):
    todo = tmp_path / "kanban/todo"
    todo.mkdir(parents=True)
    for name in (".gitkeep", "notes.txt", ".foo.swp", "foo.tmp"):
        (todo / name).write_text("not a ticket\n")

    assert not _has_todo_files(tmp_path, "kanban/todo")
    assert _todo_fingerprint(tmp_path, "kanban/todo")[1] == 0


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
        selected_ticket_body="second body",
    )

    assert Devlegate(config).run_once() == 0
    prompt = git_fixture["prompt_capture"].read_text()
    assert "Assigned ticket ID: ED-18" in prompt
    assert "second body" in prompt
    assert "first body" not in prompt


def test_merge_pending_resume_settles_blocked_graph_without_worker(
    git_fixture, monkeypatch, capsys
):
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
    git(git_fixture["publisher"], "commit", "-m", "add blocked dependency")
    git(git_fixture["publisher"], "push")

    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    old_head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    git(git_fixture["working"], "fetch", "origin", "main")
    remote_head = git(
        git_fixture["working"], "rev-parse", "origin/main"
    ).stdout.strip()
    devlegate = Devlegate(config)
    devlegate._save_state(
        "merge_pending",
        local_head=old_head,
        remote_head=remote_head,
        changed_paths="",
    )
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "observe")

    assert devlegate.run_once() == 0
    assert not git_fixture["marker"].exists()
    state_file = next((config.parent / "state").glob("*.json"))
    state = state_file.read_text()
    assert '"phase": "idle"' in state
    assert "selected_ticket_id" not in state
    assert "blocked by unfinished dependencies" in capsys.readouterr().out
    assert Devlegate(config).run_once() == 0
    assert not git_fixture["marker"].exists()


def test_recovery_uses_review_artifact_and_persisted_body(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture,
        [("ED-18", "Other", "other body", [])],
    )
    review = git_fixture["publisher"] / "kanban/review/ED-17.md"
    review.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Assigned"\n'
        "---\nbody B\n"
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "move assigned ticket")
    git(git_fixture["publisher"], "push")
    config = git_fixture["tmp"] / "config.env"
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    result = run_devlegate(git_fixture, mode="observe")
    assert result.returncode == 0
    monkeypatch.chdir(git_fixture["working"])
    config.write_text(
        config.read_text().replace(
            str(git_fixture["tmp"] / "prompt.txt"),
            str(Path(__file__).parents[1] / "agent-prompt.txt"),
        )
    )
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    Devlegate(config)._save_state(
        "recovery_pending",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ED-17",
        selected_ticket_body="body A\n",
    )
    monkeypatch.setenv("FAKE_MODE", "observe")
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))

    assert Devlegate(config).run_once() == 0
    prompt = git_fixture["prompt_capture"].read_text()
    review_path = git_fixture["working"] / "kanban/review/ED-17.md"
    todo_path = git_fixture["working"] / "kanban/todo/ED-17.md"
    assert "Assigned ticket ID: ED-17" in prompt
    assert f"Assigned ticket file: {review_path}" in prompt
    assert "Assigned ticket state: review" in prompt
    assert "body A" in prompt
    assert "body B" not in prompt
    assert f"Assigned ticket file: {todo_path}" not in prompt
    assert "ED-18" not in prompt
    assert "ticket is already in review" in prompt
    assert "do not fabricate or repeat the move" in prompt
    assert "do not change the assigned ticket's current workflow state" in prompt
    assert "leave that ticket in todo" not in prompt
    assert "review -> todo" not in prompt
    assert "do not substitute another ticket" in prompt
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]


def test_recovery_unexpected_done_ticket_fails_closed(git_fixture, monkeypatch):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(git_fixture, [("ED-18", "Other", "other body", [])])
    done = git_fixture["publisher"] / "kanban/done/ED-17.md"
    done.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Assigned"\n'
        "---\nbody\n"
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "complete assigned ticket")
    git(git_fixture["publisher"], "push")
    config = git_fixture["tmp"] / "config.env"
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    assert run_devlegate(git_fixture, mode="observe").returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    monkeypatch.chdir(git_fixture["working"])
    Devlegate(config)._save_state(
        "recovery_pending",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ED-17",
        selected_ticket_body="body\n",
    )
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "observe")

    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "--once", "--env", config],
        cwd=git_fixture["working"],
        env={**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 1
    assert "unexpected recovery state done" in result.stderr
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_no_remote_change_does_not_start_agent(git_fixture):
    result = run_devlegate(git_fixture)

    assert result.returncode == 0
    assert not git_fixture["marker"].exists()
    assert "no remote changes" in result.stdout


def test_run_once_duplicate_ticket_is_a_workflow_blocker(git_fixture):
    publisher = git_fixture["publisher"]
    (publisher / "kanban/review/CORPUS-019.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Review"\n---\nreview\n'
    )
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "add review ticket")
    git(publisher, "push")
    (publisher / "kanban/todo/CORPUS-019.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Duplicate"\n---\ntodo\n'
    )
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "introduce duplicate ticket")
    git(publisher, "push")

    result = run_devlegate(git_fixture)

    assert result.returncode == 1
    assert "workflow became blocked" in result.stdout
    assert "duplicate ticket ID CORPUS-019" in result.stdout
    assert not git_fixture["marker"].exists()
    state = next((git_fixture["tmp"] / "state").glob("*.json")).read_text()
    assert '"phase": "merge_pending"' in state
    assert "handled_remote_head" not in state


def test_long_running_run_recovers_after_duplicate_ticket_fix(
    git_fixture, monkeypatch, capsys
):
    publisher = git_fixture["publisher"]
    (publisher / "kanban/review/CORPUS-019.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Review"\n---\nreview\n'
    )
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "add review ticket")
    git(publisher, "push")
    (publisher / "kanban/todo/CORPUS-019.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Duplicate"\n---\ntodo\n'
    )
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "introduce duplicate ticket")
    git(publisher, "push")
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
    sleeps = 0

    def controlled_sleep(_interval):
        nonlocal sleeps
        sleeps += 1
        if sleeps == 1:
            (publisher / "kanban/review/CORPUS-019.md").unlink()
            git(publisher, "add", ".")
            git(publisher, "commit", "-m", "repair duplicate ticket")
            git(publisher, "push")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr("devlegate.cli.time.sleep", controlled_sleep)

    with pytest.raises(KeyboardInterrupt):
        devlegate.run(once=False)
    output = capsys.readouterr().out
    assert output.count("workflow became blocked") == 1
    assert "workflow is valid again" in output
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_branch_change_after_startup_blocks_before_sync(git_fixture, monkeypatch):
    add_remote_revision(git_fixture)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    git(git_fixture["working"], "switch", "-c", "experiment")
    experiment_head = git(
        git_fixture["working"], "rev-parse", "HEAD"
    ).stdout.strip()
    add_remote_revision(git_fixture)

    assert devlegate.run(once=True) == 1
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == (
        experiment_head
    )
    assert not git_fixture["marker"].exists()


def test_branch_and_detached_checkout_recover_in_same_process(
    git_fixture, monkeypatch, capsys
):
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    git(git_fixture["working"], "switch", "-c", "experiment")
    polls = 0

    def switch_back(_interval):
        nonlocal polls
        polls += 1
        if polls == 1:
            git(git_fixture["working"], "switch", "main")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr("devlegate.cli.time.sleep", switch_back)
    with pytest.raises(KeyboardInterrupt):
        devlegate.run(once=False)
    assert "workflow became blocked" in capsys.readouterr().out

    git(git_fixture["working"], "checkout", "--detach", "HEAD")
    assert devlegate.run(once=True) == 1
    git(git_fixture["working"], "switch", "main")


def test_wrong_branch_never_merges_expected_remote(git_fixture, monkeypatch):
    add_remote_revision(git_fixture)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nAGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    git(git_fixture["working"], "switch", "-c", "experiment")
    experiment_head = git(
        git_fixture["working"], "rev-parse", "HEAD"
    ).stdout.strip()
    add_remote_revision(git_fixture)

    assert devlegate.run(once=True) == 1
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == (
        experiment_head
    )
    assert (
        git(
            git_fixture["working"], "symbolic-ref", "--short", "HEAD"
        ).stdout.strip()
        == "experiment"
    )


def test_dirty_early_return_does_not_clear_workflow_blocker(
    git_fixture, monkeypatch, capsys
):
    add_remote_revision(git_fixture)
    malformed = git_fixture["publisher"] / "kanban/todo/ED-17.md"
    malformed.write_text("not a ticket\n")
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "publish malformed ticket")
    git(git_fixture["publisher"], "push")
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

    assert devlegate.run(once=True) == 1
    first = capsys.readouterr().out
    (git_fixture["working"] / "uncommitted.txt").write_text("dirty\n")
    assert devlegate.run(once=True) == 1
    dirty = capsys.readouterr().out
    (git_fixture["working"] / "uncommitted.txt").unlink()
    assert devlegate.run(once=True) == 1
    unchanged = capsys.readouterr().out
    malformed.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Fixed"\n---\nwork\n'
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "repair malformed ticket")
    git(git_fixture["publisher"], "push")
    assert devlegate.run(once=True) == 0
    recovered = capsys.readouterr().out

    assert "workflow became blocked" in first
    assert "workflow is valid again" not in dirty
    assert "workflow is valid again" not in unchanged
    assert "workflow is valid again" in recovered
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_missing_managed_directory_blocks_run_once(git_fixture):
    config = git_fixture["tmp"] / "missing.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/missing\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")

    result = run_devlegate(git_fixture, env_file=config)

    assert result.returncode == 1
    assert "managed workflow directory is unavailable" in result.stdout
    assert not git_fixture["marker"].exists()


def test_graph_blocker_recovers_after_dependency_is_published(git_fixture):
    add_remote_revision(git_fixture)
    replace_remote_tickets(
        git_fixture, [("ED-18", "Blocked", "work", ["ED-17"])]
    )

    blocked = run_devlegate(git_fixture)

    assert blocked.returncode == 1
    assert "workflow became blocked" in blocked.stdout
    assert not git_fixture["marker"].exists()
    done = git_fixture["publisher"] / "kanban/done/ED-17.md"
    done.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\ndone\n'
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "publish dependency")
    git(git_fixture["publisher"], "push")

    repaired = run_devlegate(git_fixture)

    assert repaired.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_malformed_canonical_ticket_recovers_after_remote_repair(git_fixture):
    add_remote_revision(git_fixture)
    malformed = git_fixture["publisher"] / "kanban/todo/ED-17.md"
    malformed.write_text("not a ticket\n")
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "publish malformed ticket")
    git(git_fixture["publisher"], "push")

    blocked = run_devlegate(git_fixture)

    assert blocked.returncode == 1
    assert "workflow became blocked" in blocked.stdout
    assert not git_fixture["marker"].exists()
    malformed.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Fixed"\n---\nwork\n'
    )
    git(git_fixture["publisher"], "add", ".")
    git(git_fixture["publisher"], "commit", "-m", "repair malformed ticket")
    git(git_fixture["publisher"], "push")

    repaired = run_devlegate(git_fixture)

    assert repaired.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_long_running_run_recovers_after_missing_workflow_directory(
    git_fixture, monkeypatch, capsys
):
    config = git_fixture["tmp"] / "missing.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/missing\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    polls = 0

    def controlled_sleep(_interval):
        nonlocal polls
        polls += 1
        if polls == 1:
            missing = git_fixture["publisher"] / "kanban/missing"
            missing.mkdir()
            (missing / ".gitkeep").touch()
            git(git_fixture["publisher"], "add", "kanban/missing/.gitkeep")
            git(git_fixture["publisher"], "commit", "-m", "restore workflow directory")
            git(git_fixture["publisher"], "push")
        else:
            raise KeyboardInterrupt

    monkeypatch.setattr("devlegate.cli.time.sleep", controlled_sleep)

    with pytest.raises(KeyboardInterrupt):
        devlegate.run(once=False)
    output = capsys.readouterr().out
    assert "workflow became blocked" in output
    assert "workflow is valid again" in output
    assert not git_fixture["marker"].exists()


def test_identical_workflow_blockers_are_suppressed(git_fixture, monkeypatch, capsys):
    config = git_fixture["tmp"] / "missing.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/missing\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    monkeypatch.chdir(git_fixture["working"])
    devlegate = Devlegate(config)
    calls = 0

    def stop_after_two_polls(_interval):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise KeyboardInterrupt

    monkeypatch.setattr("devlegate.cli.time.sleep", stop_after_two_polls)

    with pytest.raises(KeyboardInterrupt):
        devlegate.run(once=False)
    assert capsys.readouterr().out.count("workflow became blocked") == 1


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
    state_file = next((git_fixture["tmp"] / "state").glob("*.json"))
    state = state_file.read_text()
    assert '"phase": "idle"' in state
    assert "selected_ticket_id" not in state
    assert "selected_ticket_body" not in state


def test_editor_sidecar_activity_does_not_redispatch_handled_todo(
    git_fixture
):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture, [("ED-17", "Ticket", "implement", [])]
    )

    first = run_devlegate(git_fixture, mode="blocked")
    assert first.returncode == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    todo = git_fixture["working"] / "kanban/todo"
    sidecar = todo / ".ED-17.md.swp"
    exclude = git_fixture["working"] / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + ".ED-17.md.swp\n")

    sidecar.write_text("first temporary contents\n")
    created = run_devlegate(git_fixture, mode="blocked")
    sidecar.write_text("changed temporary contents\n")
    changed = run_devlegate(git_fixture, mode="blocked")
    sidecar.unlink()
    deleted = run_devlegate(git_fixture, mode="blocked")

    assert [result.returncode for result in (created, changed, deleted)] == [0, 0, 0]
    assert all("unchanged todo is already handled" in result.stdout for result in (
        created,
        changed,
        deleted,
    ))
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_non_ticket_only_todo_does_not_launch_worker(git_fixture):
    todo = git_fixture["working"] / "kanban/todo"
    names = ("notes.txt", ".foo.swp", "foo.tmp")
    exclude = git_fixture["working"] / ".git/info/exclude"
    exclude.write_text(exclude.read_text() + "\n".join(names) + "\n")
    for name in names:
        (todo / name).write_text("not a ticket\n")

    result = run_devlegate(git_fixture, mode="success")

    assert result.returncode == 0
    assert not git_fixture["marker"].exists()
    assert "no remote changes" in result.stdout


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
        selected_ticket_body="changed local work",
    )

    assert Devlegate(config).run_once() == 1
    assert git_fixture["marker"].read_text().splitlines() == ["run", "run"]
    assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == local_head
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
        == remote_head
    )


def test_agent_pending_descendant_of_persisted_head_is_reconciled(
    git_fixture, monkeypatch
):
    add_remote_revision(git_fixture, ticket=True)
    replace_remote_tickets(
        git_fixture,
        [
            ("ticket", "Persisted", "implement", []),
            ("other", "Other", "do not select", []),
        ],
    )
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
        f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    working = git_fixture["working"]
    monkeypatch.chdir(working)
    git(working, "fetch", "origin", "main")
    git(working, "merge", "--ff-only", "origin/main")
    persisted_head = git(working, "rev-parse", "HEAD").stdout.strip()
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=persisted_head,
        remote_head=persisted_head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="implement",
    )
    (working / "local-progress.txt").write_text("local progress\n")
    git(working, "add", "local-progress.txt")
    git(working, "commit", "-m", "local progress")
    descendant_head = git(working, "rev-parse", "HEAD").stdout.strip()

    result = run_devlegate(git_fixture, mode="success")

    assert result.returncode == 0
    assert "persisted execution local_head does not match" not in result.stderr
    assert (
        git(working, "merge-base", "--is-ancestor", descendant_head, "HEAD").returncode
        == 0
    )
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert "Assigned ticket ID: ticket" in git_fixture["prompt_capture"].read_text()
    assert "do not select" not in git_fixture["prompt_capture"].read_text()


def test_agent_pending_rollback_fails_closed(git_fixture, monkeypatch):
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
    working = git_fixture["working"]
    monkeypatch.chdir(working)
    git(working, "fetch", "origin", "main")
    git(working, "merge", "--ff-only", "origin/main")
    persisted_remote_head = git(working, "rev-parse", "HEAD").stdout.strip()
    (working / "persisted-progress.txt").write_text("persisted progress\n")
    git(working, "add", "persisted-progress.txt")
    git(working, "commit", "-m", "persisted progress")
    persisted_local_head = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "update-ref", "refs/heads/main", persisted_remote_head)
    git(working, "checkout", "-f", "main")
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=persisted_local_head,
        remote_head=persisted_remote_head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="implement",
    )

    result = run_devlegate(git_fixture, mode="observe")

    assert result.returncode == 1
    assert "behind the persisted execution HEAD" in result.stderr
    assert not git_fixture["marker"].exists()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == persisted_remote_head


def test_agent_pending_divergence_fails_closed(git_fixture, monkeypatch):
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
    working = git_fixture["working"]
    monkeypatch.chdir(working)
    git(working, "fetch", "origin", "main")
    git(working, "merge", "--ff-only", "origin/main")
    persisted_remote_head = git(working, "rev-parse", "HEAD").stdout.strip()
    (working / "persisted-progress.txt").write_text("persisted progress\n")
    git(working, "add", "persisted-progress.txt")
    git(working, "commit", "-m", "persisted progress")
    persisted_local_head = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "update-ref", "refs/heads/main", persisted_remote_head)
    git(working, "checkout", "-f", "main")
    (working / "diverged-progress.txt").write_text("diverged progress\n")
    git(working, "add", "diverged-progress.txt")
    git(working, "commit", "-m", "diverged progress")
    divergent_head = git(working, "rev-parse", "HEAD").stdout.strip()
    Devlegate(config)._save_state(
        "agent_pending",
        local_head=persisted_local_head,
        remote_head=persisted_remote_head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="implement",
    )

    result = run_devlegate(git_fixture, mode="observe")

    assert result.returncode == 1
    assert "diverged from the persisted execution history" in result.stderr
    assert not git_fixture["marker"].exists()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == divergent_head


def test_legacy_unbound_pending_supersede_rebuilds_and_executes_once(
    git_fixture, monkeypatch, capsys
):
    add_remote_revision(git_fixture)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
        f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    working = git_fixture["working"]
    monkeypatch.chdir(working)
    git(working, "fetch", "origin", "main")
    git(working, "merge", "--ff-only", "origin/main")
    revision_one = git(working, "rev-parse", "HEAD").stdout.strip()
    devlegate = Devlegate(config)
    devlegate._state_file.write_text(
        '{"phase":"agent_pending","local_head":"%s",'
        '"remote_head":"%s","changed_paths":""}\n' % (revision_one, revision_one)
    )
    add_remote_revision(git_fixture, ticket=True)
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_PROMPT", str(git_fixture["prompt_capture"]))
    monkeypatch.setenv("FAKE_MODE", "success")

    restarted = Devlegate(config)
    assert restarted.run_once() == 0
    output = capsys.readouterr().out
    assert "superseding pending revision" in output
    assert "legacy unbound agent_pending state detected" in output
    assert "recovery state has no persisted selected ticket identity" not in output
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    assert "Assigned ticket ID: ticket" in git_fixture["prompt_capture"].read_text()

    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]
    state = next((config.parent / "state").glob("*.json")).read_text()
    assert '"phase": "idle"' in state
    assert "selected_ticket_id" not in state


def test_unbound_sync_without_runnable_work_settles_to_idle(
    git_fixture, monkeypatch
):
    add_remote_revision(git_fixture)
    config = git_fixture["tmp"] / "config.env"
    config.write_text(
        f"REMOTE_BRANCH=main\nTODO_PATH=kanban/todo\n"
        f"REVIEW_PATH=kanban/review\nPOLL_INTERVAL=0\n"
        f"AGENT_PROMPT_FILE={git_fixture['tmp'] / 'prompt.txt'}\n"
        f"OPENCODE_BIN={git_fixture['fake']}\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={git_fixture['tmp'] / 'state'}\n"
    )
    (git_fixture["tmp"] / "prompt.txt").write_text("fake prompt\n")
    working = git_fixture["working"]
    monkeypatch.chdir(working)
    git(working, "fetch", "origin", "main")
    git(working, "merge", "--ff-only", "origin/main")
    revision_one = git(working, "rev-parse", "HEAD").stdout.strip()
    devlegate = Devlegate(config)
    devlegate._state_file.write_text(
        '{"phase":"agent_pending","local_head":"%s",'
        '"remote_head":"%s","changed_paths":""}\n' % (revision_one, revision_one)
    )
    add_remote_revision(git_fixture)
    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "observe")

    assert Devlegate(config).run_once() == 0
    assert not git_fixture["marker"].exists()
    state = next((config.parent / "state").glob("*.json")).read_text()
    assert '"phase": "idle"' in state
    assert "selected_ticket_id" not in state
    assert Devlegate(config).run_once() == 0
    assert not git_fixture["marker"].exists()


def test_fresh_remote_update_binds_before_worker_start(git_fixture, monkeypatch):
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
    transitions = []
    save_state = devlegate._save_state

    def record_state(phase, **fields):
        transitions.append((phase, fields.copy()))
        save_state(phase, **fields)

    monkeypatch.setattr(devlegate, "_save_state", record_state)

    assert devlegate.run_once() == 0
    pending = [fields for phase, fields in transitions if phase == "agent_pending"]
    assert pending
    assert pending[0]["selected_ticket_id"] == "ticket"
    assert pending[0]["selected_ticket_body"] == "implement\n"
    phases = [phase for phase, _fields in transitions]
    assert phases.index("merge_pending") < phases.index("agent_pending")


def test_crash_after_sync_leaves_merge_pending_and_rebuilds(
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
    devlegate = Devlegate(config)

    def interrupt_selection():
        raise RuntimeError("simulated crash before ticket selection")

    monkeypatch.setattr(devlegate, "_ticket_store", interrupt_selection)
    add_remote_revision(git_fixture, ticket=True)
    with pytest.raises(RuntimeError, match="simulated crash"):
        devlegate.run_once()
    state = next((config.parent / "state").glob("*.json")).read_text()
    assert '"phase": "merge_pending"' in state
    assert "selected_ticket_id" not in state

    monkeypatch.setenv("FAKE_MARKER", str(git_fixture["marker"]))
    monkeypatch.setenv("FAKE_MODE", "success")
    assert Devlegate(config).run_once() == 0
    assert git_fixture["marker"].read_text().splitlines() == ["run"]


def test_partial_agent_pending_binding_fails_closed(git_fixture, monkeypatch):
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
    devlegate = Devlegate(config)
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    devlegate._state_file.write_text(
        '{"phase":"agent_pending","selected_ticket_id":"ticket",'
        '"selected_ticket_body":null,"local_head":"%s",'
        '"remote_head":"%s","changed_paths":""}\n' % (head, head)
    )

    result = run_devlegate(git_fixture, mode="observe")

    assert result.returncode == 1
    assert "invalid agent_pending state: selected ticket binding is incomplete" in (
        result.stderr
    )
    assert not git_fixture["marker"].exists()


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
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "recovery_running",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="unfinished",
    )
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
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "recovery_running",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="unfinished",
    )
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
    head = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "recovery_running",
        local_head=head,
        remote_head=head,
        changed_paths="",
        selected_ticket_id="ticket",
        selected_ticket_body="unfinished",
    )
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
        selected_ticket_body="implement",
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
    assert "Devlegate 0.2.5 preflight" in result.stdout
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
