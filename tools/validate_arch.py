#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate the portable Arch package policy."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import stat
import subprocess
from pathlib import Path, PurePosixPath

try:
    from package_standalone import PackageError
except ModuleNotFoundError:
    from tools.package_standalone import PackageError


def members(package: Path) -> list[str]:
    result = subprocess.run(
        ["tar", "--zstd", "-tf", str(package)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise PackageError(result.stderr.strip() or "cannot read Arch package")
    return [
        name
        for line in result.stdout.splitlines()
        if line
        for name in (line.removeprefix("./").removesuffix("/"),)
        if name
    ]


def fields(package: Path) -> dict[str, list[str]]:
    result = subprocess.run(
        ["tar", "--zstd", "-xOf", str(package), "./.PKGINFO"],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise PackageError("Arch package lacks .PKGINFO")
    values: dict[str, list[str]] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(" = ")
        if separator:
            values.setdefault(key, []).append(value)
    return values


def validate(package: Path, build_report: Path, extract_dir: Path) -> Path:
    package = package.resolve()
    metadata = fields(package)
    expected = {
        "pkgname": ["devlegate"],
        "pkgbase": ["devlegate"],
        "pkgrel": ["1"],
        "arch": ["x86_64"],
    }
    for key, value in expected.items():
        if metadata.get(key) != value:
            raise PackageError(f"unexpected Arch package field {key}")
    dependency_fields = ("depend", "makedepend", "checkdepend", "optdepend")
    if any(key in metadata for key in dependency_fields):
        raise PackageError("Arch package declares runtime dependencies")
    try:
        pkgver = metadata["pkgver"][0]
    except (KeyError, IndexError) as error:
        raise PackageError("Arch package lacks pkgver") from error
    report = json.loads(build_report.read_text(encoding="utf-8"))
    if pkgver != report["wheel"]["version"].replace("+", "."):
        raise PackageError("Arch version does not match standalone build report")
    names = members(package)
    if len(names) != len(set(names)) or ".PKGINFO" not in names:
        raise PackageError("Arch package has invalid or duplicate members")
    if any(name.startswith("/") or ".." in PurePosixPath(name).parts for name in names):
        raise PackageError("Arch package contains an unsafe path")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    result = subprocess.run(
        ["tar", "--zstd", "-xf", str(package), "-C", str(extract_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise PackageError(result.stderr.strip() or "cannot extract Arch package")
    if any(path.is_symlink() for path in extract_dir.rglob("*")):
        raise PackageError("Arch package contains symlink substitutions")
    binary = extract_dir / "usr/bin/devlegate"
    try:
        mode = binary.lstat().st_mode
    except FileNotFoundError as error:
        raise PackageError(
            "Arch package does not contain an executable payload"
        ) from error
    if not stat.S_ISREG(mode) or not mode & 0o111:
        raise PackageError("Arch executable must be a regular executable file")
    documentation = extract_dir / "usr/share/doc/devlegate"
    required = {
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
        "INSTALLATION-PROVENANCE.json",
    }
    if not (documentation / "LICENSES").is_dir() or any(
        not (documentation / name).is_file() for name in required
    ):
        raise PackageError("Arch package lacks compact compliance material")
    marker = documentation / "INSTALLATION-PROVENANCE.json"
    try:
        provenance = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PackageError("Arch installation provenance marker is invalid") from error
    if not isinstance(provenance, dict) or any(
        provenance.get(key) != value
        for key, value in {
            "distribution": "arch",
            "package": "devlegate",
            "version": report["wheel"]["version"],
            "source_commit": report["source_commit"],
        }.items()
    ):
        raise PackageError("Arch installation provenance marker is invalid")
    digest = provenance.get("payload_sha256")
    if not isinstance(digest, str) or hashlib.sha256(
        binary.read_bytes()
    ).hexdigest() != digest:
        raise PackageError("Arch installation provenance does not match payload")
    allowed = {
        ".PKGINFO",
        "usr",
        "usr/bin",
        "usr/bin/devlegate",
        "usr/share",
        "usr/share/doc",
        "usr/share/doc/devlegate",
    }
    allowed.update(
        f"usr/share/doc/devlegate/{path.relative_to(documentation)}"
        for path in documentation.rglob("*")
    )
    if set(names) - allowed:
        raise PackageError("Arch package contains unexpected files")
    return binary


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--extract-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        print(validate(args.package, args.build_report, args.extract_dir))
    except (
        PackageError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"validate-arch: {error}", file=os.sys.stderr)
        raise SystemExit(1)
