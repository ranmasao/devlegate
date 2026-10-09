#!/usr/bin/env python3
"""Validate the portable Arch package contract without pacman."""

from __future__ import annotations

import hashlib
import json
import stat
from pathlib import Path

try:
    from arch_format import ArchFormatError, extract, members
    from package_standalone import PackageError
    from validate_standalone_package import validate
except ModuleNotFoundError:
    from tools.arch_format import ArchFormatError, extract, members
    from tools.package_standalone import PackageError
    from tools.validate_standalone_package import validate


def validate_package(package: Path, build_report: Path, extract_dir: Path) -> Path:
    try:
        metadata = members(package)
    except (ArchFormatError, OSError) as error:
        raise PackageError(str(error)) from error
    required = {".PKGINFO", "usr/bin/devlegate"}
    if not required <= metadata.keys():
        raise PackageError("Arch package lacks required metadata or executable")
    pkginfo = metadata[".PKGINFO"].decode("ascii")
    fields = dict(line.split(" = ", 1) for line in pkginfo.splitlines() if " = " in line)
    report = json.loads(build_report.read_text(encoding="utf-8"))
    if fields.get("pkgname") != "devlegate" or fields.get("arch") != "x86_64":
        raise PackageError("Arch metadata has the wrong package or architecture")
    if fields.get("pkgver") != report["wheel"]["version"] or fields.get("pkgrel") != "1":
        raise PackageError("Arch version identity does not match standalone build report")
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
    if not stat.S_ISREG(binary.stat().st_mode) or not stat.S_IXUSR & binary.stat().st_mode:
        raise PackageError("Arch executable must be a regular executable file")
    marker = extract_dir / "usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json"
    marker_value = json.loads(marker.read_text(encoding="ascii"))
    if hashlib.sha256(binary.read_bytes()).hexdigest() != marker_value.get("payload_sha256"):
        raise PackageError("Arch installation provenance does not match payload")
    return binary


validate = validate_package
