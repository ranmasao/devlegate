#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Shared installed payload policy for native package adapters."""

from __future__ import annotations

import hashlib
import json
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

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


@dataclass(frozen=True)
class PayloadLayout:
    """Physical paths selected by a native format adapter."""

    executable: str = "usr/bin/devlegate"
    documentation: str = "usr/share/doc/devlegate"
    licenses: str = "LICENSES"
    provenance: str = "INSTALLATION-PROVENANCE.json"

    def __post_init__(self) -> None:
        for name in ("executable", "documentation", "licenses", "provenance"):
            _safe_destination(getattr(self, name), allow_file=name != "licenses")


@dataclass(frozen=True)
class PayloadEntry:
    """One logical payload member resolved to a format-specific destination."""

    role: str
    destination: str
    source: str | None
    mode: int
    digest: str | None = None


def _safe_destination(value: str, *, allow_file: bool) -> str:
    path = PurePosixPath(value)
    if (
        not value
        or path.is_absolute()
        or ".." in path.parts
        or "." in path.parts
        or "\\" in value
        or (not allow_file and path.name != value.split("/")[-1])
    ):
        raise PackageError(f"native payload layout path is unsafe: {value}")
    return path.as_posix()


def layout_for(distribution: str) -> PayloadLayout:
    """Return the installed layout for a supported native format."""
    if distribution not in {"arch", "debian"}:
        raise PackageError(f"unsupported native distribution: {distribution}")
    return PayloadLayout()


def _manifest_path(repo: Path, manifest: Path | None) -> Path:
    return (
        manifest or repo / "packaging/standalone-compliance/manifest.json"
    ).resolve()


def _expected_license_paths(manifest: dict[str, object]) -> set[str]:
    return {
        archive_license_path(file_record["path"])
        for _record, file_record in manifest_file_records(manifest)
    }


def _inventory(
    manifest: dict[str, object], layout: PayloadLayout, repo: Path | None = None
) -> tuple[PayloadEntry, ...]:
    """Build the single logical inventory used by assembly and validation."""
    documentation = _safe_destination(layout.documentation, allow_file=False)
    licenses = _safe_destination(
        f"{documentation}/{layout.licenses}", allow_file=False
    )
    digests = {
        archive_license_path(file_record["path"]): file_record["sha256"]
        for _record, file_record in manifest_file_records(manifest)
    }
    entries = [
        PayloadEntry("executable", layout.executable, "devlegate", 0o755),
        *(
            PayloadEntry(
                "fixed-document",
                f"{documentation}/{name}",
                name,
                0o644,
                digests.get(name)
                or (
                    hashlib.sha256((repo / name).read_bytes()).hexdigest()
                    if repo is not None and (repo / name).is_file()
                    else None
                ),
            )
            for name in FIXED_DOCUMENTS
        ),
        PayloadEntry(
            "provenance",
            f"{documentation}/{layout.provenance}",
            None,
            0o644,
        ),
        PayloadEntry(
            "manifest",
            f"{licenses}/standalone-compliance-manifest.json",
            "LICENSES/standalone-compliance-manifest.json",
            0o644,
        ),
    ]
    for relative in sorted(_expected_license_paths(manifest)):
        if relative in {"LICENSE", "NOTICE", "LICENSING.md"}:
            continue
        source = relative.removeprefix("LICENSES/")
        source = f"LICENSES/{source}"
        entries.append(
            PayloadEntry(
                "license",
                f"{documentation}/{relative}",
                source,
                0o644,
                next(
                    file_record["sha256"]
                    for _record, file_record in manifest_file_records(manifest)
                    if archive_license_path(file_record["path"]) == relative
                ),
            )
        )
    destinations = [entry.destination for entry in entries]
    if len(destinations) != len(set(destinations)) or any(
        left.startswith(f"{right}/") or right.startswith(f"{left}/")
        for left in destinations
        for right in destinations
        if left != right
    ):
        raise PackageError("native payload inventory contains path collisions")
    return tuple(entries)


def assemble(
    extracted: Path,
    root: Path,
    *,
    distribution: str,
    format_tool: dict[str, object],
    repo: Path,
    manifest: Path | None = None,
    layout: PayloadLayout | None = None,
) -> None:
    """Create the normalized installed payload consumed by a format adapter."""
    manifest_path = _manifest_path(repo, manifest)
    manifest_data = load_manifest(manifest_path)
    validate_manifest(manifest_path, repo)
    layout = layout or layout_for(distribution)
    inventory = _inventory(manifest_data, layout, repo)
    root.mkdir(parents=True, exist_ok=True)
    for entry in inventory:
        destination = root / entry.destination
        destination.parent.mkdir(parents=True, exist_ok=True)
        if entry.role == "provenance":
            binary = root / next(
                item.destination for item in inventory if item.role == "executable"
            )
            destination.write_text(
                json.dumps(
                    {
                        "distribution": distribution,
                        "package": "devlegate",
                        "payload_sha256": hashlib.sha256(
                            binary.read_bytes()
                        ).hexdigest(),
                        "format_tool": format_tool,
                    },
                    separators=(",", ":"),
                )
                + "\n",
                encoding="ascii",
            )
        else:
            if entry.source is None:
                raise PackageError(f"native payload entry has no source: {entry.role}")
            shutil.copy2(extracted / entry.source, destination)
        destination.chmod(entry.mode)


def validate(
    root: Path,
    *,
    distribution: str,
    format_tool: dict[str, object],
    repo: Path,
    manifest: Path | None = None,
    metadata_members: set[str] | None = None,
    expected_binary_sha256: str | None = None,
    standalone_report: dict[str, object] | None = None,
    layout: PayloadLayout | None = None,
) -> Path:
    """Validate the actual extracted installed payload against source authority."""
    if metadata_members and distribution != "arch":
        raise PackageError("format metadata cannot be excluded from native payload")
    manifest_path = _manifest_path(repo, manifest)
    manifest_data = load_manifest(manifest_path)
    validate_manifest(manifest_path, repo)
    if not expected_binary_sha256:
        raise PackageError("native validation requires standalone executable digest")
    layout = layout or layout_for(distribution)
    entries = _inventory(manifest_data, layout, repo)
    expected = {entry.destination for entry in entries}
    metadata_members = metadata_members or set()
    actual = {
        path.relative_to(root).as_posix()
        for path in root.rglob("*")
        if path.is_file() or path.is_symlink()
    }
    actual -= metadata_members
    if actual != expected:
        raise PackageError("native package contains unexpected files")
    expected_dirs: set[str] = set()
    for name in expected - metadata_members:
        parent = Path(name).parent
        while parent != Path("."):
            expected_dirs.add(parent.as_posix())
            parent = parent.parent
    for name in expected - metadata_members:
        path = root / name
        if not path.is_file() or path.is_symlink():
            raise PackageError(f"native payload member is not a regular file: {name}")
        expected_mode = next(
            entry.mode for entry in entries if entry.destination == name
        )
        if path.stat().st_mode & 0o7777 != expected_mode:
            raise PackageError(f"native payload member has unexpected mode: {name}")
    actual_dirs = {
        path.relative_to(root).as_posix() for path in root.rglob("*") if path.is_dir()
    }
    if actual_dirs != expected_dirs:
        raise PackageError("native package contains unexpected directories")
    executable_entry = next(entry for entry in entries if entry.role == "executable")
    binary = root / executable_entry.destination
    if (
        not binary.is_file()
        or binary.is_symlink()
        or binary.stat().st_mode & 0o111 != 0o111
    ):
        raise PackageError("native executable must be a regular executable file")
    marker_entry = next(entry for entry in entries if entry.role == "provenance")
    marker = root / marker_entry.destination
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
    if binary_digest != expected_binary_sha256:
        raise PackageError("native executable differs from standalone build report")
    for entry in entries:
        if entry.role in {"license", "fixed-document"} and entry.digest:
            path = root / entry.destination
            if hashlib.sha256(path.read_bytes()).hexdigest() != entry.digest:
                raise PackageError(
                    f"native payload snapshot differs: {entry.destination}"
                )
    embedded = root / next(
        entry.destination for entry in entries if entry.role == "manifest"
    )
    if embedded.read_bytes() != manifest_path.read_bytes():
        raise PackageError(
            "embedded compliance manifest differs from repository manifest"
        )
    fixed_sources = {
        entry.destination: repo / entry.source
        for entry in entries
        if entry.role == "fixed-document"
        and entry.source in {"LICENSE", "NOTICE", "LICENSING.md"}
    }
    for name, source in fixed_sources.items():
        if (root / name).read_bytes() != source.read_bytes():
            raise PackageError(f"native fixed document differs: {Path(name).name}")
    try:
        provenance = json.loads(
            (root / next(
                entry.destination
                for entry in entries
                if entry.source == "BUILD-PROVENANCE.json"
            )).read_text(encoding="utf-8")
        )
        if (
            provenance["devlegate"]["standalone_sha256"] != binary_digest
            or provenance["devlegate"]["standalone_size"] != binary.stat().st_size
        ):
            raise PackageError("native build provenance differs from payload")
        if standalone_report is not None and {
            "inputs",
            "target_libc",
            "pex",
            "science",
            "scie_jump",
            "wheel_build_toolchain",
        } <= standalone_report.keys():
            try:
                from package_standalone import split_inventory, stable_provenance
            except ModuleNotFoundError:
                from tools.package_standalone import split_inventory, stable_provenance
            with tempfile.TemporaryDirectory(
                prefix="devlegate-native-report-"
            ) as split_dir:
                expected_provenance = stable_provenance(
                    standalone_report,
                    manifest_path,
                    split_inventory(binary, Path(split_dir)),
                    repo,
                )
            if provenance != expected_provenance:
                raise PackageError(
                    "native build provenance differs from standalone evidence"
                )
        notices = (root / next(
            entry.destination
            for entry in entries
            if entry.source == "THIRD_PARTY_NOTICES.md"
        )).read_text(
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
