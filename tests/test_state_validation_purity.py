# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Baseline observations for the runtime state validation boundary."""

import builtins
import copy
import sqlite3
import subprocess

import pytest

from devlegate.runtime import DevlegateError, ServiceEngine


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"phase": "idle"}, None),
        ({"phase": "idle", "worker_identity": None}, None),
        (
            {
                "phase": "idle",
                "workspace_cleanup": {"status": "clean", "decisions": []},
            },
            None,
        ),
        ({"phase": "unknown"}, "invalid state phase: unknown"),
        (
            {"phase": "idle", "workspace_cleanup": {"status": "bad"}},
            "invalid workspace cleanup status",
        ),
        (
            {"phase": "idle", "automatic_retry": {"status": "pending"}},
            "automatic retry identity is incomplete",
        ),
        ({"phase": "idle", "worker_identity": {}}, "invalid worker identity"),
        (
            {"phase": "merge_pending"},
            "invalid merge_pending state: synchronization coordinates are incomplete",
        ),
        (
            {
                "phase": "agent_pending",
                "local_head": "a",
                "remote_head": "b",
                "changed_paths": "",
            },
            "invalid agent_pending state: selected ticket binding is incomplete",
        ),
        (
            {
                "phase": "idle",
                "reconciliation": {
                    "status": "pending",
                    "ticket_id": "T",
                    "execution_id": "E",
                    "original_base": "a",
                    "observed_product": "b",
                    "worker_checkpoint": "c",
                    "product_remote_head": "d",
                    "control_head": "e",
                    "execution_branch": "f",
                    "execution_path": "g",
                    "evidence_ref": "h",
                    "execution_report": {},
                },
            },
            "invalid retained reconciliation report",
        ),
        (
            {
                "phase": "agent_pending",
                "local_head": "a",
                "remote_head": "b",
                "changed_paths": "",
                "selected_ticket_id": "T",
                "selected_ticket_body": "",
                "control_head": "c",
                "execution_ticket_id": "T",
                "execution_base_head": "a",
                "execution_control_head": "c",
                "execution_branch": "b",
                "execution_path": "p",
                "execution_id": "E",
                "execution_remote_head": None,
                "rework_provenance": {
                    "previous_execution_id": "old",
                    "previous_report_checkpoint": "checkpoint",
                    "previous_control_head": "control",
                    "review": None,
                },
            },
            "invalid state: persisted curator review binding is malformed",
        ),
    ],
)
def test_validation_baseline_no_mutation_or_common_io(state, message, monkeypatch):
    before = copy.deepcopy(state)

    def forbidden(*args, **kwargs):
        raise AssertionError("validation attempted I/O")

    monkeypatch.setattr(builtins, "open", forbidden)
    monkeypatch.setattr(subprocess, "Popen", forbidden)
    monkeypatch.setattr(sqlite3, "connect", forbidden)
    if message is None:
        assert ServiceEngine._validate_state_invariant(state) is None
    else:
        with pytest.raises(DevlegateError) as error:
            ServiceEngine._validate_state_invariant(state)
        assert type(error.value) is DevlegateError
        assert str(error.value) == message
    assert state == before
