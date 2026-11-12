# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real Git effects, including deliberately interrupted first bootstrap."""

from subprocess import CompletedProcess

import pytest

from devlegate.control_git_workflow import (
    ControlGitWorkflowError,
    attach_existing_control,
    bootstrap_control,
)
from devlegate.git import GitObservationError, run_git

BRANCH = "devlegate/control"
REF = f"refs/heads/{BRANCH}"
PATHS = {name: f"workflow/{name}" for name in ("todo", "done")}
OLD_USAGE = (
    "usage: git worktree add [-f] [--detach] [--checkout] "
    "[--lock [--reason <string>]] [(-b | -B) <new-branch>] <path> [<commit-ish>]\n"
)


@pytest.fixture
def bootstrap_repo(tmp_path):
    repo = tmp_path / "product"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Bootstrap Test")
    run_git(repo, "config", "user.email", "bootstrap@example.test")
    (repo / "product.txt").write_text("product\n")
    run_git(repo, "add", ".")
    run_git(repo, "commit", "-m", "product")
    remote = tmp_path / "remote.git"
    run_git(tmp_path, "init", "--bare", str(remote))
    run_git(repo, "remote", "add", "origin", str(remote))
    run_git(repo, "push", "origin", "main")
    return dict(
        repo=repo, control_worktree=tmp_path / "control", control_branch=BRANCH,
        remote_name="origin", workflow_paths=PATHS,
    )


def old_runner(cwd, *args, **kwargs):
    """Use real Git for every effect; inject only lack of native support."""
    if args[:3] == ("worktree", "add", "--orphan"):
        return CompletedProcess(args, 129, "", "opaque localized diagnostic")
    if args == ("worktree", "-h"):
        return CompletedProcess(args, 129, "", OLD_USAGE)
    return run_git(cwd, *args, **kwargs)


def product_snapshot(repo):
    return tuple(run_git(repo, *args).stdout for args in (
        ("rev-parse", "HEAD"), ("symbolic-ref", "HEAD"),
        ("ls-files", "--stage"), ("status", "--porcelain"),
        ("ls-remote", "origin", "refs/heads/main"),
    ))


@pytest.mark.parametrize("compat", ["native", "simulated", "installed"])
def test_real_bootstrap_lifecycle(bootstrap_repo, compat):
    options = bootstrap_repo
    repo, control = options["repo"], options["control_worktree"]
    before = product_snapshot(repo)
    trace = []
    native = "--orphan" in run_git(repo, "worktree", "-h", check=False).stdout
    if compat == "native" and not native:
        pytest.skip("installed Git lacks native worktree --orphan")

    def runner(cwd, *args, **kwargs):
        trace.append(args)
        return (old_runner if compat == "simulated" else run_git)(cwd, *args, **kwargs)

    bootstrap_control(**options, runner=runner)
    head = run_git(control, "rev-parse", "HEAD").stdout.strip()
    parents = run_git(control, "rev-list", "--parents", "-n1", "HEAD").stdout.split()
    assert parents == [head]
    tree = run_git(control, "ls-tree", "-r", "--name-only", "HEAD").stdout
    assert set(tree.splitlines()) == {f"{path}/.gitkeep" for path in PATHS.values()}
    assert run_git(repo, "ls-remote", "origin", REF).stdout.split()[0] == head
    if compat == "simulated" or not native:
        assert ("read-tree", "--empty") in trace
    else:
        # On modern Git there is no capability probe or fallback effect.
        assert ("worktree", "-h") not in trace
        assert ("read-tree", "--empty") not in trace
    trace.clear()
    attached = attach_existing_control(**options, runner=runner)
    assert attached["result"] == "already_attached"
    assert ("worktree", "-h") not in trace
    run_git(repo, "worktree", "remove", str(control))
    run_git(repo, "branch", "-D", BRANCH)
    attached = attach_existing_control(**options, runner=runner)
    assert attached["result"] == "attached_existing_remote"
    assert run_git(control, "rev-parse", "HEAD").stdout.strip() == head
    assert product_snapshot(repo) == before
    assert (repo / "product.txt").read_text() == "product\n"


@pytest.mark.parametrize("point", ["detach", "symbolic", "empty", "commit", "publish"])
def test_fallback_interruption_preserves_or_resumes(bootstrap_repo, point):
    options = bootstrap_repo
    repo, control = options["repo"], options["control_worktree"]
    before = product_snapshot(repo)

    def interrupted(cwd, *args, **kwargs):
        result = old_runner(cwd, *args, **kwargs)
        matched = {
            "detach": args[:4] == ("worktree", "add", "--detach", "--no-checkout"),
            "symbolic": args == ("symbolic-ref", "HEAD", REF),
            "empty": args == ("read-tree", "--empty"),
            "commit": args[0] == "commit", "publish": args[0] == "push",
        }[point]
        if matched:
            assert not result.returncode
            raise InterruptedError(point)
        return result

    with pytest.raises(InterruptedError):
        bootstrap_control(**options, runner=interrupted)
    index = run_git(control, "ls-files", "--stage").stdout
    symbolic = run_git(control, "symbolic-ref", "-q", "HEAD", check=False).stdout
    if point == "detach":
        with pytest.raises(ControlGitWorkflowError, match="detached.*inspect"):
            bootstrap_control(**options, runner=old_runner)
        assert run_git(control, "ls-files", "--stage").stdout == index
        observed = run_git(control, "symbolic-ref", "-q", "HEAD", check=False)
        assert observed.stdout == symbolic
    elif point == "publish":
        attached = attach_existing_control(**options, runner=old_runner)
        assert attached["result"] == "already_attached"
    else:
        bootstrap_control(**options, runner=old_runner)
        parents = run_git(control, "rev-list", "--parents", "-n1", "HEAD").stdout
        assert len(parents.split()) == 1
    assert product_snapshot(repo) == before


@pytest.mark.parametrize(
    "kind", [
        "supported", "unknown-help", "path", "registration", "observation",
        "remote-error", "remote-ref", "local-ref",
    ]
)
def test_native_failure_is_not_fallback_authority(bootstrap_repo, kind):
    options = bootstrap_repo
    control = options["control_worktree"]
    trace = []

    def runner(cwd, *args, **kwargs):
        trace.append(args)
        if args[:3] == ("worktree", "add", "--orphan"):
            if kind == "path":
                control.mkdir()
                (control / "foreign").write_text("preserve\n")
            elif kind == "registration":
                run_git(
                    cwd, "worktree", "add", "--detach", "--no-checkout", str(control)
                )
                # Preserve registration, but make its target absent.
                (control / ".git").unlink()
                control.rmdir()
            return CompletedProcess(args, 128, "", "ordinary access failure")
        if args == ("worktree", "-h"):
            help_text = OLD_USAGE + (" [--orphan]" if kind == "supported" else "")
            if kind == "unknown-help":
                help_text = "unavailable"
            return CompletedProcess(args, 129, help_text, "")
        if kind == "observation" and args[:2] == ("worktree", "list"):
            return CompletedProcess(args, 128, "", "cannot observe")
        if kind == "remote-error" and args[0] == "ls-remote":
            return CompletedProcess(args, 128, "", "transport failed")
        if kind == "remote-ref" and args[0] == "ls-remote":
            head = run_git(cwd, "rev-parse", "HEAD").stdout.strip()
            return CompletedProcess(args, 0, f"{head}\t{REF}\n", "")
        if kind == "local-ref" and args == ("worktree", "list", "--porcelain"):
            run_git(cwd, "branch", BRANCH, "HEAD")
        return run_git(cwd, *args, **kwargs)

    with pytest.raises(ControlGitWorkflowError):
        bootstrap_control(**options, runner=runner)
    assert not any(args[:3] == ("worktree", "add", "--detach") for args in trace)
    if kind == "path":
        assert (control / "foreign").read_text() == "preserve\n"


def test_unborn_control_with_foreign_index_is_preserved(bootstrap_repo):
    options = bootstrap_repo
    repo, control = options["repo"], options["control_worktree"]
    run_git(repo, "worktree", "add", "--detach", "--no-checkout", str(control))
    run_git(control, "symbolic-ref", "HEAD", REF)
    run_git(control, "read-tree", "main")
    before = run_git(control, "ls-files", "--stage").stdout
    with pytest.raises(ControlGitWorkflowError, match="index.*skeleton"):
        bootstrap_control(**options, runner=old_runner)
    assert run_git(control, "ls-files", "--stage").stdout == before


@pytest.mark.parametrize(
    "ambiguity", ["contents", "symbolic-head", "index", "registration", "authority"]
)
def test_ambiguous_interrupted_fallback_is_not_recovery_authority(
    bootstrap_repo, ambiguity,
):
    options = bootstrap_repo
    repo, control = options["repo"], options["control_worktree"]

    def interrupt(cwd, *args, **kwargs):
        result = old_runner(cwd, *args, **kwargs)
        if args == ("read-tree", "--empty"):
            raise InterruptedError
        return result

    with pytest.raises(InterruptedError):
        bootstrap_control(**options, runner=interrupt)
    if ambiguity == "contents":
        (control / "foreign.txt").write_text("preserve me\n")
    if ambiguity == "symbolic-head":
        run_git(control, "symbolic-ref", "HEAD", "refs/heads/foreign")
    before = run_git(control, "ls-files", "--stage").stdout
    trace = []

    def denied(cwd, *args, **kwargs):
        trace.append(args)
        if ambiguity == "index" and args[0] == "ls-files":
            return CompletedProcess(args, 128, "", "unreadable index")
        if ambiguity == "registration" and args[:2] == ("worktree", "list"):
            return CompletedProcess(args, 128, "", "unreadable registration")
        if ambiguity == "authority":
            raise PermissionError("repository authority lost")
        return old_runner(cwd, *args, **kwargs)

    with pytest.raises((ControlGitWorkflowError, GitObservationError, PermissionError)):
        bootstrap_control(**options, runner=denied)
    assert not any(args[0] in {"add", "commit", "push", "read-tree"} for args in trace)
    assert run_git(control, "ls-files", "--stage").stdout == before
    if ambiguity == "contents":
        assert (control / "foreign.txt").read_text() == "preserve me\n"
    assert not run_git(repo, "ls-remote", "origin", REF).stdout
