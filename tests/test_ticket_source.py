# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Ticket packaging uses disposable real repositories, never host control state."""

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import zipfile
from pathlib import Path

import pytest

from devlegate.execution_result import build_execution_report
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import run_git
from devlegate.resource_binding import ResourceBinding, UnprovenResource
from devlegate.runtime_locator import RuntimeAuthorityPresent, RuntimeLocator
from devlegate.worker_egress import WorkerClaim
from devlegate.worker_supervisor import WorkerRunResult
from tools import arch_format
from tools import ticket_source as source
from tools.build_distribution import parser


def commit(repo, message):
    run_git(repo, "add", "--all")
    run_git(repo, "commit", "-m", message)
    return source.text(repo, "rev-parse", "HEAD")


@pytest.fixture
def isolated_git(tmp_path, monkeypatch):
    # source.git intentionally strips inherited GIT_* variables. Isolate at
    # the executable boundary so its resource manager and selected builders,
    # as well as run_git, all execute real Git without host config policy.
    real_git = shutil.which("git")
    assert real_git is not None
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    wrapper = bin_dir / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        "export GIT_CONFIG_NOSYSTEM=1\n"
        "export GIT_CONFIG_SYSTEM=/dev/null\n"
        "export GIT_CONFIG_GLOBAL=/dev/null\n"
        f"exec {shlex.quote(real_git)} \"$@\"\n"
    )
    wrapper.chmod(0o755)
    for name in tuple(os.environ):
        if name.startswith("GIT_CONFIG_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("PATH", f"{bin_dir}{os.pathsep}{os.environ['PATH']}")
    return real_git


@pytest.fixture
def ticket(tmp_path, monkeypatch, isolated_git):
    # Selected builders execute a separate (and potentially older) source
    # identity which is deleted after capture. Do not attribute their lines to
    # this checkout's coverage or retain references to removed private sources.
    for name in ("COVERAGE_PROCESS_CONFIG", "COVERAGE_PROCESS_START"):
        monkeypatch.delenv(name, raising=False)
    repo = tmp_path / "repo"
    repo.mkdir()
    run_git(repo, "init", "-b", "main")
    run_git(repo, "config", "user.name", "Test")
    run_git(repo, "config", "user.email", "test@example.invalid")
    (repo / ".gitignore").write_text("cache/\n.env\ndist/\n")
    (repo / "source.py").write_text("original\n")
    (repo / "pyproject.toml").write_text('[project]\nversion="0.5.7.dev0"\n')
    (repo / "kanban/todo").mkdir(parents=True)
    (repo / "kanban/todo/TASK-001.md").write_text("ticket specification\n")
    tools = repo / "tools"
    tools.mkdir()
    for name in ("source_preflight.py", "build_standalone.py", "package_deb.py",
                 "package_standalone.py", "validate_standalone_package.py",
                 "validate_arch.py"):
        (tools / name).write_text("# fixture component\n")
    shutil.copy2(Path(__file__).parents[1] / "tools/arch_format.py",
                 tools / "arch_format.py")
    builder = (
        "from pathlib import Path\n"
        "import tempfile, shutil\n"
        "from tools.arch_format import build\n"
        "IDENTITY = 'caller-old'\n"
        "def package(repo, output):\n"
        " with tempfile.TemporaryDirectory() as temporary:\n"
        "  root=Path(temporary)\n"
        "  (root/'.PKGINFO').write_text('pkgname = fixture\\n')\n"
        "  for f in repo.glob('*.py'): shutil.copy2(f, root/f.name)\n"
        "  (root/'builder').write_text(IDENTITY)\n"
        "  build(root, output/'fixture.pkg.tar.zst', timestamp=0)\n"
    )
    (tools / "package_arch.py").write_text(builder)
    # A legacy selected dispatcher deliberately has no --ticket argument.
    # Its artifact contains both selected source bytes and its format module.
    (tools / "build_distribution.py").write_text(
        "import argparse\n"
        "from pathlib import Path\n"
        "from tools.package_arch import package\n"
        "GRAPH={'wheel': (), 'standalone': ('wheel',), "
        "'arch': ('standalone',), 'deb': ('standalone',)}\n"
        "p=argparse.ArgumentParser()\n"
        "p.add_argument('target')\n"
        "p.add_argument('--repo', type=Path)\n"
        "p.add_argument('--output-dir', type=Path)\n"
        "p.add_argument('--keep-work', action='store_true')\n"
        "a=p.parse_args()\n"
        "package(a.repo, a.output_dir)\n"
    )
    base = commit(repo, "base")
    run_git(repo, "branch", "devlegate/control", base)
    remote = tmp_path / "remote.git"
    run_git(repo, "init", "--bare", str(remote))
    run_git(repo, "remote", "add", "origin", str(remote))
    config = {"STATE_DIR": str(tmp_path / "state")}
    (repo / ".env").write_text(f"STATE_DIR={config['STATE_DIR']}\n")
    locator = RuntimeLocator.from_config(repo / ".env", repo, config)
    root = locator.state_dir / "worktrees" / locator.state_key
    manager = ExecutionWorkspaceManager(repo, root, "TASK-001")
    binding = ResourceBinding.plan(
        manager, execution_id="execution-one", base=base, control=base,
        ticket_blob=source.text(repo, "rev-parse", "HEAD:kanban/todo/TASK-001.md"),
        remote="origin", remote_head=None,
    )
    binding.record_intent()
    binding.prepare(remote_head=None)
    binding.begin_operation("worker", {})
    binding.finish_operation("worker", {"identity": {"execution_id": "execution-one"}})
    binding.record_worker_retirement()
    binding.begin_operation("checkpoint", {"before": base})
    (manager.path / "source.py").write_text("ticket code\n")
    (manager.path / "tools/package_arch.py").write_text(
        builder.replace("caller-old", "ticket-new")
    )
    head = commit(manager.path, "ticket code and builder")
    binding.finish_operation("checkpoint", {"after": head})
    monkeypatch.chdir(repo)
    return repo, config, locator, binding


def select(ticket):
    repo, config, locator, _binding = ticket
    return source.resolve(repo, config, locator, "TASK-001")


def unchanged(binding):
    repo = binding.manager.repo
    return (
        source.git(repo, "show-ref").stdout,
        source.git(binding.manager.path, "status", "--porcelain=v1").stdout,
        (binding._admin() / "index").read_bytes(),
        {path.name: path.read_bytes() for path in binding.directory.iterdir()
         if path.is_file()},
    )


@pytest.mark.parametrize("scope", ["global", "system", "xdg"])
@pytest.mark.parametrize("driver", ["clean", "process"])
def test_real_git_fixture_ignores_host_filters(
    ticket, isolated_git, tmp_path, monkeypatch, scope, driver,
):
    repo, _config, _locator, binding = ticket
    home = tmp_path / "host-home"
    home.mkdir()
    xdg = home / "config"
    (xdg / "git").mkdir(parents=True)
    config = {
        "global": home / ".gitconfig",
        "system": home / "system.gitconfig",
        "xdg": xdg / "git/config",
    }[scope]
    config.write_text(f'[filter "host"]\n\t{driver} = false\n')
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg))
    monkeypatch.setenv("GIT_CONFIG_SYSTEM", str(home / "system.gitconfig"))
    monkeypatch.delenv("GIT_CONFIG_GLOBAL", raising=False)
    monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    # Prove the real host configuration is hostile, then query both actual
    # runners. Merely setting GIT_CONFIG_* in pytest would fail this check for
    # source.git, which discards those variables before starting Git.
    host = subprocess.run(
        [isolated_git, "-C", str(repo), "config", "--get", f"filter.host.{driver}"],
        capture_output=True, text=True, check=True,
    )
    assert host.stdout.strip() == "false"
    for runner in (run_git, source.git, binding.manager._runner):
        result = runner(repo, "config", "--get-regexp",
                        r"^filter\..*\.(clean|process)$", check=False)
        assert result.returncode == 1
        assert result.stdout == ""
    before = unchanged(binding)
    selected = select(ticket)
    assert selected.live == binding.manager.path
    assert unchanged(binding) == before


@pytest.mark.parametrize("driver", ["clean", "process"])
def test_project_filter_policy_still_fails_closed(ticket, driver):
    binding = ticket[-1]
    run_git(binding.manager.path, "config", f"filter.project.{driver}", "false")
    before = unchanged(binding)
    with pytest.raises(UnprovenResource, match="configured Git clean/process filters"):
        select(ticket)
    assert unchanged(binding) == before


def test_clean_ticket_dispatches_selected_legacy_toolchain(ticket, tmp_path):
    repo, _config, _locator, binding = ticket
    before = unchanged(binding)
    args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                              keep_work=False, python=sys.executable)
    assert source.package_ticket(args, repo) == 0
    output = next((repo / "dist/tickets/TASK-001").iterdir())
    artifact = output / "fixture.pkg.tar.zst"
    members = arch_format.members(artifact)
    assert members["builder"] == b"ticket-new"
    assert members["source.py"] == b"ticket code\n"
    extracted = tmp_path / "extracted"
    arch_format.extract(artifact, extracted)
    arch_format.validate_mtree(extracted)
    report = json.loads((output / "devlegate-arch-ticket-source.json").read_text())
    assert report["source_head"] == select(ticket).head
    assert report["build_head"] == report["source_head"]
    assert report["wip_digest"] is None
    assert report["synthetic_build_only"] is False
    assert report["artifacts"][artifact.name] == hashlib.sha256(
        artifact.read_bytes()
    ).hexdigest()
    assert len(report["toolchain_identity"]) == 64
    assert unchanged(binding) == before


def test_wip_snapshot_captures_bytes_modes_and_new_files(ticket, tmp_path):
    _repo, _config, _locator, binding = ticket
    live = binding.manager.path
    (live / "source.py").write_text("staged\n")
    run_git(live, "add", "source.py")
    (live / "source.py").write_text("unstaged actual bytes\n")
    (live / "new.py").write_text("new executable\n")
    (live / "new.py").chmod(0o755)
    (live / "cache").mkdir()
    (live / "cache/secret").write_text("ignored\n")
    before = unchanged(binding)
    target = tmp_path / "private"
    report = source.capture(select(ticket), target)
    assert (target / "source.py").read_text() == "unstaged actual bytes\n"
    assert (target / "new.py").stat().st_mode & 0o111
    assert not (target / "cache").exists()
    assert report["source_kind"] == "wip-snapshot"
    assert report["synthetic_build_only"] is True
    assert report["source_head"] != report["build_head"]
    message = source.text(target, "log", "-1", "--format=%B")
    assert "Synthetic build-only" in message
    assert report["wip_digest"] in message
    assert not source.git(target, "status", "--porcelain").stdout
    assert unchanged(binding) == before
    repeated = source.capture(select(ticket), tmp_path / "second")
    assert repeated["wip_digest"] == report["wip_digest"]
    assert repeated["build_head"] == report["build_head"]


@pytest.mark.parametrize("fault", ["active", "uncertain", "foreign", "missing"])
def test_resolution_rejects_unproved_resources_without_mutation(ticket, fault):
    _repo, _config, _locator, binding = ticket
    if fault == "active":
        next(binding.directory.glob("worker-retired.*.json")).unlink()
    elif fault == "uncertain":
        binding.begin_operation("publication", {"before": None})
    elif fault == "foreign":
        (binding._admin() / "devlegate-generation.json").write_text("{}")
    else:
        (binding.directory / "receipt.json").unlink()
    before = unchanged(binding)
    with pytest.raises((ExecutionWorkspaceError, FileNotFoundError, ValueError)):
        select(ticket)
    assert unchanged(binding) == before


def test_changing_source_fails_capture(ticket, tmp_path, monkeypatch):
    binding = ticket[-1]
    original = source.file_bytes
    changed = False

    def changing(root, name):
        nonlocal changed
        value = original(root, name)
        if root == binding.manager.path and name == "source.py" and not changed:
            changed = True
            (root / name).write_text("changed during capture\n")
        return value

    monkeypatch.setattr(source, "file_bytes", changing)
    with pytest.raises(source.TicketSourceError, match="changed during capture"):
        source.capture(select(ticket), tmp_path / "private")


def test_changing_index_without_status_change_fails_capture(
    ticket, tmp_path, monkeypatch,
):
    binding = ticket[-1]
    selection = select(ticket)
    original = source.file_bytes
    changed = False

    def changing(root, name):
        nonlocal changed
        value = original(root, name)
        if root == binding.manager.path and name == "source.py" and not changed:
            changed = True
            # An index-only write can preserve porcelain status and source bytes.
            run_git(root, "update-index", "--assume-unchanged", "source.py")
        return value

    monkeypatch.setattr(source, "file_bytes", changing)
    with pytest.raises(source.TicketSourceError, match="worktree/index changed"):
        source.capture(selection, tmp_path / "private")


def test_unresolved_index_is_not_silently_resolved_for_packaging(ticket, tmp_path):
    live = ticket[-1].manager.path
    head = source.text(live, "rev-parse", "HEAD:source.py")
    run_git(live, "update-index", "--index-info", input_text=(
        f"0 {'0' * 40}\tsource.py\n"
        f"100644 {head} 1\tsource.py\n"
        f"100644 {head} 2\tsource.py\n"
    ))
    before = unchanged(ticket[-1])
    with pytest.raises(source.TicketSourceError, match="unresolved conflicts"):
        source.capture(select(ticket), tmp_path / "private")
    assert unchanged(ticket[-1]) == before


@pytest.mark.parametrize("executable", [False, True])
def test_staged_only_executable_mode_is_refused_without_mutation(ticket, executable):
    repo, _config, _locator, binding = ticket
    live = binding.manager.path
    path = live / "source.py"
    if executable:
        binding.begin_operation("checkpoint", {"before": select(ticket).head})
        path.chmod(0o755)
        head = commit(live, "executable source")
        binding.finish_operation("checkpoint", {"after": head})
    run_git(live, "update-index", "--chmod=-x" if executable else "--chmod=+x",
            "source.py")
    before = unchanged(binding)
    before_file = (path.read_bytes(), path.stat().st_mode)
    args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                              keep_work=False, python=sys.executable)
    with pytest.raises(source.TicketSourceError, match="align the index.*executable"):
        source.package_ticket(args, repo)
    assert unchanged(binding) == before
    assert (path.read_bytes(), path.stat().st_mode) == before_file
    assert not (repo / "dist").exists()


def test_staged_executable_mode_matching_working_file_is_wip(ticket, tmp_path):
    binding = ticket[-1]
    live = binding.manager.path
    (live / "source.py").chmod(0o755)
    run_git(live, "add", "source.py")
    before = unchanged(binding)
    target = tmp_path / "private"
    report = source.capture(select(ticket), target)
    assert report["source_kind"] == "wip-snapshot"
    assert report["synthetic_build_only"] is True
    assert report["wip_digest"]
    assert source.text(target, "ls-files", "--stage", "source.py").startswith("100755 ")
    assert (target / "source.py").stat().st_mode & 0o111
    assert unchanged(binding) == before


def test_unsafe_symlink_fails_capture(ticket, tmp_path):
    (ticket[-1].manager.path / "new.py").symlink_to(tmp_path / "outside-secret")
    with pytest.raises((ExecutionWorkspaceError, ValueError)):
        source.capture(select(ticket), tmp_path / "private")


def test_retired_worktree_uses_exact_receipted_checkpoint(ticket, tmp_path):
    binding = ticket[-1]
    expected = select(ticket).head
    run_git(binding.manager.repo, "worktree", "remove", str(binding.manager.path))
    run_git(binding.manager.repo, "branch", "-D", binding.manager.branch)
    selection = select(ticket)
    assert selection.live is None
    assert selection.head == expected
    report = source.capture(selection, tmp_path / "private")
    assert report["source_head"] == expected
    assert (tmp_path / "private/source.py").read_text() == "ticket code\n"


def test_wrong_project_and_ticket_are_rejected(ticket, tmp_path, monkeypatch):
    repo, config, locator, _binding = ticket
    with pytest.raises(ValueError, match="missing or ambiguous"):
        source.resolve(repo, config, locator, "TASK-999")
    other = tmp_path / "foreign"
    other.mkdir()
    run_git(other, "init")
    monkeypatch.chdir(other)
    with pytest.raises(ValueError, match="within this Devlegate project"):
        source.project(repo)


def publish_report(ticket):
    repo, _config, _locator, binding = ticket
    report = build_execution_report(
        execution_id=str(binding.intent["execution_id"]), ticket_id="TASK-001",
        code_base_head=str(binding.intent["base"]),
        control_head=str(binding.intent["control"]),
        execution_branch=binding.manager.branch,
        execution_path=str(binding.manager.path), workspace_head=select(ticket).head,
        run=WorkerRunResult(0, None, WorkerClaim("completed", "fixture", (), ()), None),
    )
    report = dataclasses.replace(
        report, resource_generation=str(binding.intent["generation"])
    )
    control = repo.parent / "control"
    run_git(repo, "worktree", "add", str(control), "devlegate/control")
    directory = control / "executions/TASK-001"
    directory.mkdir(parents=True)
    (directory / f"{report.execution_id}.json").write_text(report.to_json())
    (control / "kanban/review").mkdir()
    (control / "kanban/todo/TASK-001.md").rename(
        control / "kanban/review/TASK-001.md"
    )
    commit(control, "atomic fixture report and review admission")
    return report, control


def retire(ticket):
    binding = ticket[-1]
    run_git(binding.manager.repo, "worktree", "remove", str(binding.manager.path))
    run_git(binding.manager.repo, "branch", "-D", binding.manager.branch)


def test_immutable_history_fallback_after_integration_and_retirement(ticket, tmp_path):
    report, control = publish_report(ticket)
    binding = ticket[-1]
    retire(ticket)
    shutil.rmtree(binding.directory)
    (control / "kanban/done").mkdir()
    (control / "kanban/review/TASK-001.md").rename(control / "kanban/done/TASK-001.md")
    commit(control, "integrated and done")
    selection = select(ticket)
    assert selection.head == report.workspace_head
    assert selection.live is None
    source.capture(selection, tmp_path / "private")
    assert (tmp_path / "private/source.py").read_text() == "ticket code\n"
    assert source.text(ticket[0], "rev-parse", "HEAD") != selection.head


@pytest.mark.parametrize("missing", ["object", "report"])
def test_never_substitutes_product_when_latest_evidence_is_unavailable(ticket, missing):
    report, control = publish_report(ticket)
    binding = ticket[-1]
    retire(ticket)
    shutil.rmtree(binding.directory)
    if missing == "object":
        common = ticket[0] / ".git/objects"
        head = report.workspace_head
        (common / head[:2] / head[2:]).unlink()
    else:
        (control / f"executions/TASK-001/{report.execution_id}.json").unlink()
        commit(control, "lost execution evidence")
    with pytest.raises(ValueError, match="Git observation failed|evidence was removed"):
        select(ticket)


def test_ambiguous_generations_are_rejected(ticket):
    binding = ticket[-1]
    other = ResourceBinding(binding.manager, {
        **binding.intent, "generation": "a" * 64, "execution_id": "other",
    })
    shutil.copytree(binding.directory, other.directory)
    # Duplicate independent evidence with a different generation is not ordered
    # by UUID, filename, status or timestamp. Both remain independently settled.
    for path in other.directory.glob("*.json"):
        payload = json.loads(path.read_text())
        serialized = json.dumps(payload).replace(
            str(binding.intent["generation"]), "a" * 64
        ).replace("execution-one", "other")
        path.write_text(serialized)
    with pytest.raises(ValueError, match="ambiguous ticket generations"):
        select(ticket)


def test_parallel_publications_cannot_be_ordered_by_git_log_dates(ticket):
    report, control = publish_report(ticket)
    base = str(ticket[-1].intent["control"])
    run_git(control, "checkout", "-b", "parallel-control", base)
    other = dataclasses.replace(report, execution_id="parallel-execution")
    directory = control / "executions/TASK-001"
    directory.mkdir(parents=True)
    (directory / f"{other.execution_id}.json").write_text(other.to_json())
    commit(control, "independent execution publication")
    run_git(control, "checkout", "devlegate/control")
    run_git(control, "merge", "--no-edit", "parallel-control")
    before = unchanged(ticket[-1])
    with pytest.raises(ValueError, match="ambiguous latest execution publication"):
        select(ticket)
    assert unchanged(ticket[-1]) == before


def test_parser_distinguishes_implicit_and_explicit_repo():
    assert not parser().parse_args(["arch", "--ticket", "TASK-001"]).repo_explicit
    for arguments in (["--repo", "/repo", "arch", "--ticket", "TASK-001"],
                      ["arch", "--ticket", "TASK-001", "--repo=/repo"]):
        assert parser().parse_args(arguments).repo_explicit


def test_explicit_repo_conflicts_with_ticket_before_resolution(monkeypatch, capsys):
    from tools.build_distribution import main

    monkeypatch.setattr(sys, "argv", ["package", "arch", "--repo", "/missing",
                                    "--ticket", "TASK-001"])
    assert main() == 1
    assert "mutually exclusive" in capsys.readouterr().err


def test_owner_authority_prevents_capture(ticket):
    repo, _config, locator, binding = ticket
    locator.lock_path.parent.mkdir(parents=True)
    before = unchanged(binding)
    with locator.lock_path.open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                                  keep_work=False, python=sys.executable)
        with pytest.raises(RuntimeAuthorityPresent):
            source.package_ticket(args, repo)
    assert unchanged(binding) == before


def test_incompatible_selected_toolchain_has_actionable_refusal(ticket):
    repo, _config, _locator, binding = ticket
    (binding.manager.path / "tools/build_distribution.py").write_text("# old graph\n")
    before = unchanged(binding)
    args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                              keep_work=False, python=sys.executable)
    with pytest.raises(ValueError, match="update the ticket source"):
        source.package_ticket(args, repo)
    assert unchanged(binding) == before


@pytest.mark.parametrize("path", ["runtime.sqlite", "runtime.log", ".devlegate/state"])
def test_untracked_runtime_state_is_not_snapshotted(ticket, tmp_path, path):
    live = ticket[-1].manager.path
    private = live / path
    private.parent.mkdir(parents=True, exist_ok=True)
    private.write_text("private state\n")
    with pytest.raises(ValueError, match="private/runtime"):
        source.capture(select(ticket), tmp_path / "private")


def test_selected_real_wheel_graph_captures_wip_and_validates_payload(ticket, tmp_path):
    repo, _config, _locator, binding = ticket
    live = binding.manager.path
    root = Path(__file__).parents[1]
    for directory in ("src", "tools", "LICENSES", "packaging"):
        shutil.copytree(root / directory, live / directory, dirs_exist_ok=True,
                        ignore=shutil.ignore_patterns(".git", "__pycache__"))
    for name in ("pyproject.toml", "README.md", "LICENSE", "NOTICE", "LICENSING.md"):
        shutil.copy2(root / name, live / name)
    (live / ".gitignore").write_text("cache/\n.env\ndist/\n__pycache__/\n")
    before_head = source.text(live, "rev-parse", "HEAD")
    binding.begin_operation("checkpoint", {"before": before_head})
    head = commit(live, "real selected development package graph")
    binding.finish_operation("checkpoint", {"after": head})
    (live / "src/devlegate/dogfood_fixture.py").write_text("VALUE = 'ticket WIP'\n")
    before = unchanged(binding)
    args = argparse.Namespace(ticket="TASK-001", target="wheel",
                              output_dir=tmp_path / "output", keep_work=False,
                              python=sys.executable)
    assert source.package_ticket(args, repo) == 0
    wheel = next(args.output_dir.glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        assert archive.read("devlegate/dogfood_fixture.py") == b"VALUE = 'ticket WIP'\n"
        assert "devlegate/default_templates/.env.example" in archive.namelist()
    report = json.loads(
        (args.output_dir / "devlegate-wheel-ticket-source.json").read_text()
    )
    assert report["source_head"] == head
    assert report["synthetic_build_only"] is True
    assert unchanged(binding) == before


def add_submodule(ticket, tmp_path):
    binding = ticket[-1]
    sub = tmp_path / "sub"
    sub.mkdir()
    run_git(sub, "init", "-b", "main")
    run_git(sub, "config", "user.name", "Test")
    run_git(sub, "config", "user.email", "test@example.invalid")
    (sub / "module.py").write_text("pinned module\n")
    pin = commit(sub, "module pin")
    live = binding.manager.path
    binding.begin_operation("checkpoint", {"before": select(ticket).head})
    run_git(live, "-c", "protocol.file.allow=always", "submodule", "add",
            "--name", "different-module-name", str(sub), "vendor/module")
    head = commit(live, "pinned submodule")
    binding.finish_operation("checkpoint", {"after": head})
    return pin


@pytest.mark.parametrize("staged", ["blob", "deletion"])
def test_staged_changes_with_head_working_bytes_are_refused_without_mutation(
    ticket, tmp_path, staged,
):
    pin = add_submodule(ticket, tmp_path)
    repo, _config, _locator, binding = ticket
    live = binding.manager.path
    path = live / "source.py"
    original = path.read_bytes()
    if staged == "blob":
        path.write_text("staged bytes absent from working tree\n")
        run_git(live, "add", "source.py")
        assert source.text(live, "rev-parse", ":source.py") != source.text(
            live, "rev-parse", "HEAD:source.py"
        )
    else:
        run_git(live, "rm", "source.py")
        assert not source.git(live, "ls-files", "--stage", "source.py").stdout
    path.write_bytes(original)
    before = unchanged(binding)
    before_file = (path.read_bytes(), path.stat().st_mode)
    sub = live / "vendor/module"

    def submodule_state():
        return (
            source.text(sub, "rev-parse", "HEAD"),
            source.git(sub, "show-ref").stdout,
            source.git(sub, "status", "--porcelain=v1").stdout,
            Path(source.text(sub, "rev-parse", "--path-format=absolute",
                             "--git-path", "index")).read_bytes(),
            (sub / "module.py").read_bytes(),
        )

    before_sub = submodule_state()
    assert before_sub[0] == pin
    args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                              keep_work=False, python=sys.executable)
    with pytest.raises(source.TicketSourceError,
                       match="align the index and working files"):
        source.package_ticket(args, repo)
    assert unchanged(binding) == before
    assert (path.read_bytes(), path.stat().st_mode) == before_file
    assert submodule_state() == before_sub
    assert not (repo / "dist").exists()


def test_staged_only_gitlink_change_is_refused_without_mutation(ticket, tmp_path):
    pin = add_submodule(ticket, tmp_path)
    repo, _config, _locator, binding = ticket
    live = binding.manager.path
    sub = live / "vendor/module"
    (sub / "module.py").write_text("new pin\n")
    next_pin = commit(sub, "next submodule pin")
    run_git(sub, "checkout", "--detach", pin)
    run_git(live, "update-index", "--cacheinfo", f"160000,{next_pin},vendor/module")
    before = unchanged(binding)
    before_sub = (
        source.text(sub, "rev-parse", "HEAD"),
        source.git(sub, "show-ref").stdout,
        source.git(sub, "status", "--porcelain=v1").stdout,
        Path(source.text(sub, "rev-parse", "--path-format=absolute",
                         "--git-path", "index")).read_bytes(),
        (sub / "module.py").read_bytes(),
    )
    args = argparse.Namespace(ticket="TASK-001", target="arch", output_dir=None,
                              keep_work=False, python=sys.executable)
    with pytest.raises(source.TicketSourceError, match="restore the index.*HEAD pins"):
        source.package_ticket(args, repo)
    assert unchanged(binding) == before
    assert (
        source.text(sub, "rev-parse", "HEAD"),
        source.git(sub, "show-ref").stdout,
        source.git(sub, "status", "--porcelain=v1").stdout,
        Path(source.text(sub, "rev-parse", "--path-format=absolute",
                         "--git-path", "index")).read_bytes(),
        (sub / "module.py").read_bytes(),
    ) == before_sub
    assert not (repo / "dist").exists()


def test_snapshot_preserves_exact_pinned_submodule(ticket, tmp_path, monkeypatch):
    pin = add_submodule(ticket, tmp_path)
    (ticket[-1].manager.path / "source.py").write_text("WIP with pinned submodule\n")
    target = tmp_path / "private"
    source.capture(select(ticket), target)
    assert source.text(target / "vendor/module", "rev-parse", "HEAD") == pin
    assert (target / "vendor/module/module.py").read_text() == "pinned module\n"
    assert "vendor/module/module.py" in source.git(
        target, "ls-files", "--recurse-submodules"
    ).stdout.splitlines()
    assert not source.git(target, "status", "--porcelain").stdout
    from tools.build_full_source import checkout_source

    # Even the selected graph's fresh recursive clone must use captured local
    # objects, rather than going back to a network source or a live worktree.
    for key, value in source.local_submodule_config(target).items():
        monkeypatch.setenv(key, value)
    cloned = tmp_path / "full-source-clone"
    checkout_source(target, source.text(target, "rev-parse", "HEAD"), cloned)
    assert (cloned / "vendor/module/module.py").read_text() == "pinned module\n"


@pytest.mark.parametrize("fault", ["dirty", "missing", "stale", "symlink"])
def test_unsafe_submodule_is_preserved_and_refused(ticket, tmp_path, fault):
    add_submodule(ticket, tmp_path)
    sub = ticket[-1].manager.path / "vendor/module"
    if fault == "dirty":
        (sub / "module.py").write_text("dirty module\n")
    elif fault == "missing":
        sub.rename(sub.with_name("absent"))
    elif fault == "stale":
        (sub / "module.py").write_text("stale pin\n")
        commit(sub, "another pin")
    else:
        original = sub.with_name("original")
        sub.rename(original)
        sub.symlink_to(original, target_is_directory=True)
    before = unchanged(ticket[-1])
    with pytest.raises((ValueError, ExecutionWorkspaceError, OSError)):
        source.capture(select(ticket), tmp_path / "private")
    assert unchanged(ticket[-1]) == before
