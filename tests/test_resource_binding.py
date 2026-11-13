# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real-Git generation evidence, including indistinguishable-SHA replacements."""

import dataclasses
import json
import os
import subprocess
from pathlib import Path

import pytest

from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import run_git
from devlegate.resource_binding import ResourceBinding


def read_only(repo, *args, **kwargs):
    return run_git(repo, "--no-optional-locks", *args, **kwargs)


@pytest.fixture
def resources(tmp_path):
    repo = tmp_path / "repo"
    remote = tmp_path / "remote.git"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "user.email", "test@example.com")
    (repo / "source").write_text("base\n")
    run_git(repo, "add", "source")
    run_git(repo, "commit", "-m", "base")
    run_git(repo, "init", "--bare", str(remote))
    run_git(repo, "remote", "add", "origin", str(remote))
    base = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    manager = ExecutionWorkspaceManager(repo, tmp_path / "resources", "TASK-001")
    binding = ResourceBinding.plan(
        manager, execution_id="execution-one", base=base, control=base,
        ticket_blob=run_git(repo, "rev-parse", "HEAD:source").stdout.strip(),
        remote="origin", remote_head=None,
    )
    binding.record_intent()
    return binding


def proof(binding, expected=None):
    # The preview must not take the owner's write-capable Git runner.
    manager = ExecutionWorkspaceManager(
        binding.manager.repo, binding.manager.root, binding.manager.ticket_id,
        runner=read_only,
    )
    return ResourceBinding(manager, binding.intent).prove(
        runner=read_only, expected_remote=expected,
    )


def snapshot(root):
    return {
        str(path.relative_to(root)): (
            path.read_bytes(), path.stat().st_mtime_ns, path.stat().st_ino,
        )
        for path in root.rglob("*") if path.is_file() and not path.is_symlink()
    }


def test_fresh_restart_and_read_only_proof(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    before = snapshot(binding.manager.root.parent)
    first = proof(binding)
    second = proof(binding)
    assert dataclasses.asdict(first) == dataclasses.asdict(second)
    assert first.local_state == "proved"
    assert first.state == "unproven"
    assert first.upstream_exact
    assert "same-SHA recreation" in first.reason
    assert snapshot(binding.manager.root.parent) == before
    restarted = ResourceBinding.for_execution(binding.manager, "execution-one")
    assert restarted.prepare(remote_head=None) == workspace
    assert snapshot(binding.manager.root.parent) == before


@pytest.mark.parametrize("target", ["marker", "checkout", "registration", "reflog"])
def test_same_content_replaced_witness(resources, target):
    binding = resources
    binding.prepare(remote_head=None)
    path = binding._targets(binding._admin())[target]
    content = path.read_bytes()
    path.unlink()
    path.write_bytes(content)
    result = proof(binding)
    assert result.state == "conflict"
    assert "witness" in result.reason


@pytest.mark.parametrize("change", ["missing", "changed", "dirty", "locked", "symlink"])
def test_lost_marker_dirty_lock_and_symlink_fail_closed(resources, change):
    binding = resources
    binding.prepare(remote_head=None)
    manager = binding.manager
    marker = binding._admin() / "devlegate-generation.json"
    if change == "missing":
        marker.unlink()
    elif change == "changed":
        marker.write_text("{}")
    elif change == "dirty":
        (manager.path / "source").write_text("dirty\n")
    elif change == "locked":
        run_git(
            manager.repo, "worktree", "lock", "--reason", "foreign lease",
            str(manager.path),
        )
    else:
        moved = manager.path.with_name("moved")
        manager.path.rename(moved)
        manager.path.symlink_to(moved, target_is_directory=True)
    before = snapshot(manager.root.parent)
    assert proof(binding).state in {"unproven", "conflict"}
    assert proof(binding).local_state != "proved"
    with pytest.raises((ExecutionWorkspaceError, OSError)):
        binding.prepare(remote_head=None)
    assert snapshot(manager.root.parent) == before


def test_same_sha_worktree_recreation_is_not_the_same_generation(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    manager = binding.manager
    run_git(manager.repo, "worktree", "remove", str(manager.path))
    run_git(manager.repo, "worktree", "add", str(manager.path), manager.branch)
    assert run_git(manager.path, "rev-parse", "HEAD").stdout.strip() == workspace.head
    assert proof(binding).local_state != "proved"


def test_remote_drift_unavailable_and_same_sha_recreation(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    manager = binding.manager
    ref = f"refs/heads/{manager.branch}"
    run_git(manager.repo, "push", "origin", f"{workspace.head}:{ref}")
    assert proof(binding).state == "conflict"
    assert proof(binding, workspace.head).state == "unproven"
    run_git(manager.repo, "push", "origin", f":{ref}")
    run_git(manager.repo, "push", "origin", f"{workspace.head}:{ref}")
    result = proof(binding, workspace.head)
    assert result.upstream_exact and result.upstream_state == "unproven"
    remote = Path(run_git(manager.repo, "remote", "get-url", "origin").stdout.strip())
    remote.rename(remote.with_name("offline.git"))
    assert "unavailable" in proof(binding, workspace.head).reason


def test_an_unrelated_execution_never_inherits_identical_resources(resources):
    binding = resources
    binding.prepare(remote_head=None)
    intent = {**binding.intent, "execution_id": "execution-two"}
    # A new admission has a new token, including when all other values match.
    new = ResourceBinding.plan(
        binding.manager, execution_id=intent["execution_id"],
        base=str(intent["base"]), control=str(intent["control"]),
        ticket_blob=str(intent["ticket_blob"]), remote="origin", remote_head=None,
    )
    assert new.intent["generation"] != binding.intent["generation"]
    new.record_intent()
    before = snapshot(binding.manager.root.parent)
    with pytest.raises(ExecutionWorkspaceError, match="foreign"):
        new.prepare(remote_head=None)
    assert snapshot(binding.manager.root.parent) == before
    assert proof(binding).local_state == "proved"


@pytest.mark.parametrize("boundary", ["branch", "worktree", "marker", "receipt"])
def test_creation_faults_never_adopt_effect_without_receipt(
    resources, monkeypatch, boundary
):
    binding = resources
    manager = binding.manager
    original = manager._runner

    def interrupted(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if (boundary == "branch" and args[0] == "update-ref") or (
            boundary == "worktree" and args[:2] == ("worktree", "add")
        ):
            raise OSError("injected after external effect")
        return result

    if boundary in {"branch", "worktree"}:
        monkeypatch.setattr(manager, "_runner", interrupted)
    else:
        import devlegate.resource_binding as module
        write = module._write_once

        def interrupted_write(path, value):
            write(path, value)
            if path.name == (
                "devlegate-generation.json" if boundary == "marker" else "receipt.json"
            ):
                raise OSError("injected after durable file")

        monkeypatch.setattr(module, "_write_once", interrupted_write)
    with pytest.raises(OSError, match="injected"):
        binding.prepare(remote_head=None)
    monkeypatch.undo()
    before = snapshot(manager.root.parent)
    if boundary == "receipt":
        binding.prepare(remote_head=None)
        assert proof(binding).local_state == "proved"
    else:
        with pytest.raises(ExecutionWorkspaceError, match="ambiguous"):
            binding.prepare(remote_head=None)
    assert snapshot(manager.root.parent) == before


def test_wrong_generation_and_common_directory(resources):
    binding = resources
    binding.prepare(remote_head=None)
    for field, value in (
        ("generation", "a" * 64), ("path", "/wrong"), ("common", "/wrong"),
        ("ref", "refs/heads/wrong"), ("execution_id", "wrong"),
    ):
        bad = ResourceBinding(binding.manager, {**binding.intent, field: value})
        assert proof(bad).local_state != "proved"


def test_marker_contents_cannot_be_reconstructed_from_runtime_intent(resources):
    binding = resources
    binding.prepare(remote_head=None)
    marker = binding._admin() / "devlegate-generation.json"
    marker.unlink()
    marker.write_text(json.dumps(binding.intent))
    assert proof(binding).local_state != "proved"
    assert os.stat(marker).st_ino != os.stat(
        binding.directory / "witness-marker"
    ).st_ino


def test_explicit_retry_handoff_requires_independent_predecessor(resources):
    previous = resources
    workspace = previous.prepare(remote_head=None)
    next_binding = ResourceBinding.plan(
        previous.manager, execution_id="retry-two", base=workspace.base_head,
        control=str(previous.intent["control"]),
        ticket_blob=str(previous.intent["ticket_blob"]), remote="origin",
        remote_head=None,
    )
    next_binding.intent.update({
        "predecessor_execution": previous.intent["execution_id"],
        "predecessor_generation": previous.intent["generation"],
    })
    next_binding.record_intent()
    next_binding.prepare_handoff(previous, remote_head=None)
    assert proof(next_binding).local_state == "proved"
    assert proof(previous).local_state != "proved"
    assert run_git(workspace.path, "rev-parse", "HEAD").stdout.strip() == workspace.head


def test_archived_worktree_reports_and_evidence_refs_are_untouched(resources):
    binding = resources
    repo = binding.manager.repo
    archived = binding.manager.root / "work" / "TASK-134"
    archived.parent.mkdir(parents=True)
    run_git(repo, "worktree", "add", "-b", "devlegate/work/TASK-134", str(archived))
    evidence = "refs/devlegate/evidence/archived/TASK-134"
    run_git(repo, "update-ref", evidence, str(binding.intent["base"]))
    reports = binding.manager.root / "immutable-reports"
    reports.mkdir()
    (reports / "TASK-134-report.json").write_text('{"archived": true}\n')
    before = snapshot(archived), snapshot(reports)
    refs = run_git(repo, "show-ref").stdout
    binding.prepare(remote_head=None)
    assert proof(binding).local_state == "proved"
    assert (snapshot(archived), snapshot(reports)) == before
    for line in refs.splitlines():
        assert line in run_git(repo, "show-ref").stdout.splitlines()


@pytest.mark.parametrize("changed", ["dirty", "head"])
def test_submodule_changes_are_not_a_valid_generation(resources, monkeypatch, changed):
    binding = resources
    repo = binding.manager.repo
    dependency = repo.parent / "dependency"
    dependency.mkdir()
    run_git(dependency, "init", "-b", "main")
    run_git(dependency, "config", "user.name", "Test")
    run_git(dependency, "config", "user.email", "test@example.com")
    (dependency / "library").write_text("library\n")
    run_git(dependency, "add", ".")
    run_git(dependency, "commit", "-m", "dependency")
    monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
    run_git(repo, "submodule", "add", str(dependency), "dependency")
    run_git(repo, "commit", "-am", "add dependency")
    base = run_git(repo, "rev-parse", "HEAD").stdout.strip()
    binding = ResourceBinding.plan(
        binding.manager, execution_id="with-submodule", base=base, control=base,
        ticket_blob=str(binding.intent["ticket_blob"]),
        remote="origin", remote_head=None,
    )
    binding.record_intent()
    workspace = binding.prepare(remote_head=None)
    checkout = workspace.path / "dependency"
    (checkout / "library").write_text("changed\n")
    if changed == "head":
        run_git(checkout, "config", "user.name", "Test")
        run_git(checkout, "config", "user.email", "test@example.com")
        run_git(checkout, "commit", "-am", "foreign dependency commit")
    before = snapshot(workspace.path)
    assert proof(binding).state == "conflict"
    assert "submodule" in proof(binding).reason
    assert snapshot(workspace.path) == before


def test_same_sha_recreation_between_effect_and_receipt_is_not_adopted(
    resources, monkeypatch
):
    binding = resources
    manager = binding.manager
    original = manager._runner

    def replace_after_creation(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if args[:2] == ("worktree", "add"):
            original(manager.repo, "worktree", "remove", str(manager.path))
            original(manager.repo, "worktree", "add", str(manager.path), manager.branch)
        return result

    monkeypatch.setattr(manager, "_runner", replace_after_creation)
    with pytest.raises(ExecutionWorkspaceError, match="directory was replaced"):
        binding.prepare(remote_head=None)
    assert not (binding.directory / "receipt.json").exists()
    assert proof(binding).local_state != "proved"


@pytest.mark.parametrize("when", ["before", "after"])
def test_same_sha_branch_creation_race_is_not_adopted(resources, monkeypatch, when):
    binding = resources
    manager = binding.manager
    original = manager._runner
    reference = str(binding.intent["ref"])
    base = str(binding.intent["base"])

    def race(repo, *args, **kwargs):
        if args[0] != "update-ref":
            return original(repo, *args, **kwargs)
        if when == "before":
            original(repo, "update-ref", "--create-reflog", reference, base, "")
            return original(repo, *args, **kwargs)
        result = original(repo, *args, **kwargs)
        original(repo, "update-ref", "-d", reference, base)
        original(repo, "update-ref", "--create-reflog", reference, base, "")
        return result

    monkeypatch.setattr(manager, "_runner", race)
    with pytest.raises(ExecutionWorkspaceError, match="CAS failed|anchor differs"):
        binding.prepare(remote_head=None)
    assert not (manager.path / ".git").exists()
    assert not (binding.directory / "receipt.json").exists()


def test_changed_local_ref_requires_a_new_owner_receipt(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    (workspace.path / "source").write_text("foreign commit\n")
    run_git(workspace.path, "commit", "-am", "foreign ref change")
    result = proof(binding)
    assert result.state == "conflict"
    assert "local execution ref differs" in result.reason


@pytest.mark.parametrize("change", ["ref", "branch", "moved", "git-lock", "endpoint"])
def test_real_topology_substitution_is_not_a_valid_binding(resources, change):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    manager = binding.manager
    if change == "ref":
        reference = str(binding.intent["ref"])
        run_git(manager.repo, "update-ref", "-d", reference, workspace.head)
        run_git(
            manager.repo, "update-ref", "--create-reflog", reference, workspace.head,
        )
    elif change == "branch":
        run_git(workspace.path, "switch", "-c", "foreign")
    elif change == "moved":
        run_git(manager.repo, "worktree", "move", str(manager.path),
                str(manager.path.with_name("moved")))
    elif change == "git-lock":
        (binding._admin() / "index.lock").touch()
    else:
        run_git(manager.repo, "remote", "set-url", "origin", "/foreign")
    before = snapshot(manager.root.parent)
    assert proof(binding).local_state != "proved"
    with pytest.raises((ExecutionWorkspaceError, OSError)):
        binding.prepare(remote_head=None)
    assert snapshot(manager.root.parent) == before


def test_partial_creation_receipt_is_not_repaired(resources):
    binding = resources
    binding.prepare(remote_head=None)
    (binding.directory / "receipt.json").write_text('{"generation":')
    before = snapshot(binding.manager.root.parent)
    assert proof(binding).state == "conflict"
    with pytest.raises(ExecutionWorkspaceError, match="invalid independent"):
        binding.prepare(remote_head=None)
    assert snapshot(binding.manager.root.parent) == before


def test_unavailable_local_observation_is_unproven(resources):
    binding = resources
    binding.prepare(remote_head=None)

    def unavailable(repo, *args, **kwargs):
        if "worktree" in args:
            return subprocess.CompletedProcess(args, 128, "", "observation failed")
        return read_only(repo, *args, **kwargs)

    manager = ExecutionWorkspaceManager(
        binding.manager.repo, binding.manager.root, binding.manager.ticket_id,
        runner=unavailable,
    )
    result = ResourceBinding(manager, binding.intent).prove(
        runner=unavailable, expected_remote=None,
    )
    assert result.state == "unproven" and result.local_state == "unproven"
    assert "observation unavailable" in result.reason


def test_ref_log_rewrite_without_inode_replacement_is_a_conflict(resources):
    binding = resources
    binding.prepare(remote_head=None)
    reflog = binding._targets(binding._admin())["reflog"]
    reflog.write_text("foreign reflog\n")
    result = proof(binding)
    assert result.state == "conflict" and "reflog prefix changed" in result.reason


def test_cleanup_ref_reobservation_rejects_recreation_after_worktree_loss(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    run_git(binding.manager.repo, "worktree", "remove", str(workspace.path))
    binding.require_ref(workspace.head)
    reference = str(binding.intent["ref"])
    run_git(binding.manager.repo, "update-ref", "-d", reference, workspace.head)
    run_git(
        binding.manager.repo, "update-ref", "--create-reflog",
        reference, workspace.head,
    )
    with pytest.raises(ExecutionWorkspaceError, match="witness was lost or replaced"):
        binding.require_ref(workspace.head)


def test_read_only_proof_does_not_run_configured_clean_filters(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    output = workspace.path / "filter-side-effect"
    run_git(workspace.path, "config", "filter.effect.clean", f"touch {output}; cat")
    (workspace.path / ".gitattributes").write_text("source filter=effect\n")
    before = snapshot(binding.manager.root.parent)
    result = proof(binding)
    assert result.state == "unproven"
    assert "active Git clean/process filter" in result.reason
    assert not output.exists()
    assert snapshot(binding.manager.root.parent) == before


@pytest.mark.parametrize("target", ["registration", "common"])
def test_symlinked_administrative_path_is_not_hidden_by_resolving_paths(
    resources, target
):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    admin = binding._admin()
    alias = binding.manager.repo.parent / "alias"
    if target == "registration":
        alias.symlink_to(workspace.path, target_is_directory=True)
        (admin / "gitdir").write_text(f"{alias / '.git'}\n")
    else:
        alias.symlink_to(binding.intent["common"], target_is_directory=True)
        (admin / "commondir").write_text(f"{alias}\n")
    before = snapshot(binding.manager.root.parent)
    result = proof(binding)
    assert result.state == "conflict"
    assert "path differs" in result.reason or "symlink" in result.reason
    assert snapshot(binding.manager.root.parent) == before


@pytest.mark.parametrize("interrupted", [None, "worktree", "receipt"])
def test_zero_delta_rework_after_ordinary_retirement(
    resources, monkeypatch, interrupted
):
    previous = resources
    workspace = previous.prepare(remote_head=None)
    manager = previous.manager
    manager.retire(workspace, workspace.head)
    run_git(
        manager.repo, "update-ref", "-d", str(previous.intent["ref"]), workspace.head
    )
    current = ResourceBinding.plan(
        manager, execution_id="reviewed-rework", base=workspace.head,
        control=str(previous.intent["control"]),
        ticket_blob=str(previous.intent["ticket_blob"]),
        remote="origin", remote_head=None,
    )
    current.record_intent()
    original = manager._runner

    def create(repo, *args, **kwargs):
        result = original(repo, *args, **kwargs)
        if interrupted == "worktree" and args[:2] == ("worktree", "add"):
            raise OSError("interrupted creation")
        return result

    monkeypatch.setattr(manager, "_runner", create)
    if interrupted == "receipt":
        import devlegate.resource_binding as module
        write = module._write_once

        def receipt(path, value):
            write(path, value)
            if path.name == "receipt.json":
                raise OSError("interrupted receipt")

        monkeypatch.setattr(module, "_write_once", receipt)
    if interrupted:
        with pytest.raises(OSError, match="interrupted"):
            current.prepare_rework(
                previous, workspace.head, workspace.head, remote_head=None,
            )
        monkeypatch.undo()
    if interrupted == "worktree":
        before = snapshot(manager.root.parent)
        with pytest.raises(ExecutionWorkspaceError):
            current.prepare_rework(
                previous, workspace.head, workspace.head, remote_head=None,
            )
        assert snapshot(manager.root.parent) == before
    else:
        recreated = current.prepare_rework(
            previous, workspace.head, workspace.head, remote_head=None,
        )
        assert recreated.head == workspace.head
        assert proof(current).local_state == "proved"
        assert proof(previous).local_state != "proved"


def test_repeatable_operations_do_not_reuse_uncertain_attempts(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    for operation in ("rewrite", "rollback", "rewrite", "rewrite", "rollback"):
        binding.require_local(clean=True)
        binding.begin_operation(operation, {"before": workspace.head})
        binding.finish_operation(operation, {"after": workspace.head})
        binding = ResourceBinding.for_execution(binding.manager, "execution-one")
    assert [item[1] for item in binding._attempts()] == [
        "rewrite", "rollback", "rewrite", "rewrite", "rollback",
    ]
    assert proof(binding).local_state == "proved"
    binding.begin_operation("rewrite", {"before": workspace.head})
    before = snapshot(binding.manager.root.parent)
    restarted = ResourceBinding.for_execution(binding.manager, "execution-one")
    with pytest.raises(ExecutionWorkspaceError, match="receipt missing"):
        restarted.begin_operation("rewrite", {"before": workspace.head})
    assert "receipt missing" in proof(restarted).reason
    assert snapshot(binding.manager.root.parent) == before


@pytest.mark.parametrize("interrupted", [False, True])
def test_no_branch_rework_manager_uses_generation_creator(
    resources, monkeypatch, interrupted
):
    binding = resources
    base = str(binding.intent["base"])
    called = []

    def create(admitted):
        called.append(admitted)
        return binding.prepare(remote_head=None)

    if interrupted:
        monkeypatch.setattr(
            binding, "_record_creation",
            lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("lost receipt")),
        )
        with pytest.raises(OSError, match="lost receipt"):
            binding.manager.prepare_rework(base, base, base, creator=create)
        monkeypatch.undo()
        before = snapshot(binding.manager.root.parent)
        with pytest.raises(ExecutionWorkspaceError, match="ambiguous"):
            binding.prepare(remote_head=None)
        assert snapshot(binding.manager.root.parent) == before
    else:
        workspace = binding.manager.prepare_rework(base, base, base, creator=create)
        assert workspace.head == base
        assert proof(binding).local_state == "proved"
    assert called == [base]


def test_failed_rebase_cannot_receipt_an_unexpected_branch_change(resources):
    binding = resources
    workspace = binding.prepare(remote_head=None)
    binding.begin_operation("rewrite", {"before": workspace.head})
    repo = binding.manager.repo
    tree = run_git(repo, "rev-parse", "HEAD^{tree}").stdout.strip()
    foreign = run_git(
        repo, "commit-tree", tree, "-p", workspace.head, input_text="foreign\n",
    ).stdout.strip()
    run_git(repo, "update-ref", str(binding.intent["ref"]), foreign, workspace.head)
    before = snapshot(binding.manager.root.parent)
    with pytest.raises(ExecutionWorkspaceError, match="failed rebase changed"):
        binding.finish_operation("rewrite", {
            "after": foreign, "returncode": 1, "rebase_state": {},
        })
    assert not (binding.directory / "rewrite-receipt.json").exists()
    assert snapshot(binding.manager.root.parent) == before
