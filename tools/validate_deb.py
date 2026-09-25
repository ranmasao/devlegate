#!/usr/bin/env python3
"""Validate the portable Debian proof package policy."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from package_standalone import PackageError, sha256


def fields(package: Path) -> dict[str, str]:
    result = subprocess.run(
        ["dpkg-deb", "-f", str(package)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise PackageError(result.stderr.strip() or "cannot read Debian control data")
    values: dict[str, str] = {}
    for line in result.stdout.splitlines():
        key, separator, value = line.partition(":")
        if separator:
            values[key] = value.strip()
    return values


def validate(package: Path, build_report: Path, extract_dir: Path) -> Path:
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
    with tempfile.TemporaryDirectory(prefix="devlegate-deb-control-") as control_dir:
        result = subprocess.run(
            ["dpkg-deb", "-e", str(package), control_dir],
            text=True,
            capture_output=True,
            check=False,
        )
        if result.returncode:
            raise PackageError(result.stderr.strip() or "cannot extract Debian control")
        forbidden = {"preinst", "postinst", "prerm", "postrm"}
        if forbidden.intersection(path.name for path in Path(control_dir).iterdir()):
            raise PackageError("Debian package contains maintainer scripts")
    report = json.loads(build_report.read_text(encoding="utf-8"))
    if metadata.get("Version") != report["wheel"]["version"]:
        raise PackageError("Debian version does not match standalone build report")
    if extract_dir.exists():
        shutil.rmtree(extract_dir)
    extract_dir.mkdir(parents=True)
    result = subprocess.run(
        ["dpkg-deb", "-x", str(package), str(extract_dir)],
        text=True,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        raise PackageError(result.stderr.strip() or "cannot extract Debian package")
    binary = extract_dir / "usr/lib/devlegate/devlegate"
    link = extract_dir / "usr/bin/devlegate"
    if not binary.is_file() or not binary.stat().st_mode & 0o111:
        raise PackageError("Debian package does not contain an executable payload")
    if not link.is_symlink() or link.resolve() != binary:
        raise PackageError("Debian executable link is invalid")
    systemd_root = extract_dir / "usr/lib/systemd"
    if (extract_dir / "etc/systemd").exists() or (
        systemd_root.exists() and any(systemd_root.glob("**/*"))
    ):
        raise PackageError("Debian package contains a systemd unit")
    documentation = extract_dir / "usr/share/doc/devlegate"
    archive = next(documentation.glob("devlegate-*.tar.gz"), None)
    sidecar = next(documentation.glob("devlegate-*.tar.gz.sha256"), None)
    if archive is None or sidecar is None:
        raise PackageError("Debian package lacks standalone archive proof")
    expected_sidecar = f"{sha256(archive)}  {archive.name}"
    if sidecar.read_text(encoding="ascii").strip() != expected_sidecar:
        raise PackageError("embedded standalone archive sidecar mismatch")
    return binary


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--package", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--extract-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        print(validate(args.package, args.build_report, args.extract_dir))
    except (
        PackageError,
        OSError,
        subprocess.SubprocessError,
        json.JSONDecodeError,
    ) as error:
        print(f"validate-deb: {error}", file=os.sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
