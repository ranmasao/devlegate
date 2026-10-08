# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import os
import subprocess
import sys

from devlegate.execution_containment import (
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
