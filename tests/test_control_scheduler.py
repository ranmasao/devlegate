# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F403, F405, I001
"""Scheduler and admission semantics; workspace and restart proof live elsewhere."""

from _control_support import *  # noqa: F403,F405

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

def test_control_init_bootstraps_empty_orphan_control_plane(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    product_head = git(working, "rev-parse", "HEAD").stdout.strip()

    initialized = invoke(working, "control", "init", config=config)

    assert initialized.returncode == 0, initialized.stderr
    control = next((state / "worktrees").glob("*/control"))
    assert git(control, "symbolic-ref", "--short", "HEAD").stdout.strip() == (
        "devlegate/control"
    )
    assert len(git(control, "rev-list", "--parents", "-n1", "HEAD").stdout.split()) == 1
    for state_name in ("backlog", "todo", "review", "accepted", "done"):
        assert (control / "workflow" / state_name / ".gitkeep").is_file()
    assert not (working / "workflow").exists()
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_head
    assert git(working, "ls-remote", "origin", "refs/heads/devlegate/control").stdout
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert (
        json.loads(invoke(working, "plan", "--json", config=config).stdout)["action"]
        == "none"
    )
    assert invoke(working, "check", config=config).returncode == 0
    assert invoke(working, "status", "--json", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 0

def test_control_init_does_not_treat_remote_observation_failure_as_absence(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    config.write_text(
        config.read_text().replace(
            "REMOTE_BRANCH=main", "REMOTE_NAME=missing\nREMOTE_BRANCH=main"
        )
    )

    result = invoke(working, "control", "init", config=config)

    assert result.returncode == 1
    assert "cannot observe control branch" in result.stderr
    assert not state.exists()
    assert not subprocess.run(
        ["git", "show-ref", "--verify", "refs/heads/devlegate/control"],
        cwd=working,
        text=True,
        capture_output=True,
    ).stdout

def test_control_init_rejects_ambiguous_local_control_state(tmp_path):
    working, config, state = fresh_control_fixture(tmp_path)
    key = hashlib.sha256(str(working.resolve()).encode()).hexdigest()
    control_path = state / "worktrees" / key / "control"
    control_path.parent.mkdir(parents=True)
    control_path.write_text("foreign\n")

    result = invoke(working, "control", "init", config=config)

    assert result.returncode == 1
    assert "expected control worktree path already exists" in result.stderr
    assert control_path.read_text() == "foreign\n"

def test_dependency_state_change_on_control_changes_plan(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/review/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\ndependency\n'
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
    snapshot = Devlegate(config).status_view()
    assert snapshot.eligible == (("T-1", "Waiting"),)
    assert snapshot.blocked == ()

def test_idle_diagnostic_uses_scheduler_barrier_reason(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").rename(control / "kanban/review/T-1.md")
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Independent"\n---\nwork\n'
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "add review barrier")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    messages = []
    monkeypatch.setattr(runtime, "service_log", messages.append)

    assert run_test_iteration(Devlegate(config)) == 0

    assert any("waiting for review: T-1" in message for message in messages)
    assert not any("unfinished dependencies" in message for message in messages)

def test_idle_diagnostic_reports_unfinished_dependencies(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").unlink()
    (control / "kanban/backlog/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\nwork\n'
    )
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Waiting"\n'
        '"depends_on":\n  - "D-1"\n---\nwork\n'
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "add dependency barrier")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    messages = []
    monkeypatch.setattr(runtime, "service_log", messages.append)

    assert run_test_iteration(Devlegate(config)) == 0

    assert any("unfinished dependencies" in message for message in messages)

def test_accepted_boundary_diagnostic_is_not_reported_as_dependencies(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").rename(control / "kanban/accepted/T-1.md")
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Independent"\n---\nwork\n'
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "add accepted boundary")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    checkpoint = git(working, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    engine = Devlegate(config)
    engine._save_state(
        "idle",
        accepted_integration={
            "ticket_id": "T-1",
            "checkpoint": checkpoint,
            "control_head": control_head,
        },
    )
    messages = []
    monkeypatch.setattr(runtime, "service_log", messages.append)
    monkeypatch.setattr(Devlegate, "_recover_accepted_integration", lambda _self: 0)

    assert run_test_iteration(engine) == 0

    assert any(
        "no worker scheduled: accepted integration recovery in progress: T-1"
        in message
        for message in messages
    )
    assert not any("unfinished dependencies" in message for message in messages)

def test_diamond_frontier_keeps_all_ready_tickets_eligible(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").unlink()
    (control / "kanban/done/A-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Root"\n---\ndone\n'
    )
    for ticket_id, title in (("B-1", "Branch B"), ("C-1", "Branch C")):
        (control / f"kanban/todo/{ticket_id}.md").write_text(
            f'---\n"type": "devlegate.ticket"\n"title": "{title}"\n'
            '"depends_on":\n  - "A-1"\n---\nwork\n'
        )
    (control / "kanban/todo/D-1.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Join"\n'
        '"depends_on":\n  - "B-1"\n  - "C-1"\n---\nwork\n'
    )
    git(control, "add", "-A")
    git(control, "commit", "-m", "add diamond workflow")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    snapshot = Devlegate(config).status_view()

    assert snapshot.plan.ticket_id == "B-1"
    assert snapshot.eligible == (("B-1", "Branch B"), ("C-1", "Branch C"))
    assert snapshot.blocked == (
        (
            "D-1",
            "Join",
            BlockedReason(
                "dependencies",
                tickets=(("B-1", "todo"), ("C-1", "todo")),
            ),
        ),
    )

def test_wide_frontier_keeps_join_blockers_immediate_and_deterministic(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-1.md").unlink()
    for ticket_id, title in (
        ("F-1", "Front 1"),
        ("F-2", "Front 2"),
        ("F-3", "Front 3"),
    ):
        (control / f"kanban/todo/{ticket_id}.md").write_text(
            f'---\n"type": "devlegate.ticket"\n"title": "{title}"\n---\nwork\n'
        )
    for ticket_id, title, dependencies in (
        ("J-1", "Join 1", ("F-1", "F-2")),
        ("J-2", "Join 2", ("F-2", "F-3")),
    ):
        dependency_lines = "".join(f'  - "{item}"\n' for item in dependencies)
        (control / f"kanban/todo/{ticket_id}.md").write_text(
            f'---\n"type": "devlegate.ticket"\n"title": "{title}"\n'
            f'"depends_on":\n{dependency_lines}---\nwork\n'
        )
    git(control, "add", "-A")
    git(control, "commit", "-m", "add wide workflow frontier")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    snapshot = Devlegate(config).status_view()

    assert snapshot.plan.ticket_id == "F-1"
    assert snapshot.eligible == (
        ("F-1", "Front 1"),
        ("F-2", "Front 2"),
        ("F-3", "Front 3"),
    )
    assert snapshot.blocked == (
        (
            "J-1",
            "Join 1",
            BlockedReason("dependencies", tickets=(("F-1", "todo"), ("F-2", "todo"))),
        ),
        (
            "J-2",
            "Join 2",
            BlockedReason("dependencies", tickets=(("F-2", "todo"), ("F-3", "todo"))),
        ),
    )

def test_explicit_control_reconciliation_adopts_exact_divergent_history(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    control, from_head, to_head = divergent_control_heads(working, config, tmp_path)
    monkeypatch.chdir(working)
    devlegate = runtime.ServiceEngine(config)
    product_before = git(working, "rev-parse", "HEAD").stdout.strip()
    remote_before = git(
        working, "ls-remote", "origin", "refs/heads/devlegate/control"
    ).stdout.split()[0]

    assert devlegate.reconcile_control(from_head, to_head) == 0

    assert git(control, "rev-parse", "HEAD").stdout.strip() == to_head
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_before
    remote_after = git(
        working, "ls-remote", "origin", "refs/heads/devlegate/control"
    ).stdout.split()[0]
    assert remote_after == remote_before == to_head
    evidence = f"refs/devlegate/recovery/control/{from_head}-{to_head}"
    assert git(working, "rev-parse", "--verify", evidence).stdout.strip() == from_head
    assert devlegate._ticket_store()
    assert state.exists()

def test_control_reconciliation_remote_change_is_side_effect_free(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    control, from_head, to_head = divergent_control_heads(working, config, tmp_path)
    monkeypatch.chdir(working)
    devlegate = runtime.ServiceEngine(config)
    product_before = git(working, "rev-parse", "HEAD").stdout.strip()
    state_before = {
        path.relative_to(state): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file() and path.parent.name != "locks"
    }
    evidence = f"refs/devlegate/recovery/control/{from_head}-{to_head}"
    original_validate = devlegate._validate_control_reconcile_admission
    calls = 0

    def validate(from_value, to_value):
        nonlocal calls
        calls += 1
        original_validate(from_value, to_value)
        if calls == 1:
            git(
                control,
                "push",
                "--force",
                "origin",
                f"{from_head}:refs/heads/devlegate/control",
            )

    monkeypatch.setattr(devlegate, "_validate_control_reconcile_admission", validate)
    with pytest.raises(DevlegateError, match="remote HEAD changed"):
        devlegate.reconcile_control(from_head, to_head)

    assert calls == 2
    assert git(control, "rev-parse", "HEAD").stdout.strip() == from_head
    missing_evidence = subprocess.run(
        ["git", "rev-parse", "--verify", evidence],
        cwd=working,
        text=True,
        capture_output=True,
    )
    assert missing_evidence.returncode != 0
    assert git(working, "rev-parse", "HEAD").stdout.strip() == product_before
    state_after = {
        path.relative_to(state): path.read_bytes()
        for path in state.rglob("*")
        if path.is_file() and path.parent.name != "locks"
    }
    assert state_after == state_before

@pytest.mark.parametrize(
    "case",
    ["wrong-from", "wrong-to", "ordinary-fast-forward", "dirty", "running"],
)
def test_control_reconciliation_refuses_unsafe_or_unnecessary_cases(
    tmp_path, monkeypatch, case
):
    working, config, _state = control_fixture(tmp_path)
    control, from_head, to_head = divergent_control_heads(working, config, tmp_path)
    monkeypatch.chdir(working)
    devlegate = runtime.ServiceEngine(config)
    before = git(control, "rev-parse", "HEAD").stdout.strip()
    if case == "wrong-from":
        from_head = "0" * 40
    elif case == "wrong-to":
        to_head = "f" * 40
    elif case == "ordinary-fast-forward":
        to_head = from_head
        git(control, "push", "--force", "origin", f"{from_head}:devlegate/control")
    elif case == "dirty":
        (control / "uncommitted.txt").write_text("dirty\n")
    elif case == "running":
        devlegate._state["phase"] = "agent_running"
    with pytest.raises(DevlegateError):
        devlegate.reconcile_control(from_head, to_head)
    assert git(control, "rev-parse", "HEAD").stdout.strip() == before

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

def test_once_validates_control_before_product_fast_forward(tmp_path):
    working, config, _state = control_fixture(tmp_path)
    publisher = control_publisher(tmp_path)
    git(publisher, "switch", "main")
    (publisher / "new-product.txt").write_text("new\n")
    git(publisher, "add", ".")
    git(publisher, "commit", "-m", "product update")
    git(publisher, "push", "origin", "main")
    before = git(working, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    assert git(working, "rev-parse", "HEAD").stdout.strip() == before
    assert not (working / "new-product.txt").exists()
    assert "control worktree is missing" in result.stdout

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

def test_independent_ticket_remains_blocked_by_review_barrier(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    calls = []
    monkeypatch.setattr(
        devlegate._workers,
        "run",
        lambda workspace, _prompt, **_kwargs: (
            calls.append(workspace.ticket_id)
            or WorkerRunResult(
                0, None, WorkerClaim("completed", "implemented", (), ()), None
            )
        ),
    )
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    (control / "kanban/todo/T-3.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Third"\n'
        '"depends_on":\n  - "T-2"\n---\nwork\n'
    )
    (control / "kanban/todo/T-4.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Fourth"\n'
        '"depends_on":\n  - "T-1"\n  - "T-2"\n---\nwork\n'
    )
    git(
        control,
        "add",
        "kanban/todo/T-2.md",
        "kanban/todo/T-3.md",
        "kanban/todo/T-4.md",
    )
    git(control, "commit", "-m", "add independent ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    assert run_test_iteration(devlegate) == 0
    plan = devlegate.plan_view()
    assert calls == ["T-1"]
    assert plan.action == "none"
    assert "waiting for review" in plan.reason
    assert (control / "kanban/review/T-1.md").is_file()
    assert (control / "kanban/todo/T-2.md").is_file()
    snapshot = devlegate.status_view()
    assert snapshot.eligible == ()
    assert snapshot.blocked == (
        ("T-2", "Second", BlockedReason("review", ticket_id="T-1")),
        (
            "T-3",
            "Third",
            BlockedReason("dependencies", tickets=(("T-2", "todo"),)),
        ),
        (
            "T-4",
            "Fourth",
            BlockedReason(
                "dependencies",
                tickets=(("T-1", "review"), ("T-2", "todo")),
            ),
        ),
    )
    status = invoke(working, "status", "--json", config=config)
    payload = json.loads(status.stdout)
    assert payload["tickets"]["eligible"] == []
    assert payload["tickets"]["blocked"] == [
        {
            "id": "T-2",
            "title": "Second",
            "reason": {"kind": "review", "ticket_id": "T-1"},
        },
        {
            "id": "T-3",
            "title": "Third",
            "reason": {
                "kind": "dependencies",
                "tickets": [{"id": "T-2", "state": "todo"}],
            },
        },
        {
            "id": "T-4",
            "title": "Fourth",
            "reason": {
                "kind": "dependencies",
                "tickets": [
                    {"id": "T-1", "state": "review"},
                    {"id": "T-2", "state": "todo"},
                ],
            },
        },
    ]
    human = invoke(working, "status", config=config)
    assert "Eligible" not in human.stdout
    assert "Blocked" in human.stdout
    assert "T-1 awaiting review" in human.stdout
    assert "T-2 unfinished (todo)" in human.stdout

def test_review_and_accepted_states_enforce_serial_planning(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    control = next((state / "worktrees").glob("*/control"))
    (control / "kanban/todo/T-2.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Second"\n---\nwork\n'
    )
    (control / "kanban/todo/T-1.md").rename(control / "kanban/review/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "send ticket to review")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    review = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert review["action"] == "none"
    assert "waiting for review" in review["reason"]
    review_status = Devlegate(config).status_view()
    assert review_status.eligible == ()
    assert review_status.blocked == (
        ("T-2", "Second", BlockedReason("review", ticket_id="T-1")),
    )

    (control / "kanban/review/T-1.md").rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept ticket")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    accepted = json.loads(invoke(working, "plan", "--json", config=config).stdout)
    assert accepted["action"] == "integrate"
    assert accepted["ticket"]["id"] == "T-1"
    accepted_status = Devlegate(config).status_view()
    assert accepted_status.eligible == ()
    assert accepted_status.blocked == (
        ("T-2", "Second", BlockedReason("accepted", ticket_id="T-1")),
    )

def test_control_fast_forward_logs_generation_change(tmp_path, monkeypatch, capsys):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    control = next((state / "worktrees").glob("*/control"))
    old_head = git(control, "rev-parse", "HEAD").stdout.strip()
    seed = control_publisher(tmp_path)
    (seed / "kanban/backlog/refresh.md").write_text("refresh\n")
    git(seed, "add", "kanban/backlog/refresh.md")
    git(seed, "commit", "-m", "refresh control")
    git(seed, "push", "origin", "HEAD:devlegate/control")

    new_head, _remote_head = devlegate._sync_control()
    output = capsys.readouterr().out
    assert new_head != old_head
    assert f"control updated: {old_head} -> {new_head}" in output

def test_new_control_generation_refreshes_attempt_authority(tmp_path):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    execution = next((state / "worktrees").glob("*/work/T-1"))
    original_base = state_payload(state)["execution_base_head"]
    control = next((state / "worktrees").glob("*/control"))
    (control / "unrelated.md").write_text("architect note\n")
    git(control, "add", "unrelated.md")
    git(control, "commit", "-m", "unrelated control update")
    git(control, "push", "origin", "devlegate/control")
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_base_head"] == original_base
    final_control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    assert payload["execution_control_head"] == final_control_head
    assert final_control_head != control_head
    assert git(execution, "rev-parse", "HEAD").stdout.strip() == original_base

def test_bound_pending_generation_preserves_authority_and_rejects_stale_control(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    assert invoke(working, "--once", config=config).returncode == 1
    original = state_payload(state)
    control = next((state / "worktrees").glob("*/control"))
    (control / "authority-race.md").write_text("changed\n")
    git(control, "add", ".")
    git(control, "commit", "-m", "advance control")
    git(control, "push", "origin", "devlegate/control")
    current_control = git(control, "rev-parse", "HEAD").stdout.strip()

    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    execution_path = (
        state / "worktrees" / next(state.glob("worktrees/*")).name / "work" / "T-1"
    )
    devlegate._save_state(
        "agent_pending",
        local_head=original["local_head"],
        remote_head=original["remote_head"],
        changed_paths=original["changed_paths"],
        control_head=original["handled_control_head"],
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=original["execution_base_head"],
        execution_control_head=original["handled_control_head"],
        execution_branch="devlegate/work/T-1",
        execution_path=str(execution_path),
    )

    result = invoke(working, "--once", config=config)

    assert result.returncode == 1
    payload = state_payload(state)
    assert payload["execution_control_head"] == original["handled_control_head"]
    assert payload["execution_control_head"] != current_control
    assert "stale execution" in result.stderr

def test_public_recover_owner_admission_reaches_exact_matching_operation(
    tmp_path, monkeypatch
):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(base)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head="f" * 40
    )
    observed_head = workspace.head
    calls = []
    monkeypatch.setattr(
        devlegate,
        "_recover_unsafe_bound_execution_workspace",
        lambda ticket, execution, head: calls.append((ticket, execution, head)),
    )
    result = []

    def submit():
        result.append(
            devlegate.submit_recover(
                "T-1", "current", observed_head, request_id="recover-boundary"
            )
        )

    thread = threading.Thread(target=submit)
    thread.start()
    deadline = time.monotonic() + 2
    while (
        not devlegate._operator_command_pending()
        and time.monotonic() < deadline
    ):
        time.sleep(0.01)
    command = devlegate._take_operator_command()
    assert command is not None
    devlegate._admit_operator_command(command)
    devlegate._dispatch_operator_command(command, None)
    thread.join(timeout=2)

    assert result == [
        {
            "accepted": True,
            "ticket_id": "T-1",
            "execution_id": "current",
            "observed_head": observed_head,
        }
    ]
    assert calls == [("T-1", "current", observed_head)]
