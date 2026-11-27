# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from dataclasses import replace

import pytest

from devlegate import implementation_transfer as module
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.git import run_git
from devlegate.implementation_transfer import (
    ImplementationTransfer,
    ResolutionAuthorization,
    TransferConflict,
    TransferEffect,
    TransferIdentity,
    TransferInterrupted,
    TransferReady,
    TransferUnavailable,
)


def git(path, *args):
    return run_git(path, *map(str, args)).stdout.strip()


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def init(path):
    path.mkdir()
    git(path, "init", "-b", "product")
    git(path, "config", "user.email", "worker@example.com")
    git(path, "config", "user.name", "Worker")
    return path


@pytest.fixture
def repo(tmp_path):
    product = init(tmp_path / "product")
    (product / "common").write_text("original\n")
    (product / "gone").write_text("delete\n")
    (product / "old").write_text("rename me\n")
    (product / "script").write_text("#!/bin/sh\n")
    a = commit(product, "base")
    git(product, "branch", "control")
    source = tmp_path / "state" / "work" / "T-1"
    git(product, "worktree", "add", "-b", "devlegate/work/T-1", source, a)
    return product, source, a


@pytest.fixture
def owner(repo, tmp_path):
    product, source, a = repo

    def open_transfer(h, b, *, identity=None, runner=run_git, require_source=None):
        if identity is None:
            identity = TransferIdentity("owner", "T-1", "a" * 64, "b" * 64, a, h, b)

        def source_proof(admitted):
            assert admitted == identity
            assert git(source, "rev-parse", "HEAD") == admitted.checkpoint
            assert not git(source, "status", "--porcelain")

        return ImplementationTransfer(
            product, tmp_path / "state", identity, require_owner=lambda: None,
            require_source=require_source or source_proof, runner=runner,
        )

    return open_transfer


def test_multicommit_transfer_preserves_trees_sources_and_remote(repo, owner, tmp_path):
    product, source, a = repo
    remote = tmp_path / "remote.git"
    git(tmp_path, "init", "--bare", remote)
    git(product, "remote", "add", "origin", remote)
    (source / "binary").write_bytes(bytes(range(256)))
    (source / "script").chmod(0o755)
    one = commit(source, "binary and mode")
    git(source, "mv", "old", "renamed")
    (source / "gone").unlink()
    h = commit(source, "rename and delete")
    git(product, "push", "origin", "--all")
    (product / "upstream").write_text("retained\n")
    b = commit(product, "advance")
    refs = git(product, "show-ref")
    remote_refs = git(remote, "show-ref")
    index_path = source / git(source, "rev-parse", "--git-path", "index")
    index = index_path.read_bytes()

    transfer = owner(h, b)
    result = transfer.prepare()
    assert isinstance(result, TransferReady), result
    assert result.effect == TransferEffect.CHANGED and not result.externally_resolved
    assert git(result.path, "log", "--format=%s|%ae", f"{b}..HEAD").splitlines() == [
        git(source, "show", "-s", "--format=%s|%ae", sha) for sha in (h, one)
    ]
    # The exact final contribution includes binary data and mode changes.
    assert git(result.path, "diff", "--binary", "-M", b, result.head) == git(
        source, "diff", "--binary", "-M", a, h,
    )
    assert git(result.path, "show", "HEAD:upstream") == "retained"
    assert git(result.path, "rev-parse", f"{h}^{{commit}}") == h
    assert git(result.path, "rev-parse", f"{one}^{{commit}}") == one
    assert git(product, "show-ref") == refs
    assert git(remote, "show-ref") == remote_refs
    assert git(source, "rev-parse", "HEAD") == h
    assert index_path.read_bytes() == index
    assert not git(source, "status", "--porcelain")
    assert not git(product, "status", "--porcelain")
    assert str(result.path) not in git(product, "worktree", "list", "--porcelain")
    git(product, "worktree", "prune", "--expire=now")
    assert owner(h, b).prepare() == result
    # Downstream can import the exact detached candidate into the original
    # object store without first installing any branch or publishing a report.
    git(product, "fetch", "--no-tags", "--no-write-fetch-head", result.path, "HEAD")
    assert git(product, "rev-parse", f"{result.head}^{{tree}}") == result.tree
    assert git(product, "show-ref") == refs


@pytest.mark.parametrize(("case", "effect"), [
    ("same", TransferEffect.CHANGED),
    ("empty", TransferEffect.SOURCE_EMPTY),
    ("canceled", TransferEffect.SOURCE_EMPTY),
    ("ancestor", TransferEffect.ALREADY_ANCESTOR),
    ("equivalent", TransferEffect.TARGET_TREE_UNCHANGED),
    ("partial", TransferEffect.CHANGED),
    ("replacement", TransferEffect.CHANGED),
])
def test_classifies_incorporation_and_retains_exact_history(repo, owner, case, effect):
    product, source, a = repo
    h = a
    if case != "empty":
        (source / "worker").write_text("worker\n")
        h = commit(source, "work")
    if case == "canceled":
        (source / "worker").unlink()
        h = commit(source, "cancel work")
    if case == "partial":
        (source / "more").write_text("second\n")
        h = commit(source, "more work")
    if case == "ancestor":
        git(product, "merge", "--ff-only", h)
    if case == "replacement":
        (product / "base-fix").write_text("replacement\n")
        git(product, "add", "base-fix")
        git(product, "commit", "--amend", "--no-edit")
    elif case != "same":
        (product / "upstream").write_text("target\n")
        if case in {"equivalent", "partial"}:
            (product / "worker").write_text("worker\n")
        commit(product, "target")
    b = git(product, "rev-parse", "HEAD")
    transfer = owner(h, b)
    result = transfer.prepare()
    assert isinstance(result, TransferReady), result
    assert result.effect == effect
    if case == "same":
        assert result.head == h
    elif case in {"empty", "canceled", "ancestor"}:
        assert result.head == b
    elif case == "equivalent":
        assert result.head != b
        assert result.tree == git(product, "rev-parse", "HEAD^{tree}")
    elif case == "partial":
        assert git(result.path, "rev-list", "--count", f"{b}..HEAD") == "2"
        assert git(result.path, "show", "HEAD:more") == "second"
    if case in {"same", "empty", "canceled", "ancestor"}:
        assert result.path is None
        assert list(transfer.root.iterdir()) == [transfer.intent]
    assert git(result.path or product, "rev-parse", f"{h}^{{commit}}") == h


def test_gitlink_transfer_keeps_submodule_checkouts_unchanged(repo, owner, tmp_path):
    product, source, _ = repo
    dependency = init(tmp_path / "dependency")
    (dependency / "pin").write_text("one\n")
    first = commit(dependency, "one")
    git(product, "-c", "protocol.file.allow=always", "submodule", "add",
        dependency, "sub")
    a = commit(product, "submodule base")
    git(source, "merge", "--ff-only", a)
    git(source, "-c", "protocol.file.allow=always", "submodule", "update", "--init")
    (dependency / "pin").write_text("two\n")
    second = commit(dependency, "two")
    git(source / "sub", "fetch", "origin", second)
    git(source / "sub", "checkout", "--detach", second)
    h = commit(source, "update pin")
    (product / "upstream").write_text("advance\n")
    b = commit(product, "advance")
    identity = TransferIdentity("owner", "T-1", "a" * 64, "b" * 64, a, h, b)
    result = owner(h, b, identity=identity).prepare()
    assert isinstance(result, TransferReady), result
    assert git(result.path, "ls-tree", "HEAD", "sub") == f"160000 commit {second}\tsub"
    assert not (result.path / "sub" / ".git").exists()
    assert git(product / "sub", "rev-parse", "HEAD") == first
    assert git(source / "sub", "rev-parse", "HEAD") == second


@pytest.fixture
def conflict_input(repo):
    product, source, _ = repo
    (source / "common").write_text("worker\n")
    first = commit(source, "worker edits")
    (source / "follow-up").write_text("later edit\n")
    h = commit(source, "worker follow-up")
    (product / "common").write_text("product\n")
    b = commit(product, "conflicting product edits")
    return h, b, first


def edit_resolution(conflict):
    (conflict.path / "common").write_text("resolved\n")
    (conflict.path / "explanation").write_text("additional justified change\n")


def test_explicit_resolution_is_exact_and_attributed(repo, owner, conflict_input):
    product, source, _ = repo
    h, b, first = conflict_input
    transfer = owner(h, b)
    conflict = transfer.prepare()
    assert isinstance(conflict, TransferConflict), conflict
    assert conflict.source_commit == first and conflict.parent == b
    assert "<<<<<<<" in (conflict.path / "common").read_text()
    assert owner(h, b).prepare() == conflict
    assert isinstance(transfer.checkpoint_resolution(ResolutionAuthorization(
        conflict, "1" * 40, "attempt while unresolved",
    )), TransferUnavailable)
    edit_resolution(conflict)
    tree = transfer.stage_resolution(conflict, ("common", "explanation"))
    assert transfer.resolution_tree(conflict) == tree
    auth = ResolutionAuthorization(conflict, tree, "reason for the extra path")
    other = replace(transfer.identity, attempt="c" * 64)
    assert isinstance(transfer.checkpoint_resolution(replace(
        auth, conflict=replace(conflict, identity=other),
    )), TransferUnavailable)
    assert isinstance(transfer.checkpoint_resolution(replace(auth, tree="2" * 40)),
                      TransferUnavailable)
    (conflict.path / "untracked").write_text("not part of the approval")
    assert isinstance(transfer.checkpoint_resolution(auth), TransferUnavailable)
    (conflict.path / "untracked").unlink()
    assert transfer.prepare() == conflict  # Staging never provides approval.
    result = owner(h, b).checkpoint_resolution(auth)
    assert isinstance(result, TransferReady), result
    assert result.externally_resolved and result.effect == TransferEffect.CHANGED
    assert git(result.path, "rev-parse", "HEAD^^") == b
    assert git(result.path, "rev-parse", "HEAD^^{tree}") == tree
    assert git(result.path, "show", "HEAD:explanation") == "additional justified change"
    assert git(result.path, "show", "HEAD:follow-up") == "later edit"
    assert git(result.path, "rev-parse", f"{h}^{{commit}}") == h
    assert git(product, "rev-parse", "HEAD") == b
    assert git(source, "rev-parse", "HEAD") == h
    assert isinstance(transfer.checkpoint_resolution(auth), TransferUnavailable)
    assert owner(h, b).prepare() == result
    transfer.release(result, require_consumed=lambda _: None)
    assert not result.path.exists() and not conflict.path.exists()


class Crash(Exception):
    pass


@pytest.mark.parametrize("after", [False, True])
@pytest.mark.parametrize("boundary", [
    "intent", "init", "checkout", "cherry-pick", "outcome",
])
def test_reopen_boundaries(repo, owner, monkeypatch, boundary, after):
    product, source, _ = repo
    (source / "work").write_text("implementation\n")
    h = commit(source, "worker edit")
    (product / "upstream").write_text("advance\n")
    b = commit(product, "advance")
    refs = git(product, "show-ref")
    publish = module._publish
    first_path = None

    def interrupted_write(path, data):
        kind = "outcome" if path.name.endswith(".outcome.json") else "intent"
        if boundary != kind or after:
            publish(path, data)
        if boundary == kind:
            raise Crash

    def interrupted_git(cwd, *args, **kwargs):
        nonlocal first_path
        if "init" in args:
            first_path = cwd
        if boundary in {"init", "checkout", "cherry-pick"} and boundary in args:
            if after:
                run_git(cwd, *args, **kwargs)
            raise Crash
        return run_git(cwd, *args, **kwargs)

    with monkeypatch.context() as patch:
        patch.setattr(module, "_publish", interrupted_write)
        with pytest.raises(Crash):
            owner(h, b, runner=interrupted_git).prepare()
    result = owner(h, b).prepare()
    assert isinstance(result, TransferReady), result
    assert result.identity == owner(h, b).identity
    assert git(result.path, "show", "HEAD:work") == "implementation"
    assert git(result.path, "show", "HEAD:upstream") == "advance"
    assert git(result.path, "rev-parse", "HEAD^") == b
    if first_path is not None:
        assert (result.path == first_path) == (boundary == "outcome" and after)
        assert first_path.is_dir()
    assert owner(h, b).prepare() == result
    assert git(source, "rev-parse", "HEAD") == h
    assert git(product, "rev-parse", "HEAD") == b
    assert git(product, "show-ref") == refs


@pytest.mark.parametrize(("command", "after"), [
    ("hash-object", False), ("hash-object", True),
    ("checkout", True), ("cherry-pick", True),
])
def test_replay_resolution_checkpoint(repo, owner, conflict_input, command, after):
    h, b, _ = conflict_input
    transfer = owner(h, b)
    conflict = transfer.prepare()
    assert isinstance(conflict, TransferConflict), conflict
    edit_resolution(conflict)
    tree = transfer.stage_resolution(conflict, ("common", "explanation"))

    def interrupt(cwd, *args, **kwargs):
        if command in args and (command != "hash-object" or "-w" in args):
            if after:
                run_git(cwd, *args, **kwargs)
            raise Crash
        return run_git(cwd, *args, **kwargs)

    with pytest.raises(Crash):
        owner(h, b, runner=interrupt).checkpoint_resolution(
            ResolutionAuthorization(conflict, tree, "authorized exact index"),
        )
    result = owner(h, b).prepare()
    assert isinstance(result, TransferReady), result
    assert result.externally_resolved
    assert git(result.path, "rev-parse", "HEAD^^{tree}") == tree


def test_foreign_path_and_same_sha_ref_are_not_adopted(owner, conflict_input):
    h, b, _ = conflict_input
    transfer = owner(h, b)
    transfer.root.mkdir(parents=True)
    transfer.intent.write_text("{}")
    result = transfer.prepare()
    assert isinstance(result, TransferUnavailable) and "identity" in result.reason
    transfer.intent.unlink()
    conflict = transfer.prepare()
    assert isinstance(conflict, TransferConflict), conflict
    git(conflict.path, "update-ref", "refs/heads/foreign", b)
    assert isinstance(owner(h, b).prepare(), TransferUnavailable)
    git(conflict.path, "update-ref", "-d", "refs/heads/foreign", b)
    original = conflict.path.with_name("original-candidate")
    conflict.path.rename(original)
    git(conflict.path.parent, "clone", "--no-local", original, conflict.path)
    git(conflict.path, "checkout", "--detach", b)
    assert isinstance(owner(h, b).prepare(), TransferUnavailable)


def test_missing_proof_and_release_require_owner_custody(repo, owner):
    product, source, _ = repo
    (source / "work").write_text("worker edit\n")
    h = commit(source, "work")
    (product / "upstream").write_text("advance\n")
    b = commit(product, "advance")

    def unavailable(_):
        raise ExecutionWorkspaceError("source checkpoint receipt unavailable")

    transfer = owner(h, b, require_source=unavailable)
    result = transfer.prepare()
    assert isinstance(result, TransferUnavailable)
    assert "receipt unavailable" in result.reason
    assert not transfer.root.exists()
    result = owner(h, b).prepare()
    assert isinstance(result, TransferReady), result
    # Borrowed objects alone do not prove the admitted generation on reopening.
    refused = transfer.prepare()
    assert isinstance(refused, TransferUnavailable)
    assert "receipt unavailable" in refused.reason
    transfer = owner(h, b)
    with pytest.raises(ExecutionWorkspaceError, match="receipt unavailable"):
        transfer.release(result, require_consumed=unavailable)
    assert transfer.prepare() == result
    (result.path / "external").write_text("do not erase\n")
    with pytest.raises(ExecutionWorkspaceError, match="clean checkpoint"):
        transfer.release(result, require_consumed=lambda _: None)
    assert (result.path / "external").exists()
    (result.path / "external").unlink()
    transfer.release(result, require_consumed=lambda _: None)
    transfer.release(result, require_consumed=unavailable)
    assert not result.path.exists()
    assert isinstance(transfer.prepare(), TransferUnavailable)
    assert git(source, "rev-parse", "HEAD") == h
    assert git(product, "rev-parse", "HEAD") == b


def test_owner_and_canonical_path_are_checked_before_reservation(repo, tmp_path):
    product, source, a = repo
    identity = TransferIdentity("owner", "T-1", "a" * 64, "b" * 64, a, a, a)

    def reject_owner():
        raise RuntimeError("owner region is not held")

    transfer = ImplementationTransfer(
        product, tmp_path / "private", identity, require_owner=reject_owner,
        require_source=lambda _: pytest.fail("source proof ran outside owner region"),
        runner=run_git,
    )
    with pytest.raises(RuntimeError, match="owner region"):
        transfer.prepare()
    assert not transfer.root.exists()
    transfer = ImplementationTransfer(
        product, source / "nested", identity, require_owner=lambda: None,
        require_source=lambda _: None, runner=run_git,
    )
    result = transfer.prepare()
    assert isinstance(result, TransferUnavailable) and "overlaps" in result.reason
    assert not transfer.root.exists()


def test_replay_from_independent_packed_source_evidence(repo, owner, conflict_input):
    product, source, _ = repo
    h, b, _ = conflict_input
    pin = "refs/devlegate/evidence/retained-h"
    git(product, "update-ref", pin, h)
    # Simulate an origin that has already retained H under its own proof.
    git(product, "worktree", "remove", source)
    git(product, "branch", "-D", "devlegate/work/T-1")
    git(product, "gc", "--prune=now")
    refs = git(product, "show-ref")

    def prove_pin(identity):
        if git(product, "rev-parse", pin) != identity.checkpoint:
            raise ExecutionWorkspaceError("retained source pin changed")

    result = owner(h, b, require_source=prove_pin).prepare()
    assert isinstance(result, TransferConflict), result
    assert git(result.path, "rev-parse", f"{h}^{{commit}}") == h
    assert git(product, "show-ref") == refs
    git(product, "update-ref", pin, b, h)
    refused = owner(h, b, require_source=prove_pin).prepare()
    assert isinstance(refused, TransferUnavailable) and "pin changed" in refused.reason
    assert "<<<<<<<" in (result.path / "common").read_text()


def test_each_conflict_in_a_series_needs_its_own_approval(repo, owner):
    product, source, _ = repo
    (source / "prefix").write_text("applied before the first conflict\n")
    commit(source, "clean prefix")
    (source / "common").write_text("first conflict\n")
    first = commit(source, "first")
    (source / "gone").write_text("second conflict\n")
    h = commit(source, "second")
    (product / "common").write_text("product common\n")
    (product / "gone").write_text("product gone\n")
    b = commit(product, "product")
    transfer = owner(h, b)
    conflict = transfer.prepare()
    assert isinstance(conflict, TransferConflict), conflict
    assert (conflict.step, conflict.source_commit) == (1, first)
    (conflict.path / "common").write_text("first resolution\n")
    tree = transfer.stage_resolution(conflict, ("common",))
    first_auth = ResolutionAuthorization(conflict, tree, "first decision")
    second = transfer.checkpoint_resolution(first_auth)
    assert isinstance(second, TransferConflict), second
    assert (second.step, second.source_commit) == (2, h)
    assert second.path != conflict.path
    assert (second.path / "common").read_text() == "first resolution\n"
    transfer = owner(h, b)
    assert isinstance(transfer.checkpoint_resolution(first_auth), TransferUnavailable)
    assert transfer.prepare() == second
    (second.path / "gone").unlink()
    tree = transfer.stage_resolution(second, ("gone",))
    ready = transfer.checkpoint_resolution(
        ResolutionAuthorization(second, tree, "remove the obsolete path"),
    )
    assert isinstance(ready, TransferReady) and ready.externally_resolved
    assert not (ready.path / "gone").exists()
    assert (ready.path / "prefix").read_text() == "applied before the first conflict\n"
    assert git(ready.path, "rev-list", "--count", f"{b}..HEAD") == "3"
    assert git(source, "rev-parse", "HEAD") == h
    assert git(product, "rev-parse", "HEAD") == b


def test_source_non_linear_and_missing_input_are_unavailable(repo, owner):
    product, source, a = repo
    (product / "upstream").write_text("advance\n")
    b = commit(product, "advance")
    (source / "work").write_text("worker edit\n")
    commit(source, "worker edit")
    git(source, "merge", "--no-ff", "-m", "not linear", b)
    h = git(source, "rev-parse", "HEAD")
    invalid = owner(h, b).prepare()
    assert isinstance(invalid, TransferUnavailable)
    assert "source lineage" in invalid.reason
    assert git(source, "rev-parse", "HEAD") == h
    missing = TransferIdentity("owner", "T-1", "a" * 64, "c" * 64, a, h, "1" * 40)
    result = owner(h, b, identity=missing).prepare()
    assert isinstance(result, TransferUnavailable) and missing.target in result.reason


def test_concurrent_checkout_change_is_never_a_mechanical_result(repo, owner):
    product, source, _ = repo
    (source / "work").write_text("worker edit\n")
    h = commit(source, "worker edit")
    (product / "upstream").write_text("advance\n")
    b = commit(product, "advance")

    foreign = None

    def replace_child(cwd, *args, **kwargs):
        nonlocal foreign
        result = run_git(cwd, *args, **kwargs)
        if "cherry-pick" in args:
            assert result.returncode == 0
            git(cwd, "reset", "--hard", b)
            (cwd / "foreign").write_text("foreign edit\n")
            commit(cwd, "concurrent foreign child of B")
            foreign = cwd
        return result

    transfer = owner(h, b, runner=replace_child)
    result = transfer.prepare()
    assert isinstance(result, TransferInterrupted) and "attributable" in result.reason
    clean = owner(h, b).prepare()
    assert isinstance(clean, TransferReady) and clean.path != foreign
    assert git(clean.path, "show", "HEAD:work") == "worker edit"
    assert not (clean.path / "foreign").exists()
    transfer.release(clean, require_consumed=lambda _: None)
    assert git(foreign, "show", "HEAD:foreign") == "foreign edit"
    assert git(source, "rev-parse", "HEAD") == h


def test_uncertain_conflict_and_post_authorization_edits_are_preserved(
    owner, conflict_input,
):
    h, b, _ = conflict_input

    abandoned = None

    def interrupt_pick(cwd, *args, **kwargs):
        nonlocal abandoned
        result = run_git(cwd, *args, **kwargs)
        if "cherry-pick" in args:
            assert result.returncode != 0
            abandoned = cwd
            raise Crash
        return result

    transfer = owner(h, b, runner=interrupt_pick)
    with pytest.raises(Crash):
        transfer.prepare()
    assert "<<<<<<<" in (abandoned / "common").read_text()
    (abandoned / "common").write_text("external edits to abandoned conflict\n")
    published = owner(h, b).prepare()
    assert isinstance(published, TransferConflict) and published.path != abandoned
    assert "<<<<<<<" in (published.path / "common").read_text()
    assert (abandoned / "common").read_text() == (
        "external edits to abandoned conflict\n"
    )

    identity = replace(transfer.identity, attempt="c" * 64)
    transfer = owner(h, b, identity=identity)
    conflict = transfer.prepare()
    assert isinstance(conflict, TransferConflict), conflict
    edit_resolution(conflict)
    tree = transfer.stage_resolution(conflict, ("common", "explanation"))

    def interrupt_checkpoint(cwd, *args, **kwargs):
        if "hash-object" in args and "-w" in args:
            raise Crash
        return run_git(cwd, *args, **kwargs)

    interrupted = owner(h, b, identity=identity, runner=interrupt_checkpoint)
    with pytest.raises(Crash):
        interrupted.checkpoint_resolution(
            ResolutionAuthorization(conflict, tree, "approved exact tree"),
        )
    extra = conflict.path / "explanation"
    extra.write_text("new resolvers' edits\n")
    git(conflict.path, "add", "explanation")
    reopened = owner(h, b, identity=identity).prepare()
    assert isinstance(reopened, TransferInterrupted)
    assert "tree changed" in reopened.reason
    assert extra.read_text() == "new resolvers' edits\n"
    assert git(conflict.path, "rev-parse", "HEAD") == conflict.parent
