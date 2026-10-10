#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate the portable Debian proof package policy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import stat
import tempfile
from pathlib import Path

try:
    from native_payload import PackageError
    from native_payload import validate as validate_payload
except ModuleNotFoundError:
    from tools.native_payload import PackageError
    from tools.native_payload import validate as validate_payload
try:
    from deb_format import TOOL_IDENTITY, DebFormatError, control, extract
except ModuleNotFoundError:
    from tools.deb_format import DebFormatError, control, extract
try:
    from distribution_boundary import BoundaryError, validate_members
except ModuleNotFoundError:
    from tools.distribution_boundary import BoundaryError, validate_members


def fields(package: Path) -> dict[str, str]:
    try:
        return control(package)
    except DebFormatError as error:
        raise PackageError(str(error)) from error


def installed_size_kib(root: Path) -> int:
    """Return deterministic Debian Installed-Size units for a data tree."""
    total = 0
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if relative.parts and relative.parts[0] == "DEBIAN":
            continue
        mode = path.lstat().st_mode
        if stat.S_ISREG(mode) or stat.S_ISLNK(mode):
            total += (path.lstat().st_size + 1023) // 1024
        else:
            total += 1
    return max(1, total)


def validate(
    package: Path,
    build_report: Path,
    extract_dir: Path,
    repo: Path | None = None,
    manifest: Path | None = None,
) -> Path:
    metadata = fields(package)
    required = {
        "Package": "devlegate",
        "Architecture": "amd64",
    }
    for key, expected in required.items():
        if metadata.get(key) != expected:
            raise PackageError(f"unexpected Debian control field {key}")
    if "Depends" in metadata or "Pre-Depends" in metadata:
        raise PackageError("portable Debian package declares runtime dependencies")
    try:
        installed_size = int(metadata["Installed-Size"])
    except (KeyError, ValueError) as error:
        raise PackageError("Debian package has an invalid Installed-Size") from error
    if installed_size <= 0:
        raise PackageError("Debian package Installed-Size is not positive")
    with tempfile.TemporaryDirectory(prefix="devlegate-deb-control-") as control_dir:
        try:
            extract(package, Path(control_dir), control_only=True)
        except DebFormatError as error:
            raise PackageError(str(error)) from error
        forbidden = {"preinst", "postinst", "prerm", "postrm"}
        if forbidden.intersection(path.name for path in Path(control_dir).iterdir()):
            raise PackageError("Debian package contains maintainer scripts")
    report = json.loads(build_report.read_text(encoding="utf-8"))
    if metadata.get("Version") != report["wheel"]["version"]:
        raise PackageError("Debian version does not match standalone build report")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    try:
        extract(package, extract_dir)
    except DebFormatError as error:
        raise PackageError(str(error)) from error
    try:
        validate_members(
            {str(path.relative_to(extract_dir)) for path in extract_dir.rglob("*")},
            extract_dir,
            "Debian package",
        )
    except BoundaryError as error:
        raise PackageError(str(error)) from error
    if installed_size != installed_size_kib(extract_dir):
        raise PackageError("Debian Installed-Size does not match the data payload")
    if repo is None:
        raise PackageError("Debian validation requires verified source authority")
    binary = validate_payload(
        extract_dir,
        distribution="debian",
        format_tool=TOOL_IDENTITY,
        repo=repo,
        manifest=manifest,
        expected_binary_sha256=report["scie"]["sha256"],
        standalone_report=report,
    )
    return binary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--extract-dir", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        print(
            validate(
                args.package,
                args.build_report,
                args.extract_dir,
                args.repo,
                args.manifest,
            )
        )
    except (
        PackageError,
        OSError,
        json.JSONDecodeError,
    ) as error:
        print(f"validate-deb: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
