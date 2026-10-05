# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""CLI tests not owned by syntax, IPC routing, or production topology."""

from _cli_support import *  # noqa: F403,F405

def test_same_parent_replacement_requires_exact_single_parent_git_topology(
    git_fixture,
):
    repo = git_fixture["working"]
    (repo / "replacement.txt").write_text("original\n")
    git(repo, "add", "replacement.txt")
    git(repo, "commit", "-m", "original product tip")
    original = git(repo, "rev-parse", "HEAD").stdout.strip()
    parent = git(repo, "rev-parse", "HEAD^").stdout.strip()

    git(repo, "reset", "--hard", parent)
    (repo / "replacement.txt").write_text("replacement\n")
    git(repo, "add", "replacement.txt")
    git(repo, "commit", "-m", "rewritten product tip")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert _is_same_parent_replacement(repo, original, target)

    git(repo, "reset", "--hard", parent)
    (repo / "divergent.txt").write_text("divergent parent\n")
    git(repo, "add", "divergent.txt")
    git(repo, "commit", "-m", "unrelated product parent")
    (repo / "divergent.txt").write_text("divergent target\n")
    git(repo, "add", "divergent.txt")
    git(repo, "commit", "-m", "unrelated product tip")
    unrelated = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert not _is_same_parent_replacement(repo, original, unrelated)

def test_same_parent_replacement_rejects_merge_commit(git_fixture):
    repo = git_fixture["working"]
    original = git(repo, "rev-parse", "HEAD").stdout.strip()
    git(repo, "switch", "-c", "replacement-side")
    (repo / "side.txt").write_text("side\n")
    git(repo, "add", "side.txt")
    git(repo, "commit", "-m", "replacement side")
    git(repo, "switch", "main")
    (repo / "main.txt").write_text("main\n")
    git(repo, "add", "main.txt")
    git(repo, "commit", "-m", "main side")
    git(repo, "merge", "--no-ff", "replacement-side", "-m", "merge replacement")
    target = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert not _is_same_parent_replacement(repo, original, target)

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

def test_machine_format_flags_are_mutually_exclusive():
    parser = build_parser()
    with pytest.raises(SystemExit) as error:
        parser.parse_args(["status", "--yaml", "--json"])
    assert error.value.code == 2

@pytest.mark.parametrize("old", ["--foreground", "--once"])
def test_removed_service_flags_are_rejected(old, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["devlegate", old])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2

def test_removed_top_level_forms_are_rejected(git_fixture):
    for args in (("run",), ("daemon",), ("--check",)):
        result = invoke(git_fixture, *args)
        assert result.returncode == 2

def test_read_only_commands_do_not_create_runtime_database_or_fetch(git_fixture):
    state = git_fixture["tmp"] / "read-only-state"
    config = git_fixture["config"]
    config.write_text(f"REMOTE_BRANCH=main\nSTATE_DIR={state}\n")
    before = git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip()
    result = invoke(git_fixture, "plan", "--json", env_file=config)
    assert result.returncode == 0
    assert not list(state.glob("*.sqlite3"))
    assert (
        git(git_fixture["working"], "rev-parse", "origin/main").stdout.strip() == before
    )

@pytest.mark.parametrize("unrelated_git", [False, True])
@pytest.mark.parametrize("selector", ["alias", "env"])
@pytest.mark.parametrize("command", ["status", "plan"])
def test_registered_read_only_commands_ignore_caller_cwd(
    git_fixture, unrelated_git, selector, command
):
    unrelated = git_fixture["tmp"] / ("unrelated-git" if unrelated_git else "unrelated")
    unrelated.mkdir()
    if unrelated_git:
        git(unrelated, "init", "-b", "main")
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
    }
    address = "@test" if selector == "alias" else str(git_fixture["config"])
    command_line = [sys.executable, "-m", "devlegate"]
    if selector == "alias":
        command_line.append(address)
    else:
        command_line.extend(["--env", address])
    command_line.append(command)
    result = subprocess.run(
        command_line,
        cwd=unrelated,
        env=environment,
        text=True,
        capture_output=True,
    )

    assert result.returncode in {0, 1}
    assert "not a git repository" not in result.stderr

def test_force_payload_skew_detection_is_narrow() -> None:
    assert cli._force_payload_rejected(
        IPCClientError(
            "service error: restart payload fields are invalid",
            application=True,
            code="invalid_request",
        )
    )
    assert not cli._force_payload_rejected(
        IPCClientError(
            "service error: lifecycle intent conflicts with an active stop",
            application=True,
            code="application_error",
        )
    )

def test_service_engine_status_and_plan_return_immutable_views(
    git_fixture, monkeypatch
):
    monkeypatch.chdir(git_fixture["working"])
    application = ServiceEngine(git_fixture["config"], read_only=True)

    status = application.status_view()
    plan = application.plan_view()

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

def test_operational_cli_constructs_service_engine_directly(git_fixture, monkeypatch):
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False, repository=None):
            calls.append(("init", env_file, read_only))

    monkeypatch.setattr("devlegate.cli.ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(
        "devlegate.cli.run_service",
        lambda engine, **kwargs: calls.append(("host", engine, kwargs)) or 8,
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "once"],
    )
    monkeypatch.chdir(git_fixture["working"])

    assert main() == 8
    assert calls[0] == ("init", git_fixture["config"], False)
    assert calls[1][0] == "host"
    assert calls[1][1] is not None
    assert calls[1][2]["once"] is True

def test_once_uses_canonical_service_host(git_fixture, monkeypatch):
    calls = []
    monkeypatch.delenv("DEVLEGATE_HOST_MODE", raising=False)
    monkeypatch.delenv("DEVLEGATE_REQUIRE_NOTIFY", raising=False)
    monkeypatch.delenv("NOTIFY_SOCKET", raising=False)

    def host(
        engine,
        *,
        host_mode,
        once=False,
        startup_fd=None,
        startup_report=None,
        readiness_report=None,
    ):
        assert startup_fd is None
        assert host_mode is cli.HostingMode.DIRECT
        assert readiness_report is None
        calls.append((engine, once))
        return 0

    monkeypatch.setattr("devlegate.cli.run_service", host)
    monkeypatch.setattr(
        sys,
        "argv",
        ["devlegate", "--env", str(git_fixture["config"]), "once"],
    )
    monkeypatch.chdir(git_fixture["working"])

    assert main() == 0
    assert len(calls) == 1
    assert calls[0][1] is True

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
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", "integration_product_after_effect")
    service = LiveService(
        git_fixture["working"],
        config,
        command=_recovery_driver(config, "integration_product_after_effect"),
    )
    service.start()
    service.wait_ready()
    try:
        _wait_process_death(service)
        assert attempts.read_text().splitlines() == ["attempt"]
        assert isinstance(_disk_state(config).get("accepted_integration"), dict)

        monkeypatch.delenv("DEVLEGATE_TEST_CRASH_POINT")
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
    monkeypatch.setattr(
        "devlegate.cli._project_target",
        lambda **_kwargs: ProjectTarget(
            "test",
            Path("/tmp/test-project/.env"),
            Path("/tmp/test-project"),
            RuntimeLocator(
                Path("/tmp/test-project"), Path("/tmp/test-state"), "a" * 64
            ),
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
        connection.execute("SELECT payload FROM runtime_state WHERE id = 1").fetchone()[
            0
        ]
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
    publisher = _ensure_publisher(git_fixture)
    (publisher / "remote.txt").write_text("remote\n")
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "remote")
    git(publisher, "push", "origin", "main")
    result = invoke(git_fixture, "--once")
    assert result.returncode == 1
    assert "cannot fast-forward" in result.stdout

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
