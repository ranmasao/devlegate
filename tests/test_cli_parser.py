# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""CLI syntax, bootstrap, and presentation; no production service topology."""

from _cli_support import *  # noqa: F403,F405
from _cli_support import __version__

def test_help_and_parser_expose_phase1_commands(monkeypatch, capsys):
    parser = build_parser()
    assert parser.parse_args(["once"]).command == "once"
    assert parser.parse_args(["foreground"]).command == "foreground"
    assert parser.parse_args(["start"]).command == "start"
    assert parser.parse_args(["restart", "--force"]).force is True
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
    assert "usage: devlegate [--env FILE | @ALIAS] [COMMAND ...]" in output
    assert "{init,render,retry,reconcile,check,status,plan,stop,control}" not in output
    assert "Use `start` to run the persistent background service." in output
    assert "Service:" in output
    assert "Project:" in output
    assert "Recovery:" in output
    assert "Other:" in output
    assert "stop" in output and "stop the persistent service" in output
    assert "control" in output and "manage workflow history" in output
    for command in (
        "foreground",
        "once",
        "start",
        "stop",
        "status",
        "plan",
        "init",
        "render",
        "check",
        "control",
        "retry",
        "drop",
        "reconcile",
        "version",
    ):
        assert sum(line.startswith(f"  {command}") for line in output.splitlines()) == 1
    assert (
        "  foreground     run the persistent service attached to this terminal"
        in output
    )
    assert "  reconcile      perform explicit reconciliation" in output
    assert build_parser().parse_args(["retry", "T-1"]).ticket_id == "T-1"
    assert build_parser().parse_args(["retry", "T-1", "--force"]).force is True
    assert build_parser().parse_args(["drop", "T-1"]).ticket_id == "T-1"

def test_bare_invocation_prints_help_without_starting_or_resolving(
    monkeypatch, capsys
):
    monkeypatch.setattr("sys.argv", ["devlegate"])
    monkeypatch.setattr(cli, "_run_default_command", pytest.fail)
    monkeypatch.setattr(cli, "_project_target", pytest.fail)

    assert main() == 0
    output = capsys.readouterr().out
    assert "usage: devlegate [--env FILE | @ALIAS] [COMMAND ...]" in output
    assert "start" in output

def test_nested_command_help_uses_command_sections():
    parser = build_parser()
    top_level = parser.format_help()
    assert "usage: devlegate [--env FILE | @ALIAS] [COMMAND ...]" in top_level
    assert (
        "{init,render,retry,reconcile,check,status,plan,stop,control}" not in top_level
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
                ["Request an orderly shutdown"],
        ),
        (
                ["check", "--help"],
                ["Validate project setup without running a worker."],
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
                ["Initialize or attach the separate workflow Git history."],
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
                "exact current product HEAD",
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
    monkeypatch.chdir(Path("/tmp"))
    monkeypatch.setattr("sys.argv", ["devlegate", "version"])

    assert main() == 0
    output = capsys.readouterr().out
    assert "Devlegate runtime" in output
    assert __version__ in output
    assert "Distribution:" in output
    assert "Installation:" in output
    assert "OS:" in output
    assert "Project:" not in output
    assert "pid" not in output.lower()

@pytest.mark.parametrize("flag", ["--yaml", "--json"])
def test_version_machine_formats(monkeypatch, capsys, flag):
    monkeypatch.setattr("sys.argv", ["devlegate", "version", flag])

    assert main() == 0
    output = capsys.readouterr().out
    value = nanoyaml.loads(output) if flag == "--yaml" else json.loads(output)
    assert value["program"] == "devlegate"
    assert value["version"] == __version__
    assert set(value) == {"program", "version", "distribution", "python", "os"}
    assert "pid" not in output.lower()

def test_root_version_is_one_line_and_does_not_resolve_project(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", "--version"])
    monkeypatch.setattr(cli, "_project_target", pytest.fail)
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 0
    assert capsys.readouterr().out == f"devlegate {__version__}\n"

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

    initialized = invoke(fixture, "init", "test")

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

def test_background_child_safe_bootstrap_rejects_checkout_package_shadowing(
    git_fixture, tmp_path, monkeypatch
):
    marker = tmp_path / "shadowed-child"
    local_package = git_fixture["working"] / "devlegate"
    local_package.mkdir()
    (local_package / "__init__.py").write_text("")
    (local_package / "__main__.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('checkout-shadowed\\n')\n"
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "devlegate",
            "--env",
            str(git_fixture["config"]),
            "once",
        ],
        cwd=git_fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr
    assert not marker.exists()

def test_background_child_safe_bootstrap_rejects_checkout_module_shadowing(
    git_fixture, tmp_path, monkeypatch
):
    marker = tmp_path / "shadowed-module"
    (git_fixture["working"] / "devlegate.py").write_text(
        "from pathlib import Path\n"
        f"Path({str(marker)!r}).write_text('checkout-shadowed\\n')\n"
    )
    (git_fixture["working"] / "nanoyaml.py").write_text(
        "raise RuntimeError('checkout NanoYAML shadowed')\n"
    )
    git_fixture["config"].write_text(
        git_fixture["config"].read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1")
    )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-P",
            "-m",
            "devlegate",
            "--env",
            str(git_fixture["config"]),
            "once",
        ],
        cwd=git_fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )
    assert result.returncode in (0, 1), result.stderr
    assert not marker.exists()

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
    config = git_fixture["working"] / ".env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=automation/state\n"
        "BACKLOG_PATH=workflow/waiting\nTODO_PATH=workflow/ready\n"
        "REVIEW_PATH=workflow/inspection\nDONE_PATH=workflow/accepted\n"
    )
    registry_path = (
        Path(os.environ["XDG_CONFIG_HOME"]) / "devlegate" / "projects.json"
    )
    registry_path.unlink()
    result = invoke(git_fixture, "init", "render")
    assert result.returncode == 0
    assert config.exists()
    assert "rendered 2" in result.stdout
    architect = git_fixture["working"] / "skills/architect/SKILL.md"
    reviewer = git_fixture["working"] / "skills/reviewer/SKILL.md"
    assert "automation/state" in architect.read_text()
    assert "workflow/inspection" in reviewer.read_text()

def test_check_rejects_invalid_poll_interval_without_creating_state(git_fixture):
    config = git_fixture["config"]
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
    publisher = _ensure_publisher(git_fixture)
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
    publisher = _ensure_publisher(git_fixture)
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
        [sys.executable, "-m", "devlegate", "init", "outside"],
        cwd=tmp_path,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "current directory is not a git repository" in result.stderr
    assert not (tmp_path / ".env").exists()
    assert not (tmp_path / ".devlegate").exists()

def test_init_from_repository_subdirectory_does_not_seed_project_files(git_fixture):
    subdirectory = git_fixture["working"] / "subdir"
    subdirectory.mkdir()
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "init", "subdir"],
        cwd=subdirectory,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 1
    assert "run devlegate init subdir from repository root" in result.stderr
    assert not (subdirectory / ".env").exists()
    assert not (subdirectory / ".devlegate").exists()

def test_init_rejects_explicit_env_selector(git_fixture):
    alternate = git_fixture["working"] / "other.env"
    alternate.write_text("REMOTE_BRANCH=main\n")
    before = git_fixture["config"].read_bytes()
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "devlegate",
            "--env",
            str(alternate),
            "init",
            "other",
        ],
        cwd=git_fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode == 2
    assert "not valid for init" in result.stderr
    assert git_fixture["config"].read_bytes() == before

def test_init_seeds_missing_project_env_from_package(git_fixture):
    environment = {**os.environ, "PYTHONPATH": str(Path(__file__).parents[1] / "src")}
    env = git_fixture["working"] / ".env"
    env.unlink()
    registry_path = Path(environment["XDG_CONFIG_HOME"]) / "devlegate" / "projects.json"
    registry_path.unlink()
    result = subprocess.run(
        [sys.executable, "-m", "devlegate", "init", "seeded"],
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
            [sys.executable, "-m", "devlegate", "init", "seeded"],
            cwd=git_fixture["working"],
            env=environment,
            text=True,
            capture_output=True,
        ).returncode
        == 1
    )
    assert env.read_bytes() == before

@pytest.mark.parametrize(
    ("metadata", "expected_version", "warning"),
    [
        ({"service": "devlegate", "protocol_version": 1}, "unknown", False),
        (
            {
                "service": "devlegate",
                "version": "0.5.1",
                "protocol_version": 1,
                "pid": 4321,
            },
            "0.5.1",
            True,
        ),
    ],
)
def test_status_handles_service_version_metadata(
    cli_daemon, git_fixture, monkeypatch, capsys, metadata, expected_version, warning
):
    config = _short_runtime_config(git_fixture)
    expected = cli_daemon.status_view().as_dict()
    monkeypatch.setattr(
        "devlegate.cli.request",
        lambda *_args: {**expected, "service": metadata},
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(config), "status"],
    )

    assert main() == (1 if expected["plan"]["action"] == "blocked" else 0)
    output = capsys.readouterr().out

    assert f"Service version: {expected_version}" in output
    assert ("Warning: the running service uses a different" in output) is warning

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
        def __init__(self, env_file, *, read_only=False, repository=None):
            assert read_only

        def status_view(self):
            return snapshot

        def plan_view(self):
            return plan

    monkeypatch.setattr("devlegate.cli._service_engine", FakeServiceEngine)
    monkeypatch.chdir(git_fixture["working"])
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "status"],
    )
    assert main() == 0
    assert "Repositories" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "status", "--json"],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["plan"]["action"] == "none"

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "plan"],
    )
    assert main() == 0
    assert "Execution plan:" in capsys.readouterr().out

    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "plan", "--json"],
    )
    assert main() == 0
    assert json.loads(capsys.readouterr().out)["action"] == "none"

def test_control_init_rejects_wrong_branch(git_fixture):
    assert invoke(git_fixture, "control", "init").returncode == 0
    git(git_fixture["control"], "switch", "-c", "wrong-control")
    result = invoke(git_fixture, "control", "init")
    assert result.returncode == 1
    assert "wrong branch" in result.stderr

def test_terminal_state_helpers_remain_importable():
    assert callable(Devlegate)
    assert issubclass(DevlegateError, Exception)
