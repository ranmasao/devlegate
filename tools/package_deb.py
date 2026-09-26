#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build a dependency-free Debian package from a validated standalone archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from package_standalone import PackageError
from package_standalone import run as package_run
from validate_deb import installed_size_kib
from validate_standalone_package import validate


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


def package(
    *,
    repo: Path,
    archive: Path,
    sidecar: Path,
    build_report: Path,
    output_dir: Path,
    manifest: Path | None = None,
) -> Path:
    repo = repo.resolve()
    archive = archive.resolve()
    sidecar = sidecar.resolve()
    build_report = build_report.resolve()
    report = json.loads(build_report.read_text(encoding="utf-8"))
    version = report["wheel"]["version"]
    source_commit = report["source_commit"]
    output_dir.mkdir(parents=True, exist_ok=True)
    output = output_dir / f"devlegate_{version}_amd64.deb"

    with tempfile.TemporaryDirectory(prefix="devlegate-deb-") as temporary:
        temporary_root = Path(temporary)
        extracted = validate(
            archive,
            sidecar,
            repo,
            build_report,
            manifest,
            temporary_root / "standalone",
        )
        package_root = temporary_root / "package"
        control = package_root / "DEBIAN"
        binary = package_root / "usr/bin/devlegate"
        documentation = package_root / "usr/share/doc/devlegate"
        control.mkdir(parents=True)
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
        installed_size = installed_size_kib(package_root)
        (control / "control").write_text(
            "Package: devlegate\n"
            f"Version: {version}\n"
            "Section: devel\n"
            "Priority: optional\n"
            "Architecture: amd64\n"
            f"Installed-Size: {installed_size}\n"
            "Maintainer: Devlegate maintainers <maintainers@devlegate.invalid>\n"
            "Description: deterministic local agent orchestrator\n"
            " Dependency-free standalone Devlegate executable.\n",
            encoding="ascii",
        )
        timestamp = git_timestamp(repo, source_commit)
        set_tree_timestamp(package_root, timestamp)
        environment = {**os.environ, "SOURCE_DATE_EPOCH": str(timestamp)}
        result = subprocess.run(
            [
                "dpkg-deb",
                "--build",
                "--root-owner-group",
                "-Zgzip",
                str(package_root),
                str(output),
            ],
            env=environment,
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            detail = result.stderr.strip() or result.stdout.strip() or "no output"
            raise PackageError(f"dpkg-deb failed: {detail}")
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


def main() -> int:
    args = parse_args()
    try:
        print(
            package(
                repo=args.repo,
                archive=args.archive,
                sidecar=args.sidecar,
                build_report=args.build_report,
                output_dir=args.output_dir,
                manifest=args.manifest,
            )
        )
    except (
        PackageError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"package-deb: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
