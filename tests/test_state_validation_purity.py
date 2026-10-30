# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Baseline observations for the runtime state validation boundary."""

import builtins
import copy
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from state_validation_baseline import baseline_validate_state_invariant

from devlegate.runtime import DevlegateError, ServiceEngine
from devlegate.runtime_state_validation import validate_state_invariant


@pytest.mark.parametrize(
    ("state", "message"),
    [
        ({"phase": "idle"}, None),
        ({"phase": "idle", "worker_identity": None}, None),
        (
            {
                "phase": "idle",
                "accepted_integration": {
                    "ticket_id": "T",
                    "checkpoint": "a" * 40,
                    "control_head": "b" * 40,
                },
            },
            None,
        ),
        (
            {
                "phase": "idle",
                "workspace_cleanup": {"status": "clean", "decisions": []},
            },
            None,
        ),
        ({"phase": "unknown"}, "invalid state phase: unknown"),
        (
            {
                "phase": "merge_pending",
                "local_head": "a",
                "remote_head": "b",
                "changed_paths": "",
                "control_head": "c",
            },
            None,
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
            },
            None,
        ),
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
    results = []
    for validator in (
        baseline_validate_state_invariant,
        validate_state_invariant,
        ServiceEngine._validate_state_invariant,
    ):
        try:
            result = validator(state)
        except Exception as error:
            results.append((type(error), str(error)))
        else:
            results.append((None, result))
        assert state == before
    assert results == [results[0]] * 3
    assert results[0] == ((DevlegateError, message) if message else (None, None))


@pytest.mark.parametrize(
    "first", ["devlegate.runtime", "devlegate.runtime_state_validation"]
)
def test_import_orders(first):
    code = (
        "import importlib, sys; "
        f"sys.path.insert(0, {str(Path(__file__).resolve().parents[1] / 'src')!r}); "
        f"importlib.import_module({first!r}); "
        "r=importlib.import_module('devlegate.runtime'); "
        "v=importlib.import_module('devlegate.runtime_state_validation'); "
        "assert r.DevlegateError is v.DevlegateError; "
        "assert r.ServiceEngine._validate_state_invariant is v.validate_state_invariant"
    )
    subprocess.run([sys.executable, "-c", code], check=True)


def test_differential_builtin_error():
    state = {"phase": []}
    for validator in (baseline_validate_state_invariant, validate_state_invariant):
        with pytest.raises(TypeError, match="unhashable type: 'list'"):
            validator(state)
    assert state == {"phase": []}


def test_save_load_restart(git_fixture, monkeypatch):
    from devlegate.cli import Devlegate

    monkeypatch.chdir(git_fixture["working"])
    engine = Devlegate(git_fixture["config"])
    engine._save_state(
        "merge_pending",
        local_head="a",
        remote_head="b",
        changed_paths="",
        control_head="c",
    )
    saved = copy.deepcopy(engine._state)
    restarted = Devlegate(git_fixture["config"])
    assert restarted._state == saved
    assert json.loads(json.dumps(saved)) == saved
