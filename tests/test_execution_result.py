# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import os

import pytest

import devlegate.execution_result as execution_result_module
from devlegate.execution_result import (
    ExecutionReportError,
    ExecutionReportStore,
    build_execution_report,
    build_execution_result,
    new_execution_id,
)
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


def run(
    outcome="completed",
    *,
    process=0,
    transport_error=None,
    egress_error=None,
    remaining=(),
    questions=(),
    report=None,
):
    claim = WorkerClaim(outcome, "worker summary", remaining, questions, report)
    return WorkerRunResult(process, transport_error, claim, egress_error)


def test_valid_claim_outcomes_become_canonical_conclusions():
    assert build_execution_result(run("completed")).conclusion == "completed"
    assert build_execution_result(run("incomplete")).conclusion == "incomplete"
    assert build_execution_result(run("blocked")).conclusion == "blocked"


def test_listing_missing_ticket_report_directory_is_empty(tmp_path):
    assert ExecutionReportStore(tmp_path).list("T-1") == ()


@pytest.mark.parametrize("kind", ["symlink", "file"])
def test_listing_unsafe_ticket_report_directory_fails_closed(tmp_path, kind):
    root = tmp_path / "executions"
    root.mkdir()
    target = tmp_path / "target"
    if kind == "symlink":
        target.mkdir()
        (root / "T-1").symlink_to(target, target_is_directory=True)
    else:
        (root / "T-1").write_text("unsafe")

    with pytest.raises(ExecutionReportError, match="ticket directory is unsafe"):
        ExecutionReportStore(tmp_path).list("T-1")


@pytest.mark.parametrize(
    "worker_run",
    [
        run(process=7),
        run(transport_error="invalid JSON event"),
        WorkerRunResult(
            0, None, None, "exactly one devlegate_report event is required"
        ),
    ],
)
def test_technical_or_egress_failure_becomes_failed_without_erasing_claim(worker_run):
    result = build_execution_result(worker_run)

    assert result.conclusion == "failed"
    if result.claim is not None:
        assert result.claim.summary == "worker summary"


def test_failed_result_preserves_questions_and_remaining():
    result = build_execution_result(
        run(process=7, remaining=("finish tests",), questions=("Which API?",))
    )

    assert result.conclusion == "failed"
    assert result.remaining == ("finish tests",)
    assert result.questions == ("Which API?",)


def test_valid_claim_cannot_have_typed_egress_error():
    with pytest.raises(ExecutionReportError, match="typed-egress"):
        build_execution_result(run(egress_error="invalid typed egress"))


def test_execution_report_round_trips_all_facts():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="devlegate/work/T-1",
        execution_path="/state/work/T-1",
        workspace_head="workspace-head",
        run=run(
            process=7,
            remaining=("more work",),
            questions=("Need input",),
        ),
    )

    restored = report.from_dict(json.loads(report.to_json()))

    assert restored == report
    assert restored.schema == "devlegate.execution-report.v1"
    assert restored.result.claim is not None
    assert restored.result.questions == ("Need input",)


def test_historical_v1_report_without_optional_artifact_fields_remains_readable():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )
    payload = report.as_dict()
    payload.pop("report_artifact")
    payload.pop("report_sha256")

    restored = report.from_dict(payload)

    assert restored == report
    assert restored.report_artifact is None
    assert restored.report_sha256 is None


def test_historical_v1_report_with_long_form_claim_is_not_reinterpreted():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "summary", (), (), "details"), None
        ),
    )
    payload = report.as_dict()
    payload.pop("report_artifact")
    payload.pop("report_sha256")

    with pytest.raises(ExecutionReportError, match="artifact binding"):
        report.from_dict(payload)


@pytest.mark.parametrize(
    "change",
    [
        {"schema": "future"},
        {"execution_id": None},
        {"conclusion": "unknown"},
        {"process_returncode": "0"},
        {"worker_claim": {"outcome": "completed"}},
    ],
)
def test_malformed_execution_report_is_rejected(change):
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )
    payload = report.as_dict()
    payload.update(change)

    with pytest.raises(ExecutionReportError):
        report.from_dict(payload)


def test_inconsistent_canonical_conclusion_is_rejected():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code",
        control_head="control",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(process=7),
    )
    payload = report.as_dict()
    payload["conclusion"] = "completed"

    with pytest.raises(ExecutionReportError, match="conclusion"):
        report.from_dict(payload)


def test_inconsistent_canonical_reason_is_rejected():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code",
        control_head="control",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )
    payload = report.as_dict()
    payload["reason"] = "worker exited with status 1"

    with pytest.raises(ExecutionReportError, match="reason"):
        report.from_dict(payload)


def test_report_readback_rejects_valid_claim_with_egress_error():
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code",
        control_head="control",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )
    payload = report.as_dict()
    payload["egress_error"] = "impossible"
    payload["egress_ok"] = False
    payload["conclusion"] = "failed"
    payload["reason"] = "impossible"

    with pytest.raises(ExecutionReportError, match="typed-egress"):
        report.from_dict(payload)


def test_report_store_is_control_plane_only_and_never_overwrites(tmp_path):
    store = ExecutionReportStore(tmp_path / "control")
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="devlegate/work/T-1",
        execution_path="workspace",
        workspace_head="head",
        run=run(),
    )

    path = store.write(report)

    assert path == tmp_path / "control/executions/T-1/attempt-1.json"
    assert store.read("T-1", "attempt-1") == report
    with pytest.raises(ExecutionReportError, match="already exists"):
        store.write(report)
    assert not (tmp_path / "control/kanban").exists()


def test_report_store_persists_long_form_report_with_exact_binding(tmp_path):
    store = ExecutionReportStore(tmp_path / "control")
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="workspace",
        workspace_head="head",
        run=run(report="# Findings\n\nEvidence\n"),
    )

    store.write(report)

    artifact = tmp_path / "control/executions/T-1/attempt-1.md"
    assert artifact.read_text() == "# Findings\n\nEvidence\n"
    assert store.read("T-1", "attempt-1") == report
    assert store.list("T-1") == (report,)


def test_report_store_replays_after_artifact_published_before_json(
    tmp_path, monkeypatch
):
    store = ExecutionReportStore(tmp_path / "control")
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="workspace",
        workspace_head="head",
        run=run(report="# Findings\n"),
    )
    original_link = execution_result_module.os.link
    calls = 0

    def fail_before_json(source, destination):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("simulated crash before JSON publication")
        original_link(source, destination)

    monkeypatch.setattr(execution_result_module.os, "link", fail_before_json)
    with pytest.raises(ExecutionReportError, match="cannot persist"):
        store.write(report)

    json_path = tmp_path / "control/executions/T-1/attempt-1.json"
    artifact = tmp_path / "control/executions/T-1/attempt-1.md"
    assert not json_path.exists()
    assert artifact.read_text() == "# Findings\n"

    monkeypatch.setattr(execution_result_module.os, "link", os.link)
    assert store.write(report) == json_path
    assert store.read("T-1", "attempt-1") == report


def test_report_store_rejects_conflicting_orphan_artifact(tmp_path):
    store = ExecutionReportStore(tmp_path / "control")
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code-base",
        control_head="control-head",
        execution_branch="branch",
        execution_path="workspace",
        workspace_head="head",
        run=run(report="# Expected\n"),
    )
    artifact = tmp_path / "control/executions/T-1/attempt-1.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("# Conflicting\n")

    with pytest.raises(ExecutionReportError, match="artifact already exists"):
        store.write(report)
    assert not artifact.with_suffix(".json").exists()


def test_report_store_lists_immutable_history(tmp_path):
    store = ExecutionReportStore(tmp_path / "control")
    failed = build_execution_report(
        execution_id="attempt-1", ticket_id="T-1", code_base_head="code",
        control_head="control", execution_branch="branch", execution_path="path",
        workspace_head=None, run=run(process=7),
    )
    completed = build_execution_report(
        execution_id="attempt-2", ticket_id="T-1", code_base_head="code",
        control_head="control", execution_branch="branch", execution_path="path",
        workspace_head="head", run=run(),
    )

    store.write(failed)
    store.write(completed)

    assert store.list("T-1") == (failed, completed)
    assert store.read("T-1", "attempt-1") == failed


def test_report_store_rejects_symlinked_control_worktree(tmp_path):
    real_control = tmp_path / "real-control"
    real_control.mkdir()
    linked_control = tmp_path / "control"
    linked_control.symlink_to(real_control, target_is_directory=True)
    store = ExecutionReportStore(linked_control)
    report = build_execution_report(
        execution_id="attempt-1",
        ticket_id="T-1",
        code_base_head="code",
        control_head="control",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )

    with pytest.raises(ExecutionReportError, match="control worktree root"):
        store.write(report)
    with pytest.raises(ExecutionReportError, match="control worktree root"):
        store.read("T-1", "attempt-1")


@pytest.mark.parametrize(
    "ticket_id, execution_id", [("../escape", "x"), ("T-1", "../escape")]
)
def test_report_store_rejects_path_traversal(tmp_path, ticket_id, execution_id):
    store = ExecutionReportStore(tmp_path / "control")
    report = build_execution_report(
        execution_id=execution_id,
        ticket_id=ticket_id,
        code_base_head="code",
        control_head="control",
        execution_branch="branch",
        execution_path="path",
        workspace_head=None,
        run=run(),
    )

    with pytest.raises(ExecutionReportError, match="invalid execution report"):
        store.write(report)


def test_execution_ids_are_devlegate_owned_opaque_values():
    first = new_execution_id()
    second = new_execution_id()

    assert first != second
    assert len(first) == 32
    assert first.isalnum()
