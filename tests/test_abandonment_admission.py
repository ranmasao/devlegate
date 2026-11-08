# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Durable admission and replacement refusals in disposable real Git stores."""

import dataclasses
import json

import pytest
from test_abandonment import (
    PATHS,
    commit,
    git,
    init_repo,
    owner_source,
    request,
    ticket,
)

from devlegate.abandonment_admission import (
    AbandonmentAdmission,
    observe_resources,
    require_quiescence,
)
from devlegate.abandonment_evidence import EvidenceError, build_abandonment_evidence
from devlegate.execution_result import ExecutionReportStore, build_execution_report
from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.git import run_git
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime import ServiceEngine
from devlegate.runtime_state_validation import DevlegateError, validate_state_invariant
from devlegate.tickets import load_ticket_store
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


@pytest.fixture
def admission_case(tmp_path):
    product = init_repo(tmp_path / "product")
    (product / "source").write_text("initial\n")
    base = commit(product, "product base")
    remote = tmp_path / "remote.git"
    git(product, "init", "--bare", str(remote))
    git(product, "remote", "add", "origin", str(remote))
    control = init_repo(tmp_path / "control")
    ticket(control, "A-TARGET")
    request(control, start_head=base)
    head = commit(control, "admit request and target")
    return [product, control, base, head, tmp_path / "resources"]


def resources(case):
    product, control, _base, head, root = case
    return observe_resources(
        repo=product, root=root, remote="origin",
        target=load_ticket_store(control, PATHS).by_id["A-TARGET"],
        control=control, control_head=head, source=owner_source(control),
        runner=run_git,
    )


def admission(case, observation=None):
    _product, control, base, head, _root = case
    store = load_ticket_store(control, PATHS)
    return AbandonmentAdmission.create(
        state={"phase": "idle", "worker_identity": None}, active_count=0,
        store=store, request=store.by_id["Z-REQUEST"], control=owner_source(control),
        control_head=head, product_head=base, execution_id="1" * 32,
        resources=resources(case) if observation is None else observation,
        control_root=control,
    )


def test_admission_replays_exact_blobs_and_manifest_binding(admission_case):
    product, control, base, head, _root = admission_case
    admitted = admission(admission_case)
    restored = AbandonmentAdmission.restore(json.loads(admitted.encoded))
    assert restored == admitted
    value = admitted.as_dict()
    assert value["resources"]["generation"] is None
    assert value["resources"]["upstream_continuity"] == "unproven"
    (control / "unrelated").write_text("unrelated control history\n")
    descendant = commit(control, "unrelated publication")
    store = load_ticket_store(control, PATHS)
    restored.verify(store=store, control=owner_source(control),
                    control_head=descendant, control_root=control)
    manifest = build_abandonment_evidence(
        control=owner_source(control), product=owner_source(product),
        control_head=head, request=store.by_id["Z-REQUEST"],
        target=store.by_id["A-TARGET"],
        request_path="kanban/todo/Z-REQUEST.md", target_path="kanban/todo/A-TARGET.md",
        product_head=base, admission=value,
    ).manifest()
    assert manifest["admission"] == value
    assert not any("resource observation" in gap for gap in manifest["gaps"])
    value["target"]["blob"] = "f" * 40
    with pytest.raises(EvidenceError, match="specification differs"):
        build_abandonment_evidence(
            control=owner_source(control), product=owner_source(product),
            control_head=head, request=store.by_id["Z-REQUEST"],
            target=store.by_id["A-TARGET"], request_path="kanban/todo/Z-REQUEST.md",
            target_path="kanban/todo/A-TARGET.md", product_head=base, admission=value,
        )


@pytest.mark.parametrize("change", ["reason", "target", "target-body", "removed"])
def test_admission_never_silently_retargets_or_amends(admission_case, change):
    _product, control, _base, _head, _root = admission_case
    admitted = admission(admission_case)
    path = control / "kanban/todo/Z-REQUEST.md"
    if change == "removed":
        path.unlink()
    elif change == "target":
        ticket(control, "B-TARGET")
        path.write_text(path.read_text().replace('"A-TARGET"', '"B-TARGET"'))
    elif change == "target-body":
        target_path = control / "kanban/todo/A-TARGET.md"
        target_path.write_text(target_path.read_text() + "Amended scope.\n")
    else:
        path.write_text(path.read_text().replace("The experiment", "This experiment"))
    head = commit(control, "replace admitted source")
    with pytest.raises(EvidenceError, match="blob changed|request is missing"):
        admitted.verify(store=load_ticket_store(control, PATHS),
                        control=owner_source(control), control_head=head,
                        control_root=control)
    # Removing or rewriting the request must not release the persisted target
    # reservation or let any ordinary checkout worker through the planner.
    engine = ServiceEngine.__new__(ServiceEngine)
    barrier = engine._admission_barrier(
        {"phase": "idle", "abandonment_admission": admitted.as_dict()},
        load_ticket_store(control, PATHS), False, True, None,
    )
    assert barrier.action == "blocked"
    assert "A-TARGET" in barrier.reason


@pytest.mark.parametrize("reverted", [False, True])
def test_admission_refuses_new_target_history(admission_case, reverted):
    _product, control, _base, _head, _root = admission_case
    admitted = admission(admission_case)
    path = control / "reviews/A-TARGET/new-review.md"
    path.parent.mkdir(parents=True)
    path.write_text("Additional target evidence after analysis admission.\n")
    head = commit(control, "new target evidence")
    if reverted:
        path.unlink()
        head = commit(control, "remove target evidence again")
    with pytest.raises(EvidenceError, match="evidence changed after"):
        admitted.verify(store=load_ticket_store(control, PATHS),
                        control=owner_source(control), control_head=head,
                        control_root=control)


@pytest.mark.parametrize("field", [
    "accepted_integration", "reconciliation", "operator_recovery", "automatic_retry",
    "force_retry_authorization", "resume_required", "execution_ref_retirement",
    "workspace_cleanup_pending", "worker_identity",
])
def test_uncertain_ownership_is_not_normalized_for_admission(field):
    with pytest.raises(EvidenceError, match="ownership|proven absent"):
        require_quiescence({"phase": "idle", field: {}}, 0)


@pytest.mark.parametrize("phase,count", [("agent_running", 0), ("agent_pending", 0),
                                         ("merge_pending", 0), ("idle", 1)])
def test_live_or_pending_worker_refused(phase, count):
    with pytest.raises(EvidenceError, match="idle owner"):
        require_quiescence({"phase": phase}, count)


def test_same_sha_foreign_worktree_is_not_adopted(admission_case):
    product, _control, base, _head, root = admission_case
    manager = ExecutionWorkspaceManager(product, root, "A-TARGET")
    manager.prepare(base)
    with pytest.raises(EvidenceError, match="unique independently verified"):
        resources(admission_case)


def bound_resources(case):
    product, control, base, head, root = case
    manager = ExecutionWorkspaceManager(product, root, "A-TARGET")
    binding = ResourceBinding.plan(
        manager, execution_id="2" * 32, base=base, control=head,
        ticket_blob=git(control, "rev-parse", "HEAD:kanban/todo/A-TARGET.md"),
        remote="origin", remote_head=None,
    )
    binding.record_intent()
    workspace = binding.prepare(remote_head=None)
    binding.begin_operation("worker", {"start": base})
    binding.finish_operation("worker", {"start": base, "group_retired": True})
    binding.record_worker_retirement()
    report = build_execution_report(
        execution_id="2" * 32, ticket_id="A-TARGET", code_base_head=base,
        control_head=head, execution_branch=manager.branch,
        execution_path=str(manager.path), workspace_head=workspace.head,
        run=WorkerRunResult(0, None, WorkerClaim("incomplete", "An experiment", (), ()),
                            None),
    )
    ExecutionReportStore(control).write(dataclasses.replace(
        report, resource_generation=binding.intent["generation"],
    ))
    case[3] = commit(control, "immutable execution report")
    return binding


def test_dirty_owned_generation_is_observed_without_disposal(admission_case):
    binding = bound_resources(admission_case)
    source = binding.manager.path / "source"
    source.write_text("unfinished dirty work\n")
    observed = resources(admission_case)
    assert observed["dirty"]
    assert observed["generation"] == binding.intent["generation"]
    assert observed["execution_id"] == "2" * 32
    assert admission(admission_case, observed).as_dict()["resources"] == observed
    assert source.read_text() == "unfinished dirty work\n"


def test_lost_retirement_or_same_sha_replacement_blocks(admission_case):
    binding = bound_resources(admission_case)
    retirement = next(binding.directory.glob("worker-retired.*.json"))
    saved = retirement.read_bytes()
    retirement.unlink()
    with pytest.raises(EvidenceError, match="retirement is unproven"):
        resources(admission_case)
    retirement.write_bytes(saved)
    manager = binding.manager
    git(manager.repo, "worktree", "remove", str(manager.path))
    git(manager.repo, "worktree", "add", str(manager.path), manager.branch)
    with pytest.raises(EvidenceError, match="ownership unproven"):
        resources(admission_case)


def test_remote_outage_and_upstream_only_resource_refused(admission_case):
    product, _control, base, _head, _root = admission_case
    git(product, "push", "origin", f"{base}:refs/heads/devlegate/work/A-TARGET")
    with pytest.raises(EvidenceError, match="upstream-only"):
        resources(admission_case)
    git(product, "remote", "set-url", "origin", "/absent/disposable-test-remote")
    from devlegate.git import GitObservationError

    with pytest.raises(GitObservationError, match="cannot observe"):
        resources(admission_case)


def test_admission_state_validation_rejects_execution_overlap(admission_case):
    value = admission(admission_case).as_dict()
    validate_state_invariant({"phase": "idle", "abandonment_admission": value})
    with pytest.raises(DevlegateError, match="conflicts with execution"):
        validate_state_invariant({"phase": "agent_running",
                                  "abandonment_admission": value})
    value["resources"]["upstream_continuity"] = "proved by matching SHA"
    with pytest.raises(DevlegateError, match="cannot assert"):
        validate_state_invariant({"phase": "idle", "abandonment_admission": value})


@pytest.mark.parametrize("mode", [
    "completed", "cancelled", "process-loss", "live-loss", "launch-loss",
])
def test_owner_dispatch_persists_identity_result_and_reservation_across_restart(
    tmp_path_factory, monkeypatch, mode,
):
    from _daemon_support import make_engine
    from runtime_helpers import run_test_iteration

    from devlegate.evidence_worker import REPORT_SECTIONS, EvidenceWorkspace
    from devlegate.worker_supervisor import WorkerProcessIdentity

    root = tmp_path_factory.mktemp("ab")
    engine, config, _state = make_engine(root, monkeypatch)
    product_head = git(engine.repo, "rev-parse", "HEAD")
    # This is a disposable test control worktree, never the implementation's
    # actual control history or any hosted operator resource.
    ticket(engine.control_worktree, "A-TARGET")
    request(engine.control_worktree, start_head=product_head)
    commit(engine.control_worktree, "request abandonment in disposable fixture")
    git(engine.control_worktree, "push", "origin", "HEAD:devlegate/control")
    calls = []

    def worker(workspace, prompt, **kwargs):
        assert isinstance(workspace, EvidenceWorkspace)
        assert not (workspace.path / ".git").exists()
        admission_value = engine._state["abandonment_admission"]
        assert engine._state["abandonment_analysis"]["stage"] == "running"
        assert admission_value["execution_id"] == kwargs["execution_id"]
        manifest = kwargs["evidence"].bundle.manifest()
        assert manifest["admission"] == admission_value
        assert "not a kernel" in prompt
        calls.append(kwargs["execution_id"])
        if mode.endswith("loss"):
            if mode != "launch-loss":
                kwargs["identity_handler"](WorkerProcessIdentity(
                    kwargs["execution_id"], 9999999, 9999999, 9999999, "test-boot", 1,
                    "cgroup:/disposable/test-containment",
                ))
            raise RuntimeError("simulated owner process loss")
        report = "\n".join(f"## {name}\nSubstantive analysis of {name}."
                           for name in REPORT_SECTIONS)
        report += "\n" + manifest["manifest_id"]
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "Summarized the experiment", (), (),
                                 report), None,
            interruption_kind="service_shutdown" if mode == "cancelled" else None,
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    if mode.endswith("loss"):
        with pytest.raises(RuntimeError, match="simulated owner process loss"):
            run_test_iteration(engine)
    else:
        run_test_iteration(engine)
    assert len(calls) == 1
    record = engine._state["abandonment_analysis"]
    assert record["stage"] == ("running" if mode.endswith("loss") else "reported")
    restored = ServiceEngine(config)
    monkeypatch.setattr(
        restored._workers, "run", lambda *_a, **_k: pytest.fail("replay")
    )
    monkeypatch.setattr(
        restored._workers, "observe",
        lambda _: "matching-live" if mode == "live-loss" else "absent",
    )
    message = (
        "ownership is matching-live" if mode == "live-loss" else
        "ownership is unknown"
    )
    if mode in {"live-loss", "launch-loss"}:
        with pytest.raises(DevlegateError, match=message):
            run_test_iteration(restored)
    else:
        run_test_iteration(restored)
    if mode != "process-loss":
        assert restored._state["abandonment_analysis"] == record
    if mode not in {"live-loss", "launch-loss"}:
        result = restored._state["abandonment_analysis"]["report"]
        assert result["workspace_head"] is None
        assert result["execution_branch"] == ""
        assert result["conclusion"] == (
            "completed" if mode == "completed" else "failed"
        )
    assert git(engine.repo, "rev-parse", "HEAD") == product_head
    assert (engine.control_worktree / "kanban/todo/A-TARGET.md").exists()
    assert not (engine.control_worktree / "abandonments").exists()


def test_reported_analysis_binding_cannot_be_replaced(admission_case):
    value = admission(admission_case).as_dict()
    report = build_execution_report(
        execution_id=value["execution_id"], ticket_id=value["request_id"],
        code_base_head=value["product_head"], control_head=value["control_head"],
        execution_branch="", execution_path="/disposable/evidence", workspace_head=None,
        run=WorkerRunResult(-1, "process loss", None, None),
    )
    analysis = {
        "stage": "reported", "worker_identity": None, "report": report.as_dict(),
        "workspace": {"ticket_id": value["request_id"], "path": "/disposable/evidence",
                      "manifest_id": "a" * 64, "manifest_sha256": "b" * 64,
                      "directory_identity": [1, 2]},
    }
    state = {"phase": "idle", "abandonment_admission": value,
             "abandonment_analysis": analysis}
    validate_state_invariant(state)
    analysis["report"]["execution_id"] = "9" * 32
    with pytest.raises(DevlegateError, match="report binding differs"):
        validate_state_invariant(state)
