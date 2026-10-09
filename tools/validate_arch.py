#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate the portable Arch package contract without pacman."""

from __future__ import annotations

import json
from pathlib import Path

try:
    from arch_format import TOOL_IDENTITY, ArchFormatError, extract, members
    from native_payload import PackageError
    from native_payload import validate as validate_payload
    from package_standalone import COMPLIANCE_DIR
    from validate_standalone_package import validate
except ModuleNotFoundError:
    from tools.arch_format import TOOL_IDENTITY, ArchFormatError, extract, members
    from tools.native_payload import PackageError
    from tools.native_payload import validate as validate_payload
    from tools.package_standalone import COMPLIANCE_DIR
    from tools.validate_standalone_package import validate


def validate_package(
    package: Path,
    build_report: Path,
    extract_dir: Path,
    repo: Path,
    manifest_path: Path | None = None,
) -> Path:
    try:
        metadata = members(package)
    except (ArchFormatError, OSError) as error:
        raise PackageError(str(error)) from error
    required = {
        ".PKGINFO",
        "usr/bin/devlegate",
        "usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json",
    }
    if not required <= metadata.keys():
        raise PackageError("Arch package lacks required metadata or executable")
    try:
        pkginfo = metadata[".PKGINFO"].decode("ascii")
    except UnicodeDecodeError as error:
        raise PackageError("Arch metadata is not valid ASCII") from error
    fields: dict[str, str] = {}
    for line in pkginfo.splitlines():
        if " = " not in line:
            continue
        name, value = line.split(" = ", 1)
        if not name or name in fields:
            raise PackageError("Arch metadata contains an invalid or duplicate field")
        fields[name] = value
    report = json.loads(build_report.read_text(encoding="utf-8"))
    required_fields = {
        "pkgname",
        "pkgbase",
        "pkgver",
        "size",
        "pkgdesc",
        "url",
        "builddate",
        "packager",
        "arch",
        "license",
        "xdata",
    }
    if not required_fields <= fields.keys() or fields.get("xdata") != "pkgtype=pkg":
        raise PackageError("Arch metadata lacks mandatory PKGINFO v2 fields")
    if (
        fields.get("pkgname") != "devlegate"
        or fields.get("pkgbase") != "devlegate"
        or fields.get("arch") != "x86_64"
        or fields.get("license") != "EUPL-1.2"
        or fields.get("xdata") != "pkgtype=pkg"
    ):
        raise PackageError("Arch metadata has the wrong package or architecture")
    if fields.get("pkgver") != f'{report["wheel"]["version"]}-1':
        raise PackageError(
            "Arch version identity does not match standalone build report"
        )
    try:
        if int(fields["size"]) <= 0:
            raise ValueError
    except ValueError as error:
        raise PackageError("Arch metadata has an invalid installed size") from error
    manifest_path = (manifest_path or repo / COMPLIANCE_DIR / "manifest.json").resolve()
    if any(name == "dev" or name.startswith("dev/") for name in metadata):
        raise PackageError("Arch package contains source-tree developer tooling")
    if extract_dir.exists():
        import shutil
        shutil.rmtree(extract_dir)
    try:
        extract(package, extract_dir)
    except (ArchFormatError, OSError) as error:
        raise PackageError(str(error)) from error
    binary = validate_payload(
        extract_dir,
        distribution="arch",
        format_tool=TOOL_IDENTITY,
        repo=repo,
        manifest=manifest_path,
        metadata_members={".PKGINFO"},
        expected_binary_sha256=report["scie"]["sha256"],
    )
    actual_size = sum(
        path.stat().st_size
        for path in extract_dir.rglob("*")
        if path.is_file() and path.name != ".PKGINFO"
    )
    if int(fields["size"]) != actual_size:
        raise PackageError("Arch metadata size does not match the data payload")
    return binary


validate = validate_package
