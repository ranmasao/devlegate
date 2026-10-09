#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate the portable Arch package contract without pacman."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

try:
    from arch_format import ArchFormatError, extract, members
    from package_standalone import (
        COMPLIANCE_DIR,
        PackageError,
        archive_license_path,
        load_manifest,
        manifest_file_records,
        validate_manifest,
    )
    from validate_standalone_package import validate
except ModuleNotFoundError:
    from tools.arch_format import ArchFormatError, extract, members
    from tools.package_standalone import (
        COMPLIANCE_DIR,
        PackageError,
        archive_license_path,
        load_manifest,
        manifest_file_records,
        validate_manifest,
    )
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
    manifest = load_manifest(manifest_path)
    validate_manifest(manifest_path, repo)
    expected = {
        ".PKGINFO",
        "usr/bin/devlegate",
        "usr/share/doc/devlegate/LICENSE",
        "usr/share/doc/devlegate/NOTICE",
        "usr/share/doc/devlegate/LICENSING.md",
        "usr/share/doc/devlegate/THIRD_PARTY_NOTICES.md",
        "usr/share/doc/devlegate/BUILD-PROVENANCE.json",
        "usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json",
        "usr/share/doc/devlegate/LICENSES/standalone-compliance-manifest.json",
    }
    expected.update(
        "usr/share/doc/devlegate/" + archive_license_path(file_record["path"])
        for _record, file_record in manifest_file_records(manifest)
    )
    if set(metadata) != expected:
        raise PackageError("Arch package contains unexpected files")
    if any(name == "dev" or name.startswith("dev/") for name in metadata):
        raise PackageError("Arch package contains source-tree developer tooling")
    if extract_dir.exists():
        import shutil
        shutil.rmtree(extract_dir)
    try:
        extract(package, extract_dir)
    except (ArchFormatError, OSError) as error:
        raise PackageError(str(error)) from error
    binary = extract_dir / "usr/bin/devlegate"
    mode = binary.stat().st_mode
    if not stat.S_ISREG(mode) or not stat.S_IXUSR & mode:
        raise PackageError("Arch executable must be a regular executable file")
    marker = extract_dir / "usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json"
    marker_value = json.loads(marker.read_text(encoding="ascii"))
    if marker_value.get("distribution") != "arch":
        raise PackageError("Arch installation provenance has the wrong distribution")
    if (
        hashlib.sha256(binary.read_bytes()).hexdigest()
        != marker_value.get("payload_sha256")
    ):
        raise PackageError("Arch installation provenance does not match payload")
    embedded_manifest = (
        extract_dir / "usr/share/doc/devlegate/LICENSES/standalone-compliance-manifest.json"
    )
    if embedded_manifest.read_bytes() != manifest_path.read_bytes():
        raise PackageError("embedded compliance manifest differs from repository manifest")
    return binary


validate = validate_package
