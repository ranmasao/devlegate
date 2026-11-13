# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable real-Git resource prune policy and owner admission."""

import dataclasses
import threading

import pytest
from _control_support import Devlegate, git
from runtime_helpers import run_test_iteration
from test_clean_preview import owner as owner
from test_clean_preview import snapshot

from devlegate import cli
from devlegate.ipc_protocol import IPCRequest
from devlegate.ipc_server import dispatch_request
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime import DevlegateError, WorkflowBlockedError


@pytest.fixture
def done(owner, monkeypatch):
    control = owner.control_worktree
    (control / "kanban/review/T-1.md").rename(control / "kanban/accepted/T-1.md")
    git(control, "add", "-A")
    git(control, "commit", "-m", "accept T-1")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    # Retain a normal integrated generation for the explicit prune operation.
    monkeypatch.setattr(owner, "_finish_integrated_workspace_cleanup", lambda: None)
    monkeypatch.setattr(owner, "_reconcile_workspace_backlog", lambda: None)
    assert run_test_iteration(owner) == 0
    return owner


def admit(owner, method, payload=None, request_id="prune-one"):
    owner.begin_hosted_owner()
    owner._service_wake.clear()
    results = []
    errors = []

    def client():
        try:
            results.append(dispatch_request(owner, IPCRequest(
                1, request_id, method, payload or {},
            )))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=client)
    thread.start()
    assert owner._service_wake.wait(2)
    command, _scheduler = owner._claim_owner_work(True)
    assert command is not None
    try:
        owner._admit_operator_command(command)
    finally:
        owner._release_operator_command()
        thread.join(5)
        owner.end_hosted_owner()
    assert not thread.is_alive()
    if errors:
        raise errors[0]
    return results[0]


def test_done_preview_and_inventory_are_observation_only(done):
    store = done._runtime_store.observe()
    before = snapshot(done.repo.parent)
    for method, payload in (
        ("workspace-ls", {}), ("cleanup-workspaces", {"limit": 20}),
    ):
        result = admit(done, method, payload)
        assert result["dry_run"] is True
        assert result["decisions"][0]["status"] == "eligible", result
    assert snapshot(done.repo.parent) == before
    assert done._runtime_store.observe() == store


def test_apply_retains_audit_and_replays_durable_result_after_restart(done):
    audit = snapshot(done.control_worktree / "executions")
    result = admit(done, "cleanup-workspaces", {"apply": True, "limit": 20})
    decision = result["decisions"][0]
    assert decision["status"] == "retired", decision["reason"]
    assert result["decisions"][0]["resources"] == {
        "worktree": "absent", "local_ref": "absent", "upstream_ref": "absent",
    }
    assert snapshot(done.control_worktree / "executions") == audit
    restarted = Devlegate(done.env_file)
    assert restarted.submit_cleanup_workspaces(
        apply=True, limit=20, request_id="prune-one",
    ) == result
    with pytest.raises(DevlegateError, match="collision"):
        restarted.submit_cleanup_workspaces(
            apply=True, limit=1, request_id="prune-one",
        )


@pytest.mark.parametrize("change", ["dirty", "locked", "unproven", "remote"])
def test_prune_preserves_unsafe_or_unproven_resources(done, change, tmp_path):
    manager = done.recovery_workspace_manager("T-1")
    if change == "dirty":
        (manager.path / "untracked").write_text("keep me\n")
    elif change == "locked":
        git(done.repo, "worktree", "lock", str(manager.path))
    elif change == "unproven":
        binding = ResourceBinding.for_execution(
            manager, done._accepted_execution_report("T-1").execution_id,
        )
        (binding.directory / "intent.json").rename(binding.directory / "preserved.json")
    else:
        git(done.repo, "remote", "set-url", "origin", str(tmp_path / "missing-remote"))
    before = snapshot(done.repo.parent)
    result = admit(done, "cleanup-workspaces", {"limit": 20})
    assert all(d["status"] in {"blocked", "unproven"} for d in result["decisions"])
    assert snapshot(done.repo.parent) == before
    result = admit(done, "cleanup-workspaces", {"apply": True, "limit": 20})
    assert all(d["status"] == "blocked" for d in result["decisions"])
    assert manager.path.exists()


@pytest.mark.parametrize("state", [
    "review", "todo", "backlog", "accepted", "abandoned",
])
def test_non_done_is_never_a_prune_candidate(owner, state):
    if state != "review":
        control = owner.control_worktree
        (control / f"kanban/{state}").mkdir(exist_ok=True)
        (control / "kanban/review/T-1.md").rename(control / f"kanban/{state}/T-1.md")
        git(control, "add", "-A")
        git(control, "commit", "-m", f"move fixture to {state}")
    result = admit(owner, "cleanup-workspaces", {"apply": True})
    assert result["decisions"][0]["status"] == "blocked"
    assert "not durably done" in result["decisions"][0]["reason"]
    assert owner.recovery_workspace_manager("T-1").path.exists()


@pytest.mark.parametrize("after_refs", [False, True])
def test_interrupted_apply_resumes_only_its_durable_cohort(
    done, monkeypatch, after_refs,
):
    retire_refs = done._retire_execution_refs

    def crash(*_args, **_kwargs):
        if after_refs:
            retire_refs(*_args, **_kwargs)
        raise RuntimeError("simulated owner loss after worktree retirement")

    monkeypatch.setattr(done, "_retire_execution_refs", crash)
    with pytest.raises(RuntimeError, match="owner loss"):
        admit(done, "cleanup-workspaces", {"apply": True, "limit": 1})
    assert not done.recovery_workspace_manager("T-1").path.exists()
    receipt = done._state["mutable_receipts"]["prune-one"]
    assert "result" not in receipt
    git(done.repo, "branch", "devlegate/work/T-99")
    restarted = Devlegate(done.env_file)
    result = admit(restarted, "cleanup-workspaces", {"apply": True, "limit": 1})
    assert [item["ticket_id"] for item in result["decisions"]] == ["T-1"]
    decision = result["decisions"][0]
    assert decision["status"] == "retired", decision["reason"]
    assert git(done.repo, "rev-parse", "refs/heads/devlegate/work/T-99").stdout


def test_concurrent_prune_cannot_bypass_owner_admission(done):
    done.begin_hosted_owner()
    results = []
    errors = []

    def first():
        try:
            results.append(done.submit_cleanup_workspaces(
                apply=True, limit=1, request_id="first",
            ))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=first)
    thread.start()
    assert done._service_wake.wait(2)
    with pytest.raises(DevlegateError, match="busy"):
        done.submit_cleanup_workspaces(apply=True, limit=1, request_id="second")
    command, _ = done._claim_owner_work(True)
    done._admit_operator_command(command)
    done._release_operator_command()
    thread.join(5)
    done.end_hosted_owner()
    assert not errors
    assert results[0]["decisions"][0]["status"] == "retired"
    assert "second" not in done._state["mutable_receipts"]


@pytest.mark.parametrize("owner", ["nested"], indirect=True)
def test_nested_submodule_preview_is_read_only_and_dirty_submodule_blocks(done):
    before = snapshot(done.repo.parent)
    assert admit(done, "workspace-ls")["decisions"][0]["status"] == "eligible"
    assert snapshot(done.repo.parent) == before
    path = done.recovery_workspace_manager("T-1").path / "dependency/nested/data"
    path.write_text("retain dirty nested work\n")
    before = snapshot(done.repo.parent)
    result = admit(done, "cleanup-workspaces")
    assert result["decisions"][0]["status"] == "blocked"
    assert snapshot(done.repo.parent) == before


def test_cli_table_reports_partial_effects(done, monkeypatch, capsys):
    def blocked(*_args, **_kwargs):
        raise WorkflowBlockedError("upstream unavailable after worktree retirement")

    monkeypatch.setattr(done, "_retire_execution_refs", blocked)

    def request(_path, method, payload, **kwargs):
        assert kwargs["mutable"] is True
        return admit(done, method, payload, kwargs["request_id"])

    monkeypatch.setattr(cli, "request", request)
    args = cli.build_parser().parse_args([
        "workspace", "prune", "--apply", "--request-id", "cli-apply",
    ])
    assert cli._workspace_daemon(done.env_file, args) == 0
    output = capsys.readouterr().out
    assert "Workspace prune (apply)" in output
    assert "blocked" in output and "absent" in output and "present" in output


def test_immediate_automatic_cleanup_also_preserves_unbound_history(done):
    report = done._accepted_execution_report("T-1")
    integration = done._find_accepted_integration_commit("T-1", report)
    before = snapshot(done.repo.parent)
    with pytest.raises(WorkflowBlockedError, match="generation is unproven"):
        done._retire_integrated_workspace(
            "T-1", dataclasses.replace(report, resource_generation=None), integration,
        )
    assert snapshot(done.repo.parent) == before
