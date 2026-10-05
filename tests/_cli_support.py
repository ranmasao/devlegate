# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F401, I001

"""Shared CLI fixtures and implementation-seam source.

Semantic ``test_cli_*`` modules own parser/bootstrap, IPC client, host
lifecycle, and production-topology selections. This module retains shared
subprocess and Git fixture ownership and is not itself a collection target.
"""

import dataclasses

import fcntl

import hashlib

import io

import json

import os

import shutil

import signal

import socket

import sqlite3

import subprocess

import sys

import threading

import time

from argparse import Namespace

from concurrent.futures import ThreadPoolExecutor

from contextlib import nullcontext, redirect_stdout

from pathlib import Path

import pytest

from git_support import clone_world

from runtime_helpers import run_test_iteration

from service_harness import LiveService

import devlegate.cli as cli

from devlegate import __version__

from devlegate._vendor import nanoyaml

from devlegate.cli import (
    Devlegate,
    DevlegateError,
    ReadOnlyView,
    build_parser,
    main,
)

from devlegate.host_installation import HostInstallation

from devlegate.host_installation import write as write_installation

from devlegate.ipc_client import IPCClientError

from devlegate.ipc_client import request as ipc_request

from devlegate.ipc_server import UnixIPCServer

from devlegate.project_registry import ProjectRegistry, ProjectTarget

from devlegate.runtime import (
    BlockedReason,
    ExecutionPlan,
    GitObservation,
    StatusSnapshot,
    _is_same_parent_replacement,
    _todo_fingerprint,
)

from devlegate.runtime_locator import RuntimeAuthorityPresent, RuntimeLocator

from devlegate.service import ServiceEngine

from devlegate.worker_egress import WorkerClaim, WorkerRunResult

def git(cwd, *args):
    return subprocess.run(
        ["git", *map(str, args)], cwd=cwd, text=True, capture_output=True, check=True
    )

def _make_git_fixture(tmp_path, request, short_state_dir, baseline):
    state = Path("/tmp") / (
        "devlegate-test-" + hashlib.sha256(str(tmp_path).encode()).hexdigest()[:8]
    )
    request.addfinalizer(lambda: shutil.rmtree(state, ignore_errors=True))
    world = clone_world(
        tmp_path,
        baseline=baseline,
        state=state,
    )
    bare = world["bare"]
    working = world["working"]

    key = hashlib.sha256(str(working.resolve()).encode()).hexdigest()
    control = state / "worktrees" / key / "control"
    git(working, "fetch", "origin", "devlegate/control")
    git(
        working,
        "worktree",
        "add",
        "--track",
        "-b",
        "devlegate/control",
        control,
        "origin/devlegate/control",
    )
    publisher_control = tmp_path / "publisher-control"

    config = working / ".env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\nPOLL_INTERVAL=0\n"
        f"STATE_DIR={state}\n"
    )
    (working / ".git" / "info" / "exclude").open("a").write(
        "\n*.env\n.registry-config/\n"
    )
    registry_home = tmp_path / "registry-config"
    previous_registry_home = os.environ.get("XDG_CONFIG_HOME")
    os.environ["XDG_CONFIG_HOME"] = str(registry_home)
    request.addfinalizer(
        lambda: (
            os.environ.__setitem__("XDG_CONFIG_HOME", previous_registry_home)
            if previous_registry_home is not None
            else os.environ.pop("XDG_CONFIG_HOME", None)
        )
    )
    write_installation(
        registry_home / "devlegate" / "installation.json",
        HostInstallation("internal"),
    )
    ProjectRegistry().register("test", config)
    return {
        "bare": bare,
        "working": working,
        "control": control,
        "publisher_control": publisher_control,
        "config": config,
        "state": state,
        "short_state": short_state_dir,
        "tmp": tmp_path,
    }

@pytest.fixture
def git_fixture(tmp_path, request, short_state_dir):
    return _make_git_fixture(tmp_path, request, short_state_dir, "empty-control")

@pytest.fixture
def cli_daemon(git_fixture, monkeypatch):
    """IPC server only; routing and views, not owner-side mutation coverage."""
    monkeypatch.chdir(git_fixture["working"])
    config = _short_runtime_config(git_fixture)
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        engine.repo,
        "worktree",
        "move",
        git_fixture["control"],
        engine.control_worktree,
    )
    with redirect_stdout(io.StringIO()):
        assert engine.control_init() == 0
    authority = engine._lock()
    engine.ensure_hosted_views()
    engine.published_status_payload = lambda: engine.status_view().as_dict()
    engine.published_plan_view = engine.plan_view
    retry_candidates = engine.retry_candidates_view
    engine.published_retry_candidates_view = retry_candidates
    drop_candidates = engine.drop_candidates_view
    engine.published_drop_candidates_view = drop_candidates
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        yield engine
    finally:
        server.stop()
        engine.end_hosted_owner()
        authority.close()

def _short_runtime_config(git_fixture):
    config = git_fixture["config"]
    config.write_text(
        git_fixture["config"]
        .read_text()
        .replace(str(git_fixture["state"]), str(git_fixture["short_state"]))
    )
    return config

@pytest.fixture
def plain_project_fixture(tmp_path):
    bare = tmp_path / "remote.git"
    seed = tmp_path / "seed"
    working = tmp_path / "working"
    state = tmp_path / "state"
    git(tmp_path, "init", "--bare", bare)
    git(tmp_path, "init", "-b", "main", seed)
    git(seed, "config", "user.email", "test@example.com")
    git(seed, "config", "user.name", "Test User")
    (seed / "README.md").write_text("project context\n")
    (seed / "docs").mkdir()
    (seed / "docs/architecture.md").write_text("architecture\n")
    git(seed, "add", ".")
    git(seed, "commit", "-m", "ordinary project")
    git(seed, "remote", "add", "origin", bare)
    git(seed, "push", "-u", "origin", "main")
    git(tmp_path, "clone", "-b", "main", bare, working)
    git(working, "config", "user.email", "test@example.com")
    git(working, "config", "user.name", "Test User")
    (working / ".git" / "info" / "exclude").open("a").write("\n.env\n")
    config = working / ".env"
    config.write_text(
        "REMOTE_BRANCH=main\nCONTROL_BRANCH=devlegate/control\n"
        "OPENCODE_BIN=true\nOPENCODE_MODEL=fake\nPOLL_INTERVAL=0\n"
        f"STATE_DIR={state}\n"
    )
    return {"bare": bare, "working": working, "config": config, "state": state}

def invoke(fixture, *args, env_file=None):
    args = list(args)
    if "--foreground" in args:
        args[args.index("--foreground")] = "foreground"
    elif "--once" in args:
        args[args.index("--once")] = "once"
    selected_env = env_file or fixture["config"]
    config_home = fixture.get("tmp", fixture["working"].parent) / "registry-config"
    if not args or args[0] != "init":
        alias = "test" if selected_env == fixture["config"] else selected_env.stem
        ProjectRegistry(config_home / "devlegate" / "projects.json").register(
            alias, selected_env
        )
    environment = {
        **os.environ,
        "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        "XDG_CONFIG_HOME": str(config_home),
    }
    if args[:2] not in (["host", "install"], ["host", "uninstall"]):
        write_installation(
            config_home / "devlegate" / "installation.json",
            HostInstallation("internal"),
        )
    command = [sys.executable, "-m", "devlegate", *args]
    if not args or args[0] != "init":
        command[3:3] = ["--env", str(selected_env)]
    return subprocess.run(
        command,
        cwd=fixture["working"],
        env=environment,
        text=True,
        capture_output=True,
    )

def publish_control(fixture, message, files, *, sync=True):
    publisher_control = _ensure_publisher_control(fixture)
    for relative, content in files.items():
        path = publisher_control / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    git(publisher_control, "add", ".")
    git(publisher_control, "commit", "-m", message)
    git(publisher_control, "push", "origin", "HEAD:devlegate/control")
    if sync:
        git(fixture["control"], "fetch", "origin", "devlegate/control")
        git(fixture["control"], "merge", "--ff-only", "origin/devlegate/control")

def _ensure_publisher_control(fixture):
    path = fixture["publisher_control"]
    if path.exists():
        return path
    publisher = _ensure_publisher(fixture)
    git(publisher, "fetch", "origin", "devlegate/control")
    git(
        publisher,
        "worktree",
        "add",
        "--track",
        "-b",
        "publisher-control",
        path,
        "origin/devlegate/control",
    )
    return path

def _ensure_publisher(fixture):
    publisher = fixture.get("publisher")
    if publisher is not None:
        return publisher
    publisher = fixture["tmp"] / "publisher"
    git(fixture["tmp"], "clone", "-b", "main", fixture["bare"], publisher)
    fixture["publisher"] = publisher
    return publisher

def ticket(title="Ticket", body="work", depends=None):
    lines = ["---", '"type": "devlegate.ticket"', f'"title": "{title}"']
    if depends:
        lines.append('"depends_on":')
        lines.extend(f'  - "{item}"' for item in depends)
    return "\n".join(lines + ["---", body, ""])

def _worker_script(path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import json, os, pathlib, sys\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--dir') + 1])\n"
        "(workspace / 'process-worker.txt').write_text('completed\\n')\n"
        "attempts = os.environ.get('DEVLEGATE_TEST_ATTEMPTS')\n"
        "if attempts:\n"
        "    pathlib.Path(attempts).open('a').write('attempt\\n')\n"
        "print(json.dumps({'type': 'tool_use', 'part': {'type': 'tool', "
        "'tool': 'devlegate_report', 'state': {'status': 'completed', "
        "'input': {'outcome': 'completed', 'summary': 'process worker', "
        "'remaining': [], 'questions': []}}}}), flush=True)\n"
    )
    path.chmod(0o755)

def _service_engine_with_control(git_fixture, config):
    engine = ServiceEngine(config)
    engine.control_worktree.parent.mkdir(parents=True, exist_ok=True)
    git(
        engine.repo,
        "worktree",
        "move",
        git_fixture["control"],
        engine.control_worktree,
    )
    with redirect_stdout(io.StringIO()):
        assert engine.control_init() == 0
    return engine

def _add_service_ticket(engine):
    ticket_path = engine.control_worktree / "kanban/todo/T-1.md"
    ticket_path.write_text(ticket("Control ticket", "work"))
    git(
        engine.control_worktree,
        "add",
        str(ticket_path.relative_to(engine.control_worktree)),
    )
    git(engine.control_worktree, "commit", "-m", "add service test ticket")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )

def _recovery_driver(config, point=None):
    command = (
        sys.executable,
        str(Path(__file__).with_name("recovery_driver.py")),
        "--env",
        str(config),
    )
    return command

def _wait_process_death(service):
    assert service.process is not None
    service.process.wait(timeout=20)
    service._collect_output()
    service._abruptly_killed = True
    assert service.process.returncode == -signal.SIGKILL

def _disk_state(config):
    return ServiceEngine(config)._runtime_store.load()

def _recovery_config(git_fixture):
    config = _short_runtime_config(git_fixture)
    config.write_text(config.read_text().replace("POLL_INTERVAL=0", "POLL_INTERVAL=1"))
    return config

def _advance_product(git_fixture):
    publisher = _ensure_publisher(git_fixture)
    (publisher / "downtime.txt").write_text("advanced\n")
    git(publisher, "add", "downtime.txt")
    git(publisher, "commit", "-m", "advance product")
    git(publisher, "push", "origin", "HEAD:main")
    return git(git_fixture["working"], "rev-parse", "HEAD").stdout.strip(), git(
        publisher, "rev-parse", "HEAD"
    ).stdout.strip()

def _recovery_execution_service(git_fixture, monkeypatch, point):
    worker = git_fixture["tmp"] / f"recovery-worker-{point}.py"
    attempts = git_fixture["tmp"] / f"recovery-attempts-{point}.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("DEVLEGATE_TEST_CRASH_POINT", point)
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    service = LiveService(
        git_fixture["working"], config, command=_recovery_driver(config, point)
    )
    service.start()
    service.wait_ready()
    return service, config, engine, attempts

def _retryable_service(git_fixture, monkeypatch):
    worker = git_fixture["tmp"] / "retry-worker.py"
    pid_file = git_fixture["tmp"] / "retry-worker.pid"
    attempts = git_fixture["tmp"] / "retry-attempts.txt"
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid_file))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    monkeypatch.setattr(
        engine._workers,
        "run",
        lambda *_args, **_kwargs: WorkerRunResult(1, None, None, None),
    )
    assert run_test_iteration(engine) == 1
    service = LiveService(git_fixture["working"], config)
    service.start()
    service.wait_ready()
    return service, config, engine, attempts, pid_file

def _parallel_service_requests(service, methods):
    start = threading.Event()

    def call(method, _index):
        assert start.wait(3)
        return ipc_request(service.locator.socket_path, method, timeout=10)

    with ThreadPoolExecutor(max_workers=len(methods)) as pool:
        futures = [
            pool.submit(call, method, index) for index, method in enumerate(methods)
        ]
        start.set()
        return [future.result(timeout=15) for future in futures]

def _long_worker_script(path):
    path.write_text(
        "#!/usr/bin/env python3\n"
        "import os, pathlib, time\n"
        "attempts = os.environ.get('DEVLEGATE_TEST_ATTEMPTS')\n"
        "if attempts:\n"
        "    with pathlib.Path(attempts).open('a') as handle:\n"
        "        handle.write('attempt\\n')\n"
        "        handle.flush()\n"
        "pathlib.Path(os.environ['DEVLEGATE_TEST_WORKER_PID']).write_text(str(os.getpid()))\n"
        "while True:\n"
        "    time.sleep(1)\n"
    )
    path.chmod(0o755)

def _worker_body_started(pid_file, identity, attempts, expected_count):
    if (
        not isinstance(identity, dict)
        or not pid_file.is_file()
        or not attempts.is_file()
    ):
        return False
    try:
        marker_pid = int(pid_file.read_text().strip())
        attempt_lines = attempts.read_text().splitlines()
    except (OSError, ValueError):
        return False
    return (
        marker_pid == identity["pid"] and attempt_lines == ["attempt"] * expected_count
    )

def _kill_worker_identity(identity):
    if isinstance(identity, dict) and isinstance(identity.get("pgid"), int):
        try:
            os.killpg(identity["pgid"], signal.SIGKILL)
        except ProcessLookupError:
            pass

def _prepare_accepted_integration(git_fixture, monkeypatch):
    worker = git_fixture["tmp"] / "accepted-worker.py"
    attempts = git_fixture["tmp"] / "accepted-attempts.txt"
    _worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    monkeypatch.setenv("DEVLEGATE_TEST_WAKE_AFTER_READY", "1")
    config = _recovery_config(git_fixture)
    config.write_text(
        config.read_text().replace("OPENCODE_BIN=true", f"OPENCODE_BIN={worker}")
    )
    engine = _service_engine_with_control(git_fixture, config)
    _add_service_ticket(engine)
    review = engine.control_worktree / "kanban/review/T-1.md"
    assert run_test_iteration(engine) == 0
    assert review.is_file()
    accepted = engine.control_worktree / "kanban/accepted/T-1.md"
    review.rename(accepted)
    git(engine.control_worktree, "add", "-A")
    git(engine.control_worktree, "commit", "-m", "accept T-1")
    git(
        engine.control_worktree,
        "push",
        "origin",
        "HEAD:refs/heads/devlegate/control",
    )
    return (
        config,
        engine,
        attempts,
        git(engine.control_worktree, "rev-parse", "HEAD").stdout.strip(),
    )

# Export all support names, including private helper functions used by domain tests.
__all__ = [name for name in globals() if not name.startswith("__")]
