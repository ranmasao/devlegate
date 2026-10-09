#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Shared installed payload policy for native package adapters."""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

try:
    from package_standalone import (
        PackageError,
        archive_license_path,
        load_manifest,
        manifest_file_records,
        notice_text,
        validate_manifest,
    )
except ModuleNotFoundError:
    from tools.package_standalone import (
        PackageError,
        archive_license_path,
        load_manifest,
        manifest_file_records,
        notice_text,
        validate_manifest,
    )


FIXED_DOCUMENTS = (
    "LICENSE",
    "NOTICE",
    "LICENSING.md",
    "THIRD_PARTY_NOTICES.md",
    "BUILD-PROVENANCE.json",
)


def _manifest_path(repo: Path, manifest: Path | None) -> Path:
    return (
        manifest or repo / "packaging/standalone-compliance/manifest.json"
    ).resolve()


def _expected_license_paths(manifest: dict[str, object]) -> set[str]:
    return {
        archive_license_path(file_record["path"])
        for _record, file_record in manifest_file_records(manifest)
    }


def assemble(
    extracted: Path,
    root: Path,
    *,
    distribution: str,
    format_tool: dict[str, object],
    repo: Path,
    manifest: Path | None = None,
) -> None:
    """Create the normalized installed payload consumed by a format adapter."""
    manifest_path = _manifest_path(repo, manifest)
    manifest_data = load_manifest(manifest_path)
    validate_manifest(manifest_path, repo)
    root.mkdir(parents=True, exist_ok=True)
    binary = root / "usr/bin/devlegate"
    documentation = root / "usr/share/doc/devlegate"
    binary.parent.mkdir(parents=True, exist_ok=True)
    documentation.mkdir(parents=True, exist_ok=True)
    shutil.copy2(extracted / "devlegate", binary)
    binary.chmod(0o755)
    for name in FIXED_DOCUMENTS:
        shutil.copy2(extracted / name, documentation / name)
        (documentation / name).chmod(0o644)
    for relative in sorted(_expected_license_paths(manifest_data)):
        source = extracted / "LICENSES" / relative.removeprefix("LICENSES/")
        if relative in {"LICENSE", "NOTICE", "LICENSING.md"}:
            source = extracted / relative
        destination = documentation / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source, destination)
        destination.chmod(0o644)
    shutil.copy2(
        extracted / "LICENSES/standalone-compliance-manifest.json",
        documentation / "LICENSES/standalone-compliance-manifest.json",
    )
    (documentation / "LICENSES/standalone-compliance-manifest.json").chmod(0o644)
    (documentation / "INSTALLATION-PROVENANCE.json").write_text(
        json.dumps(
            {
                "distribution": distribution,
                "package": "devlegate",
                "payload_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
                "format_tool": format_tool,
            },
            separators=(",", ":"),
        )
        + "\n",
        encoding="ascii",
    )
    (documentation / "INSTALLATION-PROVENANCE.json").chmod(0o644)


def validate(
    root: Path,
    *,
    distribution: str,
    format_tool: dict[str, object],
    repo: Path,
    manifest: Path | None = None,
    metadata_members: set[str] | None = None,
    expected_binary_sha256: str | None = None,
) -> Path:
    """Validate the actual extracted installed payload against source authority."""
    manifest_path = _manifest_path(repo, manifest)
    records = validate_manifest(manifest_path, repo)
    documentation = root / "usr/share/doc/devlegate"
    expected = {"usr/bin/devlegate"}
    expected.update(
        f"usr/share/doc/devlegate/{name}" for name in FIXED_DOCUMENTS
    )
    expected.add("usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json")
    expected.add(
        "usr/share/doc/devlegate/LICENSES/standalone-compliance-manifest.json"
    )
    expected.update(
        f"usr/share/doc/devlegate/{archive_license_path(file_record['path'])}"
        for _record, file_record in records
    )
    metadata_members = metadata_members or set()
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    actual -= metadata_members
    if actual != expected:
        raise PackageError("native package contains unexpected files")
    expected_dirs = {"usr", "usr/bin", "usr/share", "usr/share/doc"}
    for name in expected - metadata_members:
        parent = Path(name).parent
        while parent != Path("."):
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    for name in expected - metadata_members:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise PackageError(f"native payload member is not a regular file: {name}")
        expected_mode = 0o755 if name == "usr/bin/devlegate" else 0o644
        if path.stat().st_mode & 0o7777 != expected_mode:
            raise PackageError(f"native payload member has unexpected mode: {name}")
    actual_dirs = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }
    if actual_dirs != expected_dirs:
        raise PackageError("native package contains unexpected directories")
    binary = root / "usr/bin/devlegate"
    if (
        not binary.is_file()
        or binary.is_symlink()
        or binary.stat().st_mode & 0o111 != 0o111
    ):
        raise PackageError("native executable must be a regular executable file")
    marker = documentation / "INSTALLATION-PROVENANCE.json"
    try:
        marker_value = json.loads(marker.read_text(encoding="ascii"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise PackageError(
            "native installation provenance marker is invalid"
        ) from error
    if (
        marker_value.get("distribution") != distribution
        or marker_value.get("package") != "devlegate"
        or marker_value.get("format_tool") != format_tool
        or marker_value.get("payload_sha256")
        != hashlib.sha256(binary.read_bytes()).hexdigest()
    ):
        raise PackageError("native installation provenance marker is inconsistent")
    binary_digest = hashlib.sha256(binary.read_bytes()).hexdigest()
    if expected_binary_sha256 is not None and binary_digest != expected_binary_sha256:
        raise PackageError("native executable differs from standalone build report")
    for _record, file_record in records:
        relative = archive_license_path(file_record["path"])
        path = documentation / relative
        if hashlib.sha256(path.read_bytes()).hexdigest() != file_record["sha256"]:
            raise PackageError(f"native license snapshot differs: {relative}")
    embedded = documentation / "LICENSES/standalone-compliance-manifest.json"
    if embedded.read_bytes() != manifest_path.read_bytes():
        raise PackageError(
            "embedded compliance manifest differs from repository manifest"
        )
    fixed_sources = {
        "LICENSE": repo / "LICENSE",
        "NOTICE": repo / "NOTICE",
        "LICENSING.md": repo / "LICENSING.md",
    }
    for name, source in fixed_sources.items():
        if (documentation / name).read_bytes() != source.read_bytes():
            raise PackageError(f"native fixed document differs: {name}")
    try:
        provenance = json.loads(
            (documentation / "BUILD-PROVENANCE.json").read_text(encoding="utf-8")
        )
        if (
            provenance["devlegate"]["standalone_sha256"] != binary_digest
            or provenance["devlegate"]["standalone_size"] != binary.stat().st_size
        ):
            raise PackageError("native build provenance differs from payload")
        notices = (documentation / "THIRD_PARTY_NOTICES.md").read_text(
            encoding="utf-8"
        )
        if notices != notice_text(load_manifest(manifest_path)["records"]):
            raise PackageError("native third-party notices differ from manifest")
    except (
        KeyError,
        TypeError,
        ValueError,
        OSError,
        UnicodeError,
        json.JSONDecodeError,
    ) as error:
        raise PackageError("native build provenance is invalid") from error
    return binary
