# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import os
import subprocess
import sys

from devlegate.execution_containment import (
    DurableDeterministicContainmentProvider,
    LinuxCgroupContainmentProvider,
    _launcher_command,
    default_containment_provider,
)


def test_default_provider_is_production_cgroup_provider():
    assert isinstance(default_containment_provider(), LinuxCgroupContainmentProvider)


def test_isolated_joiner_blocks_startup_hook_before_membership(tmp_path, monkeypatch):
    boundary = tmp_path / "execution"
    boundary.mkdir()
    cgroup_procs = boundary / "cgroup.procs"
    cgroup_procs.write_text("")
    marker = tmp_path / "pre_join-hook"
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    (hooks / "sitecustomize.py").write_text(
        "import os\n"
        "from pathlib import Path\n"
        f"procs = Path({str(cgroup_procs)!r}).read_text()\n"
        f"if str(os.getpid()) not in procs.split(): "
        f"Path({str(marker)!r}).write_text('early')\n"
    )
    monkeypatch.setenv("PEX", "standalone")
    monkeypatch.setenv("SCIE", "standalone")
    monkeypatch.setenv("PYTHONPATH", str(hooks))
    worker = tmp_path / "worker"
    command = _launcher_command(
        boundary,
        [
            sys.executable,
            "-c",
            "import pathlib, sys; pathlib.Path(sys.argv[1]).write_text('joined')",
            str(worker),
        ],
    )

    result = subprocess.run(command, env=os.environ, capture_output=True, text=True)

    assert result.returncode == 0, result.stderr
    assert worker.read_text() == "joined"
    assert not marker.exists()
    assert cgroup_procs.read_text()


def test_isolated_joiner_has_no_source_launcher_dependency(tmp_path):
    boundary = tmp_path / "execution"
    boundary.mkdir()
    (boundary / "cgroup.procs").write_text("")
    command = _launcher_command(boundary, [sys.executable, "-c", "pass"])

    assert "execution_launcher.py" not in command
    assert command[1:4] == ["-I", "-S", "-c"]


def test_durable_provider_observes_boundary_after_provider_restart(tmp_path):
    first = DurableDeterministicContainmentProvider(str(tmp_path / "root"))
    boundary = first.create("execution/one")
    process = boundary.spawn([sys.executable, "-c", "pass"])

    second = DurableDeterministicContainmentProvider(tmp_path / "root")

    assert boundary.identity == "deterministic:execution_one"
    assert second.observe(boundary.identity) == "matching-live"

    process.wait()
    assert second.observe(boundary.identity) == "absent"
    assert boundary.wait_empty(1) is True
    assert second.observe(boundary.identity) == "absent"


def test_durable_provider_observes_absent_after_boundary_is_destroyed(tmp_path):
    provider = DurableDeterministicContainmentProvider(tmp_path / "root")
    boundary = provider.create("finished")
    boundary.state.parent.mkdir(parents=True, exist_ok=True)
    boundary.state.write_text("absent")

    boundary.destroy()

    restarted = DurableDeterministicContainmentProvider(str(tmp_path / "root"))
    assert restarted.observe(boundary.identity) == "absent"


def test_durable_provider_rejects_malformed_or_foreign_identity(tmp_path):
    provider = DurableDeterministicContainmentProvider(tmp_path / "root")

    assert provider.observe("cgroup:/some/path") == "indeterminate"
    assert provider.observe("deterministic:") == "indeterminate"
    assert provider.observe("deterministic:../escape") == "indeterminate"
    assert provider.observe("deterministic:missing") == "absent"


def test_linux_provider_observes_cgroup_states(tmp_path):
    provider = LinuxCgroupContainmentProvider()
    boundary = tmp_path / "execution"
    boundary.mkdir()
    events = boundary / "cgroup.events"
    identity = f"cgroup:{boundary}"

    events.write_text("populated 1\n")
    assert provider.observe(identity) == "matching-live"
    events.write_text("populated 0\n")
    assert provider.observe(identity) == "absent"
    events.write_text("not-a-cgroup-state\n")
    assert provider.observe(identity) == "indeterminate"
    assert provider.observe(f"cgroup:{tmp_path / 'missing'}") == "absent"
