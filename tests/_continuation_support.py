# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable authentic historical sources, without historical SQLite ownership."""

import subprocess
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory

from _control_support import control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.cli import Devlegate
from devlegate.execution_result import (
    ExecutionReport,
    ExecutionReportStore,
    WorkerRunResult,
    build_execution_report,
)
from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.ipc_client import request
from devlegate.ipc_server import UnixIPCServer
from devlegate.worker_egress import WorkerClaim


@dataclass
class DeferredSource:
    working: Path
    control: Path
    config: Path
    state: Path
    engine: Devlegate
    reviewed: ExecutionReport
    failed: ExecutionReport
    workspace: Path
    immutable: dict[Path, bytes]

    def assert_retained(self):
        for path, content in self.immutable.items():
            assert path.read_bytes() == content
        assert git(self.workspace, "rev-parse", "HEAD").stdout.strip() == (
            self.failed.workspace_head
        )
        assert git(self.working, "rev-parse", "devlegate/work/T-1").stdout.strip() == (
            self.failed.workspace_head
        )
        assert git(
            self.working, "ls-remote", "origin", "refs/heads/devlegate/work/T-1"
        ).stdout.split()[0] == self.failed.workspace_head
        assert (self.workspace / "unowned-dirty.txt").read_text() == "unowned\n"


def ipc_plan(source, action, reason=None):
    engine = source.engine
    engine.ensure_hosted_views()
    before = engine._state_file.read_bytes()
    refs = git(source.working, "show-ref").stdout
    remote = git(source.working, "ls-remote", "origin").stdout
    with TemporaryDirectory(prefix="dg-prefix-ipc-") as directory:
        socket = Path(directory) / "service.sock"
        server = UnixIPCServer(engine, socket)
        server.start()
        try:
            for _ in range(2):
                status = request(socket, "status")
                plan = request(socket, "plan")
                assert status["plan"]["action"] == plan["action"] == action
                if reason:
                    assert reason in plan["reason"]
                    entry = next(
                        e for e in status["tickets"]["blocked"] if e["id"] == "T-1"
                    )
                    assert reason in entry["reason"]["detail"]
        finally:
            server.stop()
    assert engine._state_file.read_bytes() == before
    assert git(source.working, "show-ref").stdout == refs
    assert git(source.working, "ls-remote", "origin").stdout == remote


def publish_report(control, report):
    path = ExecutionReportStore(control).write(report)
    if report.result.conclusion != "failed":
        git(control, "mv", "kanban/todo/T-1.md", "kanban/review/T-1.md")
    git(control, "add", str(path.relative_to(control)))
    git(control, "commit", "-m", "publish immutable lifecycle report")
    return path


def seed_deferred_source(tmp_path, monkeypatch, *, advance=True, mismatch=False):
    """Build the 17/18 DAG before production observes it; no source-owner record.

    Historical Git objects, reports and genuine curator disposition are created
    directly as immutable fixture inputs. All target work uses production paths.
    """
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    engine = Devlegate(config)
    control = engine.control_worktree
    base = git(working, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(working, engine.execution_worktree_root, "T-1")
    workspace = manager.prepare(base).path
    reviewed_chain = []
    for ordinal in range(1, 18):
        (workspace / "ordinal.txt").write_text(f"{ordinal}\n")
        git(workspace, "add", ".")
        git(workspace, "commit", "-m", f"reviewed {ordinal}")
        reviewed_chain.append(git(workspace, "rev-parse", "HEAD").stdout.strip())
    reviewed = build_execution_report(
        execution_id="1" * 32, ticket_id="T-1", code_base_head=base,
        control_head=git(control, "rev-parse", "HEAD").stdout.strip(),
        execution_branch=manager.branch, execution_path=str(workspace),
        workspace_head=reviewed_chain[-1],
        run=WorkerRunResult(
            0, None, WorkerClaim("completed", "reviewed", (), ()), None
        ),
    )
    reviewed_path = publish_report(control, reviewed)
    review = control / "reviews/T-1" / f"{reviewed.execution_id}.md"
    review.parent.mkdir(parents=True)
    review.write_text(
        "<!-- devlegate-review-v1\n"
        "ticket: T-1\n"
        f"execution: {reviewed.execution_id}\n"
        f"checkpoint: {reviewed.workspace_head}\n"
        f"control: {reviewed.control_head}\n"
        "disposition: rework\n-->\nGenuine curator: preserve every reviewed step.\n"
    )
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "add", str(review.relative_to(control)))
    git(control, "commit", "-m", "atomic genuine curator rework")
    parent, failed_chain = base, []
    for ordinal, original in enumerate(reviewed_chain, 1):
        tree = git(workspace, "rev-parse", f"{original}^{{tree}}").stdout.strip()
        if mismatch and ordinal == 8:
            tree = git(workspace, "rev-parse", f"{base}^{{tree}}").stdout.strip()
        replay = git(
            workspace, "commit-tree", tree, "-p", parent, "-m", f"replayed {ordinal}"
        ).stdout.strip()
        assert replay != original
        if not mismatch or ordinal != 8:
            assert git(workspace, "rev-parse", f"{replay}^{{tree}}"
                       ).stdout.strip() == tree
        failed_chain.append(replay)
        parent = replay
    git(workspace, "reset", "--hard", parent)
    (workspace / "suffix.txt").write_text("genuine failed-worker continuation\n")
    git(workspace, "add", ".")
    git(workspace, "commit", "-m", "failed worker step 18")
    failed_chain.append(git(workspace, "rev-parse", "HEAD").stdout.strip())
    assert git(workspace, "merge-base", reviewed_chain[-1], failed_chain[-1]
               ).stdout.strip() == base
    assert subprocess.run(
        ["git", "merge-base", "--is-ancestor", reviewed_chain[-1], failed_chain[-1]],
        cwd=workspace, check=False, capture_output=True,
    ).returncode == 1
    for chain, length in ((reviewed_chain, 17), (failed_chain, 18)):
        assert git(workspace, "rev-list", "--reverse", f"{base}..{chain[-1]}"
                   ).stdout.splitlines() == chain
        assert len(chain) == length
        parent = base
        for commit in chain:
            assert git(workspace, "show", "-s", "--format=%P", commit
                       ).stdout.strip() == parent
            parent = commit
    # The matching tip before the suffix is insufficient when step 8 differs.
    assert git(workspace, "rev-parse", f"{reviewed_chain[-1]}^{{tree}}"
               ).stdout == git(workspace, "rev-parse", f"{failed_chain[-2]}^{{tree}}"
                              ).stdout
    failed = build_execution_report(
        execution_id="2" * 32, ticket_id="T-1", code_base_head=base,
        control_head=git(control, "rev-parse", "HEAD").stdout.strip(),
        execution_branch=manager.branch, execution_path=str(workspace),
        workspace_head=failed_chain[-1],
        run=WorkerRunResult(1, "stdout event exceeds maximum size", None, "transport"),
    )
    failed_path = publish_report(control, failed)
    git(workspace, "push", "origin", f"HEAD:refs/heads/{manager.branch}")
    canonical = (control / "kanban/todo/T-1.md").read_text()
    git(control, "mv", "kanban/todo/T-1.md", "kanban/backlog/T-1.md")
    (control / "kanban/todo/T-2.md").write_text(canonical.replace("T-1", "T-2"))
    git(control, "add", ".")
    git(control, "commit", "-m", "defer source, admit unrelated owner")
    git(control, "push", "origin", "devlegate/control")
    monkeypatch.setattr(engine._workers, "run", lambda *_a, **_k: WorkerRunResult(
        0, None, WorkerClaim("completed", "unrelated owner", (), ()), None
    ))
    assert run_test_iteration(engine) == 0
    git(control, "mv", "kanban/review/T-2.md", "kanban/accepted/T-2.md")
    git(control, "commit", "-m", "accept unrelated owner")
    git(control, "push", "origin", "devlegate/control")
    assert run_test_iteration(engine) == 0
    assert (control / "kanban/done/T-2.md").exists()
    assert engine._state["execution_ticket_id"] == "T-2"
    assert "T-1" not in engine._state["failed_executions"]
    git(control, "mv", "kanban/backlog/T-1.md", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "requeue source")
    for cycle in range(3):
        for before, after in (("todo", "backlog"), ("backlog", "todo")):
            git(control, "mv", f"kanban/{before}/T-1.md", f"kanban/{after}/T-1.md")
            other = f"T-{10 + cycle}-{before}"
            path = control / f"kanban/backlog/{other}.md"
            path.write_text(canonical.replace("T-1", other))
            git(control, "add", str(path.relative_to(control)))
            git(control, "commit", "-m",
                f"legal {before} to {after} with independent ticket")
    git(control, "push", "origin", "devlegate/control")
    if advance:
        (working / "product.txt").write_text("advanced product\n")
        git(working, "add", ".")
        git(working, "commit", "-m", "advance product")
        git(working, "push", "origin", "HEAD")
    (workspace / "unowned-dirty.txt").write_text("unowned\n")
    return DeferredSource(
        working, control, config, state, engine, reviewed, failed, workspace,
        {p: p.read_bytes() for p in (reviewed_path, failed_path, review)},
    )
