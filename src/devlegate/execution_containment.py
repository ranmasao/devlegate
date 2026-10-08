# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Kernel-enforced ownership boundaries for Linux executions.

This module deliberately knows only cgroup v2 core semantics.  Host
supervisors provision the delegated root; they are not part of this API.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol


class ContainmentError(RuntimeError):
    """The host cannot provide a usable execution containment boundary."""


class ContainmentBoundary(Protocol):
    """Lifecycle contract shared by production and test boundaries."""

    def spawn(self, command: Sequence[str], **kwargs): ...

    def request_graceful(self, pid: int | None) -> None: ...

    def force_terminate(self) -> None: ...

    def wait_empty(self, timeout: float) -> bool: ...

    def destroy(self) -> None: ...


class ContainmentProvider(Protocol):
    """Provider seam for mandatory execution containment."""

    def create(self, execution_id: str) -> ContainmentBoundary:
        """Create an empty boundary for one execution."""

    def observe(self, identity: str) -> str:
        """Classify a persisted boundary as live, absent, or indeterminate."""


def default_containment_provider() -> ContainmentProvider:
    """Return the mandatory production containment provider."""
    return LinuxCgroupContainmentProvider()


@dataclass(frozen=True)
class ContainmentCapability:
    status: str
    root: Path | None = None
    recursive_kill: bool = False
    detail: str = ""

    @property
    def usable(self) -> bool:
        return self.status == "available"


def _cgroup2_mount() -> Path | None:
    try:
        lines = Path("/proc/self/mountinfo").read_text().splitlines()
    except OSError:
        return None
    for line in lines:
        before, separator, after = line.partition(" - ")
        if separator and after.split()[0:1] == ["cgroup2"]:
            fields = before.split()
            if len(fields) > 4:
                return Path(fields[4])
    return None


def _current_cgroup(mount: Path) -> Path | None:
    try:
        record = Path("/proc/self/cgroup").read_text().splitlines()
    except OSError:
        return None
    for line in record:
        if line.startswith("0::"):
            return mount / line[3:].lstrip("/")
    return None


def _configured_root(mount: Path) -> Path | None:
    configured = os.environ.get("DEVLEGATE_CGROUP_ROOT")
    if configured:
        return Path(configured).resolve()
    return _current_cgroup(mount)


def probe_containment() -> ContainmentCapability:
    """Return a deterministic capability result without distro detection."""
    if os.name != "posix" or not sys_platform_linux():
        return ContainmentCapability("unsupported", detail="Linux is required")
    mount = _cgroup2_mount()
    if mount is None:
        return ContainmentCapability(
            "no-unified-hierarchy", detail="cgroup v2 is not mounted"
        )
    root = _configured_root(mount)
    if root is None or not root.is_dir():
        return ContainmentCapability(
            "no-delegated-subtree", detail="no delegated cgroup root"
        )
    probe: Path | None = None
    marker: Path | None = None
    try:
        probe = root / ".devlegate-capability-probe"
        probe.mkdir()
        # cgroup.procs being writable is not enough: admission requires that a
        # separate process can join before it executes workload code.  The
        # disposable child proves that exact operation without moving the
        # daemon itself.
        recursive_kill = (probe / "cgroup.kill").is_file()
        if not recursive_kill:
            capability = ContainmentCapability(
                "no-recursive-kill",
                root,
                detail="cgroup.kill is required for recursive termination",
            )
        else:
            descriptor, marker_name = tempfile.mkstemp(prefix="devlegate-cgroup-")
            os.close(descriptor)
            marker = Path(marker_name)
            marker.unlink()
            command = _launcher_command(
                probe,
                [
                    sys.executable,
                    "-c",
                    (
                        "import pathlib; "
                        "pathlib.Path(__import__('sys').argv[1]).write_text("
                        "pathlib.Path('/proc/self/cgroup').read_text())"
                    ),
                    str(marker),
                ],
            )
            child = subprocess.run(
                command, capture_output=True, text=True, check=False
            )
            expected = probe.relative_to(mount)
            observed = marker.read_text().splitlines() if marker.is_file() else []
            expected_record = f"0::/{expected}"
            if child.returncode or expected_record not in observed:
                detail = (
                    child.stderr.strip()
                    or "disposable child joined but membership was not observed"
                )
                capability = ContainmentCapability(
                    "no-delegated-subtree", root, detail=detail
                )
            else:
                capability = ContainmentCapability(
                    "available", root, recursive_kill=True
                )
        if marker is not None:
            marker.unlink(missing_ok=True)
        probe.rmdir()
        return capability
    except (OSError, ValueError) as error:
        if marker is not None:
            marker.unlink(missing_ok=True)
        if probe is not None:
            try:
                probe.rmdir()
            except OSError:
                pass
        return ContainmentCapability(
            "no-delegated-subtree", root, detail=str(error)
        )
    return ContainmentCapability("no-delegated-subtree", root, detail="probe failed")


probe_cgroup_v2 = probe_containment


def sys_platform_linux() -> bool:
    return sys.platform.startswith("linux")


_TRUSTED_JOINER = (
    "import os,sys;"
    "a=sys.argv[1:];"
    "i=a.index('--');"
    "c=a[a.index('--cgroup')+1];"
    "f=os.open(os.path.join(c,'cgroup.procs'),os.O_WRONLY);"
    "os.write(f,(str(os.getpid())+'\\n').encode('ascii'));"
    "os.close(f);"
    "os.execvpe(a[i+1],a[i+1:],os.environ)"
)


_DURABLE_MONITOR = (
    "import os,signal,subprocess,sys;"
    "s,pf=sys.argv[1:3];a=sys.argv[sys.argv.index('--')+1:];"
    "p=subprocess.Popen(a,start_new_session=True);"
    "open(pf,'w',encoding='ascii').write(str(p.pid));"
    "h=lambda n,f: os.kill(p.pid,n);"
    "signal.signal(signal.SIGTERM,h);signal.signal(signal.SIGINT,h);"
    "r=p.wait();"
    "open(s,'w',encoding='ascii').write('absent\\n');"
    "sys.exit(r)"
)


def _launcher_command(cgroup: Path, command: Sequence[str]) -> list[str]:
    """Build an isolated joiner that also works inside packaged runtimes."""
    return [
        _launcher_interpreter(),
        "-I",
        "-S",
        "-c",
        _TRUSTED_JOINER,
        "execution-launcher",
        "--cgroup",
        str(cgroup),
        "--",
        *command,
    ]


def _launcher_interpreter() -> str:
    """Use the embedded interpreter rather than the outer standalone stub."""
    if os.environ.get("PEX") or os.environ.get("SCIE"):
        base = getattr(sys, "_base_executable", "")
        if base and Path(base).is_file():
            return base
    return sys.executable


def _process_is_running(process) -> bool:
    """Observe process completion without requiring a full Popen test double."""
    returncode = getattr(process, "returncode", None)
    if returncode is not None:
        return False
    wait = getattr(process, "wait", None)
    if wait is None:
        return True
    try:
        wait(timeout=0)
    except subprocess.TimeoutExpired:
        return True
    except TypeError:
        # Minimal test doubles expose a settled returncode but not timed wait.
        # Treat an unset returncode as running; the caller still enforces its
        # deadline and never enters an unbounded wait.
        return True
    return getattr(process, "returncode", None) is None


class _MonitoredProcess:
    """Expose the worker identity while a detached monitor owns retirement."""

    def __init__(self, monitor, pid: int) -> None:
        self._monitor = monitor
        self.pid = pid
        self.stdin = monitor.stdin
        self.stdout = monitor.stdout
        self.stderr = monitor.stderr

    @property
    def returncode(self):
        return self._monitor.returncode

    def poll(self):
        return self._monitor.poll()

    def wait(self, timeout=None):
        if timeout is None:
            return self._monitor.wait()
        return self._monitor.wait(timeout=timeout)

    def terminate(self):
        return self._monitor.terminate()

    def kill(self):
        return self._monitor.kill()


class ExecutionContainment:
    """One execution-specific cgroup, including its lifecycle proof."""

    def __init__(self, path: Path, *, recursive_kill: bool) -> None:
        self.path = path
        self.recursive_kill = recursive_kill
        self.identity = f"cgroup:{path}"

    @classmethod
    def create(cls, execution_id: str) -> ExecutionContainment:
        capability = probe_containment()
        if not capability.usable or capability.root is None:
            raise ContainmentError(
                "execution containment unavailable: "
                f"{capability.status}: {capability.detail}"
            )
        safe_id = "".join(
            character
            if character.isalnum() or character in "-_"
            else "_"
            for character in execution_id
        )
        path = capability.root / f"execution-{safe_id}"
        try:
            path.mkdir()
        except OSError as error:
            raise ContainmentError(
                f"cannot create execution cgroup {path}: {error}"
            ) from error
        return cls(path, recursive_kill=capability.recursive_kill)

    def spawn(self, command: Sequence[str], **kwargs):
        """Start a trusted joiner that execs the worker after joining."""
        if not isinstance(command, (list, tuple)) or not command:
            raise ContainmentError("execution command must be a non-empty argv")
        launcher = _launcher_command(self.path, command)
        return subprocess.Popen(launcher, **kwargs)

    def populated(self) -> bool:
        values = (self.path / "cgroup.events").read_text().splitlines()
        state = next((line for line in values if line.startswith("populated ")), None)
        if state is None or state not in {"populated 0", "populated 1"}:
            raise ContainmentError("malformed cgroup.events")
        return state.endswith("1")

    def request_graceful(self, pid: int | None) -> None:
        if pid is not None:
            try:
                os.kill(pid, signal.SIGTERM)
            except (OSError, ProcessLookupError):
                pass

    def force_terminate(self) -> None:
        if not self.recursive_kill:
            raise ContainmentError("recursive cgroup termination is unavailable")
        try:
            (self.path / "cgroup.kill").write_text("1\n")
        except OSError as error:
            raise ContainmentError(
                f"cannot recursively terminate execution cgroup: {error}"
            ) from error

    def wait_empty(self, timeout: float) -> bool:
        deadline = time.monotonic() + timeout
        while True:
            if not self.populated():
                return True
            if time.monotonic() >= deadline:
                return False
            time.sleep(0.05)

    def destroy(self) -> None:
        if self.populated():
            raise ContainmentError("cannot destroy populated execution cgroup")
        try:
            self.path.rmdir()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise ContainmentError(
                f"cannot destroy execution cgroup {self.path}: {error}"
            ) from error


class LinuxCgroupContainmentProvider:
    """Production cgroup-v2 provider.

    Host supervisors only provision the delegated root.  They are deliberately
    absent from this provider's contract.
    """

    def create(self, execution_id: str) -> ExecutionContainment:
        return ExecutionContainment.create(execution_id)

    def observe(self, identity: str) -> str:
        if not identity.startswith("cgroup:"):
            return "indeterminate"
        path = Path(identity.removeprefix("cgroup:"))
        try:
            values = (path / "cgroup.events").read_text().splitlines()
        except FileNotFoundError:
            return "absent"
        except OSError:
            return "indeterminate"
        populated = next(
            (line for line in values if line.startswith("populated ")), None
        )
        if populated == "populated 1":
            return "matching-live"
        if populated == "populated 0":
            return "absent"
        return "indeterminate"


class DeterministicContainmentProvider:
    """Small non-production provider for semantic and supervisor tests.

    It preserves the containment lifecycle contract while using an isolated
    process group instead of claiming kernel cgroup ownership.  Production
    composition never selects this provider.
    """

    def __init__(self, *, populated_after_leader_exit: bool = False) -> None:
        self.populated_after_leader_exit = populated_after_leader_exit

    def observe(self, identity: str) -> str:
        return "indeterminate"

    class _Boundary:
        def __init__(self, *, populated_after_leader_exit: bool = False) -> None:
            self._process = None
            self._synthetic_populated = populated_after_leader_exit
            # The lightweight double is intentionally not durable across a
            # daemon lifetime; use the legacy diagnostic identity fallback.
            self.identity = None

        def spawn(self, command: Sequence[str], **kwargs):
            self._process = subprocess.Popen(command, **kwargs)
            return self._process

        def request_graceful(self, pid: int | None) -> None:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

        def force_terminate(self) -> None:
            if self._process is not None and _process_is_running(self._process):
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except OSError:
                    self._process.kill()

        def wait_empty(self, timeout: float) -> bool:
            if self._synthetic_populated:
                return False
            if self._process is None:
                return True
            deadline = time.monotonic() + timeout
            while _process_is_running(self._process):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
            return True

        def mark_populated(self) -> None:
            """Model a descendant in contract tests without PGID inference."""
            self._synthetic_populated = True

        def mark_empty(self) -> None:
            """Release synthetic descendant population in contract tests."""
            self._synthetic_populated = False

        def destroy(self) -> None:
            return None

    def create(self, execution_id: str) -> DeterministicContainmentProvider._Boundary:
        del execution_id
        return self._Boundary(
            populated_after_leader_exit=self.populated_after_leader_exit
        )


class DurableDeterministicContainmentProvider:
    """Filesystem-backed test boundary that survives a daemon restart.

    This is test composition only.  It models the durable observation contract
    without pretending that a regular file is a kernel ownership boundary.
    """

    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)
        self.root.mkdir(parents=True, exist_ok=True)

    def create(
        self, execution_id: str
    ) -> DurableDeterministicContainmentProvider._Boundary:
        safe_id = "".join(
            character if character.isalnum() or character in "-_" else "_"
            for character in execution_id
        )
        return self._Boundary(
            self.root / f"execution-{safe_id}", f"deterministic:{safe_id}"
        )

    def observe(self, identity: str) -> str:
        if not identity.startswith("deterministic:"):
            return "indeterminate"
        safe_id = identity.removeprefix("deterministic:")
        if not safe_id or Path(safe_id).name != safe_id:
            return "indeterminate"
        state = self.root / f"execution-{safe_id}"
        try:
            value = state.read_text().strip()
            return value if value in {"matching-live", "absent"} else "indeterminate"
        except FileNotFoundError:
            return "absent"
        except OSError:
            return "indeterminate"

    class _Boundary:
        def __init__(self, state: Path, identity: str) -> None:
            self.state = state
            self.identity = identity
            self._process = None

        def spawn(self, command: Sequence[str], **kwargs):
            self.state.parent.mkdir(parents=True, exist_ok=True)
            self.state.write_text("matching-live")
            pid_file = self.state.with_suffix(".pid")
            pid_file.unlink(missing_ok=True)
            monitor = [
                sys.executable,
                "-I",
                "-S",
                "-c",
                _DURABLE_MONITOR,
                str(self.state),
                str(pid_file),
                "--",
                *command,
            ]
            monitor_process = subprocess.Popen(monitor, **kwargs)
            deadline = time.monotonic() + 5
            while not pid_file.is_file() and monitor_process.poll() is None:
                if time.monotonic() >= deadline:
                    monitor_process.kill()
                    raise ContainmentError("durable containment monitor did not start")
                time.sleep(0.01)
            if not pid_file.is_file():
                raise ContainmentError("durable containment worker did not start")
            self._process = _MonitoredProcess(
                monitor_process, int(pid_file.read_text())
            )
            return self._process

        def request_graceful(self, pid: int | None) -> None:
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGTERM)
                except OSError:
                    pass

        def force_terminate(self) -> None:
            if self._process is not None and _process_is_running(self._process):
                try:
                    os.killpg(self._process.pid, signal.SIGKILL)
                except OSError:
                    self._process.kill()

        def wait_empty(self, timeout: float) -> bool:
            if self._process is None:
                return True
            deadline = time.monotonic() + timeout
            while _process_is_running(self._process):
                if time.monotonic() >= deadline:
                    return False
                time.sleep(0.01)
            # The monitor owns this transition, so it also occurs if the
            # daemon that created the boundary is terminated abruptly.
            self.state.write_text("absent")
            return True

        def destroy(self) -> None:
            self.state.with_suffix(".pid").unlink(missing_ok=True)
            self.state.unlink(missing_ok=True)
