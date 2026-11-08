# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable real-Git analysis/review/publication and retirement lifecycles."""

import json
from pathlib import Path

import pytest
from _daemon_support import make_engine
from runtime_helpers import run_test_iteration
from test_abandonment import PATHS, commit, git, request, ticket
from test_abandonment_admission import bound_resources

from devlegate.abandonment_publication import review_header
from devlegate.evidence_worker import REPORT_SECTIONS
from devlegate.execution_result import ExecutionReportStore
from devlegate.runtime import ServiceEngine
from devlegate.runtime_state_validation import DevlegateError
from devlegate.tickets import load_ticket_store
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


@pytest.fixture
def tmp_path(tmp_path_factory):
    # Keep the real AF_UNIX evidence endpoint below the kernel path limit,
    # including pytest-xdist's per-worker directory component.
    return tmp_path_factory.mktemp("ap")


def setup_analysis(tmp_path, monkeypatch, *, owned=False):
    engine, config, _ = make_engine(tmp_path, monkeypatch)
    control = engine.control_worktree
    (control / "kanban/backlog").mkdir(parents=True, exist_ok=True)
    (control / "kanban/todo/T-1.md").rename(control / "kanban/backlog/T-1.md")
    base = git(engine.repo, "rev-parse", "HEAD")
    target = ticket(control, "A-TARGET")
    request(control, start_head=base)
    head = commit(control, "request in disposable control fixture")
    binding = None
    if owned:
        case = [engine.repo, control, base, head, engine.execution_worktree_root]
        binding = bound_resources(case)
        (binding.manager.path / "unfinished.txt").write_text(
            "discard this dirty work\n"
        )
    git(control, "push", "origin", "HEAD:devlegate/control")
    calls = []

    def worker(workspace, prompt, **kwargs):
        manifest = kwargs["evidence"].bundle.manifest()
        calls.append(kwargs["execution_id"])
        report = "\n".join(f"## {name}\nSubstantive observations about {name}."
                           for name in REPORT_SECTIONS)
        report += "\n" + manifest["manifest_id"]
        for execution in manifest["executions"]:
            if execution.get("execution_id"):
                report += "\n" + execution["execution_id"]
            if execution.get("checkpoint"):
                report += "\n" + execution["checkpoint"]
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "Summarized unfinished experiment",
                                 (), (), report), None,
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    return engine, config, calls, binding, target


def review(engine, disposition="accept", *, tamper=None):
    control = engine.control_worktree
    admission = engine._state["abandonment_admission"]
    analysis = engine._state["abandonment_analysis"]
    publication = engine._state["abandonment_publication"]["proposal"]["commit"]
    path = control / f"reviews/Z-REQUEST/{admission['execution_id']}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    content = review_header(
        admission, publication, analysis["workspace"]["manifest_id"],
        analysis["report"]["report_sha256"], disposition,
    ) + "\nIndependently checked the exact manifest and proposed report.\n"
    path.write_text(content.replace(publication, "f" * 40) if tamper else content)
    destination = "accepted" if disposition == "accept" else "todo"
    (control / f"kanban/{destination}").mkdir(parents=True, exist_ok=True)
    (control / "kanban/review/Z-REQUEST.md").rename(
        control / f"kanban/{destination}/Z-REQUEST.md"
    )
    head = commit(control, f"independent reviewer: {disposition}")
    git(control, "push", "origin", "HEAD:devlegate/control")
    return head, path


@pytest.mark.parametrize("resource", [
    "absent", "dirty-owned", "foreign", "remote", "pin",
])
def test_reviewed_terminal_transition_and_guarded_retirement(
    tmp_path, monkeypatch, resource,
):
    engine, config, calls, binding, _ = setup_analysis(
        tmp_path, monkeypatch, owned=resource != "absent",
    )
    control = engine.control_worktree
    target_blob = git(control, "rev-parse", "HEAD:kanban/todo/A-TARGET.md")
    request_blob = git(control, "rev-parse", "HEAD:kanban/todo/Z-REQUEST.md")
    product_head = git(engine.repo, "rev-parse", "HEAD")
    run_test_iteration(engine)
    assert len(calls) == 1
    admission = engine._state["abandonment_admission"]
    execution = admission["execution_id"]
    proposal = control / f"executions/Z-REQUEST/{execution}.json"
    persisted = json.loads(proposal.read_text())
    assert persisted == engine._state["abandonment_analysis"]["report"]
    metadata = json.loads((control / "abandonment-proposals/Z-REQUEST"
                           / f"{execution}.json").read_text())
    assert metadata["admission"] == admission
    assert metadata["conclusion"] == "completed"
    assert [item.execution_id for item in ExecutionReportStore(control).list(
        "Z-REQUEST"
    )] == [execution]
    assert (control / "kanban/review/Z-REQUEST.md").exists()
    assert not (control / "abandonments").exists()
    if resource == "absent":
        # Scratch loss after publication must use the canonical manifest and
        # must not relaunch the analyst or lose the durable proposed report.
        saved = engine._state["abandonment_analysis"]["workspace"]
        (Path(saved["path"]) / "manifest.json").unlink()
    if binding is not None:
        assert (binding.manager.path / "unfinished.txt").exists()
    reviewed, review_path = review(engine)
    if resource == "foreign":
        manager = binding.manager
        git(engine.repo, "worktree", "remove", "--force", str(manager.path))
        git(engine.repo, "worktree", "add", str(manager.path), manager.branch)
    elif resource == "remote":
        git(engine.repo, "push", "origin",
            f"{product_head}:refs/heads/{binding.manager.branch}")
    elif resource == "pin":
        git(engine.repo, "update-ref", "refs/devlegate/executions/A-TARGET/" + "2" * 32,
            product_head)
    run_test_iteration(engine)
    store = load_ticket_store(control, PATHS)
    assert store.by_id["A-TARGET"].state == "abandoned"
    assert store.by_id["Z-REQUEST"].state == "done"
    assert git(control, "rev-parse", "HEAD:kanban/abandoned/A-TARGET.md") == target_blob
    assert git(control, "rev-parse", "HEAD:kanban/done/Z-REQUEST.md") == request_blob
    published = control / f"abandonments/A-TARGET/{execution}.md"
    assert published.is_file()
    terminal = engine._state["abandonment_retirements"]["A-TARGET"]["publication"][
        "terminal"
    ]["commit"]
    assert git(control, "rev-parse", terminal + "^") == reviewed
    assert review_path.is_file() and proposal.is_file()
    assert git(engine.repo, "rev-parse", "HEAD") == product_head
    cleanup = engine._state["abandonment_cleanup"]
    if resource in {"absent", "dirty-owned"}:
        assert cleanup["status"] == "retired", cleanup
        if binding:
            assert not binding.manager.path.exists()
            assert git(engine.repo, "branch", "--list", binding.manager.branch) == ""
    else:
        assert cleanup["status"] == "blocked", cleanup
        assert binding.manager.path.exists()
        assert git(engine.repo, "branch", "--list", binding.manager.branch)
    # Exact canonical publication and blocked retirement survive restart. There
    # is no second worker, terminal report or product integration on redelivery.
    restored = ServiceEngine(config)
    monkeypatch.setattr(
        restored._workers, "run", lambda *_a, **_k: pytest.fail("rerun")
    )
    run_test_iteration(restored)
    assert git(control, "rev-parse", "HEAD") == terminal
    assert len(list((control / "abandonments/A-TARGET").glob("*.md"))) == 1
    assert restored._state["abandonment_cleanup"] == cleanup


def test_rework_keeps_old_proposal_and_requires_new_exact_acceptance(
    tmp_path, monkeypatch,
):
    engine, _, calls, _, _ = setup_analysis(tmp_path, monkeypatch)
    run_test_iteration(engine)
    first = calls[0]
    review(engine, "rework")
    run_test_iteration(engine)
    assert engine._state["abandonment_analysis"] is None
    assert (engine.control_worktree / f"executions/Z-REQUEST/{first}.json").exists()
    assert not (engine.control_worktree / "abandonments").exists()
    run_test_iteration(engine)
    assert len(calls) == 2 and calls[0] != calls[1]
    review(engine)
    run_test_iteration(engine)
    assert (engine.control_worktree / f"abandonments/A-TARGET/{calls[1]}.md").exists()
    assert not (engine.control_worktree / f"abandonments/A-TARGET/{first}.md").exists()


@pytest.mark.parametrize("tamper", ["header", "proposal", "review", "source"])
def test_identity_drift_never_authorizes_terminal_transition(
    tmp_path, monkeypatch, tamper,
):
    engine, _, _, binding, _ = setup_analysis(tmp_path, monkeypatch, owned=True)
    run_test_iteration(engine)
    _, review_path = review(engine, tamper=tamper == "header")
    control = engine.control_worktree
    if tamper == "proposal":
        execution = engine._state["abandonment_admission"]["execution_id"]
        path = control / f"executions/Z-REQUEST/{execution}.md"
        path.write_text(path.read_text() + "Altered proposal\n")
    elif tamper == "review":
        review_path.write_text(review_path.read_text() + "Altered review\n")
    elif tamper == "source":
        path = control / "kanban/todo/A-TARGET.md"
        path.write_text(path.read_text() + "Amended target\n")
    if tamper != "header":
        commit(control, "alter exact attribution")
        git(control, "push", "origin", "HEAD:devlegate/control")
    with pytest.raises(DevlegateError, match="changed|differs|mutable"):
        run_test_iteration(engine)
    assert not (control / "abandonments").exists()
    assert binding.manager.path.exists()


@pytest.mark.parametrize("boundary", [
    "proposal-push", "terminal-push", "worktree-remove", "local-delete",
])
def test_crash_replay_converges_without_duplicate_publication(
    tmp_path, monkeypatch, boundary,
):
    engine, config, calls, binding, _ = setup_analysis(
        tmp_path, monkeypatch, owned=True
    )
    original = engine._publish_abandonment_transaction
    armed = boundary == "proposal-push"

    def publish(transaction):
        nonlocal armed
        original(transaction)
        if armed:
            armed = False
            raise RuntimeError("simulated owner crash after push")

    monkeypatch.setattr(engine, "_publish_abandonment_transaction", publish)
    if armed:
        with pytest.raises(RuntimeError, match="owner crash"):
            run_test_iteration(engine)
    run_test_iteration(engine)
    review(engine)
    if boundary == "terminal-push":
        armed = True
    if boundary == "worktree-remove":
        from devlegate.execution_workspace import ExecutionWorkspaceManager
        original_remove = ExecutionWorkspaceManager.retire_abandoned

        def remove(manager, *args):
            original_remove(manager, *args)
            raise RuntimeError("simulated owner crash after worktree removal")

        monkeypatch.setattr(ExecutionWorkspaceManager, "retire_abandoned", remove)
    if boundary == "local-delete":
        original_git = engine._git_runtime

        def interrupted_git(repo, *args, **kwargs):
            result = original_git(repo, *args, **kwargs)
            if args[:2] == ("update-ref", "-d") and result.returncode == 0:
                raise RuntimeError("simulated owner crash after local ref deletion")
            return result

        monkeypatch.setattr(engine, "_git_runtime", interrupted_git)
    if boundary != "proposal-push":
        with pytest.raises(RuntimeError, match="owner crash"):
            run_test_iteration(engine)
    else:
        run_test_iteration(engine)
    restored = ServiceEngine(config)
    monkeypatch.setattr(
        restored._workers, "run", lambda *_a, **_k: pytest.fail("rerun")
    )
    run_test_iteration(restored)
    cleanup = restored._state["abandonment_cleanup"]
    assert cleanup["status"] == "retired", cleanup
    assert not binding.manager.path.exists()
    assert len(calls) == 1
    reports = list((engine.control_worktree / "abandonments/A-TARGET").glob("*.md"))
    assert len(reports) == 1


def test_cleanup_retry_is_explicit_and_terminal_does_not_reserve_other_work(
    tmp_path, monkeypatch,
):
    engine, _, _, binding, _ = setup_analysis(tmp_path, monkeypatch, owned=True)
    run_test_iteration(engine)
    review(engine)
    pin = "refs/devlegate/executions/A-TARGET/" + "2" * 32
    head = git(engine.repo, "rev-parse", "HEAD")
    git(engine.repo, "update-ref", pin, head)
    run_test_iteration(engine)
    assert engine._state["abandonment_cleanup"]["status"] == "blocked"
    assert engine._state["abandonment_admission"] is None
    control_head = git(engine.control_worktree, "rev-parse", "HEAD")
    # Simulate independently resolved pin ownership in this disposable fixture.
    # A normal scheduler tick must not repeat expensive blocked cleanup checks.
    git(engine.repo, "update-ref", "-d", pin, head)
    run_test_iteration(engine)
    assert binding.manager.path.exists()
    with engine._lock():
        result = engine.cleanup_workspaces(apply=True)
    decision = next(item for item in result["decisions"]
                    if item["ticket_id"] == "A-TARGET")
    assert decision["status"] == "retired", decision
    assert not binding.manager.path.exists()
    assert git(engine.control_worktree, "rev-parse", "HEAD") == control_head
    manager = binding.manager
    git(engine.repo, "worktree", "add", "-b", manager.branch, str(manager.path), head)
    with engine._lock():
        repeated = engine.cleanup_workspaces(apply=True)
    replacement = next(item for item in repeated["decisions"]
                       if item["ticket_id"] == "A-TARGET")
    assert replacement["status"] == "blocked"
    assert manager.path.exists()
