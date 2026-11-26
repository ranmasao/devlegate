# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import json
import os
import socket
import subprocess
import sys
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from shlex import quote
from types import SimpleNamespace

import pytest
import service_harness
from service_harness import LiveService

from devlegate.launcher import LaunchCommand
from devlegate.project_registry import ProjectTarget
from devlegate.runtime_locator import RuntimeLocator
from devlegate.systemd_supervisor import SystemdSupervisor, render_unit, unit_path
from tools import build_distribution as graph
from tools import native_install as update


def write_payload(root, kind, content):
    binary = root / update.LAYOUT.executable
    binary.parent.mkdir(parents=True, exist_ok=True)
    binary.write_bytes(b"\x7fELF" + content)
    binary.chmod(0o755)
    marker = root / update.LAYOUT.provenance
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.write_text(
        json.dumps(
            {
                "package": "devlegate",
                "distribution": "debian" if kind == "deb" else "arch",
                "payload_sha256": update.digest(binary),
            }
        )
    )
    marker.chmod(0o644)
    return binary


@pytest.fixture
def host(tmp_path, monkeypatch):
    root = tmp_path / "host"
    proc = tmp_path / "proc"
    proc.mkdir()
    state = SimpleNamespace(
        root=root,
        proc=proc,
        events=[],
        projects={},
        issued=set(),
        stopped=set(),
        clients={},
        kind="deb",
        owners={"deb": "devlegate:amd64"},
        invalid=False,
        external=set(),
        build_error=None,
        install_effect="updated",
        restart_errors=set(),
        on_install=None,
        on_poll=None,
    )
    observe_service = update.observe
    monkeypatch.setattr(update, "ROOT", root)
    monkeypatch.setattr(update, "PROC", proc)
    monkeypatch.setattr(update, "_tool_exists", lambda _tool: True)
    monkeypatch.setattr(update.os, "getuid", lambda: 1234)
    monkeypatch.setattr(update.os, "geteuid", lambda: 1234)
    monkeypatch.setattr(update, "require_tools", lambda _tools: None)
    monkeypatch.setattr(update, "validate_builder_python", lambda _python: None)
    # Advance the mocked drain on poll intervals, independent of host I/O time.
    now = 0.0

    def advance(seconds):
        nonlocal now
        now += seconds

    monkeypatch.setattr(update, "DRAIN_TIMEOUT", 0.25)
    monkeypatch.setattr(
        update, "time", SimpleNamespace(monotonic=lambda: now, sleep=advance),
    )

    def query(tool, *argv):
        kind = "deb" if tool == "dpkg-query" else "arch"
        owner = state.owners.get(kind)
        if argv[0] in {"--search", "-Qoq"}:
            if owner is None:
                msg = (
                    "no path found matching pattern"
                    if kind == "deb"
                    else "No package owns"
                )
                return subprocess.CompletedProcess(argv, 1, "", msg)
            suffix = f": {argv[-1]}" if kind == "deb" else ""
            return subprocess.CompletedProcess(argv, 0, f"{owner}{suffix}\n", "")
        if kind == "deb":
            status = "unpacked" if state.invalid else "installed"
            line = f"install ok {status}\tamd64\t0.5.8\n"
        else:
            line = f"{owner} 0.5.8-1\n"
        return subprocess.CompletedProcess(argv, 0, line, "")

    def add(alias, mode=None):
        repo = tmp_path / alias
        locator = RuntimeLocator(repo, tmp_path / "state", alias)
        target = ProjectTarget(alias, repo / ".env", repo, locator)
        pid = 100 + len(state.projects)
        process = (
            None
            if mode is None
            else update.ProcessHost(
                update.ProcessIdentity(pid, "123", 1234),
                mode,
                str(target.env_file),
            )
        )
        unit = (
            update.Unit("devlegate-one.service", "unit-hash", pid, False)
            if (mode == "external")
            else None
        )
        state.projects[alias] = update.Service(
            target,
            unit,
            process,
            f"old-{alias}" if process else None,
        )
        return state.projects[alias]

    def observe(target, _binary):
        service = state.projects[target.alias]
        if target.alias in state.stopped & state.issued:
            unit = replace(service.unit, pid=0, idle=True) if service.unit else None
            return update.Service(target, unit)
        return service

    state.observe_fixture = observe

    def projects(binary):
        return {
            key: update.observe(service.target, binary)
            for key, service in state.projects.items()
        }

    class Client:
        code = 0

        def poll(self):
            if state.on_poll:
                state.on_poll()
            return self.code

    def request_stop(service, binary):
        assert binary == root / update.LAYOUT.executable
        state.events.append(("stop", service.target.alias))
        state.issued.add(service.target.alias)
        client = Client()
        state.clients[service.target.alias] = client
        return client

    def proof(service, _binary):
        key = service.target.alias
        state.events.append(("proof", key))
        if key in state.issued & state.stopped:
            return f"completed-{key}"
        return None

    def build(args, *, expected_source, native_result):
        assert expected_source.commit == "reviewed-source"
        assert state.issued == {
            key for key, value in state.projects.items() if value.host
        }
        assert not any(event[0] == "proof" for event in state.events)
        state.events.append(("build", args.target))
        if state.build_error:
            raise state.build_error
        state.artifact = args.output_dir / f"exact.{args.target}"
        state.artifact.write_bytes(b"validated package")
        binary = write_payload(tmp_path / "validated", state.kind, b"new payload")
        native_result(state.artifact, binary)
        return 0

    def run(command, **kwargs):
        if command[0] == sys.executable:
            return subprocess.CompletedProcess(command, 0)
        state.events.append(("install", command))
        assert set(state.issued) <= state.stopped
        assert command[-1] == str(state.artifact)
        assert command[1:4] == (
            ["--", "/usr/bin/dpkg", "--install"]
            if state.kind == "deb"
            else ["--", "/usr/bin/pacman", "-U"]
        )
        if state.on_install:
            state.on_install()
        if state.install_effect == "partial":
            state.invalid = True
            (root / update.LAYOUT.executable).write_bytes(b"\x7fELFpartial")
        if state.install_effect == "updated":
            write_payload(root, state.kind, b"new payload")
        return subprocess.CompletedProcess(
            command, int(state.install_effect not in {"updated", "unchanged-success"})
        )

    def restart(service, binary):
        state.events.append(("restart", service.target.alias, service.mode))
        assert binary == root / update.LAYOUT.executable
        if service.target.alias in state.restart_errors:
            raise update.UpdateError("readiness not confirmed")

    monkeypatch.setattr(update, "_run_query", query)
    monkeypatch.setattr(update, "exceptions", lambda *_args: set(state.external))
    monkeypatch.setattr(update, "inventory", projects)
    monkeypatch.setattr(update, "observe", observe)
    monkeypatch.setattr(update, "stop", request_stop)
    monkeypatch.setattr(update, "stop_proof", proof)
    monkeypatch.setattr(update, "restart", restart)
    monkeypatch.setattr(update.subprocess, "run", run)
    monkeypatch.setattr(
        update,
        "ProjectRegistry",
        lambda: SimpleNamespace(
            target_for_alias=lambda alias: state.projects[alias].target,
        ),
    )
    state.add = add
    state.observe_service = observe_service
    state.project = graph.Source(tmp_path, "reviewed-source", "0.5.8", sys.executable)
    state.output = tmp_path / "out"

    def invoke(*, preflight=lambda _args: state.project, extra=()):
        args = graph.parser().parse_args(
            ["native-install", "--output-dir", str(state.output), *extra]
        )
        return update.native_install(args, preflight=preflight, build=build)

    state.invoke = invoke
    return state


def journal(host):
    (path,) = host.output.glob("*/restart-inventory.jsonl")
    return [json.loads(line) for line in path.read_text().splitlines()]


@pytest.mark.parametrize("kind", ["deb", "arch"])
def test_same_version_reinstall_uses_database_owner_and_exact_artifact(host, kind):
    host.kind = kind
    host.owners = {kind: "devlegate"}
    write_payload(host.root, kind, b"old payload")
    assert host.invoke() == 0
    assert [event[0] for event in host.events] == ["build", "install"]
    assert host.events[0] == ("build", kind)
    first, last = journal(host)[0], journal(host)[-1]
    assert first["installed"]["version"] == last["installed"]["version"]
    assert first["installed"]["sha256"] != last["installed"]["sha256"]


def test_install_exit_zero_cannot_substitute_for_payload_verification(host):
    write_payload(host.root, "deb", b"old payload")
    host.add("one", "internal")
    host.stopped.add("one")
    host.install_effect = "unchanged-success"
    assert host.invoke() == 1
    assert host.events[-1] == ("restart", "one", "internal")
    assert "observed-package" in {row["event"] for row in journal(host)}


@pytest.mark.parametrize(
    "owners",
    [
        {},
        {"arch": "standalone"},
        {"deb": "devlegate", "arch": "devlegate"},
        {"deb": "devlegate, unrelated"},
    ],
)
def test_refuses_unowned_unsupported_or_ambiguous_executable(host, owners):
    write_payload(host.root, "deb", b"old payload")
    host.owners = owners
    host.add("one", "internal")
    with pytest.raises(update.UpdateError, match="one supported native owner"):
        host.invoke()
    assert host.events == []


@pytest.mark.parametrize(
    "reason",
    ["missing executable", "venv", "source", "installer", "uncertain owner", "ticket"],
)
def test_preflight_refusals_are_effect_free(host, monkeypatch, reason):
    host.add("one", "external")
    options = {}

    def refuse(_arg):
        raise update.UpdateError("uncertain preflight")

    if reason != "missing executable":
        binary = write_payload(host.root, "deb", b"old payload")
        if reason == "venv":
            binary.write_text("#!/venv/bin/python\n")
        elif reason == "source":
            options["preflight"] = refuse
        elif reason == "installer":
            monkeypatch.setattr(update, "_tool_exists", lambda tool: tool != "sudo")
        elif reason == "uncertain owner":
            monkeypatch.setattr(update, "inventory", refuse)
        elif reason == "ticket":
            options["extra"] = ["--ticket", "T-1"]
    with pytest.raises(update.UpdateError):
        host.invoke(**options)
    assert host.events == []


def cached_services(host, binary, monkeypatch):
    """Use production host selection with simulated workflow observations."""
    monkeypatch.setattr(update, "observe", host.observe_service)
    bootstrap = host.proc.parent / "unzipped_pexes" / "hash"
    bootstrap.mkdir(parents=True)
    envs = {}
    for service in host.projects.values():
        envs[service.target.env_file] = service.target
        if service.host is None:
            continue
        mode = service.host.mode
        env = [f"SCIE={binary}"]
        if mode in {"external", "internal"}:
            env.append(f"DEVLEGATE_HOST_MODE={mode}")
        if mode == "external":
            env.append("DEVLEGATE_REQUIRE_NOTIFY=1")
        command = "once" if mode == "once" else "foreground"
        proc_process(
            host.proc,
            service.host.identity.pid,
            ["/cache/nce/cpython/bin/python3.12", bootstrap,
             "--env", service.target.env_file, command],
            env,
        )

    def health(env):
        service = host.observe_fixture(envs[env], binary)
        if service.host is None:
            return None
        return {
            "pid": service.host.identity.pid, "instance_id": service.instance,
            "ready": True, "lifecycle": {"intent": "none"},
        }

    monkeypatch.setattr(update, "_healthy_service", health)

    def unit(target, _binary):
        return host.observe_fixture(target, binary).unit

    monkeypatch.setattr(update, "observe_unit", unit)
    real_identity = update.process_identity

    def identity(path):
        info = real_identity(path)
        return replace(info, uid=1234) if info else None

    monkeypatch.setattr(update, "process_identity", identity)


def test_all_graceful_stops_overlap_build_and_wait_for_each_completion(
    host, capsys, monkeypatch,
):
    host.kind = "arch"
    host.owners = {"arch": "devlegate"}
    binary = write_payload(host.root, "arch", b"old payload")
    host.add("one", "external")
    host.add("two", "internal")
    host.add("stopped")
    cached_services(host, binary, monkeypatch)

    def progress():
        assert ("build", "arch") in host.events
        host.clients["one"].code = 1  # Lost acknowledgement; proof still required.
        host.stopped.add("one")
        if host.events.count(("proof", "two")) >= 2:
            host.stopped.add("two")

    host.on_poll = progress
    assert host.invoke() == 0
    assert host.events[:3] == [("stop", "one"), ("stop", "two"), ("build", "arch")]
    assert [event for event in host.events if event[0] == "restart"] == [
        ("restart", "one", "systemd"),
        ("restart", "two", "internal"),
    ]
    events = [row["event"] for row in journal(host)]
    assert events.index("install-attempt") > max(
        i for i, event in enumerate(events) if event == "stopped"
    )
    assert "stop client exited 1; graceful completion proved" in capsys.readouterr().out


@pytest.mark.parametrize("reason", ["wrong pid", "wrong user"])
def test_cached_systemd_identity_mismatch_is_effect_free(host, monkeypatch, reason):
    binary = write_payload(host.root, "deb", b"old payload")
    service = host.add("one", "external")
    cached_services(host, binary, monkeypatch)
    if reason == "wrong pid":
        host.projects["one"] = replace(service, unit=replace(service.unit, pid=999))
        message = "supervisor disagree"
    else:
        monkeypatch.setattr(update.os, "getuid", lambda: 2345)
        monkeypatch.setattr(update.os, "geteuid", lambda: 2345)
        message = "cannot be attributed to this user"
    with pytest.raises(update.UpdateError, match=message):
        host.invoke()
    assert host.events == []


@pytest.mark.parametrize(
    "failure",
    [
        "build",
        "stop",
        "changed",
        "old",
        "partial",
        "interrupted",
        "install-interrupted",
        "restart",
    ],
)
def test_failure_recovers_only_known_stopped_persistent_owners(
    host, monkeypatch, failure
):
    write_payload(host.root, "deb", b"old payload")
    host.add("one", "external")
    host.add("two", "internal")
    host.add("once", "once")
    host.add("foreground", "foreground")
    monkeypatch.setattr("builtins.input", lambda _prompt: "yes")
    host.stopped.update(("one", "once", "foreground"))
    if failure not in {"stop", "build", "interrupted"}:
        host.stopped.add("two")
    if failure == "build":
        host.build_error = update.UpdateError("failed validation")
    elif failure == "interrupted":
        host.build_error = KeyboardInterrupt()
    elif failure in {"old", "partial"}:
        host.install_effect = failure
    elif failure == "restart":
        host.restart_errors.add("one")
    elif failure == "changed":

        def changed(*_args):
            if host.issued:
                host.stopped.discard("two")
                host.projects["two"] = replace(host.projects["two"], instance="new")
            return set()

        monkeypatch.setattr(update, "exceptions", changed)
    elif failure == "install-interrupted":

        def interrupt():
            raise KeyboardInterrupt

        host.on_install = interrupt

    assert host.invoke() == (130 if "interrupted" in failure else 1)
    restarts = [event[1] for event in host.events if event[0] == "restart"]
    assert restarts == (
        []
        if failure in {"partial", "install-interrupted"}
        else ["one", "two"]
        if failure in {"old", "restart"}
        else ["one"]
    )
    assert any(event[0] == "install" for event in host.events) == (
        failure in {"old", "partial", "install-interrupted", "restart"}
    )
    assert journal(host)[0]["event"] == "preflight"


@pytest.mark.parametrize("response", ["no", "", None, "yes"])
def test_exceptions_require_affirmative_acknowledgement(
    host, capsys, monkeypatch, response
):
    write_payload(host.root, "deb", b"old payload")
    host.add("attached", "foreground")
    host.add("once", "once")
    cached_services(host, host.root / update.LAYOUT.executable, monkeypatch)
    host.stopped.update(("attached", "once"))
    exceptional = update.ProcessHost(
        update.ProcessIdentity(888, "1", 4567), "once", "/other"
    )
    host.external.add(exceptional)

    def answer(prompt):
        assert "Continue" in prompt
        assert not host.events
        if response is None:
            raise EOFError
        return response

    monkeypatch.setattr("builtins.input", answer)
    if response == "yes":
        assert host.invoke() == 0
        assert [event for event in host.events if event[0] == "stop"] == [
            ("stop", "attached"), ("stop", "once"),
        ]
        assert not any(event[0] == "restart" for event in host.events)
    else:
        with pytest.raises(update.UpdateError, match="did not acknowledge"):
            host.invoke()
        assert host.events == []
    output = capsys.readouterr().out
    assert "PID 888" in output and "old runtime" in output
    assert "cannot be restored automatically" in output and "foreground" in output


@pytest.mark.parametrize("answer", ["no", "yes"])
def test_changed_exception_requires_fresh_consent(host, monkeypatch, answer):
    write_payload(host.root, "deb", b"old payload")
    host.add("one", "internal")
    host.stopped.add("one")
    exceptional = update.ProcessHost(
        update.ProcessIdentity(888, "1", 4567), "once", "/other"
    )
    host.external.add(exceptional)
    prompts = []

    def respond(prompt):
        prompts.append(prompt)
        if len(prompts) == 1:
            host.external = {replace(exceptional, project="/changed")}
            return "yes"
        return answer

    monkeypatch.setattr("builtins.input", respond)
    assert host.invoke() == int(answer == "no")
    assert len(prompts) == 2
    assert any(event[0] == "install" for event in host.events) == (answer == "yes")
    assert host.events[-1] == ("restart", "one", "internal")


def proc_process(proc, pid, args, env=(), *, lock=False):
    path = proc / str(pid)
    path.mkdir()
    (path / "stat").write_text(f"{pid} (arbitrary title) S " + "0 " * 18 + "123")
    (path / "cmdline").write_bytes(b"\0".join(os.fsencode(arg) for arg in args))
    (path / "environ").write_bytes(b"\0".join(os.fsencode(arg) for arg in env))
    (path / "cwd").symlink_to("/sample-project")
    (path / "fd").mkdir()
    if lock:
        (path / "fd" / "3").symlink_to(f"/state/locks/{'f' * 64}.lock")
        (path / "fdinfo").mkdir()
        (path / "fdinfo" / "3").write_text("lock: 1: FLOCK  ADVISORY  WRITE ...")


def test_process_inventory_uses_package_process_identity(tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(update, "PROC", tmp_path)
    binary = Path("/usr/bin/devlegate")

    def process(*args, **kwargs):
        proc_process(tmp_path, *args, **kwargs)

    process(11, [binary, "--env", "/registered/.env", "foreground"])
    process(12, [binary, "@other", "once"], lock=True)
    env = [f"SCIE={binary}", "DEVLEGATE_HOST_MODE=internal"]
    bootstrap = tmp_path / "unzipped_pexes" / "hash"
    bootstrap.mkdir(parents=True)
    cached = ["/cache/nce/cpython/bin/python3.12", bootstrap]
    process(
        13,
        [*cached, "--env", "/unregistered/.env", "foreground"],
        env,
        lock=True,
    )
    process(14, [binary, "status", "foreground"])
    process(15, ["/cache/python", "-c", "foreground"], env)
    process(16, ["/opt/other/devlegate", "foreground"])
    process(17, ["/cache/python", binary, "@restricted", "foreground"])
    process(18, ["unrelated"])
    process(19, [binary, "foreground"])
    process(20, [*cached, "@other", "status", "foreground"], env)
    process(21, [*cached, "_worker", "foreground"], env)
    process(22, [*cached, "@other", "foreground"], env)  # no runtime lock
    process(23, [*cached, "@other", "once"], ["SCIE=/opt/other/devlegate"], lock=True)
    real_items = update._items
    real_identity = update.process_identity

    def items(path):
        if (path.parent.name, path.name) in {("17", "environ"), ("18", "cmdline")}:
            raise PermissionError(path)
        return real_items(path)

    def identity(path):
        info = real_identity(path)
        return replace(info, uid=4567) if info and info.pid in {12, 17} else info

    monkeypatch.setattr(update, "_items", items)
    monkeypatch.setattr(update, "process_identity", identity)
    actual_lock = update._runtime_lock

    def restricted_lock(path):
        if path.name == "17":
            raise PermissionError(path)
        return actual_lock(path)

    monkeypatch.setattr(update, "_runtime_lock", restricted_lock)
    services = update.exceptions(binary, {identity(tmp_path / "11")})
    assert {(host.identity.pid, host.mode, host.project) for host in services} == {
        (12, "once", "@other"),
        (13, "internal", "/unregistered/.env"),
        (17, "foreground (supervision unverified)", "@restricted"),
    }
    for pid in (15, 20, 21, 23):
        info = identity(tmp_path / str(pid))
        assert update.process_host(info, binary, registered=True) == (None, False)
    assert "2 processes with incomplete metadata" in capsys.readouterr().out


def test_parser_native_install_is_explicit_and_refuses_ticket(monkeypatch, capsys):
    assert "native-install" in graph.parser().format_help()
    assert "native-install" not in graph.expand_targets("all")
    with pytest.raises(SystemExit) as help_exit:
        graph.parser().parse_args(["native-install", "--help"])
    assert help_exit.value.code == 0
    help_text = capsys.readouterr().out
    assert "--ticket is not accepted" in help_text
    assert "--ticket ID" not in help_text
    monkeypatch.setattr(
        sys, "argv", ["build_distribution.py", "--ticket", "T-1", "native-install"]
    )
    assert graph.main() == 1
    assert "native-install does not accept --ticket" in capsys.readouterr().err


@pytest.mark.parametrize("systemd", [False, True], ids=["attached", "systemd"])
def test_real_stop_receipt_requires_exit_and_released_authority(
    git_fixture,
    tmp_path,
    monkeypatch,
    systemd,
):
    binary = tmp_path / "native-launcher"
    # The stand-in outer launcher is only used by the stop client. The daemon
    # itself starts with the cached interpreter/directory argv shape.
    binary.write_text(f'#!/bin/sh\nexec {quote(sys.executable)} -m devlegate "$@"\n')
    binary.chmod(0o755)
    monkeypatch.delenv("PEX", raising=False)
    monkeypatch.setenv("SCIE", str(binary))
    cache = tmp_path / "cache"
    interpreter = cache / "nce" / "cpython" / "bin" / "python3.12"
    interpreter.parent.mkdir(parents=True)
    interpreter.symlink_to(sys.executable)
    pex = cache / "unzipped_pexes" / "hash"
    pex.mkdir(parents=True)
    bootstrap = Path(service_harness.__file__).with_name("service_bootstrap.py")
    (pex / "__main__.py").write_text(
        "import os, runpy, sys\n"
        "root = os.path.join(os.environ['XDG_CONFIG_HOME'], 'containment')\n"
        "sys.argv[1:1] = ['--containment-root', root, "
        "'--', sys.executable, '-m', 'devlegate']\n"
        f"runpy.run_path({str(bootstrap)!r}, run_name='__main__')\n"
    )

    monkeypatch.setattr(
        service_harness,
        "compose_test_service_command",
        lambda command, _: list(command),
    )
    config = git_fixture["config"]
    live = LiveService(
        git_fixture["working"], config,
        (str(interpreter), str(pex), "--env", str(config), "foreground"),
    )
    monkeypatch.setenv("XDG_CONFIG_HOME", str(live.registry_home))
    if systemd:
        monkeypatch.setenv("DEVLEGATE_HOST_MODE", "external")
        monkeypatch.setenv("DEVLEGATE_REQUIRE_NOTIFY", "1")
        notify = f"@devlegate-native-test-{os.getpid()}"
        # A disposable datagram receiver stands in for the user manager's
        # notification socket. No unit is installed or host systemctl invoked.
        receiver = socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM)
        receiver.bind("\0" + notify[1:])
        monkeypatch.setenv("NOTIFY_SOCKET", notify)
        unit_dir = tmp_path / "units"
        unit_dir.mkdir()
        unit = unit_path(live.locator, unit_dir)
        launcher = LaunchCommand.executable(binary)
        unit.write_text(render_unit(live.locator, config, launcher=launcher))
        overrides = {}

        def run(command, **_kwargs):
            if command == ["systemctl", "--user", "show-environment"]:
                return subprocess.CompletedProcess(command, 0, "", "")
            assert command[:3] == ["systemctl", "--user", "show"]
            assert command[-1] == unit.name
            pid = live.process.pid if live.process.poll() is None else 0
            values = {
                "LoadState": "loaded", "ActiveState": "active" if pid else "inactive",
                "MainPID": str(pid), "Job": "0", "FragmentPath": str(unit),
                "DropInPaths": "", "NeedDaemonReload": "no",
                "ExecStart": f"{{ path={binary} ; argv[]={binary} ... }}",
                "KillMode": "mixed", "KillSignal": "15", "TimeoutStopUSec": "infinity",
                "Restart": "no", **overrides,
            }
            output = "\n".join(f"{key}={value}" for key, value in values.items())
            return subprocess.CompletedProcess(command, 0, output, "")

        def supervisor(**kwargs):
            return SystemdSupervisor(unit_directory=unit_dir, runner=run, **kwargs)

        monkeypatch.setattr(update, "SystemdSupervisor", supervisor)
    else:
        receiver = nullcontext()
    with receiver, live:
        proc = update.PROC / str(live.process.pid)
        assert update._items(proc / "cmdline") == list(live.command)
        env = update._items(proc / "environ")
        assert f"SCIE={binary}" in env
        assert not any(item.startswith("PEX=") for item in env)
        if systemd:
            for key, value, message in (
                ("MainPID", str(live.process.pid + 1), "supervisor disagree"),
                ("NeedDaemonReload", "yes", "uncertain identity"),
                ("ExecStart", "{ path=/opt/other ; }", "does not match native"),
            ):
                overrides[key] = value
                with pytest.raises(update.UpdateError, match=message):
                    update.inventory(binary)
                del overrides[key]
            with monkeypatch.context() as restricted:
                def unavailable(_path):
                    raise PermissionError("unobservable bootstrap metadata")

                restricted.setattr(update, "_items", unavailable)
                assert next(iter(update.inventory(binary).values())).mode == "systemd"

        (service,) = update.inventory(binary).values()
        assert service.mode == ("systemd" if systemd else "foreground")
        assert service.restorable is systemd
        assert service.host.identity.pid == live.process.pid
        assert {h.identity for h in update.exceptions(binary, set())} == {
            service.host.identity,
        }
        assert update.stop_proof(service, binary) is None
        client = update.stop(service, binary)
        assert client.wait(timeout=15) == 0
        proof = update.drain(
            [service], {service.alias: client}, binary, update.Journal(tmp_path)
        )
        assert proof == {service.alias: update.read_receipt(live.locator)["request_id"]}
        assert live.process.wait(timeout=5) == 0
        assert update.inventory(binary)[service.target.alias].mode == "stopped"


def test_managed_unit_observation_detects_pending_start_and_unsafe_stop(
    tmp_path,
    monkeypatch,
):
    binary = Path("/usr/bin/devlegate")
    repo = tmp_path / "repo"
    locator = RuntimeLocator(repo, tmp_path / "state", "a" * 64)
    target = ProjectTarget("project", repo / ".env", repo, locator)
    unit = tmp_path / "devlegate-aaaaaaaa.service"
    unit.write_text("managed unit")
    values = {
        "LoadState": "loaded",
        "ActiveState": "inactive",
        "MainPID": "0",
        "Job": "17",
        "FragmentPath": str(unit),
        "DropInPaths": "",
        "NeedDaemonReload": "no",
        "ExecStart": f"{{ path={binary} ; argv[]={binary} ... }}",
        "KillMode": "mixed",
        "KillSignal": "15",
        "TimeoutStopUSec": "infinity",
        "Restart": "no",
    }

    def run(command, **_kwargs):
        payload = ""
        if "show" in command:
            assert command[-1] == unit.name
            payload = "\n".join(f"{name}={value}" for name, value in values.items())
        return subprocess.CompletedProcess(command, 0, payload, "")

    supervisor = SystemdSupervisor(unit_directory=tmp_path, runner=run)
    monkeypatch.setattr(supervisor, "inspect", lambda *_args, **_kwargs: True)
    monkeypatch.setattr(update, "SystemdSupervisor", lambda **_kwargs: supervisor)
    store = SimpleNamespace(supervision_authority=lambda: None)
    monkeypatch.setattr(
        update, "_systemd_authority_established", lambda *_: (store, True)
    )
    monkeypatch.setattr(update, "_known_systemd_name", lambda *_: unit.name)
    monkeypatch.setattr(update, "_healthy_service", lambda _env: None)

    with pytest.raises(update.UpdateError, match="may be active"):
        update.observe(target, binary)
    values["Job"] = "0"
    assert update.observe(target, binary).mode == "stopped"
    values["ActiveState"] = "active"
    values["MainPID"] = "100"
    values["TimeoutStopUSec"] = "1min 30s"
    with pytest.raises(update.UpdateError, match="does not match native hosting"):
        update.observe(target, binary)
