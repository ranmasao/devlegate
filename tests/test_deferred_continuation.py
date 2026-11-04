# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Focused admission and lifecycle tests for authentic replayed source history."""

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from _continuation_support import ipc_plan, seed_deferred_source
from _control_support import git
from runtime_helpers import run_test_iteration
from test_control_workspace import (
    _exercise_isolated_lifecycle,
    _exercise_isolated_recovery,
)

from devlegate.cli import Devlegate, DevlegateError, _blocked_reason_text
from devlegate.execution_result import ExecutionReportStore, WorkerRunResult
from devlegate.worker_egress import WorkerClaim


@pytest.fixture
def source(tmp_path, monkeypatch):
    return seed_deferred_source(tmp_path, monkeypatch)


def test_reconvergent_prefix_refusal_status_owner_and_ipc(tmp_path, monkeypatch):
    source = seed_deferred_source(tmp_path, monkeypatch, mismatch=True)
    reason = "replay-equivalent reviewed prefix tree mismatch at step 8"
    for _ in range(2):
        snapshot = source.engine.status_view()
        assert snapshot.plan.action == "blocked"
        assert reason in snapshot.plan.reason
        blocked = next(entry[2] for entry in snapshot.blocked if entry[0] == "T-1")
        assert _blocked_reason_text(blocked) == reason
        ipc_plan(source, "blocked", reason)
        with pytest.raises(DevlegateError, match=reason):
            run_test_iteration(source.engine)
        source.assert_retained()
        source.engine = Devlegate(source.config)


def test_status_admits_replayed_prefix_without_mutating_source(source):
    assert source.engine.status_view().plan.action == "run-worker"
    ipc_plan(source, "run-worker")
    proof = source.engine._prove_deferred_failed_rework(
        ExecutionReportStore(source.control).list("T-1")
    )
    assert proof.history.mode == "replay-equivalent-prefix"
    assert len(proof.history.reviewed_commits) == 17
    assert len(proof.history.failed_commits) == 18
    assert proof.sources.reviewed.execution_id == source.reviewed.execution_id
    assert proof.sources.failed.execution_id == source.failed.execution_id
    source.assert_retained()


@pytest.mark.parametrize(
    ("corruption", "reason"),
    [
        ("review", "cannot bind curator feedback"),
        ("report_base", "lifecycle publication was rewritten"),
        ("report_head", "lifecycle publication was rewritten"),
        ("report_result", "lifecycle publication was rewritten"),
        ("ambiguous_reports", "lifecycle history is ambiguous"),
        ("missing_report", "missing or cannot be attributed"),
        ("cache", "metadata conflicts"),
        ("nonterminal_owner", "workspace ownership refers to a different generation"),
    ],
)
def test_authenticated_source_or_owner_refusal(source, monkeypatch, corruption, reason):
    engine = source.engine
    if corruption == "cache":
        engine._save_state("idle", failed_executions={
            "T-1": {
                "execution_id": "conflicting",
                "product_head": source.failed.code_base_head,
                "remote_head": source.failed.code_base_head,
                "control_head": source.failed.control_head,
                "todo_fingerprint": "old", "reason": "transport failed",
            }
        })
    elif corruption == "nonterminal_owner":
        git(source.control, "mv", "kanban/done/T-2.md", "kanban/backlog/T-2.md")
        git(source.control, "commit", "-m", "unrelated owner is no longer terminal")
        git(source.control, "push", "origin", "devlegate/control")
    else:
        if corruption == "ambiguous_reports":
            for path in source.immutable:
                if path.suffix == ".json":
                    path.write_text(path.read_text() + "\n")
        elif corruption == "review":
            path = next(p for p in source.immutable if p.suffix == ".md")
            path.write_text(path.read_text().replace(
                source.reviewed.execution_id, source.failed.execution_id
            ))
        else:
            path = (
                source.control / "executions/T-1" / f"{source.failed.execution_id}.json"
            )
            if corruption == "missing_report":
                path.unlink()
            else:
                payload = json.loads(path.read_text())
                if corruption == "report_result":
                    payload = replace(
                        source.failed, result=source.reviewed.result
                    ).as_dict()
                else:
                    key = (
                        "code_base_head" if corruption == "report_base"
                        else "workspace_head"
                    )
                    payload[key] = "f" * 40
                path.write_text(json.dumps(payload))
        git(source.control, "add", ".")
        git(source.control, "commit", "-m", f"corrupt disposable {corruption}")
        git(source.control, "push", "origin", "devlegate/control")
    calls = []
    def unexpected_worker(*_a, **_k):
        calls.append("worker")

    monkeypatch.setattr(engine._workers, "run", unexpected_worker)
    before_refs = git(source.working, "show-ref").stdout
    before_remote = git(source.working, "ls-remote", "origin").stdout
    for _ in range(2):
        plan = engine.status_view().plan
        assert plan.action == "blocked"
        assert reason in plan.reason
        with pytest.raises(DevlegateError, match=reason):
            run_test_iteration(engine)
        assert not calls
        assert git(source.working, "show-ref").stdout == before_refs
        assert git(source.working, "ls-remote", "origin").stdout == before_remote
        engine = Devlegate(source.config)
        monkeypatch.setattr(engine._workers, "run", unexpected_worker)


def test_replayed_prefix_restarts_every_preparation_stage_once(source, monkeypatch):
    from devlegate.isolated_generation import PREPARATION_STAGES

    engine = source.engine
    execution = None
    calls = []

    def worker(workspace, _prompt, **kwargs):
        calls.append(kwargs["execution_id"])
        assert len(git(source.working, "rev-list",
                       f"{workspace.base_head}..{workspace.head}"
                       ).stdout.splitlines()) == 18
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "resumed", (), ()), None
        )

    for stop_stage in PREPARATION_STAGES:
        save = engine._save_state

        def interrupt(*args, **kwargs):
            result = save(*args, **kwargs)
            stage = kwargs.get("rework_provenance", {}).get("preparation_stage")
            if stage == stop_stage:
                raise RuntimeError("persisted preparation interruption")
            return result

        with monkeypatch.context() as fault:
            fault.setattr(engine, "_save_state", interrupt)
            with pytest.raises(RuntimeError, match="preparation interruption"):
                run_test_iteration(engine)
        engine = source.engine = Devlegate(source.config)
        monkeypatch.setattr(engine._workers, "run", worker)
        if execution is None:
            execution = engine._state["execution_id"]
        assert engine._state["execution_id"] == execution
        assert engine._state["rework_provenance"]["preparation_stage"] == stop_stage
        assert not calls
        source.assert_retained()
    assert run_test_iteration(engine) == 0
    assert calls == [execution]
    assert len(ExecutionReportStore(source.control).list("T-1")) == 3
    source.assert_retained()


@pytest.fixture
def published_source(source, monkeypatch):
    """Stop ordinary scheduling at its durable pre-worker publication boundary."""
    engine = source.engine
    save = engine._save_state

    def interrupt(*args, **kwargs):
        result = save(*args, **kwargs)
        if kwargs.get("rework_provenance", {}).get("preparation_stage") == (
            "remote_published"
        ):
            raise RuntimeError("published before worker admission")
        return result

    with monkeypatch.context() as fault:
        fault.setattr(engine, "_save_state", interrupt)
        with pytest.raises(RuntimeError, match="before worker admission"):
            run_test_iteration(engine)
    engine = source.engine = Devlegate(source.config)
    published = engine._inspect_published_isolated_workspace(engine._state)
    paths = source.control / "executions/T-1"
    reviewed_path = paths / f"{source.reviewed.execution_id}.json"
    report_path = paths / f"{source.failed.execution_id}.json"
    artifact = next(p for p in source.immutable if p.suffix == ".md")
    context = SimpleNamespace(
        working=source.working, config=source.config, control=source.control,
        workspace=source.workspace, checkpoint=source.failed.workspace_head,
        reviewed=source.reviewed, reviewed_path=reviewed_path,
        reviewed_bytes=source.immutable[reviewed_path], report_path=report_path,
        report_bytes=source.immutable[report_path], artifact=artifact,
        review_bytes=source.immutable[artifact], prompts=[],
    )
    source.assert_retained()
    return source, context, published


@pytest.mark.parametrize("stage", [
    "checkpointing", "post-checkpoint", "publishing", "post-publication", "lifecycle",
    "report_restart", "integration_restart", "product_advance",
    "ref_conflict", "foreign_worktree", "control_conflict",
])
def test_replayed_prefix_post_worker_and_integration_boundaries(
    published_source, monkeypatch, tmp_path, stage
):
    source, context, published = published_source
    engine = source.engine
    rework = engine._state["rework_provenance"]
    _exercise_isolated_lifecycle(
        context, engine, published, rework["candidate_head"], rework["target"],
        engine._state["execution_id"], f"isolated_lifecycle_{stage}",
        tmp_path, monkeypatch,
    )
    source.assert_retained()


@pytest.mark.parametrize("stage", [
    "isolated_recovery", "isolated_recovery_before_save",
    "isolated_recovery_after_save",
])
def test_replayed_prefix_recovery_preserves_authority_boundary(
    published_source, monkeypatch, tmp_path, stage
):
    source, context, _published = published_source
    engine = source.engine
    rework = engine._state["rework_provenance"]
    _exercise_isolated_recovery(
        context, engine, rework["candidate_head"], rework["target"],
        engine._state["execution_id"], git(source.working, "show-ref").stdout,
        git(source.working, "worktree", "list", "--porcelain").stdout,
        engine._state["failed_executions"], stage, tmp_path, monkeypatch,
    )
    source.assert_retained()


@pytest.mark.parametrize("advance", [False, True])
def test_replayed_prefix_ordinary_worker_report_accepted_done(
    tmp_path, monkeypatch, advance
):
    source = seed_deferred_source(tmp_path, monkeypatch, advance=advance)
    engine = source.engine
    calls = []

    def worker(workspace, prompt, **kwargs):
        calls.append(kwargs["execution_id"])
        assert workspace.path != source.workspace
        assert "Genuine curator: preserve every reviewed step." in prompt
        assert (workspace.path / "ordinal.txt").read_text() == "17\n"
        assert (workspace.path / "suffix.txt").exists()
        assert len(git(source.working, "rev-list",
                       f"{workspace.base_head}..{workspace.head}"
                       ).stdout.splitlines()) == 18
        (workspace.path / "continued.txt").write_text("new generation progress\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "continued", (), ()), None
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert engine.status_view().plan.action == "run-worker"
    assert run_test_iteration(engine) == 0
    execution = engine._state["execution_id"]
    assert calls == [execution]
    generation = Path(engine._state["execution_path"])
    assert generation != source.workspace
    rework = engine._state["rework_provenance"]
    assert git(source.working, "rev-parse", rework["source_ref"]
               ).stdout.strip() == source.failed.workspace_head
    reports = ExecutionReportStore(source.control).list("T-1")
    report = next(r for r in reports if r.execution_id == execution)
    assert report.execution_path == str(generation)
    assert report.execution_branch == f"devlegate/generation/T-1/{execution}"
    assert report.result.conclusion == "completed"
    source.assert_retained()
    git(source.control, "mv", "kanban/review/T-1.md", "kanban/accepted/T-1.md")
    git(source.control, "commit", "-m", "accept genuine isolated execution")
    git(source.control, "push", "origin", "devlegate/control")
    for _ in range(3):
        engine = source.engine = Devlegate(source.config)
        monkeypatch.setattr(engine._workers, "run", worker)
        assert run_test_iteration(engine) == 0
        assert (source.control / "kanban/done/T-1.md").exists()
        assert engine._state["execution_id"] == execution
        source.assert_retained()
    assert calls == [execution]
    assert len(ExecutionReportStore(source.control).list("T-1")) == 3
    assert (source.working / "continued.txt").read_text() == "new generation progress\n"
    assert generation.exists()  # Cleanup needs separate authority, even after done.
