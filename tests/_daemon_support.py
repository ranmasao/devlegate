# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F401, I001

"""Shared daemon fixtures and host/owner implementation-seam source.

Semantic ``test_daemon_*`` modules own host readiness, owner-loop policy,
lifecycle drain, and worker-loss recovery. Production service companions stay
in the CLI topology collector.
"""

import inspect

import json

import os

import signal

import subprocess

import sys

import threading

import time

from pathlib import Path

import pytest

from git_support import control_publisher

from runtime_helpers import run_test_iteration

from _control_support import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli

import devlegate.daemon as daemon

import devlegate.runtime as runtime

from devlegate.cli import DevlegateError

from devlegate.execution_result import build_execution_report

from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)

from devlegate.runtime import (
    IterationIntent,
    OperatorCommand,
    ServiceEngine,
    ShutdownInterrupted,
    WorkflowBlockedError,
)

from devlegate.service_diagnostics import read as read_service_failure

from devlegate.worker_egress import WorkerClaim, WorkerRunResult

def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), config, state

def _prepare_drop_owner_command(tmp_path, monkeypatch):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    workspace, control = persist_agent_running(
        engine,
        state,
        execution_id="drop-final-boundary",
        checkpointed=True,
        record_start=True,
        publish=True,
    )
    git(
        workspace.path,
        "commit",
        "--amend",
        "-m",
        "Devlegate checkpoint T-1 drop-final-boundary",
    )
    checkpoint = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    git(
        workspace.path,
        "push",
        "--force",
        "origin",
        f"HEAD:refs/heads/{workspace.branch}",
    )
    workspace = ExecutionWorkspaceManager(
        engine.repo, engine.execution_worktree_root, "T-1"
    ).prepare(workspace.base_head)
    report = build_execution_report(
        execution_id="drop-final-boundary",
        ticket_id="T-1",
        code_base_head=workspace.base_head,
        control_head=engine._state["execution_control_head"],
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        workspace_head=workspace.head,
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "done", (), ()), None
        ),
    )
    engine._save_state(
        "agent_running",
        execution_stage="lifecycle",
        worker_identity=None,
        execution_start_head=workspace.base_head,
        execution_remote_head=checkpoint,
        pending_execution_report=report.as_dict(),
    )
    command = OperatorCommand(
        request_id="drop-final-boundary-request",
        method="drop",
        fingerprint=runtime._drop_request_fingerprint(
            "T-1", "drop-final-boundary"
        ),
        ticket_id="T-1",
        execution_id="drop-final-boundary",
    )
    return engine, config, control, workspace, command

# Export all support names, including private helper functions used by domain tests.
__all__ = [name for name in globals() if not name.startswith("__")]
