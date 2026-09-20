# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import dataclasses
import fcntl
import hashlib
import io
import json
import os
import shutil
import signal
import socket
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import nullcontext, redirect_stdout
from pathlib import Path

import pytest
from git_support import clone_world
from runtime_helpers import run_test_iteration
from service_harness import LiveService

from devlegate import __version__
from devlegate._vendor import nanoyaml
from devlegate.cli import (
    Devlegate,
    DevlegateError,
    ReadOnlyView,
    _todo_fingerprint,
    build_parser,
    main,
)
from devlegate.ipc_client import IPCClientError
from devlegate.ipc_client import request as ipc_request
from devlegate.ipc_server import UnixIPCServer
from devlegate.runtime import (
    BlockedReason,
    ExecutionPlan,
    GitObservation,
    StatusSnapshot,
)
from devlegate.runtime_locator import RuntimeAuthorityPresent, RuntimeLocator
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True, capture_output=True, check=True
    )


@pytest.fixture
def git_fixture(tmp_path, request):
    state = Path("/tmp") / (
        "devlegate-test-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:8]
    )
    request.addfinalizer(lambda: shutil.rmtree(state, ignore_errors=True))
    world = clone_world(
        tmp_path,
        baseline="empty-control",
        state=state,
        with_publisher=True,
    )
    bare = world["bare"]
    working = world["working"]
    publisher = world["publisher"]

    key = hashlib.sha256(str(working.resolve()).encode()).hexdigest()
    control = state / "worktrees" / key / "control"
    git(working, "fetch", "origin", "devlegate/control")
    git(
        working,
        "worktree",
        "add",
        "--track",
        "-b",
        "devlegate/control",
        control,
        "origin/devlegate/control",
    )
    publisher_control = tmp_path / "publisher-control"
    git(publisher, "fetch", "origin", "devlegate/control")
    git(
        publisher,
        "worktree",
        "add",
        "--track",
        "-b",
        "publisher-control",
        publisher_control,
        "origin/devlegate/control",
    )

    config = tmp_path / "devlegate.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\nPOLL_INTERVAL=0\n"
        f"STATE_DIR={state}\n"
    )
    return {
        "bare": bare,
        "working": working,
        "publisher": publisher,
        "control": control,
        "publisher_control": publisher_control,
        "config": config,
        "state": state,
        "tmp": tmp_path,
    }


@pytest.fixture
def cli_daemon(git_fixture, monkeypatch):
    """IPC server only; routing and views, not owner-side mutation coverage."""
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        engine.repo,
        "worktree",
        "move",
        git_fixture["control"],
        engine.control_worktree,
    )
    with redirect_stdout(io.StringIO()):
        assert engine.control_init() == 0
    authority = engine._lock()
    engine.ensure_hosted_views()
    engine.published_status_payload = lambda: engine.status_view().as_dict()
    engine.published_plan_view = engine.plan_view
    retry_candidates = engine.retry_candidates_view
    engine.published_retry_candidates_view = retry_candidates
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        yield engine
    finally:
        server.stop()
        engine.end_hosted_owner()
        authority.close()


def _short_runtime_config(git_fixture):
    suffix = hashlib.sha256(str(git_fixture["tmp"]).encode()).hexdigest()[:8]
    state = Path("/tmp") / f"devlegate-f1-{suffix}"
    config = git_fixture["tmp"] / "devlegate-f1.env"
    config.write_text(git_fixture["config"].read_text().replace(
        str(git_fixture["state"]), str(state)
    ))
    return config


@pytest.fixture
def plain_project_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    state = tmp_path / "state"
    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "README.md").write_text("project context\n")
    (seed / "docs").mkdir()
    (seed / "docs/architecture.md").write_text("architecture\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "ordinary project")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", bare, working)
    git(working, "config", "user.email", "test@example.com")
    git(working, "config", "user.name", "Test User")
    config = tmp_path / "devlegate.env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\nPOLL_INTERVAL=0\n"
        f"STATE_DIR={state}\n"
    )
    return {"bare": bare, "working": working, "config": config, "state": state}


def invoke(fixture, *args, env_file=None):
    args = list(args)
    if "--foreground" in args:
        args[args.index("--foreground")] = "foreground"
    elif "--once" in args:
        args[args.index("--once")] = "once"
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "devlegate",
            *args,
            "--env",
            env_file or fixture["config"],
        ],
        cwd=fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
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
    assert parser.parse_args(["once"]).command == "once"
    assert parser.parse_args(["foreground"]).command == "foreground"
    assert parser.parse_args(["control", "init"]).command == "control"
    control_reconcile = parser.parse_args(
        ["reconcile", "control", "--from", "a" * 40, "--to", "b" * 40]
    )
    assert control_reconcile.reconcile_command == "control"
    assert control_reconcile.from_head == "a" * 40
    assert control_reconcile.to_head == "b" * 40
    assert parser.parse_args(["status", "--json"]).output_format == "json"
    monkeypatch.setattr("sys.argv", ["devlegate", "--help"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    output = capsys.readouterr().out
    assert "usage: devlegate [--env FILE]\n  devlegate COMMAND ..." in output
    assert "{init,render,retry,reconcile,check,status,plan,stop,control}" not in output
    assert "ensure the persistent background service is running" in output
    assert "Service:" in output
    assert "Project:" in output
    assert "Recovery:" in output
    assert "Other:" in output
    assert "stop" in output and "stop the persistent service" in output
    assert "control" in output and "manage workflow history" in output
    for command in (
        "foreground",
        "once",
        "stop",
        "status",
        "plan",
        "init",
        "render",
        "check",
        "control",
        "retry",
        "reconcile",
        "version",
    ):
        assert (
            sum(line.startswith(f"  {command}") for line in output.splitlines()) == 1
        )
    assert (
        "  foreground     run the persistent service attached to this terminal"
        in output
    )
    assert "  reconcile      perform explicit reconciliation" in output
    assert build_parser().parse_args(["retry", "T-1"]).ticket_id == "T-1"


@pytest.mark.parametrize("command", ["run", "daemon"])
def test_removed_service_commands_are_unknown(command, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", command])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2
    assert f"unknown command '{command}'" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["ruun", "рун"])
def test_unknown_top_level_command_is_concise(command, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", command])

    with pytest.raises(SystemExit) as error:
        main()

    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert f"unknown command '{command}'" in stderr
    assert "devlegate --help" in stderr
    assert "usage:" not in stderr
    assert "{init,render,retry,check,status,plan,stop,control}" not in stderr


def test_invalid_subcommand_argument_is_concise(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "stop", "--definitely-invalid"])

    with pytest.raises(SystemExit) as error:
        main()

    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert "devlegate stop: unrecognized argument: --definitely-invalid" in stderr
    assert "devlegate stop --help" in stderr
    assert "usage:" not in stderr


def test_missing_commands_are_concise(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "foreground", "once"])
    with pytest.raises(SystemExit) as error:
        main()
    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert "devlegate foreground: unrecognized argument: once" in stderr

    monkeypatch.setattr("sys.argv", ["devlegate", "control"])
    with pytest.raises(SystemExit) as error:
        main()
    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert "devlegate control: a control command is required" in stderr
    assert "devlegate control --help" in stderr
    assert "usage:" not in stderr

    monkeypatch.setattr("sys.argv", ["devlegate", "reconcile"])
    with pytest.raises(SystemExit) as error:
        main()
    stderr = capsys.readouterr().err
    assert error.value.code == 2
    assert "devlegate reconcile: a reconcile command is required" in stderr
    assert "usage:" not in stderr


def test_reconcile_resume_routes_to_daemon_helper(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def resume(_env_file, ticket_id):
        accepted.append(ticket_id)
        return 0

    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr("devlegate.cli._reconcile_resume_daemon", resume)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "reconcile",
            "resume",
            "LAB-111",
            "--env",
            str(config),
        ],
    )
    assert main() == 0
    assert accepted == ["LAB-111"]
    assert capsys.readouterr().out == ""


def test_nested_command_help_uses_command_sections():
    parser = build_parser()
    top_level = parser.format_help()
    assert "usage: devlegate [--env FILE]\n  devlegate COMMAND ..." in top_level
    assert (
        "{init,render,retry,reconcile,check,status,plan,stop,control}"
        not in top_level
    )
    for argv, commands in (
        (["control"], ("init",)),
        (["reconcile"], ("update-base", "resume", "control")),
    ):
        target = parser
        for name in argv:
            target = target._subparsers._group_actions[0].choices[name]
        output = target.format_help()
        assert "Commands:" in output
        assert "positional arguments:\n  {" not in output
        for command in commands:
            assert command in output
        if argv == ["reconcile"]:
            resume = target._subparsers._group_actions[0].choices["resume"]
            leaf = resume.format_help()
            assert "ticket_id" in leaf
            assert "usage: devlegate reconcile resume" in leaf


@pytest.mark.parametrize(
    "argv, expected",
    [
        (["devlegate", "--help"], "usage: devlegate"),
        (["devlegate", "stop", "--help"], "usage: devlegate stop"),
        (["devlegate", "control", "--help"], "usage: devlegate control"),
    ],
)
def test_explicit_help_remains_detailed(argv, expected, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", argv)

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 0
    assert expected in capsys.readouterr().out


@pytest.mark.parametrize(
    "argv, snippets",
    [
        (
            ["init", "--help"],
            [
                "Create missing project-local agent workflow files.",
                "how to handle existing generated files",
            ],
        ),
        (
            ["render", "--help"],
            [
                "Render project-local agent workflow files",
                "check freshness without writing files",
            ],
        ),
        (
            ["stop", "--help"],
            ["Request an orderly shutdown", "configuration file to use"],
        ),
        (
            ["check", "--help"],
            [
                "Validate project setup without running a worker.",
                "configuration file to use",
            ],
        ),
        (
            ["status", "--help"],
            ["Show the current workflow status.", "emit machine-readable JSON"],
        ),
        (
            ["plan", "--help"],
            ["Show what Devlegate plans to do next.", "emit machine-readable JSON"],
        ),
        (
            ["retry", "--help"],
            ["Retry a failed or recoverable ticket execution.", "ticket to retry"],
        ),
        (
            ["control", "--help"],
            ["Manage the separate Git history that stores workflow data."],
        ),
        (
            ["control", "init", "--help"],
            [
                "Initialize or attach the separate workflow Git history.",
                "configuration file to use",
            ],
        ),
        (
            ["reconcile", "--help"],
            ["Handle a pending product-base change for an execution."],
        ),
        (
            ["reconcile", "update-base", "--help"],
            [
                "Update a ticket execution after the product base changes.",
                "ticket execution to update",
                "product branch to use as the new base",
            ],
        ),
        (
            ["reconcile", "resume", "--help"],
            [
                "Resume retained execution progress without changing the product base.",
                "ticket execution to resume",
            ],
        ),
    ],
)
def test_public_help_surfaces_are_successful_and_useful(
    argv, snippets, monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["devlegate", *argv])

    with pytest.raises(SystemExit) as error:
        main()

    output = capsys.readouterr().out
    assert error.value.code == 0
    assert f"usage: devlegate {' '.join(argv[:-1])}".rstrip() in output
    assert all(snippet in output for snippet in snippets)


def test_version_command(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "version"])

    assert main() == 0
    output = capsys.readouterr().out
    assert "Devlegate" in output
    assert __version__ in output


@pytest.mark.parametrize("flag", ["--yaml", "--json"])
def test_version_machine_formats(monkeypatch, capsys, flag):
    monkeypatch.setattr("sys.argv", ["devlegate", "version", flag])

    assert main() == 0
    output = capsys.readouterr().out
    value = (
        nanoyaml.loads(output)
        if flag == "--yaml"
        else json.loads(output)
    )
    assert value == {"program": "devlegate", "version": __version__}


def test_machine_format_flags_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["status", "--yaml", "--json"])
    assert error.value.code == 2


@pytest.mark.parametrize("old", ["--version", "--foreground", "--once"])
def test_removed_service_and_version_flags_are_rejected(old, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", old])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


def test_check_rejects_missing_project_context(git_fixture):
    working = git_fixture["working"]
    config = git_fixture["config"]
    (working / ".devlegate/project.md").unlink()

    result = invoke(git_fixture, "check", env_file=config)

    assert result.returncode == 1
    assert "project context manifest is missing" in result.stdout


def test_fresh_init_requires_external_adoption_before_control_check(
    plain_project_fixture,
):
    fixture = plain_project_fixture
    working = fixture["working"]
    before = (working / "README.md").read_bytes()
    assert git(working, "status", "--porcelain").stdout == ""

    initialized = invoke(fixture, "init")

    assert initialized.returncode == 0
    assert git(working, "status", "--porcelain").stdout != ""
    assert (working / ".devlegate/project.md").is_file()
    assert (working / "skills/architect/SKILL.md").is_file()
    assert (working / "skills/reviewer/SKILL.md").is_file()
    assert (working / "README.md").read_bytes() == before
    assert "README.md" not in (working / ".devlegate/project.md").read_text()
    assert invoke(fixture, "check").returncode == 1

    (working / ".devlegate/project.md").write_text(
        '---\n"type": "devlegate.project"\n"common":\n  - "README.md"\n---\n'
    )
    git(working, "add", ".")
    git(working, "commit", "-m", "adopt Devlegate bootstrap")
    assert git(working, "status", "--porcelain").stdout == ""

    assert invoke(fixture, "control", "init").returncode == 0
    ready = invoke(fixture, "check")
    assert ready.returncode == 0
    assert "Ready." in ready.stdout


def test_removed_top_level_forms_are_rejected(git_fixture):
    for args in (("run",), ("daemon",), ("--check",)):
        result = invoke(git_fixture, *args)
        assert result.returncode == 2


def test_bare_cli_starts_background_service_and_stop_ends_it(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    started = invoke(git_fixture)
    assert started.returncode == 0, started.stderr
    assert "service started; log:" in started.stdout
    locator = RuntimeLocator.from_env(git_fixture["config"])
    assert locator.daemon_authority_present()
    assert locator.socket_path.exists()

    status = invoke(git_fixture, "status", "--json")
    assert status.returncode == 0, status.stderr
    assert json.loads(status.stdout)["execution"]["phase"] == "idle"

    stopped = invoke(git_fixture, "stop")
    assert stopped.returncode == 0, stopped.stderr
    for _attempt in range(100):
        if not locator.daemon_authority_present():
            break
        time.sleep(0.02)
    assert not locator.daemon_authority_present()
    assert not locator.socket_path.exists()
    log_path = locator.state_dir / "logs" / f"{locator.state_key}.log"
    deadline = time.monotonic() + 5
    log = ""
    while time.monotonic() < deadline:
        log = log_path.read_text()
        if (
            "shutdown explicitly requested through devlegate stop" in log
            and "orderly shutdown complete (stop_command)" in log
        ):
            break
        time.sleep(0.02)
    assert "shutdown explicitly requested through devlegate stop" in log
    assert "orderly shutdown complete (stop_command)" in log
    assert log.count("---< D E V L E G A T E >---") == 1
    assert "mode    : background" in log
    assert "version :" in log
    assert "repo    :" in log
    assert "product :" in log
    assert "control :" in log
    assert "mode    :" in log
    assert "pid     :" in log


@pytest.mark.parametrize("start_args", [(), ("--foreground",), ("--once",)])
def test_service_start_forms_are_idempotent_for_healthy_owner(
    git_fixture, monkeypatch, start_args
):
    monkeypatch.chdir(git_fixture["working"])
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    assert invoke(git_fixture).returncode == 0
    try:
        repeated = invoke(git_fixture, *start_args)
        assert repeated.returncode == 0, repeated.stderr
        assert repeated.stdout.strip() == "Devlegate service is already running."
    finally:
        assert invoke(git_fixture, "stop").returncode == 0


def test_once_emits_one_startup_identity_block(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    result = invoke(git_fixture, "once")
    assert result.returncode == 0, result.stderr
    assert result.stdout.count("---< D E V L E G A T E >---") == 1
    assert "mode    : once" in result.stdout
    assert "version :" in result.stdout
    assert "repo    :" in result.stdout
    assert "product :" in result.stdout
    assert "control :" in result.stdout
    assert "pid     :" in result.stdout


def test_service_start_fails_closed_when_authority_has_no_healthy_ipc(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    locator = RuntimeLocator.from_env(git_fixture["config"])
    locator.lock_path.parent.mkdir(parents=True, exist_ok=True)
    with locator.lock_path.open("a+") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = invoke(git_fixture, "--foreground")
    assert result.returncode == 1
    assert "authority exists but its IPC endpoint is unavailable" in result.stderr


def test_stop_without_service_is_conclusive(git_fixture):
    result = invoke(git_fixture, "stop")
    assert result.returncode == 1
    assert result.stderr.strip() == "devlegate: service is not running"


def test_control_init_attaches_existing_orphan_branch_and_is_idempotent(git_fixture):
    assert not git_fixture["control"].exists() or git_fixture["control"].is_dir()
    assert invoke(git_fixture, "control", "init").returncode == 0
    structured = invoke(git_fixture, "control", "init", "--json")
    payload = json.loads(structured.stdout)
    assert payload["result"] == "already_attached"
    assert payload["control_worktree"]
    assert git_fixture["control"].is_dir()
    assert (
        git(git_fixture["control"], "symbolic-ref", "--short", "HEAD").stdout.strip()
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
    architect = git_fixture["working"] / "skills/architect/SKILL.md"
    reviewer = git_fixture["working"] / "skills/reviewer/SKILL.md"
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
    assert "configured remote" in result.stdout
    assert "origin" in result.stdout
    assert "known remote HEAD" in result.stdout
    assert "Not ready." in result.stdout


def test_check_reports_local_product_branch_ahead(git_fixture):
    (git_fixture["working"] / "local.txt").write_text("local\n")
    git(git_fixture["working"], "add", "local.txt")
    git(git_fixture["working"], "commit", "-m", "local")

    result = invoke(git_fixture, "check")

    assert "ahead/behind" in result.stdout
    assert "1 0" in result.stdout


def test_check_reports_local_product_branch_behind(git_fixture):
    publisher = git_fixture["publisher"]
    (publisher / "remote.txt").write_text("remote\n")
    git(publisher, "add", "remote.txt")
    git(publisher, "commit", "-m", "remote")
    git(publisher, "push", "origin", "main")
    git(git_fixture["working"], "fetch", "origin", "main")

    result = invoke(git_fixture, "check")

    assert "ahead/behind" in result.stdout
    assert "0 1" in result.stdout


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

    assert "ahead/behind" in result.stdout
    assert "1 1" in result.stdout


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
    assert (
        subprocess.run(
            [sys.executable, "-m", "devlegate", "init"],
            cwd=git_fixture["working"],
            env=environment,
            text=True,
            capture_output=True,
        ).returncode
        == 0
    )
    assert env.read_bytes() == before


def test_read_only_commands_do_not_create_runtime_database_or_fetch(git_fixture):
    state = git_fixture["tmp"] / "read-only-state"
    config = git_fixture["tmp"] / "read-only.env"
    config.write_text(f"REMOTE_BRANCH=main\nSTATE_DIR={state}\n")
    before = git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
    result = invoke(git_fixture, "plan", "--json", env_file=config)
    assert result.returncode == 0
    assert not list(state.glob("*.sqlite3"))
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip() == before
    )


def test_status_uses_daemon_ipc_without_fallback(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    expected = cli_daemon.status_view().as_dict()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "status", "--json", "--env", str(config)],
    )

    assert main() == (1 if expected["plan"]["action"] == "blocked" else 0)
    payload = json.loads(capsys.readouterr().out)
    assert payload["service"] == {"state": "running"}
    assert set(payload) == {"service", *expected}
    assert payload["execution"] == {
        **expected["execution"],
        "state": "idle",
    }
    for key, value in expected.items():
        if key != "execution":
            assert payload[key] == value


def test_status_without_service_reports_stopped_from_local_observation(git_fixture):
    result = invoke(git_fixture, "status", "--json")

    assert result.returncode == 0
    assert json.loads(result.stdout)["service"] == {"state": "stopped"}


def test_plan_uses_daemon_ipc_without_fallback(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    expected = cli_daemon.plan_view().as_dict()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "plan", "--json", "--env", str(config)],
    )

    assert main() == 0
    assert json.loads(capsys.readouterr().out) == expected


def test_blocked_dependency_status_uses_daemon_semantics(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    control = cli_daemon.control_worktree
    (control / "kanban/review/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\nreview\n'
    )
    (control / "kanban/todo/T-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Waiting"\n'
        '"depends_on":\n  - "D-1"\n---\nwaiting\n'
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "add blocked dependency")
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "status", "--env", str(config)],
    )

    assert main() == 0
    output = capsys.readouterr().out
    assert "T-1" in output
    assert "Waiting" in output
    assert "D-1 unfinished (review)" in output


def test_retry_uses_daemon_authority_and_never_constructs_cli_engine(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, *, request_id):
        accepted.append(ticket_id)
        return {"accepted": True, "ticket_id": ticket_id}

    monkeypatch.setattr(cli_daemon, "submit_retry", submit)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI constructed mutable engine"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "retry", "T-1", "--env", str(_short_runtime_config(git_fixture))],
    )

    assert main() == 0
    assert accepted == ["T-1"]
    assert "retry accepted: T-1" in capsys.readouterr().out


def test_reconcile_uses_daemon_authority_and_never_constructs_cli_engine(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    accepted = []

    def submit(ticket_id, onto, *, request_id):
        accepted.append((ticket_id, onto, request_id))
        return {"accepted": True, "ticket_id": ticket_id, "onto": onto}

    monkeypatch.setattr(cli_daemon, "submit_reconcile_update_base", submit)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("CLI constructed mutable engine"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
            "--env",
            str(config),
        ],
    )

    assert main() == 0
    assert accepted and accepted[0][:2] == ("T-1", "abc123")
    assert "reconciliation accepted: T-1" in capsys.readouterr().out


def test_reconcile_refuses_without_daemon_without_constructing_engine(
    git_fixture, monkeypatch, capsys
):
    config = _short_runtime_config(git_fixture)
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
            "--env",
            str(config),
        ],
    )

    assert main() == 1
    stderr = capsys.readouterr().err
    assert "service is not running" in stderr
    assert "start `devlegate`" in stderr


def test_reconcile_refuses_connectable_socket_without_authority(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    locator.socket_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(locator.socket_path))
    listener.listen(1)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
            "--env",
            str(config),
        ],
    )
    try:
        assert main() == 1
        stderr = capsys.readouterr().err
        assert "service is not running" in stderr
        assert "start `devlegate`" in stderr
        listener.settimeout(0.2)
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
        locator.socket_path.unlink(missing_ok=True)


def test_reconcile_fails_closed_when_authority_exists_without_socket(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct reconciliation fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "devlegate",
            "reconcile",
            "update-base",
            "T-1",
            "--onto",
            "abc123",
            "--env",
            str(config),
        ],
    )
    try:
        assert main() == 1
        assert "service IPC unavailable" in capsys.readouterr().err
    finally:
        authority.close()


def test_retry_refuses_connectable_socket_without_authority(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    locator.socket_path.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(locator.socket_path))
    listener.listen(1)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct retry fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "retry", "T-1", "--env", str(config)],
    )
    try:
        assert main() == 1
        stderr = capsys.readouterr().err
        assert "service is not running" in stderr
        assert "start `devlegate`" in stderr
        listener.settimeout(0.2)
        with pytest.raises(socket.timeout):
            listener.accept()
    finally:
        listener.close()
        locator.socket_path.unlink(missing_ok=True)


def test_retry_fails_closed_when_authority_exists_but_socket_is_unavailable(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct retry fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "retry", "T-1", "--env", str(config)],
    )
    try:
        assert main() == 1
        assert "service IPC unavailable" in capsys.readouterr().err
    finally:
        authority.close()


def test_retry_interactive_candidates_are_rendered_and_selected_locally(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    submitted = []
    monkeypatch.setattr(
        cli_daemon,
        "published_retry_candidates_view",
        lambda: (
            {"id": "T-1", "title": "Ticket", "reason": "failed", "kind": "failed"},
        ),
    )
    monkeypatch.setattr(
        cli_daemon,
        "submit_retry",
        lambda ticket_id, *, request_id: submitted.append(ticket_id)
        or {"accepted": True, "ticket_id": ticket_id},
    )
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "1")
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "retry", "--env", str(_short_runtime_config(git_fixture))],
    )

    assert main() == 0
    assert submitted == ["T-1"]
    assert "Retry candidates:" in capsys.readouterr().out


def test_retry_interactive_cancel_submits_no_mutation(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    submitted = []
    monkeypatch.setattr(
        cli_daemon,
        "published_retry_candidates_view",
        lambda: (
            {"id": "T-1", "title": "Ticket", "reason": "failed", "kind": "failed"},
        ),
    )
    monkeypatch.setattr(cli_daemon, "submit_retry", submitted.append)
    monkeypatch.setattr("devlegate.cli._interactive_terminal", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _prompt: "0")
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "retry", "--env", str(_short_runtime_config(git_fixture))],
    )

    assert main() == 0
    assert submitted == []
    capsys.readouterr()


def test_daemon_application_error_is_authoritative(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    def fail_status():
        raise DevlegateError("authoritative daemon failure")

    monkeypatch.setattr(cli_daemon, "status_view", fail_status)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "status", "--env", str(config)],
    )

    assert main() == 1
    assert "authoritative daemon failure" in capsys.readouterr().err


def test_daemon_protocol_error_is_not_bypassed(
    cli_daemon, git_fixture, monkeypatch, capsys
):
    monkeypatch.setattr(
        "devlegate.cli.request",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            IPCClientError("service IPC protocol error: malformed response")
        ),
    )
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
    )
    config = _short_runtime_config(git_fixture)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "plan", "--env", str(config)],
    )

    assert main() == 1
    assert (
        "service is running but its IPC endpoint is unavailable"
        in capsys.readouterr().err
    )


def test_authority_guard_blocks_exclusive_lock_and_shared_guard_blocks_daemon(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    locator = RuntimeLocator.from_env(config)
    with locator.absence_guard():
        engine = ServiceEngine(config, read_only=True)
        with pytest.raises(DevlegateError, match="another devlegate instance"):
            engine._lock()

    authority = engine._lock()
    try:
        with pytest.raises(RuntimeAuthorityPresent):
            with locator.absence_guard():
                pass
    finally:
        authority.close()


@pytest.mark.parametrize("make_endpoint", [False, True])
def test_ipc_unavailable_with_authority_fails_closed(
    git_fixture, monkeypatch, capsys, make_endpoint
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    authority = engine._lock()
    endpoint = engine.ipc_socket_path
    listener = None
    try:
        if make_endpoint:
            endpoint.parent.mkdir(parents=True, exist_ok=True)
            listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            listener.bind(str(endpoint))
            listener.close()
            listener = None
        monkeypatch.setattr(
            "devlegate.cli._service_engine",
            lambda *_args, **_kwargs: pytest.fail("direct fallback used"),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["devlegate", "status", "--env", str(config)],
        )

        assert main() == 1
        assert (
            "service is running but its IPC endpoint is unavailable"
            in capsys.readouterr().err
        )
    finally:
        if listener is not None:
            listener.close()
        endpoint.unlink(missing_ok=True)
        authority.close()


def test_stale_socket_without_authority_uses_guarded_fallback(
    git_fixture, monkeypatch, capsys
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    endpoint = engine.ipc_socket_path
    endpoint.parent.mkdir(parents=True, exist_ok=True)
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(endpoint))
    listener.close()
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "plan", "--json", "--env", str(config)],
    )

    try:
        assert main() == 0
        assert json.loads(capsys.readouterr().out)["action"] == "blocked"
        assert endpoint.exists()
    finally:
        endpoint.unlink(missing_ok=True)


@pytest.mark.parametrize("command", ["status", "plan"])
def test_subdirectory_requires_repository_root_without_daemon(
    git_fixture, monkeypatch, capsys, command
):
    config = _short_runtime_config(git_fixture)
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", command, "--env", str(config)],
    )

    assert main() == 1
    assert "run devlegate from repository root" in capsys.readouterr().err


@pytest.mark.parametrize("command", ["status", "plan"])
def test_subdirectory_requires_repository_root_with_daemon(
    cli_daemon, git_fixture, monkeypatch, capsys, command
):
    config = _short_runtime_config(git_fixture)
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    monkeypatch.chdir(subdirectory)
    monkeypatch.setattr(
        "devlegate.cli._service_engine",
        lambda *_args, **_kwargs: pytest.fail("fallback used"),
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", command, "--env", str(config)],
    )

    assert main() == 1
    assert "run devlegate from repository root" in capsys.readouterr().err


def test_service_engine_status_and_plan_return_immutable_views(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    application = ServiceEngine(git_fixture["config"], read_only=True)

    status = application.status()
    plan = application.plan()

    assert status.plan == plan
    assert status.code.local_head
    with pytest.raises(dataclasses.FrozenInstanceError):
        status.phase = "changed"
    with pytest.raises(dataclasses.FrozenInstanceError):
        plan.action = "changed"


def test_service_engine_serve_reuses_one_owner_across_polling_iterations(
    git_fixture, monkeypatch
):
    # Engine-level owner-loop invariant; production topology is covered below.
    monkeypatch.chdir(git_fixture["working"])
    engine = ServiceEngine(git_fixture["config"])
    runtime_store = engine._runtime_store
    calls = []
    stop_event = threading.Event()

    def run_once():
        calls.append((id(engine), id(engine._runtime_store)))
        if len(calls) == 2:
            stop_event.set()
            raise KeyboardInterrupt
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_once)
    engine.poll_interval = "0"
    with pytest.raises(KeyboardInterrupt):
        engine.serve(stop_event)

    assert calls == [(id(engine), id(runtime_store))] * 2


def test_persistent_service_survives_successful_iteration_until_stop(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    engine = ServiceEngine(git_fixture["config"])
    stop_event = threading.Event()
    calls = []

    def run_once():
        calls.append(True)
        if len(calls) == 3:
            stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_iteration", run_once)
    engine.poll_interval = "0"
    assert engine.serve(stop_event) == 0
    assert len(calls) == 3


def test_operational_cli_constructs_service_engine_directly(git_fixture, monkeypatch):
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

    monkeypatch.setattr("devlegate.cli.ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(
        "devlegate.cli.run_service",
        lambda engine, **kwargs: calls.append(("host", engine, kwargs)) or 8,
    )
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "once", "--env", str(git_fixture["config"])]
    )
    monkeypatch.chdir(git_fixture["working"])

    assert main() == 8
    assert calls[0] == ("init", git_fixture["config"], False)
    assert calls[1][0] == "host"
    assert calls[1][1] is not None
    assert calls[1][2]["once"] is True


def test_once_uses_canonical_service_host(git_fixture, monkeypatch):
    calls = []

    def host(engine, *, once=False, startup_fd=None, startup_report=None):
        assert startup_fd is None
        calls.append((engine, once))
        return 0

    monkeypatch.setattr("devlegate.cli.run_service", host)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "once", "--env", str(git_fixture["config"])],
    )
    monkeypatch.chdir(git_fixture["working"])

    assert main() == 0
    assert len(calls) == 1
    assert calls[0][1] is True


def test_foreground_hosts_real_ipc_status_and_plan_until_stopped(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    with LiveService(git_fixture["working"], git_fixture["config"]) as service:
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 0, status.stderr
        assert plan.returncode == 0, plan.stderr
        assert json.loads(status.stdout)["execution"]["phase"] == "idle"
        assert "action" in json.loads(plan.stdout)
        assert service.process is not None
        assert service.process.poll() is None
    assert service.stdout.count("---< D E V L E G A T E >---") == 1
    assert "mode    : foreground" in service.stdout


def _worker_script(path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "(workspace / 'process-worker.txt').write_text('completed\\n')\n"
        "attempts = os.environ.get('DEVLEGATE_TEST_ATTEMPTS')\n"
        "if attempts:\n"
        "    pathlib.Path(attempts).open('a').write('attempt\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'process worker', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    path.chmod(0o755)


def _service_engine_with_control(git_fixture, config):
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        engine.repo,
        "worktree",
        "move",
        git_fixture["control"],
        engine.control_worktree,
    )
    with redirect_stdout(io.StringIO()):
        assert engine.control_init() == 0
    return engine


def _add_service_ticket(engine):
    ticket_path = engine.control_worktree / "kanban/todo/T-1.md"
    ticket_path.write_text(ticket("Control ticket", "work"))
    git(
        engine.control_worktree,
        "add",
        str(ticket_path.relative_to(engine.control_worktree)),
    )
    git(engine.control_worktree, "commit", "-m", "add service test ticket")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )


def _h1_driver(config, point=None):
    command = (
        sys.executable,
        str(Path(__file__).with_name("h1_driver.py")),
        "--env",
        str(config),
    )
    return command


def _wait_process_death(service):
    assert service.process is not None
    service.process.wait(timeout=20)
    service._collect_output()
    service._abruptly_killed = True
    assert service.process.returncode == -signal.SIGKILL


def _disk_state(config):
    return ServiceEngine(config)._runtime_store.load()


def _h1_config(git_fixture):
    config = _short_runtime_config(git_fixture)
    config.write_text(config.read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1"))
    return config


def _advance_product(git_fixture):
    publisher = git_fixture["publisher"]
    (publisher / "downtime.txt").write_text("advanced\n")
    git(publisher, "add", "downtime.txt")
    git(publisher, "commit", "-m", "advance product")
    git(publisher, "push", "origin", "HEAD:main")
    return git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip(), git(
        publisher, "rev-parse", "HEAD"
    ).stdout.strip()


def test_real_service_sigkill_restarts_without_mutation(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    config = _h1_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    try:
        service.wait_for(lambda: "handled_remote_head" in _disk_state(config))
        before = _disk_state(config)
        service.kill()
        service.restart()
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 0, status.stderr
        assert plan.returncode == 0, plan.stderr
        assert _disk_state(config) == before
    finally:
        service.stop()


@pytest.mark.parametrize("point", ["merge_pending", "merge_after_effect"])
def test_real_service_merge_pending_restart_is_observe_first(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    _advance_product(git_fixture)
    config = _h1_config(git_fixture)
    _service_engine_with_control(git_fixture, config)
    monkeypatch.setenv("H1_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        assert crashed["phase"] == "merge_pending"
        assert crashed["remote_head"]
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: _disk_state(config)["phase"] == "idle")
        assert git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip() == (
            crashed["remote_head"]
        )
    finally:
        service.stop()


def _h1_execution_service(git_fixture, monkeypatch, point):
    worker = git_fixture["tmp"] / f"h1-worker-{point}.py"
    attempts = git_fixture["tmp"] / f"h1-attempts-{point}.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("H1_CRASH_POINT", point)
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, point)
    )
    service.start()
    service.wait_ready()
    return service, config, engine, attempts


def _retryable_service(git_fixture, monkeypatch):
    worker = git_fixture["tmp"] / "h2-retry-worker.py"
    pid_file = git_fixture["tmp"] / "h2-retry-worker.pid"
    attempts = git_fixture["tmp"] / "h2-retry-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    return service, config, engine, attempts, pid_file


def _parallel_service_requests(service, methods):
    start = threading.Event()

    def call(method, _index):
        assert start.wait(3)
        return ipc_request(service.locator.socket_path, method, timeout=10)

    with ThreadPoolExecutor(max_workers=len(methods)) as pool:
        futures = [
            pool.submit(call, method, index) for index, method in enumerate(methods)
        ]
        start.set()
        return [future.result(timeout=15) for future in futures]


def test_real_service_many_observers_succeed_while_worker_runs(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "h2-live-worker.py"
    pid_file = git_fixture["tmp"] / "h2-live-worker.pid"
    attempts = git_fixture["tmp"] / "h2-live-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    identity = None
    try:
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                _disk_state(config).get("worker_identity"),
                attempts,
                1,
            ),
            timeout=30,
        )
        identity = _disk_state(config)["worker_identity"]
        responses = _parallel_service_requests(
            service, ["status", "status", "plan", "status", "plan"]
        )
        statuses = [
            response
            for response, method in zip(
                responses, ["status", "status", "plan", "status", "plan"]
            )
            if method == "status"
        ]
        plans = [
            response
            for response, method in zip(
                responses, ["status", "status", "plan", "status", "plan"]
            )
            if method == "plan"
        ]
        assert all(
            status["execution"]["phase"] in {"idle", "agent_running"}
            for status in statuses
        )
        assert all(isinstance(plan["action"], str) for plan in plans)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(identity)
        service.stop()


def test_real_service_observers_succeed_during_owner_retry(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        result = service.cli("retry", "T-1", timeout=10)
        assert result.returncode == 0, result.stderr
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                _disk_state(config).get("worker_identity"),
                attempts,
                1,
            ),
            timeout=30,
        )
        responses = _parallel_service_requests(service, ["status", "plan", "status"])
        assert all(isinstance(response, dict) for response in responses)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()


def test_real_service_admitted_retry_outlives_cli_process(git_fixture, monkeypatch):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    service_pid = service.process.pid
    cli_process = None
    try:
        cli_process = service.start_cli("retry", "T-1")
        stdout, stderr = cli_process.communicate(timeout=15)
        assert cli_process.returncode == 0, stderr
        assert "retry accepted: T-1" in stdout

        def worker_active_after_cli_exit():
            state = _disk_state(config)
            return state.get("execution_stage") == "worker-running" and (
                _worker_body_started(
                    pid_file, state.get("worker_identity"), attempts, 1
                )
            )

        service.wait_for(worker_active_after_cli_exit, timeout=30)
        assert cli_process.poll() == 0
        assert service.process is not None
        assert service.process.pid == service_pid
        assert service.process.poll() is None
        status = service.cli("status", "--json")
        assert status.returncode in {0, 1}, status.stderr
        assert json.loads(status.stdout)["execution"]["phase"] == "idle"
        identity = _disk_state(config)["worker_identity"]
        os.kill(identity["pid"], 0)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        if cli_process is not None and cli_process.poll() is None:
            cli_process.kill()
            cli_process.wait(timeout=5)
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()


def test_real_service_same_request_id_retries_concurrently_once(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        barrier = threading.Barrier(2)

        def submit():
            barrier.wait(timeout=3)
            return ipc_request(
                service.locator.socket_path,
                "retry",
                {"ticket_id": "T-1"},
                mutable=True,
                request_id="same-request",
                timeout=10,
            )

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = [pool.submit(submit) for _index in range(2)]
            replies = [future.result(timeout=15) for future in results]
        assert replies == [
            {"accepted": True, "ticket_id": "T-1"},
            {"accepted": True, "ticket_id": "T-1"},
        ]
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                _disk_state(config).get("worker_identity"),
                attempts,
                1,
            ),
            timeout=30,
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        assert set(_disk_state(config)["mutable_receipts"]) == {"same-request"}
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()


def test_real_service_distinct_concurrent_retries_do_not_duplicate_worker(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        barrier = threading.Barrier(2)

        def submit(request_id):
            barrier.wait(timeout=3)
            try:
                return ipc_request(
                    service.locator.socket_path,
                    "retry",
                    {"ticket_id": "T-1"},
                    mutable=True,
                    request_id=request_id,
                    timeout=10,
                )
            except IPCClientError as error:
                return error

        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(submit, request_id) for request_id in ("q1", "q2")]
            replies = [future.result(timeout=15) for future in futures]
        assert sum(isinstance(reply, dict) for reply in replies) == 1
        errors = [reply for reply in replies if isinstance(reply, IPCClientError)]
        assert len(errors) == 1
        assert "service busy; mutable request was not admitted" in str(errors[0])
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                _disk_state(config).get("worker_identity"),
                attempts,
                1,
            ),
            timeout=30,
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        assert len(_disk_state(config)["mutable_receipts"]) == 1
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()


def test_real_service_stale_status_cannot_authorize_second_retry(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts, _pid_file = _retryable_service(
        git_fixture, monkeypatch
    )
    try:
        stale = json.loads(service.cli("status", "--json").stdout)
        assert stale["execution"]["phase"] == "idle"
        first = service.cli("retry", "T-1", timeout=10)
        assert first.returncode == 0, first.stderr
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                Path(os.environ["DEVLEGATE_TEST_WORKER_PID"]),
                _disk_state(config).get("worker_identity"),
                attempts,
                1,
            ),
            timeout=30,
        )
        with pytest.raises(IPCClientError, match="service busy"):
            ipc_request(
                service.locator.socket_path,
                "retry",
                {"ticket_id": "T-1"},
                mutable=True,
                request_id="stale-retry",
                timeout=10,
            )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(_disk_state(config).get("worker_identity"))
        service.stop()


def _long_worker_script(path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, time\n"
        "attempts = os.environ.get('DEVLEGATE_TEST_ATTEMPTS')\n"
        "if attempts:\n"
        "    with pathlib.Path(attempts).open('a') as handle:\n"
        "        handle.write('attempt\\n')\n"
        "        handle.flush()\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_WORKER_PID']).write_text(str(os.getpid()))\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    path.chmod(0o755)


def _worker_body_started(pid_file, identity, attempts, expected_count):
    if (
        not isinstance(identity, dict)
        or not pid_file.is_file()
        or not attempts.is_file()
    ):
        return False
    try:
        marker_pid = int(pid_file.read_text().strip())
        attempt_lines = attempts.read_text().splitlines()
    except (OSError, ValueError):
        return False
    return (
        marker_pid == identity["pid"]
        and attempt_lines == ["attempt"] * expected_count
    )


def _kill_worker_identity(identity):
    if isinstance(identity, dict) and isinstance(identity.get("pgid"), int):
        try:
            os.killpg(identity["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            pass


def test_real_service_worker_launch_restart_stays_fail_closed(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, _engine, attempts = _h1_execution_service(
        git_fixture, monkeypatch, "worker-launch"
    )
    try:
        _wait_process_death(service)
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-launch"
        )
        status = service.cli("status", "--json")
        plan = service.cli("plan", "--json")
        assert status.returncode == 1
        assert json.loads(status.stdout)["execution"]["phase"] == "agent_running"
        assert "ambiguous outcome" in json.loads(plan.stdout)["reason"]
        assert not attempts.exists()
    finally:
        service.stop()


def test_real_service_matching_live_worker_restart_does_not_duplicate(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "h1-live-worker.py"
    pid_file = git_fixture["tmp"] / "h1-live-worker.pid"
    attempts = git_fixture["tmp"] / "h1-live-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    identity = None
    try:
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                pid_file, _disk_state(config).get("worker_identity"), attempts, 1
            )
        )
        identity = _disk_state(config)["worker_identity"]
        service.kill()
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").returncode == 1)
        assert _disk_state(config)["worker_identity"] == identity
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        _kill_worker_identity(identity)
        service.stop()


def test_real_service_absent_worker_uses_process_loss_resume(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "h1-absent-worker.py"
    pid_file = git_fixture["tmp"] / "h1-absent-worker.pid"
    attempts = git_fixture["tmp"] / "h1-absent-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    old_identity = None
    new_identity = None
    try:
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                pid_file, _disk_state(config).get("worker_identity"), attempts, 1
            )
        )
        old_identity = _disk_state(config)["worker_identity"]
        service.kill()
        _kill_worker_identity(old_identity)
        pid_file.unlink(missing_ok=True)
        service.restart()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and isinstance(_disk_state(config).get("worker_identity"), dict)
            and _disk_state(config).get("execution_id")
            != old_identity.get("execution_id")
        )
        new_identity = _disk_state(config)["worker_identity"]
        assert new_identity["execution_id"] != old_identity["execution_id"]
        service.wait_for(
            lambda: _worker_body_started(pid_file, new_identity, attempts, 2),
            timeout=15,
        )
    finally:
        _kill_worker_identity(old_identity)
        _kill_worker_identity(new_identity)
        service.stop()


def test_real_service_auto_resume_survives_review_barrier_and_reschedules(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "h1-auto-resume-worker.py"
    pid_file = git_fixture["tmp"] / "h1-auto-resume-worker.pid"
    attempts = git_fixture["tmp"] / "h1-auto-resume-attempts.txt"
    worker.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys, time\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "attempts = pathlib.Path(os.environ['DEVLEGATE_TEST_ATTEMPTS'])\n"
        "count = len(attempts.read_text().splitlines()) if attempts.exists() else 0\n"
        "with attempts.open('a') as handle:\n"
        "    handle.write('attempt\\n')\n"
        "    handle.flush()\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_WORKER_PID']).write_text(str(os.getpid()))\n"
        "if count == 0:\n"
        "    while True:\n"
        "        time.sleep(1)\n"
        "(workspace / 'process-worker.txt').write_text('completed\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'process worker', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    worker.chmod(0o755)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    try:
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "worker-running"
            and _worker_body_started(
                pid_file, _disk_state(config).get("worker_identity"), attempts, 1
            ),
            timeout=30,
        )
        service.stop()
        interrupted = _disk_state(config)
        assert interrupted["phase"] == "idle"
        assert interrupted["failed_executions"]["T-1"]["interrupted"] is True
        assert (
            interrupted["failed_executions"]["T-1"]["interruption_kind"]
            == "service_shutdown"
        )

        service.start()
        service.wait_ready()
        service.wait_for(
            lambda: _disk_state(config)["phase"] == "idle"
            and (engine.control_worktree / "kanban/review/T-1.md").is_file(),
            timeout=30,
        )
        time.sleep(1.5)
        assert service.process is not None and service.process.poll() is None
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]
        plan = json.loads(service.cli("plan", "--json").stdout)
        assert plan["action"] == "none"
        assert "waiting for review" in plan["reason"]

        review = engine.control_worktree / "kanban/review/T-1.md"
        review.rename(engine.control_worktree / "kanban/todo/T-1.md")
        git(engine.control_worktree, "add", "-A")
        git(engine.control_worktree, "commit", "-m", "return T-1 to todo")
        git(
            engine.control_worktree,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-1.md").is_file()
            and attempts.read_text().splitlines()
            == ["attempt", "attempt", "attempt"],
            timeout=30,
        )
        assert service.process is not None and service.process.poll() is None
    finally:
        if service.process is not None and service.process.poll() is None:
            service.stop()


def test_real_service_post_worker_loss_requires_resume(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _h1_execution_service(
        git_fixture, monkeypatch, "post-worker"
    )
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        old_id = crashed["execution_id"]
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()

        def resumed():
            state = _disk_state(config)
            return state["phase"] == "idle" and (
                engine.control_worktree / "kanban/review/T-1.md"
            ).is_file()

        service.wait_for(resumed)
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]
        reports = list((engine.control_worktree / "executions/T-1").glob("*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text())["execution_id"] != old_id
    finally:
        service.stop()


def _prepare_accepted_integration(git_fixture, monkeypatch):
    worker = git_fixture["tmp"] / "h1-accepted-worker.py"
    attempts = git_fixture["tmp"] / "h1-accepted-attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    try:
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-1.md").is_file()
        )
    finally:
        service.stop()
    review = engine.control_worktree / "kanban/review/T-1.md"
    accepted = engine.control_worktree / "kanban/accepted/T-1.md"
    review.rename(accepted)
    git(engine.control_worktree, "add", "-A")
    git(engine.control_worktree, "commit", "-m", "accept T-1")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    return config, engine, attempts, git(
        engine.control_worktree, "rev-parse", "HEAD"
    ).stdout.strip()


@pytest.mark.parametrize(
    "point",
    [
        "integration_before_effect",
        "integration_product_after_effect",
        "integration_control_commit_after_effect",
        "integration_control_push_after_effect",
    ],
)
def test_real_service_accepted_integration_restart_is_idempotent(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", point)
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, point)
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        assert crashed["accepted_integration"]["ticket_id"] == "T-1"
        assert crashed["accepted_integration"]["control_head"] == control_head
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()

        def completed():
            local = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
            remote = git(
                engine.control_worktree,
                "ls-remote",
                "origin",
                "refs/heads/devlegate/control",
            ).stdout.split()
            return (
                _disk_state(config)["phase"] == "idle"
                and (engine.control_worktree / "kanban/done/T-1.md").is_file()
                and remote
                and remote[0] == local
            )

        service.wait_for(
            completed
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        assert not (engine.control_worktree / "kanban/accepted/T-1.md").exists()
    finally:
        service.stop()


def test_accepted_integration_blocks_dependent_worker_after_product_publication(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    dependent = engine.control_worktree / "kanban/todo/T-2.md"
    dependent.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependent"\n'
        '"depends_on":\n  - "T-1"\n---\nwait for T-1\n'
    )
    git(engine.control_worktree, "add", "kanban/todo/T-2.md")
    git(engine.control_worktree, "commit", "-m", "add dependent ticket")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_product_after_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_product_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        assert attempts.read_text().splitlines() == ["attempt"]
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)

        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-2.md").is_file()
        )
        assert (engine.control_worktree / "kanban/done/T-1.md").is_file()
        assert not isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt", "attempt"]
    finally:
        service.stop()


def test_live_accepted_integration_window_precedes_dependent_scheduling(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    dependent = engine.control_worktree / "kanban/todo/T-2.md"
    dependent.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependent"\n'
        '"depends_on":\n  - "T-1"\n---\nwait for T-1\n'
    )
    git(engine.control_worktree, "add", "kanban/todo/T-2.md")
    git(engine.control_worktree, "commit", "-m", "add dependent ticket")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    entered = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()
    original_complete = engine._complete_accepted

    def pause_before_control_transition(ticket_id, expected_head):
        entered.set()
        assert release.wait(10)
        result = original_complete(ticket_id, expected_head)
        stop_event.set()
        return result

    monkeypatch.setattr(engine, "_complete_accepted", pause_before_control_transition)
    result = []
    owner = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    owner.start()
    try:
        assert entered.wait(15)
        pending = _disk_state(config)["accepted_integration"]
        assert pending["ticket_id"] == "T-1"
        assert (engine.control_worktree / "kanban/accepted/T-1.md").is_file()
        assert (
            git(
                engine.repo,
                "ls-remote",
                "origin",
                f"refs/heads/{engine.remote_branch}",
            ).stdout.split()[0]
            == pending["checkpoint"]
        )

        plan = engine.published_plan_view()
        snapshot = engine._published_status_snapshot
        assert snapshot is not None
        assert plan.action == "none"
        assert snapshot.eligible == ()
        assert snapshot.blocked == (
            (
                "T-2",
                "Dependent",
                BlockedReason("integration-in-progress", ticket_id="T-1"),
            ),
        )
        assert attempts.read_text().splitlines() == ["attempt"]

        release.set()
        owner.join(15)
        assert not owner.is_alive()
        assert result == [0]
        assert (engine.control_worktree / "kanban/done/T-1.md").is_file()
        assert _disk_state(config).get("accepted_integration") is None

        completed = engine.status_view()
        assert ("T-2", "Dependent") in completed.eligible
        assert completed.lifecycle_integration is None
    finally:
        release.set()
        stop_event.set()
        owner.join(15)


def test_real_service_foreign_local_control_descendant_stays_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_before_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_before_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        remote_before = git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        foreign = engine.control_worktree / "foreign-control.txt"
        foreign.write_text("unrelated\n")
        git(engine.control_worktree, "add", "foreign-control.txt")
        git(engine.control_worktree, "commit", "-m", "foreign control change")
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: service.cli("status", "--json").stdout != ""
        )
        assert service.process is not None and service.process.poll() is None
        assert git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0] == remote_before
        assert foreign.is_file()
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_exact_local_integration_with_foreign_descendant_stays_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_control_commit_after_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_control_commit_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        local_c = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
        remote_r = git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        assert local_c != control_head
        assert git(
            engine.control_worktree, "rev-parse", f"{local_c}^"
        ).stdout.strip() == control_head
        assert remote_r == control_head

        foreign = engine.control_worktree / "foreign-after-integration.txt"
        foreign.write_text("unrelated\n")
        git(engine.control_worktree, "add", foreign.name)
        git(engine.control_worktree, "commit", "-m", "foreign after integration")
        local_x = git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
        assert local_x != local_c
        assert git(
            engine.control_worktree, "rev-parse", f"{local_x}^"
        ).stdout.strip() == local_c
        assert git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0] == control_head

        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process is not None and service.process.poll() is None
        json.loads(service.cli("status", "--json").stdout)
        assert (
            git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip()
            == local_x
        )
        assert git(
            engine.control_worktree,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0] == control_head
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_remote_exact_control_descendant_is_recognized(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_control_push_after_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_control_push_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = git_fixture["publisher_control"]
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "later-control.txt").write_text("later\n")
        git(publisher_control, "add", "later-control.txt")
        git(publisher_control, "commit", "-m", "later control change")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/done/T-1.md").is_file()
        )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_remote_interleaving_before_control_commit_blocks(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_before_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_before_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = git_fixture["publisher_control"]
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "interleaving.txt").write_text("interleaving\n")
        git(publisher_control, "add", "interleaving.txt")
        git(publisher_control, "commit", "-m", "interleaving control change")
        accepted = publisher_control / "kanban/accepted/T-1.md"
        accepted.rename(publisher_control / "kanban/done/T-1.md")
        git(publisher_control, "add", "-A")
        git(publisher_control, "commit", "-m", "Devlegate integrate T-1")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        remote_before = git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process.poll() is None
        assert git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0] == remote_before
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_divergent_control_histories_stay_blocked(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config, engine, attempts, _control_head = _prepare_accepted_integration(
        git_fixture, monkeypatch
    )
    monkeypatch.setenv("H1_CRASH_POINT", "integration_control_commit_after_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_h1_driver(config, "integration_control_commit_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        publisher_control = git_fixture["publisher_control"]
        git(publisher_control, "fetch", "origin", "devlegate/control")
        git(publisher_control, "reset", "--hard", "origin/devlegate/control")
        (publisher_control / "divergent.txt").write_text("divergent\n")
        git(publisher_control, "add", "divergent.txt")
        git(publisher_control, "commit", "-m", "divergent control change")
        git(
            publisher_control,
            "push",
            "origin",
            "HEAD:refs/heads/devlegate/control",
        )
        remote_before = git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0]
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(lambda: service.cli("status", "--json").stdout != "")
        assert service.process.poll() is None
        assert git(
            publisher_control,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/control",
        ).stdout.split()[0] == remote_before
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_receipt_restart_is_not_a_persistent_command_queue(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "h1-receipt-worker.py"
    attempts = git_fixture["tmp"] / "h1-receipt-attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("H1_CRASH_POINT", "receipt_after_save")
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, "receipt_after_save")
    )
    service.start()
    service.wait_ready()
    try:
        try:
            service.cli("retry", "T-1", timeout=5)
        except subprocess.TimeoutExpired:
            pass
        _wait_process_death(service)
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: isinstance(_disk_state(config).get("mutable_receipts"), dict)
        )
        assert not attempts.exists()
        result = service.cli("retry", "T-1")
        assert result.returncode == 0, result.stderr
        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-1.md").is_file()
        )
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_reconcile_receipt_restart_is_not_a_queue(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _h1_config(git_fixture)
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (git_fixture["working"] / "product-change.txt").write_text("product B\n")
        git(git_fixture["working"], "add", "product-change.txt")
        git(git_fixture["working"], "commit", "-m", "advance product")
        git(git_fixture["working"], "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert run_test_iteration(engine) == 1
    target = engine._state["reconciliation"]["observed_product"]
    git(git_fixture["working"], "pull", "--ff-only", "origin", "main")
    monkeypatch.setenv("H1_CRASH_POINT", "receipt_after_save")
    release_marker = git_fixture["tmp"] / "reconcile-operator-released"
    release_marker.unlink(missing_ok=True)
    monkeypatch.setenv("H1_OPERATOR_RELEASE_MARKER", str(release_marker))
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, "receipt_after_save")
    )
    service.start()
    service.wait_ready()
    try:
        try:
            service.cli("reconcile", "update-base", "T-1", "--onto", target, timeout=5)
        except subprocess.TimeoutExpired:
            pass
        _wait_process_death(service)
        before = _disk_state(config)["reconciliation"]
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        assert _disk_state(config)["reconciliation"] == before
        result = service.cli("reconcile", "update-base", "T-1", "--onto", target)
        assert result.returncode == 0, result.stderr
        service.wait_for(
            lambda: _disk_state(config)["reconciliation"]["status"] == "resolved"
        )
        service.wait_for(lambda: release_marker.exists())
    finally:
        service.stop()


@pytest.mark.parametrize(
    "point",
    [
        "agent_pending",
        "checkpointing",
        "checkpoint_after_effect",
        "post-checkpoint",
        "publishing",
        "publication_after_effect",
        "post-publication",
        "lifecycle",
        "lifecycle_after_effect",
    ],
)
def test_real_service_transaction_stage_restart_preserves_execution(
    git_fixture, monkeypatch, point
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _h1_execution_service(
        git_fixture, monkeypatch, point
    )
    try:
        _wait_process_death(service)
        crashed = _disk_state(config)
        execution_id = crashed.get("execution_id")
        assert isinstance(execution_id, str)
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()

        def completed():
            state = _disk_state(config)
            return state["phase"] == "idle" and (
                engine.control_worktree / "kanban/review/T-1.md"
            ).is_file()

        service.wait_for(completed)
        reports = list((engine.control_worktree / "executions/T-1").glob("*.json"))
        assert len(reports) == 1
        assert json.loads(reports[0].read_text())["execution_id"] == execution_id
        assert attempts.read_text().splitlines() == ["attempt"]
    finally:
        service.stop()


def test_real_service_product_movement_during_downtime_blocks_stale_publish(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    service, config, engine, attempts = _h1_execution_service(
        git_fixture, monkeypatch, "post-checkpoint"
    )
    try:
        _wait_process_death(service)
        _advance_product(git_fixture)
        monkeypatch.delenv("H1_CRASH_POINT")
        service.restart()
        service.wait_for(
            lambda: _disk_state(config).get("execution_stage") == "post-checkpoint"
        )
        assert attempts.read_text().splitlines() == ["attempt"]
        execution = engine.execution_worktree_root / "work" / "T-1"
        assert not git(
            execution,
            "ls-remote",
            "origin",
            "refs/heads/devlegate/work/T-1",
        ).stdout.strip()
    finally:
        service.stop()


def test_real_service_process_executes_retry_from_real_cli(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "process-worker.py"
    attempts = git_fixture["tmp"] / "attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _short_runtime_config(git_fixture)
    config.write_text(
        config.read_text()
        .replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
        .replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1

    with LiveService(git_fixture["working"], config) as service:
        result = service.cli("retry", "T-1")
        assert result.returncode == 0, result.stderr
        assert "retry accepted: T-1" in result.stdout

        service.wait_for(
            lambda: (engine.control_worktree / "kanban/review/T-1.md").is_file()
        )
        status = json.loads(service.cli("status", "--json").stdout)
        assert status["tickets"]["review"] == [
            {"id": "T-1", "title": "Control ticket"}
        ]
        assert attempts.read_text().splitlines() == ["attempt"]


def test_real_service_crash_during_mutable_response_reports_uncertain_delivery(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    worker = git_fixture["tmp"] / "uncertain-worker.py"
    attempts = git_fixture["tmp"] / "uncertain-attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("H1_CRASH_POINT", "receipt_after_save")
    config = _h1_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine,
        "_run_worker",
        lambda *_args: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1
    service = LiveService(
        git_fixture["working"], config, command=_h1_driver(config, "receipt_after_save")
    )
    service.start()
    service.wait_ready()
    try:
        result = service.cli("retry", "T-1", timeout=20)
        assert result.returncode != 0
        assert "retry request outcome is uncertain" in result.stderr
        assert "inspect status before retrying" in result.stderr
        _wait_process_death(service)
        assert service.process.returncode == -signal.SIGKILL
        crashed = _disk_state(config)
        receipts = crashed.get("mutable_receipts")
        assert isinstance(receipts, dict)
        assert any(
            receipt.get("method") == "retry"
            and receipt.get("ticket_id") == "T-1"
            and receipt.get("accepted") is True
            for receipt in receipts.values()
            if isinstance(receipt, dict)
        )
        service.locator.socket_path.unlink(missing_ok=True)
    finally:
        service.stop()


def test_real_service_process_executes_reconciliation_from_real_cli(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    config.write_text(config.read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1"))
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)

    def worker(workspace, _prompt):
        (workspace.path / "implementation.txt").write_text("worker work\n")
        (git_fixture["working"] / "product-change.txt").write_text("product B\n")
        git(git_fixture["working"], "add", "product-change.txt")
        git(git_fixture["working"], "commit", "-m", "advance product")
        git(git_fixture["working"], "push", "origin", "HEAD:main")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(engine, "_run_worker", worker)
    assert run_test_iteration(engine) == 1
    target = engine._state["reconciliation"]["observed_product"]
    execution = next((engine.state_dir / "worktrees").glob("*/work/T-1"))
    git(git_fixture["working"], "pull", "--ff-only", "origin", "main")

    with LiveService(git_fixture["working"], config) as service:
        pending_before = _disk_state(config)["reconciliation"]
        service.kill()
        service.restart()
        assert _disk_state(config)["reconciliation"] == pending_before
        result = service.cli(
            "reconcile", "update-base", "T-1", "--onto", target
        )
        assert result.returncode == 0, result.stderr
        assert "reconciliation accepted: T-1" in result.stdout

        def resolved():
            status = service.cli("status", "--json")
            if status.returncode != 0:
                return False
            reconciliation = json.loads(status.stdout).get("reconciliation")
            return (
                isinstance(reconciliation, dict)
                and reconciliation.get("status") == "resolved"
            )

        service.wait_for(resolved)
        (git_fixture["working"] / "operator-downtime.txt").write_text("hold\n")
        service.kill()
        service.restart()
        status = service.cli("status", "--json")
        assert status.stdout
        payload = json.loads(status.stdout)
        assert payload["reconciliation"]["effective_base"] == target
        assert git(execution, "rev-parse", "HEAD^").stdout.strip() == target
        assert (execution / "implementation.txt").read_text() == "worker work\n"
    database = next(engine.state_dir.glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    persisted = json.loads(
        connection.execute(
            "SELECT payload FROM runtime_state WHERE id = 1"
        ).fetchone()[0]
    )
    connection.close()
    assert persisted["resume_required"]["status"] == "required"


def test_cli_status_and_plan_render_fake_engine_without_runtime(
    git_fixture, monkeypatch, capsys
):
    observation = GitObservation(
        "main", False, "local", "origin/main", "remote", True, "fingerprint"
    )
    plan = ExecutionPlan("none", "no runnable tickets", code=observation)
    snapshot = StatusSnapshot(
        "idle",
        None,
        False,
        observation,
        None,
        (("backlog", 0), ("todo", 0), ("review", 0), ("accepted", 0), ("done", 0)),
        (),
        (),
        (),
        (),
        None,
        plan,
    )

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            assert read_only

        def status(self):
            return snapshot

        def plan(self):
            return plan

    monkeypatch.setattr("devlegate.cli._service_engine", FakeServiceEngine)
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "status", "--env", str(git_fixture["config"])]
    )
    assert main() == 0
    assert "Repositories" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "status", "--json", "--env", str(git_fixture["config"])],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["plan"]["action"] == "none"

    monkeypatch.setattr(
        sys, "argv", ["devlegate", "plan", "--env", str(git_fixture["config"])]
    )
    assert main() == 0
    assert "Execution plan:" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "plan", "--json", "--env", str(git_fixture["config"])],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["action"] == "none"


def test_retry_refuses_without_daemon_without_constructing_engine(
    git_fixture, monkeypatch
):
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

        def retry(self, ticket_id=None, _stop_event=None):
            calls.append(("retry", ticket_id))
            return 3

    monkeypatch.setattr("devlegate.cli._service_engine", FakeServiceEngine)
    monkeypatch.setattr(
        sys, "argv", ["devlegate", "retry", "T-1", "--env", str(git_fixture["config"])]
    )
    assert main() == 1
    assert calls == []


def test_status_reports_nested_code_and_control_observations(git_fixture):
    publish_control(git_fixture, "ticket", {"kanban/todo/T-1.md": ticket("Control")})
    result = invoke(git_fixture, "status", "--json")
    payload = json.loads(result.stdout)
    assert result.returncode == 0
    assert payload["observation"]["code"]["branch"] == "main"
    assert payload["observation"]["control"]["branch"] == "devlegate/control"
    assert payload["tickets"]["next"]["id"] == "T-1"


def test_status_table_and_machine_formats_share_service_state(monkeypatch, capsys):
    observation = GitObservation(
        "main", False, "local", "origin/main", "remote", True, "fingerprint"
    )
    plan = ExecutionPlan("none", "no runnable tickets", code=observation)
    snapshot = StatusSnapshot(
        "idle",
        None,
        False,
        observation,
        None,
        (("backlog", 0), ("todo", 0), ("review", 0), ("accepted", 0), ("done", 0)),
        (),
        (),
        (),
        (),
        None,
        plan,
    )
    monkeypatch.setattr(
        "devlegate.cli._read_only_view",
        lambda _env, method: ReadOnlyView(
            plan if method == "plan" else snapshot, "running"
        ),
    )

    monkeypatch.setattr(sys, "argv", ["devlegate", "status"])
    assert main() == 0
    table = capsys.readouterr().out
    assert "service running" in table
    assert "running" in table

    monkeypatch.setattr(sys, "argv", ["devlegate", "status", "--json"])
    assert main() == 0
    json_value = json.loads(capsys.readouterr().out)

    monkeypatch.setattr(sys, "argv", ["devlegate", "status", "--yaml"])
    assert main() == 0
    yaml_value = nanoyaml.loads(capsys.readouterr().out)
    assert yaml_value == json_value
    assert json_value["service"] == {"state": "running"}

    monkeypatch.setattr(sys, "argv", ["devlegate", "plan", "--yaml"])
    assert main() == 0
    assert nanoyaml.loads(capsys.readouterr().out)["action"] == "none"


def test_missing_control_blocks_without_product_mutation(git_fixture):
    control = git_fixture["control"]
    git(git_fixture["working"], "worktree", "remove", "--force", control)
    before = git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip()
    result = invoke(git_fixture, "--once")
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
    result = invoke(git_fixture, "--once")
    assert result.returncode == 1
    assert "execution failed" in result.stdout
    database = next(git_fixture["state"].glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    payload = json.loads(
        connection.execute(
            "SELECT payload FROM runtime_state WHERE id = 1"
        ).fetchone()[0]
    )
    connection.close()
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
            "agent_pending",
            local_head=code_head,
            remote_head=code_head,
            changed_paths="",
            selected_ticket_id="T-1",
            selected_ticket_body="work",
        )
    devlegate._save_state(
        "agent_pending",
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
    publish_control(
        git_fixture,
        "tickets",
        {
            "kanban/todo/ED-22.md": ticket("Later"),
            "kanban/todo/ED-17.md": ticket("Earlier"),
        },
    )
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
    publish_control(
        git_fixture,
        "blocked",
        {
            "kanban/todo/ED-2.md": ticket("Waiting", depends=["ED-404"]),
        },
    )
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
    result = invoke(git_fixture, "--once")
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
    result = invoke(git_fixture, "--once")
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
        "run_iteration",
        lambda intent: selected.append(intent.retry_ticket_id) or 0,
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


@pytest.mark.parametrize(
    "answer", [pytest.param("", id="enter"), pytest.param("0", id="zero")]
)
def test_interactive_retry_cancel_without_running(monkeypatch, answer):
    devlegate = object.__new__(Devlegate)
    selected = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(devlegate, "_lock", lambda: nullcontext())
    monkeypatch.setattr(
        devlegate,
        "run_iteration",
        lambda intent: selected.append(intent.retry_ticket_id) or 0,
    )
    monkeypatch.setattr(os, "isatty", lambda _fd: True)
    monkeypatch.setattr(sys.stdin, "fileno", lambda: 0)
    monkeypatch.setattr(sys.stdout, "fileno", lambda: 1)
    monkeypatch.setattr("builtins.input", lambda _prompt: answer)

    assert devlegate.retry() == 0
    assert selected == []


def test_interactive_retry_rejects_invalid_selection_without_running(monkeypatch):
    devlegate = object.__new__(Devlegate)
    ran = []
    monkeypatch.setattr(
        devlegate, "_retry_candidates", lambda: (("T-1", "one", "first"),)
    )
    monkeypatch.setattr(
        devlegate, "run_iteration", lambda _intent: ran.append(True) or 0
    )
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


@pytest.mark.parametrize("phase", ["agent_pending", "agent_running"])
def test_modern_bound_execution_requires_every_identity_field(phase):
    state = {
        "phase": phase,
        "local_head": "local",
        "remote_head": "remote",
        "changed_paths": "",
        "control_head": "control",
        "selected_ticket_id": "T-1",
        "selected_ticket_body": "work",
        "execution_ticket_id": "T-1",
        "execution_base_head": "base",
        "execution_control_head": "control",
        "execution_branch": "devlegate/work/T-1",
        "execution_path": "/state/work/T-1",
        "execution_id": "attempt-1",
        "execution_remote_head": None,
    }
    for field in (
        "execution_ticket_id",
        "execution_base_head",
        "execution_control_head",
        "execution_branch",
        "execution_path",
        "execution_id",
        "execution_remote_head",
    ):
        incomplete = {key: value for key, value in state.items() if key != field}
        with pytest.raises(DevlegateError, match="execution workspace binding"):
            Devlegate._validate_state_invariant(incomplete)
