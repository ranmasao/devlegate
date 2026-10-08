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
import sys
import time
from dataclasses import dataclass
from pathlib import Path


class ContainmentError(RuntimeError):
    """The host cannot provide a usable execution containment boundary."""


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
    try:
        probe = root / ".devlegate-capability-probe"
        probe.mkdir()
        (probe / "cgroup.procs").open("a").close()
        recursive_kill = (probe / "cgroup.kill").is_file()
        probe.rmdir()
    except OSError as error:
        if probe is not None:
            try:
                probe.rmdir()
            except OSError:
                pass
        return ContainmentCapability(
            "no-delegated-subtree", root, detail=str(error)
        )
    return ContainmentCapability("available", root, recursive_kill)


probe_cgroup_v2 = probe_containment


def sys_platform_linux() -> bool:
    return sys.platform.startswith("linux")


class ExecutionContainment:
    """One execution-specific cgroup, including its lifecycle proof."""

    def __init__(self, path: Path, *, recursive_kill: bool) -> None:
        self.path = path
        self.recursive_kill = recursive_kill

    @classmethod
    def create(cls, execution_id: str) -> "ExecutionContainment":
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

    def spawn(self, command, **kwargs):
        """Start a child whose first trusted action is joining this cgroup."""

        def join() -> None:
            (self.path / "cgroup.procs").write_text(f"{os.getpid()}\n")

        import subprocess

        return subprocess.Popen(command, preexec_fn=join, **kwargs)

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
        if self.recursive_kill:
            try:
                (self.path / "cgroup.kill").write_text("1\n")
                return
            except OSError:
                pass
        # cgroup.procs is kernel-maintained and includes reparented descendants.
        for _ in range(20):
            try:
                pids = (self.path / "cgroup.procs").read_text().splitlines()
            except OSError:
                return
            for line in pids:
                try:
                    os.kill(int(line), signal.SIGKILL)
                except (OSError, ValueError, ProcessLookupError):
                    pass
            if not pids:
                return
            time.sleep(0.01)

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
