# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from pathlib import Path

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.blackboard_store import BlackboardStore
from devlegate.execution_result import ExecutionReportStore
from devlegate.inspection import inspect_object
from devlegate.runtime import WorkflowBlockedError
from devlegate.ticket_cycle import latest_reopening
from devlegate.ticket_reset import admit, advance
from devlegate.tickets import TicketError
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from tools import ticket_source


def completed():
    return WorkerRunResult(
        0, None, WorkerClaim("completed", "implemented", (), ()), None,
    )


@pytest.mark.parametrize("retained", [False, True])
def test_integrate_reopen_and_repeat(tmp_path, monkeypatch, retained):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    control = next((state / "worktrees").glob("*/control"))
    curator = tmp_path / "curator"
    git(tmp_path, "clone", "-b", "devlegate/control", tmp_path / "remote.git", curator)
    git(curator, "config", "user.name", "Curator")
    git(curator, "config", "user.email", "curator@example.com")
    reviews = {}

    def publish(source, destination, *, append="", review=None, feedback=None):
        git(curator, "pull", "--ff-only")
        new = f"kanban/{destination}/T-1.md"
        git(curator, "mv", f"kanban/{source}/T-1.md", new)
        if append:
            path = curator / new
            path.write_text(path.read_text() + append)
        if review:
            path = curator / "reviews" / "T-1" / f"{review.execution_id}.md"
            path.parent.mkdir(parents=True, exist_ok=True)
            disposition = "accept" if destination == "accepted" else "rework"
            reviews[review.execution_id] = (
                "<!-- devlegate-review-v1\n"
                f"ticket: T-1\nexecution: {review.execution_id}\n"
                f"checkpoint: {review.workspace_head}\ncontrol: {review.control_head}\n"
                f"disposition: {disposition}\n-->\n"
                + (feedback or "# ACCEPT\nHistorical approval.\n")
            )
            path.write_text(reviews[review.execution_id])
        git(curator, "add", "-A")
        git(curator, "commit", "-m", f"{source} -> {destination}")
        git(curator, "push")
        return git(curator, "rev-parse", "HEAD").stdout.strip()

    def run_worker(base=None, *, filename=None, revised=False):
        def worker(workspace, prompt, **kwargs):
            assert "## Historical Review Feedback" not in prompt
            assert "Historical approval." not in prompt
            assert ("Revised specification." in prompt) == revised
            if base:
                assert workspace.base_head == workspace.head == base
                if channel := kwargs["revisions"]:
                    assert channel.namespace.resolve("BASE").identity == base
                    assert channel.namespace.inventory()["checkpoint_count"] == 0
            if filename:
                (workspace.path / filename).write_text("implementation\n")
            return completed()

        monkeypatch.setattr(owner._workers, "run", worker)
        assert run_test_iteration(owner) == 0
        execution = owner._state["execution_id"]
        return ExecutionReportStore(control).read("T-1", execution)

    settled_reset = None
    if retained:
        run_worker()
        with owner._lock():
            admit(owner, "T-1")
            advance(owner)
    first = run_worker(filename="first-cycle.txt")
    if retained:
        settled_reset = owner._state["ticket_reset"]
        assert settled_reset["status"] == "started"
        assert settled_reset["fresh_execution_id"] == first.execution_id
    board = BlackboardStore(owner.state_dir, owner._state_key, "T-1")
    note = board.save("Prior findings", "Keep this note.", "text",
                      execution=first.execution_id)
    accepted = publish("review", "accepted", review=first)
    if retained:
        monkeypatch.setattr(owner, "_finish_integrated_workspace_cleanup", lambda: None)
    assert run_test_iteration(owner) == 0
    assert (control / "kanban/done/T-1.md").is_file()
    assert Path(first.execution_path).exists() == retained
    if retained:
        # The done publication survived, but model loss of the acknowledgement.
        with owner._lock():
            owner._save_state("idle", accepted_integration={
                "ticket_id": "T-1", "checkpoint": first.workspace_head,
                "control_head": accepted, "control_predecessor": accepted,
            })

    (repo / "other-product-change.txt").write_text("unrelated\n")
    git(repo, "add", "other-product-change.txt")
    git(repo, "commit", "-m", "unrelated product advancement")
    git(repo, "push", "origin", "HEAD:main")
    base = git(repo, "rev-parse", "HEAD").stdout.strip()
    reopened = publish("done", "todo", append="\nRevised specification.\n")
    owner = Devlegate(config)
    if retained:
        assert run_test_iteration(owner) == 0
        assert owner._state.get("accepted_integration") is None
        assert Path(first.execution_path).exists()
    else:
        foreign = tmp_path / "foreign"
        git(repo, "worktree", "add", "-b", first.execution_branch, foreign, base)
        with pytest.raises(WorkflowBlockedError, match="ambiguous|ownership"):
            run_test_iteration(owner)
        assert git(foreign, "rev-parse", "HEAD").stdout.strip() == base
        git(repo, "worktree", "remove", foreign)
        git(repo, "branch", "-D", first.execution_branch)
    second = run_worker(base, filename="second-cycle.txt", revised=True)
    assert second.control_head == reopened
    assert second.resource_generation != first.resource_generation
    assert owner._state.get("ticket_reset") == settled_reset
    accepted_report = second
    if retained:
        publish("review", "todo", review=second, feedback="# REWORK\nNew feedback.\n")

        def failed_rework(workspace, prompt, **kwargs):
            assert "New feedback." in prompt
            assert "Historical approval." not in prompt
            assert workspace.base_head == base
            assert (workspace.path / "second-cycle.txt").is_file()
            if channel := kwargs["revisions"]:
                checkpoint = channel.namespace.resolve("HEAD")
                assert checkpoint.identity == second.workspace_head
                assert channel.namespace.inventory()["checkpoint_count"] == 1
            return WorkerRunResult(1, "failed transport", None, None)

        monkeypatch.setattr(owner._workers, "run", failed_rework)
        assert run_test_iteration(owner) == 1
        failed = ExecutionReportStore(control).read("T-1", owner._state["execution_id"])
        assert failed.control_head != first.control_head

        owner = Devlegate(config)

        def retry(workspace, prompt, **kwargs):
            assert "New feedback." in prompt
            assert "Historical approval." not in prompt
            assert workspace.base_head == base
            assert workspace.head == failed.workspace_head
            return completed()

        monkeypatch.setattr(owner._workers, "run", retry)
        assert owner.retry("T-1") == 0
        accepted_report = ExecutionReportStore(control).read(
            "T-1", owner._state["execution_id"],
        )
        assert accepted_report.execution_id != second.execution_id
    publish("review", "accepted", review=accepted_report)
    assert run_test_iteration(owner) == 0
    second_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert second_head == accepted_report.workspace_head
    assert (repo / "first-cycle.txt").is_file()
    assert (repo / "other-product-change.txt").is_file()

    git(curator, "pull", "--ff-only")
    for state_name, identifier in (("done", "D-1"), ("todo", "D-2")):
        (curator / f"kanban/{state_name}/{identifier}.md").write_text(
            '---\n"type": "devlegate.ticket"\n"title": "Dependent"\n'
            '"depends_on": ["T-1"]\n---\nDependent work.\n'
        )
    git(curator, "add", "-A")
    git(curator, "commit", "-m", "Record completed and waiting dependents")
    git(curator, "push")
    old_done = git(curator, "rev-parse", "HEAD").stdout.strip()
    identical = publish("done", "todo")
    owner = Devlegate(config)
    with owner._lock():
        head, _ = owner._sync_control()
        graph = owner._ticket_store(head=head, require_clean=True)
        assert graph.by_id["D-1"].state == "done"
        assert tuple(ticket.id for ticket in graph.runnable) == ("T-1",)
    source_project = ticket_source.project(repo)
    with pytest.raises(ticket_source.TicketSourceError, match="no independently"):
        ticket_source.resolve(*source_project, "T-1")
    third = run_worker(second_head, revised=True)
    assert third.control_head == identical
    assert third.workspace_head == third.code_base_head == second_head
    assert third.resource_generation != second.resource_generation
    publish("review", "accepted", review=third)
    if retained:
        # A historical acknowledgement from two cycles ago cannot act on the
        # newly admitted workspace, even while the same ticket is accepted.
        with owner._lock():
            owner._save_state("idle", accepted_integration={
                "ticket_id": "T-1", "checkpoint": first.workspace_head,
                "control_head": accepted, "control_predecessor": accepted,
            })
        owner = Devlegate(config)
        assert run_test_iteration(owner) == 0
        assert owner._state.get("accepted_integration") is None
        assert (control / "kanban/accepted/T-1.md").is_file()
        assert Path(third.execution_path).is_dir()
    monkeypatch.setattr(owner, "_finish_integrated_workspace_cleanup", lambda: None)
    assert run_test_iteration(owner) == 0
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == second_head
    assert ticket_source.resolve(*source_project, "T-1").execution == third.execution_id
    with owner._lock():
        graph = owner._ticket_store(require_clean=True)
        assert graph.by_id["D-1"].state == "done"
        assert tuple(ticket.id for ticket in graph.runnable) == ("D-2",)
        manager = owner.recovery_workspace_manager("T-1")
        with pytest.raises(WorkflowBlockedError, match="cleanup admission predates"):
            owner._observe_cleanup_candidate({
                "ticket_id": "T-1", "head": second_head, "control_head": old_done,
                "path": third.execution_path, "branch": third.execution_branch,
            }, manager._registrations(), completed_only=True)
        assert Path(third.execution_path).is_dir()
        inventory = inspect_object(owner, "workspace", "T-1")["identity"]["executions"]
        assert {entry["report"]["execution_id"] for entry in inventory
                if not entry.get("historical_cycle")} == {third.execution_id}
        assert board.read(note["id"], None)["content"] == "Keep this note."
        reports = (first, second, accepted_report, third) if retained else (
            first, second, third,
        )
        for report in reports:
            saved = ExecutionReportStore(control).read("T-1", report.execution_id)
            assert saved == report
            kind = git(repo, "cat-file", "-t", report.workspace_head).stdout.strip()
            assert kind == "commit"
            result = inspect_object(owner, "execution", report.execution_id)
            view = result["observation"]
            assert view["provenance_state"] == "proved"
            assert view["report"] == report.as_dict()
            review = control / "reviews" / "T-1" / f"{report.execution_id}.md"
            assert review.read_text() == reviews[report.execution_id]


@pytest.mark.parametrize("disposition", ["separate", "duplicate", "abandoned"])
def test_reopening_requires_atomic_unique_done_departure(
    tmp_path, monkeypatch, disposition,
):
    repo, config, _state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    control = owner.control_worktree
    paths = owner._workflow_paths()
    todo = control / paths["todo"] / "T-1.md"
    original = todo.read_text()
    source = control / paths["abandoned" if disposition == "abandoned" else "done"]
    source /= "T-1.md"
    todo.rename(source)
    duplicate = control / paths["backlog"] / "T-1.md"
    if disposition == "duplicate":
        duplicate.write_text(original)
    git(control, "add", "-A")
    git(control, "commit", "-m", "Record disposition")
    if disposition == "separate":
        source.write_text(original + "\nEdit in place.\n")
        git(control, "commit", "-am", "Edit done specification without moving it")
        assert latest_reopening(control, "HEAD", paths, "T-1") is None
        git(control, "rm", source)
        git(control, "commit", "-m", "Remove done ticket")
        todo.write_text(original)
    else:
        source.rename(todo)
        if disposition == "duplicate":
            duplicate.unlink()
    git(control, "add", "-A")
    git(control, "commit", "-m", "Attempt invalid reopening")
    git(control, "push", "origin", owner.control_branch)
    with pytest.raises(TicketError, match="no valid committed done -> todo reopening"):
        latest_reopening(control, "HEAD", paths, "T-1")
    with pytest.raises(WorkflowBlockedError, match="no valid committed"):
        run_test_iteration(owner)
