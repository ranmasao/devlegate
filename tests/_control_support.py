# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F401, I001

"""Shared control-plane fixture and implementation-seam source.

Semantic ``test_control_*`` modules own the scheduler, workspace, recovery,
and cross-layer selections. This module owns their expensive Git/control
helpers only and is not itself a pytest collection target.
"""

import copy

import hashlib

import json

import os

import shutil

import signal

import sqlite3

import subprocess

import sys

import threading

import time

from pathlib import Path

import pytest

from git_support import clone_world, control_publisher

from runtime_helpers import run_test_iteration

from service_harness import LiveService

import devlegate.execution_workspace as execution_workspace

import devlegate.runtime as runtime

from devlegate.cli import Devlegate, DevlegateError

from devlegate.execution_result import (
    ExecutionReport,
    ExecutionReportStore,
    build_execution_report,
)

from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
    parse_worktree_porcelain,
)

from devlegate.host_installation import HostInstallation

from devlegate.host_installation import write as write_installation

from devlegate.project_registry import ProjectRegistry

from devlegate.runtime import BlockedReason, WorkflowBlockedError, _todo_fingerprint

from devlegate.worker_egress import WorkerClaim, WorkerRunResult

from devlegate.worker_supervisor import (
    WorkerAdmissionClosed,
    WorkerProcessIdentity,
    _capture_worker_identity,
    observe_worker_identity,
)

def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)],
        cwd=cwd,
        text=True,
        capture_output=True,
        check=True,
    )

def state_payload(state_dir):
    database = next(state_dir.glob("*.sqlite3"))
    connection = sqlite3.connect(database)
    payload = connection.execute(
        "SELECT payload FROM runtime_state WHERE id = 1"
    ).fetchone()[0]
    connection.close()
    return json.loads(payload)

def persist_agent_running(
    devlegate,
    state,
    execution_id="interrupted-old",
    *,
    checkpointed=False,
    record_start=False,
    publish=False,
):
    control = next((state / "worktrees").glob("*/control"))
    code_head = git(devlegate.repo, "rev-parse", "HEAD").stdout.strip()
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    todo_fingerprint, _count = _todo_fingerprint(control, devlegate.todo_path)
    manager = ExecutionWorkspaceManager(
        devlegate.repo, devlegate.execution_worktree_root, "T-1"
    )
    workspace = manager.prepare(code_head)
    if checkpointed:
        (workspace.path / "prior-checkpoint.txt").write_text("prior checkpoint\n")
        git(workspace.path, "add", "prior-checkpoint.txt")
        git(workspace.path, "commit", "-m", "prior checkpoint")
        if publish:
            git(
                workspace.path,
                "push",
                "origin",
                f"HEAD:refs/heads/{workspace.branch}",
            )
        workspace = manager.prepare(code_head)
    execution_start_head = workspace.head if record_start else None
    devlegate._save_state(
        "agent_running",
        local_head=code_head,
        remote_head=code_head,
        changed_paths="",
        handled_remote_head=code_head,
        handled_control_head=control_head,
        handled_todo_fingerprint=todo_fingerprint,
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=code_head,
        execution_control_head=control_head,
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        execution_id=execution_id,
        execution_remote_head=workspace.head if publish else None,
        worker_identity={
            "execution_id": execution_id,
            "pid": 1,
            "pgid": 1,
            "sid": 1,
            "boot_id": "test-prior-boot",
            "start_time": 0,
        },
        **({"execution_start_head": execution_start_head} if record_start else {}),
    )
    return workspace, control

def control_fixture(tmp_path):
    state = tmp_path / "state"
    world = clone_world(
        tmp_path,
        baseline="ticket-control",
        state=state,
    )
    config = world["working"] / ".env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={state}\n"
    )
    (world["working"] / ".git" / "info" / "exclude").open("a").write(
        "\n.env\n.registry-config/\n"
    )
    os.environ["XDG_CONFIG_HOME"] = str(world["working"] / ".registry-config")
    write_installation(
        Path(os.environ["XDG_CONFIG_HOME"]) / "devlegate" / "installation.json",
        HostInstallation("internal"),
    )
    ProjectRegistry().register("test", config)
    return world["working"], config, state

def divergent_control_heads(working, config, tmp_path):
    assert invoke(working, "control", "init", config=config).returncode == 0
    state = Path(
        next(
            line.split("=", 1)[1]
            for line in config.read_text().splitlines()
            if line.startswith("STATE_DIR=")
        )
    )
    control = next((state / "worktrees").glob("*/control"))
    base = git(control, "rev-parse", "HEAD").stdout.strip()
    (control / "local-only.txt").write_text("local\n")
    git(control, "add", "local-only.txt")
    git(control, "commit", "-m", "local control rewrite")
    from_head = git(control, "rev-parse", "HEAD").stdout.strip()
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")

    architect = tmp_path / "architect"
    git(working, "worktree", "add", "--detach", architect, base)
    git(architect, "config", "user.email", "test@example.com")
    git(architect, "config", "user.name", "Test User")
    (architect / "remote-only.txt").write_text("remote\n")
    git(architect, "add", "remote-only.txt")
    git(architect, "commit", "-m", "external control rewrite")
    to_head = git(architect, "rev-parse", "HEAD").stdout.strip()
    git(
        architect,
        "push",
        "--force",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    git(working, "worktree", "remove", "--force", architect)
    return control, from_head, to_head

def fresh_control_fixture(tmp_path):
    state = tmp_path / "state"
    world = clone_world(tmp_path, baseline="product", state=state)
    config = world["working"] / ".env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "BACKLOG_PATH=workflow/backlog\nTODO_PATH=workflow/todo\n"
        "REVIEW_PATH=workflow/review\nDONE_PATH=workflow/done\n"
        "ACCEPTED_PATH=workflow/accepted\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\n"
        f"STATE_DIR={state}\n"
    )
    (world["working"] / ".git" / "info" / "exclude").open("a").write(
        "\n.env\n.registry-config/\n"
    )
    return world["working"], config, state

def invoke(working, *args, config):
    args = list(args)
    if "--foreground" in args:
        args[args.index("--foreground")] = "foreground"
    elif "--once" in args:
        args[args.index("--once")] = "once"
    config_home = working / ".registry-config"
    write_installation(
        config_home / "devlegate" / "installation.json",
        HostInstallation("internal"),
    )
    ProjectRegistry(config_home / "devlegate" / "projects.json").register(
        "test", config
    )
    return subprocess.run(
        [sys.executable, "-m", "devlegate", "--env", config, *args],
        cwd=working,
        env={
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(config_home),
        },
        text=True,
        capture_output=True,
    )

def _pending_resume_fixture(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("worker\n")
        (working / "dirty-product.txt").write_text("operator\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None
        )

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    (working / "dirty-product.txt").unlink()
    return devlegate, working, state

def _multi_checkpoint_update_base_fixture(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    devlegate = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "first.txt").write_text("first\n")
        (working / "dirty-product.txt").write_text("product\n")
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(devlegate._workers, "run", worker)
    assert run_test_iteration(devlegate) == 1
    reconciliation = dict(devlegate._state["reconciliation"])
    execution = next((state / "worktrees").glob("*/work/T-1"))
    git(execution, "config", "user.email", "test@example.com")
    git(execution, "config", "user.name", "Test User")
    (execution / "second.txt").write_text("second\n")
    git(execution, "add", "second.txt")
    git(execution, "commit", "-m", "second checkpoint")
    second = git(execution, "rev-parse", "HEAD").stdout.strip()
    first = git(execution, "rev-parse", "HEAD^").stdout.strip()
    git(working, "add", "dirty-product.txt")
    git(working, "commit", "-m", "advance product")
    git(working, "push", "origin", "HEAD:main")
    target = git(working, "rev-parse", "HEAD").stdout.strip()
    git(execution, "update-ref", reconciliation["evidence_ref"], second)
    operation = {
        **reconciliation,
        "worker_checkpoint": second,
        "execution_report": {
            **reconciliation["execution_report"],
            "workspace_head": second,
        },
    }
    devlegate._save_state("idle", reconciliation=operation)
    return devlegate, working, state, execution, operation, first, second, target

def _persist_pending_execution(
    devlegate, state, workspace, base_head, execution_id, *, start_head=None
):
    control = next((state / "worktrees").glob("*/control"))
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()
    devlegate._save_state(
        "agent_pending",
        local_head=base_head,
        remote_head=base_head,
        changed_paths="",
        control_head=control_head,
        selected_ticket_id="T-1",
        selected_ticket_body="work\n",
        execution_ticket_id="T-1",
        execution_base_head=base_head,
        execution_control_head=control_head,
        execution_branch=workspace.branch,
        execution_path=str(workspace.path),
        execution_id=execution_id,
        execution_start_head=start_head or getattr(workspace, "head", base_head),
        execution_remote_head=None,
    )

def _write_prior_execution_receipt(control, *, execution_id, base, path, head):
    report = build_execution_report(
        execution_id=execution_id,
        ticket_id="T-1",
        code_base_head=base,
        control_head=git(control, "rev-parse", "HEAD").stdout.strip(),
        execution_branch="devlegate/work/T-1",
        execution_path=str(path),
        workspace_head=head,
        run=WorkerRunResult(1, None, None, None),
    )
    ExecutionReportStore(control).write(report)

def _operator_recovery_fixture(tmp_path, monkeypatch, *, remote=False):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    old_base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(
        working, state / "worktrees" / next(state.glob("worktrees/*")).name, "T-1"
    )
    workspace = manager.prepare(old_base)
    git(workspace.path, "config", "user.email", "test@example.com")
    git(workspace.path, "config", "user.name", "Test User")
    (workspace.path / "old.txt").write_text("old\n")
    git(workspace.path, "add", "old.txt")
    git(workspace.path, "commit", "-m", "old generation")
    old_head = git(workspace.path, "rev-parse", "HEAD").stdout.strip()
    if remote:
        git(working, "push", "origin", f"{old_head}:refs/heads/{manager.branch}")
        control = next((state / "worktrees").glob("*/control"))
        _write_prior_execution_receipt(
            control,
            execution_id="old-generation",
            base=old_base,
            path=workspace.path,
            head=old_head,
        )
        git(control, "add", "executions/T-1")
        git(control, "commit", "-m", "retain prior execution receipt")
        git(control, "push", "origin", "devlegate/control")
    (working / "new.txt").write_text("new\n")
    git(working, "add", "new.txt")
    git(working, "commit", "-m", "new admitted base")
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    git(working, "push", "origin", "HEAD:main")
    devlegate = Devlegate(config)
    _persist_pending_execution(
        devlegate, state, workspace, base, "current", start_head=old_head
    )
    if remote:
        devlegate._save_state("agent_pending", execution_remote_head=old_head)
    devlegate._save_state(
        "agent_pending",
        operator_recovery={
            "ticket_id": "T-1",
            "execution_id": "current",
            "observed_head": old_head,
        },
    )
    return working, config, state, devlegate, manager, old_head, base

# Export all support names, including private helper functions used by domain tests.
__all__ = [name for name in globals() if not name.startswith("__")]
