# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Source-only update of the native executable and its currently running services."""

from __future__ import annotations

import json
import os
import platform
import pwd
import shlex
import stat
import subprocess
import tempfile
import time
from argparse import Namespace
from dataclasses import asdict, dataclass
from pathlib import Path

from devlegate.cli import (
    _healthy_service,
    _known_systemd_name,
    _start_background,
    _systemd_authority_established,
)
from devlegate.launcher import LaunchCommand
from devlegate.lifecycle_receipt import read as read_receipt
from devlegate.project_registry import ProjectRegistry, ProjectTarget
from devlegate.runtime_state_validation import DevlegateError
from devlegate.systemd_supervisor import SystemdSupervisor, unit_path

if __package__:
    from tools.build_distribution import digest, output_directory, require_tools
    from tools.build_standalone import validate_builder_python
    from tools.native_payload import PayloadLayout
else:
    from build_distribution import digest, output_directory, require_tools
    from build_standalone import validate_builder_python
    from native_payload import PayloadLayout


class UpdateError(RuntimeError):
    pass


SYSTEM_BIN = Path("/usr/bin")
ROOT = Path("/")
PROC = Path("/proc")
LAYOUT = PayloadLayout()
DRAIN_TIMEOUT = 600


def _run_query(tool: str, *args: str) -> subprocess.CompletedProcess[str]:
    environment = {
        key: value
        for key, value in os.environ.items()
        if key not in {"DPKG_ROOT", "DPKG_ADMINDIR", "DPKG_FORCE"}
    }
    return subprocess.run(
        [str(SYSTEM_BIN / tool), *args],
        env={**environment, "LC_ALL": "C"},
        text=True,
        capture_output=True,
        timeout=15,
        check=False,
    )


def _tool_exists(name: str) -> bool:
    return (SYSTEM_BIN / name).is_file() and os.access(SYSTEM_BIN / name, os.X_OK)


def _owner_candidates(executable: Path) -> list[tuple[str, str]]:
    candidates = []
    for kind, tool, args, no_match in (
        (
            "deb",
            "dpkg-query",
            ("--search", str(executable)),
            "no path found matching pattern",
        ),
        ("arch", "pacman", ("-Qoq", "--", str(executable)), "No package owns"),
    ):
        if not _tool_exists(tool):
            continue
        result = _run_query(tool, *args)
        if result.returncode == 1 and not result.stdout and no_match in result.stderr:
            continue
        if result.returncode or result.stderr or not result.stdout.strip():
            raise UpdateError(
                f"cannot establish {kind} package ownership: {result.stderr.strip()}"
            )
        for line in result.stdout.splitlines():
            if kind == "deb":
                owner, separator, path = line.partition(": ")
                if not separator or path != str(executable):
                    raise UpdateError(
                        f"ambiguous or diverted Debian executable: {line}"
                    )
                candidates.extend((kind, item) for item in owner.split(", "))
            else:
                candidates.append((kind, line.strip()))
    return candidates


@dataclass(frozen=True)
class Installed:
    kind: str
    owner: str
    version: str
    executable: Path
    sha256: str
    provenance_sha256: str


def installed() -> Installed:
    # Both native adapters install at this exact path. PATH, a venv, and host
    # supervision settings are irrelevant to ownership of this file.
    executable = ROOT / LAYOUT.executable
    if (
        executable.is_symlink()
        or not executable.is_file()
        or not os.access(executable, os.X_OK)
    ):
        raise UpdateError(f"no supported native executable at {executable}")
    candidates = _owner_candidates(executable)
    if len(candidates) != 1 or candidates[0][1] not in {"devlegate", "devlegate:amd64"}:
        raise UpdateError(
            f"expected one supported native owner for {executable}; found {candidates}"
        )
    kind, owner = candidates[0]
    if kind == "deb":
        result = _run_query(
            "dpkg-query",
            "--showformat=${Status}\t${Architecture}\t${Version}\n",
            "--show",
            owner,
        )
        status, _, version = result.stdout.strip().rpartition("\t")
        # A held package is still installed; dpkg retains its own hold policy.
        valid = status in {"install ok installed\tamd64", "hold ok installed\tamd64"}
    else:
        result = _run_query("pacman", "-Q", "--", owner)
        expected, _, version = result.stdout.strip().partition(" ")
        valid = owner == expected == "devlegate"
    if result.returncode or result.stderr or not valid or len(version.split()) != 1:
        raise UpdateError(
            f"{owner} is not fully installed: "
            f"{result.stdout.strip()} {result.stderr.strip()}"
        )
    with executable.open("rb") as handle:
        if handle.read(4) != b"\x7fELF":
            raise UpdateError(f"{executable} is not the native executable")
    marker = ROOT / LAYOUT.provenance
    sha = digest(executable)
    if marker.is_symlink():
        raise UpdateError(f"unexpected symlink at {marker}")
    provenance = json.loads(marker.read_text(encoding="ascii"))
    if not isinstance(provenance, dict) or any(
        provenance.get(field) != value
        for field, value in {
            "package": "devlegate",
            "distribution": "debian" if kind == "deb" else "arch",
            "payload_sha256": sha,
        }.items()
    ):
        raise UpdateError(
            f"installed executable does not match native payload provenance: {marker}"
        )
    return Installed(kind, owner, version, executable, sha, digest(marker))


@dataclass(frozen=True)
class ProcessIdentity:
    pid: int
    start: str
    uid: int


def process_identity(path: Path) -> ProcessIdentity | None:
    try:
        uid = path.stat().st_uid
        fields = (path / "stat").read_text().rpartition(")")[2].split()
    except (FileNotFoundError, ProcessLookupError):
        return None
    if len(fields) <= 19:
        raise UpdateError(f"invalid process identity: {path}")
    if fields[0] in {"Z", "X"}:
        return None
    return ProcessIdentity(int(path.name), fields[19], uid)


def _items(path: Path) -> list[str]:
    return [os.fsdecode(item) for item in path.read_bytes().split(b"\0") if item]


def _invocation(arguments: list[str]) -> tuple[str, str]:
    project = ""
    if arguments and arguments[0].startswith("@"):
        project, *arguments = arguments
    elif len(arguments) >= 2 and arguments[0] == "--env":
        _, project, *arguments = arguments
    elif arguments and arguments[0].startswith("--env="):
        project = arguments[0].split("=", 1)[1]
        arguments = arguments[1:]
    return arguments[0] if arguments else "", project


def _runtime_lock(path: Path) -> bool:
    # A CLI or a worker can carry an outer executable identity too. The
    # exclusive project lock distinguishes the host when /proc permits it.
    for descriptor in (path / "fd").iterdir():
        try:
            target = Path(descriptor.readlink())
            name = target.stem
            if (
                target.parent.name == "locks"
                and target.suffix == ".lock"
                and len(name) == 64
                and all(c in "0123456789abcdef" for c in name)
                and "FLOCK  ADVISORY  WRITE"
                in (path / "fdinfo" / descriptor.name).read_text()
            ):
                return True
        except FileNotFoundError:
            continue
    return False


@dataclass(frozen=True)
class ProcessHost:
    identity: ProcessIdentity
    mode: str
    project: str


def process_host(
    identity: ProcessIdentity,
    executable: Path,
    *,
    registered: bool = False,
) -> tuple[ProcessHost | None, bool]:
    """Acknowledge restricted metadata; don't infer identity from the comm name."""
    path = PROC / str(identity.pid)
    args = _items(path / "cmdline")
    environment = {}
    limited = False
    try:
        environment = dict(
            item.split("=", 1) for item in _items(path / "environ") if "=" in item
        )
    except PermissionError:
        if registered:
            raise UpdateError(
                f"cannot observe hosting environment for PID {identity.pid}"
            ) from None
        limited = True
    outer = str(executable)
    cached_bootstrap = False
    if args and args[0] == outer:
        product_args = args[1:]
    elif len(args) > 1 and args[1] == outer and Path(args[0]).name.startswith("python"):
        product_args = args[2:]
    elif environment.get("PEX") == environment.get("SCIE") == outer:
        # SCIE execs a cached interpreter. Its argv can name the outer PEX
        # or a cached bootstrap script rather than the original executable.
        index = next((i for i, value in enumerate(args[1:], 1) if value == outer), None)
        product_args = args[(index + 1) if index is not None else 1 :]
    elif (
        environment.get("SCIE") == outer
        and "PEX" not in environment
        and len(args) > 2
        and Path(args[0]).name.startswith("python")
        and (bootstrap := Path(args[1])).is_absolute()
    ):
        # /proc/environ is the initial environment, before the PEX bootstrap
        # sets PEX in Python. The interpreter receives the unzipped PEX directory.
        try:
            if not stat.S_ISDIR(bootstrap.stat().st_mode):
                return None, limited
        except FileNotFoundError:
            return None, limited
        cached_bootstrap = True
        product_args = args[2:]
    else:
        return None, limited
    command, project = _invocation(product_args)
    if cached_bootstrap and command not in {"foreground", "once"}:
        return None, limited
    if not registered:
        try:
            if not _runtime_lock(path):
                return None, limited
        except PermissionError:
            limited = True
            # Without a lock view, only an explicit host invocation can be
            # distinguished from other clients using this exact executable.
            if command not in {"foreground", "once"}:
                return None, limited
    if command not in {"", "foreground", "once"}:
        # A cached bootstrap can prefix the real selector and arguments. At
        # this point the process is already proved to own the project lock.
        for offset in range(1, len(product_args)):
            command, project = _invocation(product_args[offset:])
            if command in {"foreground", "once"}:
                break
        else:
            command = ""
    mode = environment.get("DEVLEGATE_HOST_MODE", "direct")
    hosting = (
        (command or "foreground") if mode == "direct" or command == "once" else mode
    )
    if limited and command != "once":
        hosting += " (supervision unverified)"
    if not project:
        try:
            project = str((path / "cwd").readlink())
        except PermissionError:
            limited = True
    if process_identity(path) != identity:
        return None, limited
    return ProcessHost(identity, hosting, project), limited


def exceptions(executable: Path, selected: set[ProcessIdentity]) -> set[ProcessHost]:
    observed = set()
    limited = 0
    try:
        for path in PROC.iterdir():
            if not path.name.isascii() or not path.name.isdigit():
                continue
            try:
                identity = process_identity(path)
                if identity is None or identity in selected:
                    continue
                host, restricted = process_host(identity, executable)
                limited += restricted
                if host is not None:
                    observed.add(host)
            except (FileNotFoundError, ProcessLookupError):
                continue
            except (OSError, UpdateError):
                limited += 1
    except OSError as error:
        print(f"Warning: cannot list {PROC}: {error}")
    print(
        f"Host process discovery is best-effort; visibility may be restricted "
        f"({limited} processes with incomplete metadata observed)."
    )
    return observed


@dataclass(frozen=True)
class Unit:
    name: str
    sha256: str
    pid: int
    idle: bool


def observe_unit(target: ProjectTarget, executable: Path) -> Unit | None:
    supervisor = SystemdSupervisor(launcher=LaunchCommand.executable(executable))
    store, established = _systemd_authority_established(target, supervisor)
    if not established:
        return None
    name = _known_systemd_name(target, supervisor, store)
    if name is None or not supervisor.inspect(
        target.locator,
        env_file=target.env_file,
        name=name,
        allow_legacy=store.supervision_authority() is not None,
    ):
        raise UpdateError(f"@{target.alias}: cannot verify managed unit {name}")
    path = unit_path(target.locator, supervisor.unit_directory, name=name)
    state = supervisor.observe_unit(name)
    pid = int(state["MainPID"])
    idle = (
        state["ActiveState"] in {"inactive", "failed"}
        and pid == 0
        and state["Job"] in {"", "0"}
    )
    if (
        state["LoadState"] != "loaded"
        or state["FragmentPath"] != str(path)
        or state["DropInPaths"]
        or state["NeedDaemonReload"] != "no"
    ):
        raise UpdateError(f"@{target.alias}: loaded unit {name} has uncertain identity")
    if not idle and (
        not state["ExecStart"].startswith(f"{{ path={executable} ;")
        or state["KillMode"] != "mixed"
        or state["KillSignal"] not in {"15", "TERM", "SIGTERM"}
        or state["TimeoutStopUSec"] != "infinity"
        or state["Restart"] != "no"
    ):
        raise UpdateError(
            f"@{target.alias}: loaded unit {name} does not match native hosting"
        )
    return Unit(name, digest(path), pid, idle)


@dataclass(frozen=True)
class Service:
    target: ProjectTarget
    unit: Unit | None
    host: ProcessHost | None = None
    instance: str | None = None

    @property
    def alias(self) -> str:
        return f"@{self.target.alias}"

    @property
    def mode(self) -> str:
        if self.host is None:
            return "stopped"
        return "systemd" if self.host.mode == "external" else self.host.mode

    @property
    def restorable(self) -> bool:
        return self.mode in {"systemd", "internal"}

    def argv(self, executable: Path, *args: str) -> list[str]:
        return [str(executable), "--env", str(self.target.env_file), *args]


def observe(target: ProjectTarget, executable: Path) -> Service:
    unit = observe_unit(target, executable)
    health = _healthy_service(target.env_file)
    if health is None:
        if unit is not None and not unit.idle:
            raise UpdateError(
                f"@{target.alias}: managed unit may be active without IPC authority"
            )
        return Service(target, unit)
    pid, instance = health.get("pid"), health.get("instance_id")
    lifecycle = health.get("lifecycle")
    if (
        type(pid) is not int
        or pid <= 0
        or not isinstance(instance, str)
        or not instance
        or health.get("ready") is not True
        or not isinstance(lifecycle, dict)
        or lifecycle.get("intent") != "none"
    ):
        raise UpdateError(
            f"@{target.alias}: service identity or readiness is uncertain"
        )
    identity = process_identity(PROC / str(pid))
    if identity is None or identity.uid != os.getuid():
        raise UpdateError(
            f"@{target.alias}: service PID {pid} cannot be attributed to this user"
        )
    if unit is not None and not unit.idle:
        if unit.pid != pid:
            raise UpdateError(
                f"@{target.alias}: IPC service and managed supervisor disagree"
            )
        # The verified loaded launcher and MainPID agree with the runtime
        # authority. The bootstrap's argv and initial environment add no proof.
        host = ProcessHost(identity, "external", str(target.env_file))
        return Service(target, unit, host, instance)
    host, _ = process_host(identity, executable, registered=True)
    if host is None or host.mode not in {"foreground", "once", "internal", "external"}:
        raise UpdateError(
            f"@{target.alias}: cannot verify native service and hosting for PID {pid}"
        )
    if unit is not None and unit.idle:
        # An attached host is allowed to run while its persistent unit is idle.
        if host.mode in {"foreground", "once"}:
            return Service(target, unit, host, instance)
    if unit is not None or host.mode == "external":
        raise UpdateError(
            f"@{target.alias}: IPC service and managed supervisor disagree"
        )
    return Service(target, unit, host, instance)


def inventory(executable: Path) -> dict[str, Service]:
    registry = ProjectRegistry()
    result = {}
    keys = set()
    for alias in sorted(registry.projects()):
        target = registry.target_for_alias(alias)
        if target.locator.state_key in keys:
            raise UpdateError(f"@{alias}: ambiguous registered project identity")
        keys.add(target.locator.state_key)
        result[alias] = observe(target, executable)
    return result


def _description(host: ProcessHost) -> str:
    uid = host.identity.uid
    try:
        user = pwd.getpwuid(uid).pw_name
    except KeyError:
        user = str(uid)
    return (
        f"user {user} (uid {uid}), PID {host.identity.pid}, {host.mode}, "
        f"project {host.project!r}"
    )


def _manual(service: Service, executable: Path) -> str:
    return f"{service.alias}: {shlex.join(service.argv(executable, service.mode))}"


def confirm(
    external: set[ProcessHost], attached: list[Service], executable: Path
) -> None:
    if not external and not attached:
        return
    if external:
        print(
            "These other-user or unregistered services will not be stopped or restored "
            "and may continue on the old runtime:"
        )
        for host in sorted(external, key=lambda host: host.identity.pid):
            print(f"  {_description(host)}")
    if attached:
        print(
            "These attached services will be stopped gracefully. Their sessions cannot "
            "be restored automatically. To relaunch manually after shutdown:"
        )
        for service in attached:
            print(f"  {_manual(service, executable)}")
    try:
        answer = input("Continue with these exceptions? [y/N] ")
    except EOFError:
        answer = ""
    if answer.strip().lower() not in {"y", "yes"}:
        raise UpdateError("operator did not acknowledge the exceptions")


@dataclass(frozen=True)
class Candidate:
    path: Path
    sha256: str
    version: str
    files: dict[str, tuple[str, int]]

    @classmethod
    def validated(cls, path: Path, binary: Path, version: str) -> Candidate:
        root = binary.parents[2]
        files = {
            str(item.relative_to(root)): (
                digest(item),
                stat.S_IMODE(item.stat().st_mode),
            )
            for item in (root / "usr").rglob("*")
            if item.is_file()
        }
        if not {LAYOUT.executable, LAYOUT.provenance} <= files.keys():
            raise UpdateError("missing validated native executable or provenance")
        return cls(path, digest(path), version, files)

    def matches(self, current: Installed) -> bool:
        return current.version == self.version and all(
            not (path := ROOT / name).is_symlink()
            and path.is_file()
            and stat.S_IMODE(path.stat().st_mode) == mode
            and digest(path) == sha
            for name, (sha, mode) in self.files.items()
        )


class Journal:
    def __init__(self, directory: Path) -> None:
        self.path = directory / "restart-inventory.jsonl"

    def write(self, event: str, **details: object) -> None:
        with self.path.open("a", encoding="utf-8") as stream:
            stream.write(json.dumps({"event": event, **details}, default=str) + "\n")
            stream.flush()
            os.fsync(stream.fileno())


def stop(service: Service, executable: Path) -> subprocess.Popen:
    return subprocess.Popen(
        service.argv(executable, "stop"),
        cwd=service.target.repo,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


def stop_proof(service: Service, executable: Path) -> str | None:
    target = service.target
    assert service.host is not None
    if target.locator.daemon_authority_present():
        try:
            health = _healthy_service(target.env_file)
        except DevlegateError:
            # IPC is closed before the draining host releases its lock.
            if (
                process_identity(PROC / str(service.host.identity.pid))
                == service.host.identity
            ):
                return None
            raise
        if health is not None and health.get("instance_id") != service.instance:
            raise UpdateError(
                f"{service.alias}: a different owner started during shutdown"
            )
        return None
    if process_identity(PROC / str(service.host.identity.pid)) == service.host.identity:
        return None
    unit = observe_unit(target, executable)
    reference = (
        None if service.unit is None else (service.unit.name, service.unit.sha256)
    )
    observed = None if unit is None else (unit.name, unit.sha256)
    if observed != reference:
        raise UpdateError(f"{service.alias}: managed unit identity changed")
    if unit is not None and not unit.idle:
        return None
    receipt = read_receipt(target.locator)
    if (
        receipt
        and receipt.get("instance_id") == service.instance
        and receipt.get("action") == "stop"
        and receipt.get("state") == "completed"
        and isinstance(receipt.get("request_id"), str)
        and receipt["request_id"]
        and not target.locator.daemon_authority_present()
    ):
        return receipt["request_id"]
    return None


def drain(active, clients, executable: Path, journal: Journal) -> dict[str, str]:
    proofs = {}
    deadline = time.monotonic() + DRAIN_TIMEOUT
    while True:
        for service in active:
            if service.alias in proofs:
                continue
            code = clients[service.alias].poll()
            # The CLI may still be issuing its systemd stop after the daemon
            # exits. Never proceed while one of our stop clients is pending.
            proof = stop_proof(service, executable) if code is not None else None
            if proof is not None:
                proofs[service.alias] = proof
                journal.write(
                    "stopped", project=service.alias, request_id=proof, stop_exit=code
                )
                if code:
                    print(
                        f"{service.alias}: stop client exited {code}; "
                        "graceful completion proved"
                    )
        if len(proofs) == len(active):
            return proofs
        if time.monotonic() >= deadline:
            pending = [
                f"{s.alias} (stop exit {clients[s.alias].poll()})"
                for s in active
                if s.alias not in proofs
            ]
            raise UpdateError(f"graceful shutdown not proved: {', '.join(pending)}")
        time.sleep(0.1)


def restart(service: Service, executable: Path) -> None:
    if service.mode == "systemd":
        subprocess.run(
            service.argv(executable, "service", "start"),
            cwd=service.target.repo,
            check=True,
            stdin=subprocess.DEVNULL,
        )
    else:
        _start_background(
            service.target.env_file,
            launcher=LaunchCommand.executable(executable),
            cleanup_failed_start=False,
        )
    now = observe(service.target, executable)
    if now.mode != service.mode or now.instance == service.instance or now.host is None:
        raise UpdateError(
            f"{service.alias}: replacement did not become ready in {service.mode} mode"
        )
    if service.unit is not None and (
        now.unit is None or now.unit.name != service.unit.name
    ):
        raise UpdateError(
            f"{service.alias}: replacement did not start under the recorded unit"
        )


def restore(active, clients, executable: Path, journal: Journal) -> bool:
    success = True
    for service in active:
        if not service.restorable:
            print(f"Manual restoration: {_manual(service, executable)}")
            continue
        if service.alias not in clients:
            continue
        try:
            current = ProjectRegistry().target_for_alias(service.target.alias)
            if current != service.target:
                raise UpdateError("registered project identity changed")
            if clients[service.alias].poll() is None or not stop_proof(
                service, executable
            ):
                raise UpdateError(
                    "service is running/draining or graceful completion is uncertain"
                )
            journal.write("restart-attempt", project=service.alias)
            restart(service, executable)
            journal.write("ready", project=service.alias)
            print(f"{service.alias}: restored and ready ({service.mode})")
        except Exception as error:
            success = False
            journal.write(
                "restart-not-proved", project=service.alias, detail=str(error)
            )
            print(f"{service.alias}: restoration not proved: {error}")
    return success


def _installer(kind: str, artifact: Path) -> list[str]:
    manager, operation = ("dpkg", "--install") if kind == "deb" else ("pacman", "-U")
    return [
        str(SYSTEM_BIN / "sudo"),
        "--",
        str(SYSTEM_BIN / manager),
        operation,
        str(artifact),
    ]


def native_install(args, *, preflight, build) -> int:
    if args.ticket is not None:
        raise UpdateError("native-install does not accept --ticket")
    if os.getuid() == 0 or os.geteuid() != os.getuid():
        raise UpdateError("run native-install as the invoking user, without sudo")
    if platform.system() != "Linux" or platform.machine() != "x86_64":
        raise UpdateError("native-install requires a supported Linux x86_64 host")
    before = installed()
    executable = before.executable
    manager = "dpkg" if before.kind == "deb" else "pacman"
    for tool in ("sudo", manager):
        if not _tool_exists(tool):
            raise UpdateError(f"required installer unavailable: {SYSTEM_BIN / tool}")
    args = Namespace(**{**vars(args), "target": before.kind})
    source = preflight(args)
    require_tools(("true",))
    validate_builder_python(source.python)
    subprocess.run(
        [source.python, "-c", "import build, ensurepip, venv"],
        check=True,
        timeout=15,
    )
    projects = inventory(executable)
    active = [service for service in projects.values() if service.host is not None]
    attached = [service for service in active if not service.restorable]
    selected = {service.host.identity for service in active}
    acknowledged = exceptions(executable, selected)
    confirm(acknowledged, attached, executable)
    output = output_directory(source.repo, args.output_dir)
    output.mkdir(parents=True, exist_ok=True)
    directory = Path(tempfile.mkdtemp(prefix="native-install-", dir=output))
    journal = Journal(directory)
    journal.write(
        "preflight",
        source=source.commit,
        installed=asdict(before),
        services=[
            {
                **asdict(s),
                "mode": s.mode,
                "restorable": s.restorable,
                "launch": s.argv(
                    executable,
                    *(
                        ("service", "start")
                        if s.mode == "systemd"
                        else (s.mode,)
                        if not s.restorable
                        else ("foreground",)
                    ),
                ),
            }
            for s in active
        ],
        acknowledged=[asdict(host) for host in acknowledged],
    )
    print(f"Restart inventory and diagnostics: {journal.path}", flush=True)
    clients = {}
    candidate = None
    install_pending = False
    failure = False
    interrupted = False

    def capture(path: Path, binary: Path) -> None:
        nonlocal candidate
        version = source.version if before.kind == "deb" else f"{source.version}-1"
        candidate = Candidate.validated(path, binary, version)

    try:
        if preflight(args) != source:
            raise UpdateError("build source changed before stopping services")
        if installed() != before or inventory(executable) != projects:
            raise UpdateError(
                "installation or registered owners changed since preflight"
            )
        errors = []
        for service in active:
            try:
                if observe(service.target, executable) != service:
                    raise UpdateError("owner changed immediately before stop")
                journal.write("stop-attempt", project=service.alias)
                clients[service.alias] = stop(service, executable)
            except Exception as error:
                errors.append(f"{service.alias}: {error}")
        if errors:
            raise UpdateError("cannot request all stops: " + "; ".join(errors))
        args.output_dir = directory
        if (
            build(args, expected_source=source, native_result=capture)
            or candidate is None
        ):
            raise UpdateError("build did not return a validated native artifact")
        journal.write("validated", **asdict(candidate))
        proofs = drain(active, clients, executable, journal)
        while True:
            current = exceptions(executable, set())
            new = current - acknowledged
            if not new:
                break
            confirm(new, [], executable)
            acknowledged.update(new)
            journal.write("acknowledged", instances=[asdict(host) for host in new])
        if installed() != before or digest(candidate.path) != candidate.sha256:
            raise UpdateError(
                "installed package or validated artifact changed before installation"
            )
        final = inventory(executable)
        if (
            final.keys() != projects.keys()
            or any(
                service.target != projects[alias].target or service.host is not None
                for alias, service in final.items()
            )
            or any(
                stop_proof(service, executable) != proofs[service.alias]
                for service in active
            )
        ):
            raise UpdateError(
                "a registered owner or project identity changed before installation"
            )
        command = _installer(before.kind, candidate.path)
        journal.write("install-attempt", command=command)
        install_pending = True
        try:
            result = subprocess.run(command, check=False)
        except OSError:
            install_pending = False
            raise
        install_pending = False
        journal.write("installer-exit", code=result.returncode)
        if result.returncode:
            raise UpdateError(f"native installer exited {result.returncode}")
        current_package = installed()
        if current_package.kind != before.kind or not candidate.matches(
            current_package
        ):
            raise UpdateError("installed package does not match the validated payload")
        journal.write("installed-verified", installed=asdict(current_package))
    except (Exception, KeyboardInterrupt) as error:
        failure = True
        interrupted = isinstance(error, KeyboardInterrupt)
        detail = str(error) or type(error).__name__
        print(f"Native install did not complete: {detail}")
        journal.write("failed", detail=detail)
        try:
            actual = installed()
            journal.write("observed-package", installed=asdict(actual))
            known_good = actual == before or (
                actual.kind == before.kind
                and candidate is not None
                and candidate.matches(actual)
            )
        except Exception as observation_error:
            journal.write("package-uncertain", detail=str(observation_error))
            known_good = False
        # On interruption the native manager may still be working. Do not
        # interpret a transient package observation as settled in that case.
        if install_pending or not known_good:
            journal.write(
                "manual-recovery",
                detail="settle and verify the native package before starting services",
            )
            print(
                "Verify the package manager has finished and repair/verify the native "
                "payload, then inspect the recorded projects and restore only stopped "
                "services."
            )
            return 130 if interrupted else 1
    try:
        restored = restore(active, clients, executable, journal)
    except KeyboardInterrupt:
        journal.write("restoration-interrupted")
        print(
            "Restoration interrupted; check readiness before restarting: "
            f"{journal.path}"
        )
        return 130
    for host in sorted(acknowledged, key=lambda host: host.identity.pid):
        print(f"Left untouched: {_description(host)}")
    return 130 if interrupted else int(failure or not restored)
