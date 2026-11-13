# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Operator-only durable pre-effect boundary, using disposable real Git."""

import copy
import json
import sys
import threading

import pytest
from _control_support import Devlegate, git
from test_clean_preview import owner as preview_owner  # noqa: F401
from test_clean_preview import snapshot

from devlegate.clean_preview import inspect_clean
from devlegate.ipc_protocol import IPCProtocolError, IPCRequest
from devlegate.ipc_server import dispatch_request
from devlegate.runtime import OperatorCommand, _mutation_fingerprint
from devlegate.runtime_state_validation import DevlegateError, validate_state_invariant


@pytest.fixture
def owner(request):
    return request.getfixturevalue("preview_owner")


def confirmation(engine, *, inactive=True):
    if inactive:
        source = engine.control_worktree / engine.review_path / "T-1.md"
        destination = engine.control_worktree / engine.backlog_path / "T-1.md"
        destination.parent.mkdir(exist_ok=True)
        git(engine.control_worktree, "mv", str(source), str(destination))
        git(engine.control_worktree, "commit", "-m", "inactive ticket fixture")
    preview = inspect_clean(engine, "T-1")
    assert preview["digest"], preview["blockers"]
    return {
        "ticket_id": "T-1", "plan_digest": preview["digest"],
        "generation": preview["identity"]["executions"][0]["intent"]["generation"],
    }


def command(payload, request_id="reset-one"):
    return OperatorCommand(
        request_id=request_id, method="clean-apply",
        fingerprint=_mutation_fingerprint("clean-apply", payload), **payload,
    )


def admit(engine, payload):
    cmd = command(payload)
    engine._admit_operator_command(cmd)
    return cmd.admission_result


def test_durable_intent_has_no_resource_effect_until_scheduler_replay(owner):
    payload = confirmation(owner)
    before = snapshot(owner.execution_worktree_root / "work")
    remote = git(owner.repo, "ls-remote", "origin").stdout
    result = admit(owner, payload)
    assert result["status"] == "authorized-pre-effect"
    assert result["effects_performed"] is False
    intent = owner._state["clean_reset_intent"]
    assert intent["plan_digest"] == payload["plan_digest"]
    assert intent["identity"]["ticket"]["state"] == "backlog"
    restored = Devlegate(owner.env_file)
    assert restored._state["clean_reset_intent"] == intent
    assert restored.submit_clean_apply(**payload, request_id="reset-one") == result
    with pytest.raises(DevlegateError, match="collision"):
        restored.submit_clean_apply(
            **{**payload, "generation": "f" * 64}, request_id="reset-one",
        )
    with pytest.raises(DevlegateError, match="pre-effect"):
        restored.cleanup_workspaces(apply=True)
    assert snapshot(owner.execution_worktree_root / "work") == before
    assert git(owner.repo, "ls-remote", "origin").stdout == remote
    assert restored.run_iteration() == 0
    assert restored._state["clean_reset_intent"]["status"] == "completed"


@pytest.mark.parametrize("change", ["digest", "generation", "ticket", "control",
                                    "ref", "recreated"])
def test_stale_confirmation_has_no_intent(owner, change):
    payload = confirmation(owner)
    if change in {"digest", "generation"}:
        payload["plan_digest" if change == "digest" else change] = "f" * 64
    elif change in {"ticket", "control"}:
        path = owner.control_worktree / owner.backlog_path / "T-1.md"
        if change == "ticket":
            path.write_text(path.read_text() + "\nChanged specification.\n")
        else:
            (owner.control_worktree / "unrelated.txt").write_text("changed\n")
        git(owner.control_worktree, "add", ".")
        git(owner.control_worktree, "commit", "-m", "stale preview fixture")
    else:
        manager = owner.recovery_workspace_manager("T-1")
        if change == "ref":
            git(owner.repo, "update-ref", f"refs/heads/{manager.branch}", "main")
        else:
            git(owner.repo, "worktree", "remove", str(manager.path))
            git(owner.repo, "worktree", "add", str(manager.path), manager.branch)
    before = owner._runtime_store.observe()
    resources = snapshot(owner.execution_worktree_root / "work")
    with pytest.raises(DevlegateError, match="stale|generation"):
        admit(owner, payload)
    assert owner._runtime_store.observe() == before
    assert "clean_reset_intent" not in owner._state
    assert snapshot(owner.execution_worktree_root / "work") == resources


@pytest.mark.parametrize("field", ["worker_identity", "automatic_retry",
                                  "force_retry_authorization", "reconciliation",
                                  "operator_recovery", "accepted_integration",
                                  "execution_ref_retirement", "abandonment_retirements",
                                  "workspace_cleanup_pending"])
def test_pending_authority_denied(owner, field):
    payload = confirmation(owner)
    owner._state[field] = {"pending": "fixture"}
    before = snapshot(owner.execution_worktree_root / "work")
    with pytest.raises(DevlegateError, match="stale|incomplete|authority"):
        admit(owner, payload)
    assert "clean_reset_intent" not in owner._state
    assert snapshot(owner.execution_worktree_root / "work") == before


@pytest.mark.parametrize("state", ["review", "todo", "accepted"])
def test_active_ticket_state_denied(owner, state):
    payload = confirmation(owner, inactive=False)
    if state != "review":
        path = owner._workflow_paths()[state]
        destination = owner.control_worktree / path / "T-1.md"
        destination.parent.mkdir(exist_ok=True)
        git(owner.control_worktree, "mv",
            str(owner.control_worktree / owner.review_path / "T-1.md"),
            str(destination))
        git(owner.control_worktree, "commit", "-m", "active state fixture")
        payload["plan_digest"] = inspect_clean(owner, "T-1")["digest"] or "f" * 64
    with pytest.raises(DevlegateError, match="active ticket|incomplete"):
        admit(owner, payload)
    assert "clean_reset_intent" not in owner._state


@pytest.mark.parametrize("authority", ["phase", "owner", "lifecycle"])
def test_active_owner_denied(owner, authority):
    payload = confirmation(owner)
    if authority == "phase":
        owner._state["phase"] = "agent_running"
    elif authority == "owner":
        owner._owned_execution_id = "active-worker"
    else:
        owner._lifecycle_intent = "restart"
    with pytest.raises(DevlegateError):
        admit(owner, payload)
    assert "clean_reset_intent" not in owner._runtime_store.load()


@pytest.mark.parametrize("committed", [False, True])
def test_crash_at_intent_write_is_unknown_and_restart_resolves(
    owner, monkeypatch, committed,
):
    payload = confirmation(owner)
    original = owner._runtime_store._commit
    before = snapshot(owner.execution_worktree_root / "work")

    def crash(connection):
        if committed:
            original(connection)
        raise OSError("lost write acknowledgement")

    monkeypatch.setattr(owner._runtime_store, "_commit", crash)
    cmd = command(payload)
    with pytest.raises(DevlegateError, match="unknown"):
        owner._admit_operator_command(cmd)
    assert "unknown" in str(cmd.admission_error)
    assert owner._state_write_uncertain
    assert "clean_reset_intent" not in owner._state
    with pytest.raises(DevlegateError, match="uncertain"):
        owner.submit_clean_apply(**payload, request_id="reset-one")
    restored = Devlegate(owner.env_file)
    assert bool(restored._state.get("clean_reset_intent")) == committed
    if committed:
        result = restored.submit_clean_apply(**payload, request_id="reset-one")
        assert result["accepted"]
    else:
        assert admit(restored, payload)["accepted"]
    assert snapshot(owner.execution_worktree_root / "work") == before


def test_contended_requests_and_duplicate_receipt(owner):
    payload = confirmation(owner)
    owner.begin_hosted_owner()
    results, errors = [], []

    def client(request_id):
        try:
            results.append(dispatch_request(owner, IPCRequest(
                1, request_id, "clean-apply", payload,
            )))
        except DevlegateError as error:
            errors.append(str(error))

    first = threading.Thread(target=client, args=("reset-one",))
    first.start()
    assert owner._service_wake.wait(2)
    second = threading.Thread(target=client, args=("reset-two",))
    second.start()
    second.join(2)
    assert not second.is_alive()
    assert errors and "busy" in errors[0]
    duplicate = threading.Thread(target=client, args=("reset-one",))
    duplicate.start()
    cmd, scheduler = owner._claim_owner_work(True)
    assert cmd is not None and not scheduler
    owner._admit_operator_command(cmd)
    owner._release_operator_command()
    first.join(2)
    duplicate.join(2)
    owner.end_hosted_owner()
    assert len(results) == 2 and results[0] == results[1]
    assert list(owner._state["mutable_receipts"]) == ["reset-one"]
    with pytest.raises(DevlegateError, match="pre-effect"):
        owner._admit_operator_command(command(payload, "reset-three"))


def test_scheduler_contention_does_not_admit(owner):
    payload = confirmation(owner)
    owner._scheduler_active = True
    with pytest.raises(DevlegateError, match="busy"):
        owner.submit_clean_apply(**payload, request_id="reset-one")
    assert "clean_reset_intent" not in owner._state


def test_runtime_cas_rejects_write_between_observation_and_intent(owner, monkeypatch):
    from devlegate.runtime_store import SQLiteRuntimeStore

    payload = confirmation(owner)
    original = owner._runtime_store.replace
    other = SQLiteRuntimeStore(owner.state_dir, owner._state_key)

    def race(state, **kwargs):
        other.replace({**owner._state, "concurrent_observation": True})
        original(state, **kwargs)

    monkeypatch.setattr(owner._runtime_store, "replace", race)
    with pytest.raises(DevlegateError, match="unknown"):
        admit(owner, payload)
    assert owner._state_write_uncertain
    assert "clean_reset_intent" not in other.load()


def test_crash_before_recording_never_authorizes(owner, monkeypatch):
    payload = confirmation(owner)
    before = owner._runtime_store.observe()

    def crash(_command):
        raise OSError("crash before intent")

    monkeypatch.setattr(owner, "_record_operator_admission", crash)
    with pytest.raises(OSError, match="before intent"):
        admit(owner, payload)
    assert owner._runtime_store.observe() == before
    assert "clean_reset_intent" not in Devlegate(owner.env_file)._state


@pytest.mark.parametrize(
    "damage", ["digest", "generation", "receipt", "intent", "identity"],
)
def test_restoration_rejects_damaged_authorization(owner, damage):
    admit(owner, confirmation(owner))
    state = copy.deepcopy(owner._state)
    if damage == "receipt":
        state["mutable_receipts"] = {}
    elif damage == "intent":
        del state["clean_reset_intent"]
    elif damage == "identity":
        state["clean_reset_intent"]["identity"]["ticket"]["blob"] = "f" * 40
    else:
        field = "plan_digest" if damage == "digest" else damage
        state["clean_reset_intent"][field] = "f" * 64
    with pytest.raises(DevlegateError, match="clean reset"):
        validate_state_invariant(state)


def test_confirmation_ipc_and_cli_contract(owner, monkeypatch, capsys):
    import devlegate.cli as cli

    payload = confirmation(owner)
    with pytest.raises(IPCProtocolError, match="incomplete"):
        dispatch_request(owner, IPCRequest(
            1, "bad", "clean-apply", {"ticket_id": "T-1"},
        ))
    calls = []

    def request(_socket, method, data, **kwargs):
        calls.append((method, data, kwargs))
        return {"accepted": True, "effects_performed": False}

    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(sys, "argv", [
        "devlegate", "clean", "apply", "T-1", "--plan-digest", payload["plan_digest"],
        "--generation", payload["generation"], "--request-id", "reset-one", "--json",
    ])
    assert cli.main() == 0
    assert calls == [(
        "clean-apply", payload, {"mutable": True, "request_id": "reset-one"},
    )]
    assert json.loads(capsys.readouterr().out)["effects_performed"] is False
