# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Reviewed origin and interrupted worker generations are distinct lineages."""

import copy
import os
import signal
import subprocess
import sys
from pathlib import Path

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.execution_result import ExecutionReportStore
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime import DevlegateError
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from devlegate.worker_supervisor import _capture_worker_identity


class ProcessLoss(BaseException):
    pass


def completed():
    return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)


@pytest.fixture
def interrupted_rework(tmp_path, monkeypatch):
    repo, config, state = control_fixture(tmp_path)
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)

    def first(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("reviewed\n")
        return completed()

    monkeypatch.setattr(owner._workers, "run", first)
    assert run_test_iteration(owner) == 0
    control = next((state / "worktrees").glob("*/control"))
    report = ExecutionReportStore(control).list("T-1")[0]
    artifact = control / "reviews" / "T-1" / f"{report.execution_id}.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text(
        "<!-- devlegate-review-v1\n"
        "ticket: T-1\n"
        f"execution: {report.execution_id}\n"
        f"checkpoint: {report.workspace_head}\n"
        f"control: {report.control_head}\n"
        "disposition: rework\n-->\nKeep all interrupted progress.\n"
    )
    git(control, "mv", "kanban/review/T-1.md", "kanban/todo/T-1.md")
    git(control, "add", str(artifact.relative_to(control)))
    git(control, "commit", "-m", "request rework")
    git(control, "push", "origin", "devlegate/control")

    def lost_worker(workspace, prompt, *, execution_id, identity_handler, **_kwargs):
        assert "Keep all interrupted progress." in prompt
        helper = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(60)"],
            start_new_session=True,
        )
        try:
            identity_handler(_capture_worker_identity(helper, execution_id))
            (workspace.path / "implementation.txt").write_text("dirty tracked WIP\n")
            (workspace.path / "untracked.txt").write_text("dirty untracked WIP\n")
        finally:
            os.killpg(helper.pid, signal.SIGKILL)
            helper.wait(timeout=10)
        raise ProcessLoss()

    monkeypatch.setattr(owner._workers, "run", lost_worker)
    with pytest.raises(ProcessLoss):
        run_test_iteration(owner)
    prior = owner._execution_generation_binding()
    assert owner._state["phase"] == "agent_running"
    assert owner._state["rework_provenance"]["preparation_stage"] == (
        "remote_predecessor_installed"
    )
    assert not list(prior.directory.glob("worker-retired.*.json"))
    return owner, config, prior, copy.deepcopy(owner._state["rework_provenance"])


@pytest.mark.parametrize("boundary", ["before-intent", "intent", "receipt"])
def test_rework_process_loss_replays_same_admitted_handoff(
    interrupted_rework, monkeypatch, boundary,
):
    owner, config, prior, review = interrupted_rework
    old_head = git(prior.manager.path, "rev-parse", "HEAD").stdout.strip()
    original_record = ResourceBinding.record_intent
    original_handoff = ResourceBinding.prepare_handoff

    def record(binding):
        if boundary != "before-intent":
            original_record(binding)
        if boundary in {"before-intent", "intent"}:
            raise ProcessLoss()

    def handoff(binding, predecessor, **kwargs):
        original_handoff(binding, predecessor, **kwargs)
        raise ProcessLoss()

    monkeypatch.setattr(ResourceBinding, "record_intent", record)
    if boundary == "receipt":
        monkeypatch.setattr(ResourceBinding, "prepare_handoff", handoff)
    resumed = Devlegate(config)
    with pytest.raises(ProcessLoss):
        run_test_iteration(resumed)
    admitted = copy.deepcopy(resumed._state["execution_resource_binding"])
    assert admitted["execution_id"] != prior.intent["execution_id"]
    assert admitted["generation"] != prior.intent["generation"]
    assert admitted["predecessor_execution"] == prior.intent["execution_id"]
    assert admitted["predecessor_generation"] == prior.intent["generation"]
    assert resumed._state["phase"] == "agent_pending"
    assert resumed._state["rework_provenance"] == review
    assert git(prior.manager.path, "rev-parse", "HEAD").stdout.strip() == old_head
    assert not list(prior.directory.glob("worker-retired.*.json"))
    monkeypatch.setattr(ResourceBinding, "record_intent", original_record)
    monkeypatch.setattr(ResourceBinding, "prepare_handoff", original_handoff)

    # Repeated pre-launch restart must neither admit nor publish another generation.
    for _ in range(2):
        pending = Devlegate(config)
        with monkeypatch.context() as patch:
            patch.setattr(pending, "_stop_before_admission", lambda: True)
            assert run_test_iteration(pending) == 0
        assert pending._state["execution_resource_binding"] == admitted

    final = Devlegate(config)
    launches = []

    def worker(workspace, prompt, **_kwargs):
        launches.append(final._state["execution_id"])
        assert "Keep all interrupted progress." in prompt
        assert workspace.path == prior.manager.path
        assert (workspace.path / "implementation.txt").read_text() == (
            "dirty tracked WIP\n"
        )
        assert (workspace.path / "untracked.txt").read_text() == "dirty untracked WIP\n"
        assert git(workspace.path, "rev-parse", "HEAD").stdout.strip() == old_head
        return completed()

    monkeypatch.setattr(final._workers, "run", worker)
    monkeypatch.setattr(
        final, "_prepare_rework_remote_predecessor",
        lambda *_a: pytest.fail("duplicate reviewed predecessor publication"),
    )
    monkeypatch.setattr(
        final, "_prepare_rework_remote_evidence",
        lambda *_a: pytest.fail("duplicate review evidence publication"),
    )
    assert run_test_iteration(final) == 0
    assert launches == [admitted["execution_id"]]
    assert final._state["execution_resource_binding"] == admitted
    assert final._state["rework_provenance"] == review
    head = git(prior.manager.path, "rev-parse", "HEAD").stdout.strip()
    assert head != old_head
    assert git(prior.manager.path, "merge-base", old_head, head).stdout.strip() == (
        old_head
    )
    remote = git(owner.repo, "ls-remote", "origin", admitted["remote_ref"])
    assert remote.stdout.split()[0] == head
    # Completed lifecycle replay cannot launch a duplicate worker.
    again = Devlegate(config)
    monkeypatch.setattr(
        again._workers, "run", lambda *_a, **_k: pytest.fail("duplicate")
    )
    assert run_test_iteration(again) == 0
    assert Path(admitted["path"]).joinpath("untracked.txt").read_text() == (
        "dirty untracked WIP\n"
    )


@pytest.mark.parametrize("observation", ["matching-live", "indeterminate"])
def test_pending_rework_retry_reobserves_predecessor_worker(
    interrupted_rework, monkeypatch, observation,
):
    _owner, config, prior, _review = interrupted_rework
    resumed = Devlegate(config)
    record = ResourceBinding.record_intent

    def crash(binding):
        record(binding)
        raise ProcessLoss()

    with monkeypatch.context() as patch:
        patch.setattr(ResourceBinding, "record_intent", crash)
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
    admitted = resumed._state["execution_resource_binding"]
    pending = Devlegate(config)
    assert pending._state["worker_identity"] is None
    monkeypatch.setattr(pending._workers, "observe", lambda _identity: observation)
    monkeypatch.setattr(
        pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
    )
    with pytest.raises(
        (DevlegateError, ExecutionWorkspaceError), match="absence is unproven"
    ):
        run_test_iteration(pending)
    assert pending._state["execution_resource_binding"] == admitted
    assert (prior.manager.path / "untracked.txt").read_text() == "dirty untracked WIP\n"
    assert not list(prior.directory.glob("worker-retired.*.json"))


def test_repeated_rework_process_loss_uses_immediate_resource_predecessor(
    interrupted_rework, monkeypatch,
):
    owner, config, prior, review = interrupted_rework
    generation = prior.intent
    for _ in range(2):
        resumed = Devlegate(config)
        monkeypatch.setattr(resumed._workers, "run", owner._workers.run)
        monkeypatch.setattr(
            resumed, "_prepare_rework_remote_predecessor",
            lambda *_a: pytest.fail("duplicate predecessor publication"),
        )
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
        current = resumed._state["execution_resource_binding"]
        assert current["predecessor_execution"] == generation["execution_id"]
        assert current["predecessor_generation"] == generation["generation"]
        assert resumed._state["rework_provenance"] == review
        assert current["predecessor_execution"] != review["previous_execution_id"]
        generation = current


def test_pending_rework_handoff_rejects_altered_control_lineage(
    interrupted_rework, monkeypatch,
):
    owner, config, prior, _review = interrupted_rework
    resumed = Devlegate(config)
    with monkeypatch.context() as patch:
        patch.setattr(
            resumed, "_stop_before_admission",
            lambda: resumed._state.get("phase") == "agent_pending",
        )
        assert run_test_iteration(resumed) == 0
    admitted = resumed._state["execution_resource_binding"]
    control = owner.control_worktree
    ticket = control / "kanban/todo/T-1.md"
    ticket.write_text(ticket.read_text() + "\nChanged assignment.\n")
    git(control, "add", "kanban/todo/T-1.md")
    git(control, "commit", "-m", "alter pending ticket")
    git(control, "push", "origin", "devlegate/control")
    pending = Devlegate(config)
    monkeypatch.setattr(
        pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
    )
    with pytest.raises(DevlegateError):
        run_test_iteration(pending)
    assert pending._state["execution_resource_binding"] == admitted
    assert (prior.manager.path / "untracked.txt").read_text() == "dirty untracked WIP\n"


@pytest.mark.parametrize(
    "boundary", ["handoff-intent.json", "devlegate-generation.json"]
)
def test_uncertain_rework_handoff_never_admits_another_generation(
    interrupted_rework, monkeypatch, boundary,
):
    import devlegate.resource_binding as module

    _owner, config, prior, review = interrupted_rework
    write = module._write_once

    def crash(path, value):
        write(path, value)
        if path.name == boundary:
            raise ProcessLoss()

    resumed = Devlegate(config)
    with monkeypatch.context() as patch:
        patch.setattr(module, "_write_once", crash)
        with pytest.raises(ProcessLoss):
            run_test_iteration(resumed)
    admitted = resumed._state["execution_resource_binding"]
    bindings = set(prior.directory.parent.iterdir())
    for _ in range(2):
        pending = Devlegate(config)
        monkeypatch.setattr(
            pending._workers, "run", lambda *_a, **_k: pytest.fail("launch")
        )
        with pytest.raises(
            (DevlegateError, ExecutionWorkspaceError), match="operator recovery"
        ):
            run_test_iteration(pending)
        assert pending._state["execution_resource_binding"] == admitted
        assert pending._state["rework_provenance"] == review
        assert set(prior.directory.parent.iterdir()) == bindings
        assert (prior.manager.path / "implementation.txt").read_text() == (
            "dirty tracked WIP\n"
        )
        assert (prior.manager.path / "untracked.txt").read_text() == (
            "dirty untracked WIP\n"
        )
