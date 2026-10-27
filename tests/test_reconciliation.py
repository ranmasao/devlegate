# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Reconciliation evidence identity proofs."""

import subprocess
import threading
from types import SimpleNamespace

import pytest

from devlegate.reconciliation import has_valid_evidence_identity
from devlegate.runtime import DevlegateError, ServiceEngine, ShutdownInterrupted


def _rewritten_reconciliation():
    return {
        "ticket_id": "LAB-158",
        "execution_id": "c68282b27e6244f0a7260233a3a9f7c0",
        "status": "resolved",
        "resolution": "update-base",
        "resolution_class": "update-base with published-lineage rewrite",
        "evidence_ref": (
            "refs/devlegate/reconciliation/LAB-158/"
            "c68282b27e6244f0a7260233a3a9f7c0-rewritten"
        ),
        "displaced_evidence_ref": (
            "refs/devlegate/reconciliation/LAB-158/"
            "c68282b27e6244f0a7260233a3a9f7c0"
        ),
    }


def test_rewritten_evidence_identity_accepts_resolved_update_base_provenance():
    assert has_valid_evidence_identity(_rewritten_reconciliation())


def test_rewritten_evidence_identity_accepts_legacy_resolved_provenance():
    reconciliation = _rewritten_reconciliation()
    reconciliation.pop("resolution_class")

    assert has_valid_evidence_identity(reconciliation)


@pytest.mark.parametrize(
    "field,value",
    [
        ("evidence_ref", "refs/devlegate/reconciliation/LAB-158/other-rewritten"),
        ("displaced_evidence_ref", "refs/devlegate/reconciliation/LAB-157/exec"),
        ("displaced_evidence_ref", None),
        ("status", "pending"),
        ("resolution", "resume"),
        ("resolution_class", "unrecognized rewrite"),
    ],
)
def test_rewritten_evidence_identity_rejects_incoherent_provenance(field, value):
    reconciliation = _rewritten_reconciliation()
    reconciliation[field] = value

    assert not has_valid_evidence_identity(reconciliation)


def test_canonical_evidence_identity_remains_valid():
    assert has_valid_evidence_identity(
        {
            "ticket_id": "T-1",
            "execution_id": "done-execution",
            "evidence_ref": "refs/devlegate/reconciliation/T-1/done-execution",
        }
    )


@pytest.mark.parametrize("shutdown", [False, True])
def test_product_evidence_rejects_unowned_or_stopped_git(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    report = SimpleNamespace(
        ticket_id="T-1", execution_id="exec", code_base_head="base",
        workspace_head="checkpoint",
    )
    product = {"remote_head": "remote", "local_head": "local"}

    def unexpected_git(*args, **kwargs):
        pytest.fail("Git must not be invoked")

    monkeypatch.setattr("devlegate.runtime._git", unexpected_git)
    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._persist_product_reconciliation(report, product, "checkpoint")


@pytest.mark.parametrize("shutdown", [False, True])
def test_checkpoint_push_rejects_unowned_or_stopped_git(
    tmp_path, monkeypatch, shutdown
):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None if shutdown else -1
    engine._stop_event = threading.Event()
    if shutdown:
        engine._stop_event.set()
    workspace = SimpleNamespace(path=tmp_path, branch="work")
    monkeypatch.setattr(engine, "_resolve_commit_runtime", lambda *_: "checkpoint")
    monkeypatch.setattr(engine, "_execution_remote_head", lambda *_: None)
    monkeypatch.setattr(
        "devlegate.runtime._git", lambda *args, **kwargs: pytest.fail("Git spawned")
    )

    with pytest.raises(ShutdownInterrupted if shutdown else DevlegateError):
        engine._publish_execution_branch(workspace, "checkpoint", None)


def test_checkpoint_ambiguous_push_reobserves_remote(tmp_path, monkeypatch):
    engine = ServiceEngine.__new__(ServiceEngine)
    engine.repo = tmp_path
    engine.remote_name = "origin"
    engine._host_owner_thread_id = None
    engine._stop_event = None
    workspace = SimpleNamespace(path=tmp_path, branch="work")
    monkeypatch.setattr(engine, "_resolve_commit_runtime", lambda *_: "checkpoint")
    observations = iter([None, "checkpoint"])
    monkeypatch.setattr(engine, "_execution_remote_head", lambda *_: next(observations))
    calls = []

    def failed_push(repo, *args, check=False):
        calls.append(args)
        return subprocess.CompletedProcess(args, 1, "", "connection lost")

    monkeypatch.setattr("devlegate.runtime._git", failed_push)
    engine._publish_execution_branch(workspace, "checkpoint", None)
    assert len(calls) == 1
    assert calls[0][0] == "push"
