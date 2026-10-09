#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build a dependency-free Arch package from the standalone payload."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

try:
    from build_progress import ComponentEvent, ComponentPlan, ComponentStep
except ModuleNotFoundError:
    from tools.build_progress import ComponentEvent, ComponentPlan, ComponentStep

try:
    from arch_format import TOOL_IDENTITY, build
    from package_standalone import run as package_run
    from validate_standalone_package import validate
except ModuleNotFoundError:
    from tools.arch_format import TOOL_IDENTITY, build
    from tools.package_standalone import run as package_run
    from tools.validate_standalone_package import validate


@contextmanager
def progress_stage(emit: Callable | None, step: ComponentStep):
    """Emit the package component's exact local build leaf events."""
    if emit is not None:
        emit(ComponentEvent("start", step, step.identity))
    try:
        yield
    except Exception:
        if emit is not None:
            emit(ComponentEvent("fail", step, step.identity))
        raise
    else:
        if emit is not None:
            emit(ComponentEvent("complete", step, step.identity))


def git_timestamp(repo: Path, commit: str) -> int:
    return int(
        package_run(
            ["git", "-C", str(repo), "show", "-s", "--format=%ct", commit]
        ).stdout.strip()
    )


def set_tree_timestamp(root: Path, timestamp: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    os.utime(root, (timestamp, timestamp), follow_symlinks=False)


def _package(
    *, repo: Path, archive: Path, sidecar: Path, build_report: Path, output_dir: Path,
    emit: Callable | None = None,
) -> Path:
    report = json.loads(build_report.read_text(encoding="utf-8"))
    version = report["wheel"]["version"]
    source_commit = report["source_commit"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"devlegate-{version}-1-x86_64.pkg.tar.zst"
    with tempfile.TemporaryDirectory(prefix="devlegate-arch-") as temporary:
        extracted = validate(
            archive, sidecar, repo, build_report, None, Path(temporary) / "standalone"
        )
        root = Path(temporary) / "package"
        binary = root / "usr/bin/devlegate"
        documentation = root / "usr/share/doc/devlegate"
        binary.parent.mkdir(parents=True)
        documentation.mkdir(parents=True)
        shutil.copy2(extracted / "devlegate", binary)
        binary.chmod(0o755)
        for name in (
            "LICENSE",
            "NOTICE",
            "LICENSING.md",
            "THIRD_PARTY_NOTICES.md",
            "BUILD-PROVENANCE.json",
        ):
            shutil.copy2(extracted / name, documentation / name)
        (documentation / "INSTALLATION-PROVENANCE.json").write_text(
            json.dumps(
                {
                    "distribution": "arch",
                    "package": "devlegate",
                    "payload_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                    "format_tool": TOOL_IDENTITY,
                },
                separators=(",", ":"),
            )
            + "\n",
            encoding="ascii",
        )
        shutil.copytree(extracted / "LICENSES", documentation / "LICENSES")
        installed_size = sum(
            path.stat().st_size for path in root.rglob("*") if path.is_file()
        )
        (root / ".PKGINFO").write_text(
            "pkgname = devlegate\n"
            "pkgbase = devlegate\n"
            f"pkgver = {version}-1\n"
            f"size = {installed_size}\n"
            "pkgdesc = deterministic local agent orchestrator\n"
            "url = https://github.com/ranmasao/devlegate\n"
            "builddate = 0\n"
            "packager = Devlegate maintainers\n"
            "arch = x86_64\n"
            "license = EUPL-1.2\n"
            "xdata = pkgtype=pkg\n",
            encoding="ascii",
        )
        set_tree_timestamp(root, git_timestamp(repo, source_commit))
        build(root, output, timestamp=git_timestamp(repo, source_commit))
    output.with_name(f"{output.name}.sha256").write_text(
        f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n",
        encoding="ascii",
    )
    return output


def package(
    *, repo: Path, archive: Path, sidecar: Path, build_report: Path, output_dir: Path,
    emit: Callable | None = None,
) -> Path:
    with progress_stage(emit, semantic_plan()[0]):
        return _package(
            repo=repo,
            archive=archive,
            sidecar=sidecar,
            build_report=build_report,
            output_dir=output_dir,
        )


def semantic_plan() -> tuple[ComponentStep, ...]:
    return (ComponentStep("build Arch package", key="build"),)


def component_plan() -> ComponentPlan:
    return ComponentPlan(
        "Arch package", tuple(ComponentPlan.leaf(step) for step in semantic_plan())
    )
