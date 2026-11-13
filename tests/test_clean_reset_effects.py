# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Destructive reset tests operate exclusively on disposable real repositories."""

import copy
import subprocess
from pathlib import Path

import pytest
from _control_support import Devlegate, git
from test_clean_preview import owner as owner  # noqa: F401
from test_clean_preview import snapshot
from test_clean_reset import admit, confirmation

from devlegate import clean_reset
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime_state_validation import DevlegateError, validate_state_invariant


def authorize(owner):
    payload = confirmation(owner)
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    ack = admit(owner, payload)
    return payload, binding, ack


def effect(args):
    if args[:2] == ("worktree", "remove"):
        return "worktree"
    if args[:3] == ("update-ref", "--no-deref", "-d"):
        return "local"
    if "push" in args:
        return "remote"
    return None


def assert_retired(owner, binding):
    assert owner._state["clean_reset_intent"]["status"] == "completed"
    assert [e["operation"] for e in owner._state["clean_reset_intent"]["effects"]] == [
        "worktree", "local", "remote",
    ]
    assert not binding.manager.path.exists()
    assert binding.manager.path.resolve() not in binding.manager._registrations()
    assert clean_reset.observe_git(
        owner.repo, "show-ref", "--verify", "--quiet", str(binding.intent["ref"]),
        check=False,
    ).returncode == 1
    assert not git(
        owner.repo, "ls-remote", "origin", binding.intent["remote_ref"],
    ).stdout


def test_exact_success_preserves_reports_pins_and_other_refs(owner):
    payload, binding, ack = authorize(owner)
    head = git(binding.manager.path, "rev-parse", "HEAD").stdout.strip()
    git(owner.repo, "update-ref", "refs/devlegate/evidence/keep", head)
    git(owner.repo, "branch", "unrelated", head)
    git(owner.repo, "push", "origin", "unrelated")
    reports = snapshot(owner.control_worktree / "executions")
    evidence = snapshot(binding.directory)
    assert owner.run_iteration() == 0
    assert_retired(owner, binding)
    assert snapshot(owner.control_worktree / "executions") == reports
    assert snapshot(binding.directory) == evidence
    assert git(
        owner.repo, "rev-parse", "refs/devlegate/evidence/keep",
    ).stdout.strip() == head
    assert git(owner.repo, "rev-parse", "unrelated").stdout.strip() == head
    assert head in git(owner.repo, "ls-remote", "origin", "refs/heads/unrelated").stdout
    restored = Devlegate(owner.env_file)
    before = restored._runtime_store.observe()
    assert restored.submit_clean_apply(**payload, request_id="reset-one") == ack
    assert restored.run_iteration() == 0
    assert restored._runtime_store.observe() == before


@pytest.mark.parametrize("change", [
    "local", "remote", "dirty", "untracked", "ignored", "locked", "recreated",
    "ticket", "owner", "marker", "symlink", "hidden-index", "other-registration",
])
def test_changed_authorized_identity_refuses_all_effects(owner, change):
    _, binding, _ = authorize(owner)
    path = binding.manager.path
    ref = binding.intent["ref"]
    base = binding.intent["base"]
    if change == "local":
        git(owner.repo, "update-ref", ref, base)
    elif change == "remote":
        git(owner.repo, "push", "--force", "origin", f"{base}:{ref}")
    elif change == "dirty":
        (path / "implementation.txt").write_text("preserve dirty bytes\n")
    elif change in {"untracked", "ignored"}:
        (path / "untracked.data").write_text("preserve untracked bytes\n")
        if change == "ignored":
            common = binding.intent["common"]
            with (Path(str(common)) / "info" / "exclude").open("a") as stream:
                stream.write("\nuntracked.data\n")
    elif change == "locked":
        git(owner.repo, "worktree", "lock", "--reason",
            "operator pinned evidence", str(path))
    elif change == "recreated":
        git(owner.repo, "worktree", "remove", str(path))
        git(owner.repo, "worktree", "add", str(path), binding.manager.branch)
    elif change == "ticket":
        (owner.control_worktree / owner.backlog_path / "T-1.md").write_text("changed\n")
    elif change == "owner":
        owner._state["foreign_owner"] = True
    elif change == "marker":
        (binding._admin() / "devlegate-generation.json").write_text("{}\n")
    elif change == "symlink":
        moved = path.with_name("preserved")
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=True)
    elif change == "hidden-index":
        git(path, "update-index", "--assume-unchanged", "implementation.txt")
        (path / "implementation.txt").write_text("hidden dirty bytes\n")
    else:
        git(owner.repo, "worktree", "add", "--force", str(path.with_name("other")),
            binding.manager.branch)
    resources = snapshot(owner.execution_worktree_root / "work")
    refs = git(owner.repo, "show-ref").stdout
    remote = git(owner.repo, "ls-remote", "origin").stdout
    with pytest.raises(DevlegateError, match="clean reset blocked"):
        owner.run_iteration()
    assert owner._state["clean_reset_intent"]["status"] == "authorized-pre-effect"
    assert snapshot(owner.execution_worktree_root / "work") == resources
    assert git(owner.repo, "show-ref").stdout == refs
    assert git(owner.repo, "ls-remote", "origin").stdout == remote
    if change == "locked":
        assert "locked operator pinned evidence" in git(
            owner.repo, "worktree", "list", "--porcelain",
        ).stdout


@pytest.mark.parametrize("operation", ["worktree", "local", "remote"])
@pytest.mark.parametrize("after", [False, True])
def test_interrupt_effect_and_restart_replays_exact_stage(
    owner, monkeypatch, operation, after,
):
    payload, binding, ack = authorize(owner)
    original = clean_reset.observe_git

    def crash(repo, *args, **kwargs):
        if effect(args) == operation:
            if after:
                original(repo, *args, **kwargs)
            raise OSError("interrupted Git acknowledgement")
        return original(repo, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(clean_reset, "observe_git", crash)
        with pytest.raises(DevlegateError, match="interrupted"):
            owner.run_iteration()
    assert owner._state["clean_reset_intent"]["status"] == f"{operation}-pending"
    restored = Devlegate(owner.env_file)
    assert restored.submit_clean_apply(**payload, request_id="reset-one") == ack
    assert restored.run_iteration() == 0
    assert_retired(restored, binding)


@pytest.mark.parametrize("operation", ["worktree", "local", "remote"])
@pytest.mark.parametrize("committed", [False, True])
def test_effect_receipt_write_unknown_requires_restart(
    owner, monkeypatch, operation, committed,
):
    _, binding, _ = authorize(owner)
    original_save = owner._save_state
    original_commit = owner._runtime_store._commit
    target = {
        "worktree": "worktree-retired", "local": "local-retired", "remote": "completed",
    }

    def crash(connection):
        if committed:
            original_commit(connection)
        raise OSError("lost effect receipt acknowledgement")

    def save(*args, **kwargs):
        status = kwargs.get("clean_reset_intent", {}).get("status")
        if status == target[operation]:
            monkeypatch.setattr(owner._runtime_store, "_commit", crash)
        return original_save(*args, **kwargs)

    monkeypatch.setattr(owner, "_save_state", save)
    with pytest.raises(DevlegateError, match="lost effect receipt"):
        owner.run_iteration()
    assert owner._state_write_uncertain
    with pytest.raises(DevlegateError, match="uncertain"):
        owner.run_iteration()
    restored = Devlegate(owner.env_file)
    assert restored.run_iteration() == 0
    assert_retired(restored, binding)


@pytest.mark.parametrize("operation", ["local", "remote"])
def test_effect_uses_exact_cas_when_ref_moves_after_observation(
    owner, monkeypatch, operation,
):
    _, binding, _ = authorize(owner)
    original = clean_reset.observe_git
    ref = binding.intent["ref"]
    base = binding.intent["base"]

    def race(repo, *args, **kwargs):
        if effect(args) == operation:
            if operation == "local":
                git(owner.repo, "update-ref", ref, base)
            else:
                git(owner.repo, "push", "--force", "origin", f"{base}:{ref}")
        return original(repo, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(clean_reset, "observe_git", race)
        with pytest.raises(DevlegateError):
            owner.run_iteration()
    assert owner._state["clean_reset_intent"]["status"] == f"{operation}-pending"
    restored = Devlegate(owner.env_file)
    with pytest.raises(DevlegateError, match=f"{operation} ref changed"):
        restored.run_iteration()
    observed = (git(owner.repo, "rev-parse", ref).stdout if operation == "local"
                else git(owner.repo, "ls-remote", "origin", ref).stdout)
    assert base in observed


@pytest.mark.parametrize("observation", ["worktree", "local", "remote"])
def test_observation_failure_never_means_absence(owner, monkeypatch, observation):
    _, binding, _ = authorize(owner)
    original = clean_reset.observe_git

    def fail(repo, *args, **kwargs):
        if ((observation == "worktree" and args[:2] == ("worktree", "list"))
                or (observation == "local" and args[0] == "show-ref")
                or (observation == "remote" and args[0] == "ls-remote")):
            raise subprocess.TimeoutExpired("git", 30)
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(clean_reset, "observe_git", fail)
    with pytest.raises(DevlegateError):
        owner.run_iteration()
    assert binding.manager.path.exists()
    assert owner._state["clean_reset_intent"]["status"] == "authorized-pre-effect"


def test_partial_worktree_removal_is_unknown_and_never_pruned(owner, monkeypatch):
    _, binding, _ = authorize(owner)
    original = clean_reset.observe_git

    def partial(repo, *args, **kwargs):
        if effect(args) == "worktree":
            binding.manager.path.rename(binding.manager.path.with_name("preserved"))
            raise OSError("partial worktree removal")
        return original(repo, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(clean_reset, "observe_git", partial)
        with pytest.raises(DevlegateError):
            owner.run_iteration()
    restored = Devlegate(owner.env_file)
    with pytest.raises(DevlegateError):
        restored.run_iteration()
    assert binding.manager.path.resolve() in binding.manager._registrations()
    assert git(
        owner.repo, "show-ref", "--verify", binding.intent["ref"],
    ).returncode == 0


@pytest.mark.parametrize("resource", ["worktree", "local", "remote"])
def test_restart_rejects_reappeared_retired_resources(owner, monkeypatch, resource):
    _, binding, _ = authorize(owner)
    original_save = owner._save_state
    target = {"worktree": "worktree-retired", "local": "local-retired",
              "remote": "completed"}[resource]

    def stop(*args, **kwargs):
        result = original_save(*args, **kwargs)
        if kwargs.get("clean_reset_intent", {}).get("status") == target:
            raise OSError("stop after durable effect receipt")
        return result

    monkeypatch.setattr(owner, "_save_state", stop)
    with pytest.raises(DevlegateError, match="stop after"):
        owner.run_iteration()
    head = binding._expected_head()
    if resource == "worktree":
        git(owner.repo, "worktree", "add", str(binding.manager.path),
            binding.manager.branch)
    elif resource == "local":
        git(owner.repo, "update-ref", binding.intent["ref"], head)
    else:
        # Completed intents do no further effects, including on duplicate replay.
        git(owner.repo, "push", "origin", f"{head}:{binding.intent['remote_ref']}")
    restored = Devlegate(owner.env_file)
    before = git(owner.repo, "show-ref").stdout
    remote = git(owner.repo, "ls-remote", "origin").stdout
    if resource == "remote":
        assert restored.run_iteration() == 0
    else:
        with pytest.raises(DevlegateError, match="reappeared"):
            restored.run_iteration()
    assert git(owner.repo, "show-ref").stdout == before
    assert git(owner.repo, "ls-remote", "origin").stdout == remote


@pytest.mark.parametrize("owner", ["submodule", "nested"], indirect=True)
def test_clean_recursive_submodules_can_retire(owner):
    _, binding, _ = authorize(owner)
    assert owner.run_iteration() == 0
    assert_retired(owner, binding)


@pytest.mark.parametrize("owner", ["submodule", "nested"], indirect=True)
def test_dirty_recursive_submodules_are_preserved(owner):
    _, binding, _ = authorize(owner)
    dependency = binding.manager.path / "dependency"
    target = dependency / "nested" if (dependency / "nested").exists() else dependency
    (target / "untracked.data").write_text("preserve dependency evidence\n")
    before = snapshot(binding.manager.path)
    with pytest.raises(DevlegateError):
        owner.run_iteration()
    assert snapshot(binding.manager.path) == before


def test_restore_rejects_stage_without_effect_receipts(owner):
    authorize(owner)
    state = copy.deepcopy(owner._state)
    state["clean_reset_intent"]["status"] = "remote-pending"
    with pytest.raises(DevlegateError, match="effect journal"):
        validate_state_invariant(state)
