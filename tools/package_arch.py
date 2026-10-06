#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build a dependency-free Arch package from a validated standalone archive."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Callable
from contextlib import contextmanager
from pathlib import Path

try:
    from package_standalone import PackageError
    from package_standalone import run as package_run
    from validate_standalone_package import validate
except ModuleNotFoundError:
    from tools.package_standalone import PackageError
    from tools.package_standalone import run as package_run
    from tools.validate_standalone_package import validate
try:
    from build_progress import ComponentEvent, ComponentPlan, ComponentStep
except ModuleNotFoundError:
    from tools.build_progress import ComponentEvent, ComponentPlan, ComponentStep


def semantic_plan() -> tuple[ComponentStep, ...]:
    return tuple(
        ComponentStep(name, key=key)
        for key, name in (
            ("validate-input", "validate standalone input for Arch"),
            ("assemble", "assemble Arch filesystem"),
            ("metadata", "write Arch package metadata"),
            ("build", "build Arch package"),
            ("checksum", "write Arch package checksum"),
        )
    )


def component_plan() -> ComponentPlan:
    return ComponentPlan(
        "Arch package", tuple(ComponentPlan.leaf(step) for step in semantic_plan())
    )


@contextmanager
def progress_stage(emit: Callable | None, step: ComponentStep):
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
        package_run(["git", "-C", str(repo), "show", "-s", "--format=%ct", commit])
        .stdout.strip()
    )


def set_tree_timestamp(root: Path, timestamp: int) -> None:
    for path in sorted(root.rglob("*"), reverse=True):
        os.utime(path, (timestamp, timestamp), follow_symlinks=False)
    os.utime(root, (timestamp, timestamp), follow_symlinks=False)


def arch_version(version: str) -> str:
    """Map the authoritative project version into an Arch pkgver."""
    mapped = version.replace("+", ".")
    allowed = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789.@_+-"
    if not mapped or any(char not in allowed for char in mapped):
        raise PackageError(
            f"version cannot be represented as an Arch pkgver: {version}"
        )
    return mapped


def package(
    *,
    repo: Path,
    archive: Path,
    sidecar: Path,
    build_report: Path,
    output_dir: Path,
    manifest: Path | None = None,
    emit: Callable | None = None,
) -> Path:
    repo = repo.resolve()
    report = json.loads(build_report.resolve().read_text(encoding="utf-8"))
    version = report["wheel"]["version"]
    pkgver = arch_version(version)
    source_commit = report["source_commit"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"devlegate-{pkgver}-1-x86_64.pkg.tar.zst"
    timestamp = git_timestamp(repo, source_commit)

    with tempfile.TemporaryDirectory(prefix="devlegate-arch-") as temporary:
        temporary_root = Path(temporary)
        with progress_stage(emit, semantic_plan()[0]):
            extracted = validate(
                archive.resolve(),
                sidecar.resolve(),
                repo,
                build_report.resolve(),
                manifest,
                temporary_root / "standalone",
            )
        package_root = temporary_root / "package"
        binary = package_root / "usr/bin/devlegate"
        documentation = package_root / "usr/share/doc/devlegate"
        with progress_stage(emit, semantic_plan()[1]):
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
            shutil.copytree(extracted / "LICENSES", documentation / "LICENSES")
            (documentation / "INSTALLATION-PROVENANCE.json").write_text(
                json.dumps(
                    {
                        "distribution": "arch",
                        "package": "devlegate",
                        "version": version,
                        "source_commit": source_commit,
                        "payload_sha256": hashlib.sha256(
                            binary.read_bytes()
                        ).hexdigest(),
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="ascii",
            )
        with progress_stage(emit, semantic_plan()[2]):
            pkginfo = package_root / ".PKGINFO"
            pkginfo.write_text(
                "pkgname = devlegate\n"
                "pkgbase = devlegate\n"
                f"pkgver = {pkgver}\n"
                "pkgrel = 1\n"
                "pkgdesc = deterministic local agent orchestrator\n"
                "url = https://github.com/ranmasao/devlegate\n"
                f"builddate = {timestamp}\n"
                "packager = Daniil Romanov <romanov.at.bg@gmail.com>\n"
                "arch = x86_64\n"
                "license = EUPL-1.2\n",
                encoding="ascii",
            )
        set_tree_timestamp(package_root, timestamp)
        environment = {**os.environ, "SOURCE_DATE_EPOCH": str(timestamp)}
        with progress_stage(emit, semantic_plan()[3]):
            result = subprocess.run(
                [
                    "tar",
                    "--sort=name",
                    f"--mtime=@{timestamp}",
                    "--owner=0",
                    "--group=0",
                    "--numeric-owner",
                    "--format=gnu",
                    "--zstd",
                    "-cf",
                    str(output),
                    "-C",
                    str(package_root),
                    ".",
                ],
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            if result.returncode:
                detail = result.stderr.strip() or result.stdout.strip() or "no output"
                raise PackageError(f"tar failed: {detail}")
        with progress_stage(emit, semantic_plan()[4]):
            output.with_name(f"{output.name}.sha256").write_text(
                f"{hashlib.sha256(output.read_bytes()).hexdigest()}  {output.name}\n",
                encoding="ascii",
            )
    return output


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    try:
        print(package(**vars(args)))
    except (
        PackageError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"package-arch: {error}", file=os.sys.stderr)
        raise SystemExit(1)
