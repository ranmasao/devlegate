#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Validate an extracted standalone compliance archive."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import tarfile
import tempfile
from pathlib import Path

from package_standalone import (
    COMPLIANCE_DIR,
    PackageError,
    archive_license_path,
    load_manifest,
    manifest_file_records,
    notice_text,
    run,
    sha256,
    split_inventory,
    stable_provenance,
    validate_manifest,
)

FORBIDDEN_EPHEMERAL_PATHS = (b"/tmp/devlegate-standalone-", b"/tmp/devlegate-package-")


def safe_extract(archive: Path, destination: Path, expected_root: str) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members or members[0].name != expected_root:
            raise PackageError("archive does not have the expected top-level root")
        names: set[str] = set()
        for member in members:
            path = Path(member.name)
            if member.name in names or path.is_absolute() or ".." in path.parts:
                raise PackageError(f"unsafe or duplicate archive member: {member.name}")
            if not (
                member.name == expected_root
                or member.name.startswith(expected_root + "/")
            ):
                raise PackageError(f"archive member escapes root: {member.name}")
            if not (member.isdir() or member.isreg()):
                raise PackageError(f"unsupported archive member type: {member.name}")
            expected_mode = 0o755 if member.isdir() else 0o644
            if member.name == f"{expected_root}/devlegate":
                expected_mode = 0o755
            if member.mode & 0o7777 != expected_mode:
                raise PackageError(f"unexpected archive mode: {member.name}")
            names.add(member.name)
        tar.extractall(destination, filter="data")
    return destination / expected_root


def expected_files(manifest: dict[str, object]) -> set[str]:
    files = {
        "devlegate",
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
        "LICENSES/standalone-compliance-manifest.json",
    }
    for record, file_record in manifest_file_records(manifest):
        files.add(archive_license_path(file_record["path"]))
    return files


def expected_directories(files: set[str]) -> set[str]:
    directories = {"LICENSES"}
    for file in files:
        path = Path(file).parent
        while path != Path("."):
            directories.add(str(path))
            path = path.parent
    return directories


def reject_ephemeral_paths(root: Path) -> None:
    for path in root.rglob("*"):
        if path.is_file() and any(
            value in path.read_bytes() for value in FORBIDDEN_EPHEMERAL_PATHS
        ):
            raise PackageError(f"archive member contains an ephemeral path: {path}")


def validate(
    archive: Path,
    sidecar: Path,
    repo: Path,
    build_report: Path,
    manifest_path: Path | None,
    extract_dir: Path,
) -> Path:
    report = json.loads(build_report.read_text(encoding="utf-8"))
    manifest_path = (manifest_path or repo / COMPLIANCE_DIR / "manifest.json").resolve()
    manifest = load_manifest(manifest_path)
    validate_manifest(manifest_path, repo)
    digest = sha256(archive)
    if sidecar.read_text(encoding="ascii").strip() != f"{digest}  {archive.name}":
        raise PackageError("archive SHA-256 sidecar mismatch")
    version = report["wheel"]["version"]
    root_name = f"devlegate-{version}-linux-x86_64"
    root = safe_extract(archive, extract_dir, root_name)
    actual_files = {
        str(path.relative_to(root))
        for path in root.rglob("*")
        if path.is_file()
    }
    expected = expected_files(manifest)
    if actual_files != expected:
        raise PackageError(
            f"archive file set mismatch: missing={sorted(expected - actual_files)}, "
            f"unexpected={sorted(actual_files - expected)}"
        )
    actual_directories = {
        str(path.relative_to(root)) for path in root.rglob("*") if path.is_dir()
    }
    expected_dirs = expected_directories(expected)
    if actual_directories != expected_dirs:
        raise PackageError(
            "archive directory set mismatch: "
            f"missing={sorted(expected_dirs - actual_directories)}, "
            f"unexpected={sorted(actual_directories - expected_dirs)}"
        )
    executable = root / "devlegate"
    if not executable.stat().st_mode & 0o111:
        raise PackageError("standalone executable is not executable")
    for path in root.rglob("*"):
        if path.is_file() and path != executable and path.stat().st_mode & 0o111:
            raise PackageError(f"unexpected executable package member: {path}")
    if run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip() != report[
        "source_commit"
    ]:
        raise PackageError("source commit does not match selected repository state")
    provenance = json.loads((root / "BUILD-PROVENANCE.json").read_text())
    if provenance["devlegate"]["standalone_sha256"] != sha256(executable):
        raise PackageError("BUILD-PROVENANCE executable hash mismatch")
    if provenance["devlegate"]["standalone_sha256"] != report["scie"]["sha256"]:
        raise PackageError("package executable differs from standalone build report")
    embedded_manifest = root / "LICENSES/standalone-compliance-manifest.json"
    if embedded_manifest.read_bytes() != manifest_path.read_bytes():
        raise PackageError(
            "embedded compliance manifest differs from repository manifest"
        )
    notices = (root / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    if notices != notice_text(manifest["records"]):
        raise PackageError("notice file does not match deterministic regeneration")
    with tempfile.TemporaryDirectory(prefix="devlegate-validate-") as split_dir:
        expected_provenance = stable_provenance(
            report, manifest_path, split_inventory(executable, Path(split_dir))
        )
    if provenance != expected_provenance:
        raise PackageError("BUILD-PROVENANCE does not match reconstructed provenance")
    if provenance["devlegate"]["standalone_size"] != executable.stat().st_size:
        raise PackageError("BUILD-PROVENANCE executable size mismatch")
    reject_ephemeral_paths(root)
    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    parser.add_argument("--extract-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = validate(
            args.archive,
            args.sidecar,
            args.repo,
            args.build_report,
            args.manifest,
            args.extract_dir,
        )
    except (PackageError, OSError, tarfile.TarError, json.JSONDecodeError) as error:
        print(f"validate-standalone-package: {error}", file=os.sys.stderr)
        return 1
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
