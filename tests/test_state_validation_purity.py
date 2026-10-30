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
            {"phase": "idle", "workspace_cleanup": {"status": "clean", "decisions": []}},
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
