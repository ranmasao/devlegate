#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Package a verified standalone executable with repository-owned notices."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

COMPLIANCE_DIR = "packaging/standalone-compliance"
MANIFEST_SCHEMA = "devlegate.standalone-compliance.v1"
TARGET = "linux-x86_64"
LIBC = "glibc"
PBS_ARCHIVE = "cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_SHA256 = "936c246dfdbbfa7cb22dd01814a21f582a892689fae96b06071a5e433baffa22"
PEX_VERSION = "2.103.2"
PEX_WHEEL_SHA256 = "f1316f1f6f0e125c44c8d6f49cd6ebc4b294b7582a385b3999e814824e607ec7"
SCIENCE_VERSION = "0.21.0"
SCIE_JUMP_VERSION = "1.13.0"
SCIE_JUMP_ASSET = "scie-jump-gnu-linux-x86_64"
SCIE_JUMP_SPLIT_NAME = "scie-jump"
SCIE_JUMP_SHA256 = "a5afd5cd99ac201865d329980e9856521d394e2780441c4abc2f73add5b7e2d0"


class PackageError(RuntimeError):
    """The standalone package cannot be assembled safely."""


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def run(command: list[str], *, env=None) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(
        command, env=env, text=True, capture_output=True, check=False
    )
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip() or "no output"
        raise PackageError(f"command failed ({' '.join(command)}): {detail}")
    return result


def load_manifest(path: Path) -> dict[str, object]:
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as error:
        raise PackageError(f"cannot read compliance manifest: {error}") from error
    if manifest.get("schema") != MANIFEST_SCHEMA:
        raise PackageError("unsupported standalone compliance manifest schema")
    if manifest.get("target") != {"platform": TARGET, "libc": LIBC}:
        raise PackageError(
            "compliance manifest target does not match standalone target"
        )
    if not isinstance(manifest.get("records"), list):
        raise PackageError("compliance manifest records must be a list")
    return manifest


def manifest_file_records(manifest: dict[str, object]) -> list[tuple[dict, dict]]:
    records = []
    paths: set[str] = set()
    for record in manifest["records"]:
        if not isinstance(record, dict):
            raise PackageError("compliance manifest record is not an object")
        files = record.get("files")
        if not isinstance(files, list):
            raise PackageError("compliance manifest record files must be a list")
        for file_record in files:
            if not isinstance(file_record, dict):
                raise PackageError("compliance manifest file is not an object")
            path = file_record.get("path")
            digest = file_record.get("sha256")
            if not isinstance(path, str) or not isinstance(digest, str):
                raise PackageError("compliance manifest file lacks path or sha256")
            if path in paths:
                raise PackageError(f"duplicate compliance snapshot path: {path}")
            if Path(path).is_absolute() or ".." in Path(path).parts:
                raise PackageError(f"unsafe compliance snapshot path: {path}")
            if len(digest) != 64:
                raise PackageError(f"invalid compliance snapshot hash: {path}")
            paths.add(path)
            records.append((record, file_record))
    return records


def validate_manifest(manifest_path: Path) -> list[tuple[dict, dict]]:
    manifest = load_manifest(manifest_path)
    root = manifest_path.parent
    records = manifest_file_records(manifest)
    for record, file_record in records:
        path = root / file_record["path"]
        if not path.is_file():
            raise PackageError(f"missing compliance snapshot: {path}")
        actual = sha256(path)
        if actual != file_record["sha256"]:
            raise PackageError(
                f"compliance snapshot hash mismatch for {file_record['path']}: "
                f"expected {file_record['sha256']}, found {actual}"
            )
        for key in ("component", "version", "role", "license", "source_repository"):
            if not record.get(key):
                raise PackageError(f"incomplete compliance record: {key}")
    return records


def archive_license_path(relative: str) -> str:
    if relative == "DEVLEGATE-LICENSE":
        return "LICENSE"
    if relative == "DEVLEGATE-NOTICE":
        return "NOTICE"
    if relative == "DEVLEGATE-LICENSING.md":
        return "LICENSING.md"
    if relative == "CC0-1.0.txt":
        return "LICENSES/CC0-1.0.txt"
    if relative == "NanoYAML-MIT.txt":
        return "LICENSES/NanoYAML-MIT.txt"
    if "/" not in relative:
        raise PackageError(f"unmapped compliance snapshot: {relative}")
    category, name = relative.split("/", 1)
    if category not in {"pex", "pex-vendored", "scie-jump", "python-runtime"}:
        raise PackageError(f"unmapped compliance category: {relative}")
    return f"LICENSES/{category}/{name}"


def source_provenance(record: dict) -> str:
    source = record["source_repository"]
    if record.get("source_tag"):
        source += f" tag {record['source_tag']}"
    if record.get("source_commit"):
        source += f" commit {record['source_commit']}"
    return source + f" path {record['source_path']}"


def notice_text(records: list[tuple[dict, dict]]) -> str:
    runtime = [item for item in records if "not redistributed" not in item[0]["role"]]
    build_only = [item for item in records if "not redistributed" in item[0]["role"]]

    def section(items: list[tuple[dict, dict]]) -> list[str]:
        lines = [
            "| Component | Version | Role | License | License file(s) | "
            "Source/provenance |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        grouped: dict[int, list[str]] = {}
        for record, file_record in items:
            key = id(record)
            grouped.setdefault(key, []).append(
                archive_license_path(file_record["path"])
            )
        seen: set[int] = set()
        for record, _file_record in items:
            key = id(record)
            if key in seen:
                continue
            seen.add(key)
            files = ", ".join(f"`{path}`" for path in grouped[key]) or "none"
            lines.append(
                f"| {record['component']} | {record['version']} | {record['role']} | "
                f"{record['license']} | {files} | {source_provenance(record)} |"
            )
        return lines

    return "\n".join(
        [
            "# Third-Party Notices",
            "",
            "This file is generated from the committed standalone compliance manifest.",
            "License texts are included under `LICENSES/`.",
            "",
            "## Redistributed Runtime Components",
            "",
            *section(runtime),
            "",
            "## Build Provenance Not Redistributed",
            "",
            *section(build_only),
            "",
            "Science and Python Standalone Builds build machinery are recorded as",
            "provenance only. They are not runtime notice entries.",
            "",
            "ptex is absent from the eager runtime and is not redistributed.",
            "",
        ]
    )


def split_inventory(artifact: Path, destination: Path) -> dict[str, dict[str, object]]:
    from build_standalone import split_scie

    files = split_scie(artifact, destination)
    inventory = {
        name: {"size": path.stat().st_size, "sha256": sha256(path)}
        for name, path in sorted(files.items())
    }
    if inventory[SCIE_JUMP_SPLIT_NAME]["sha256"] != SCIE_JUMP_SHA256:
        raise PackageError("embedded scie-jump hash does not match target asset")
    if inventory[PBS_ARCHIVE]["sha256"] != PBS_SHA256:
        raise PackageError("embedded PBS archive hash mismatch")
    return inventory


def stable_provenance(
    report: dict[str, object], manifest_path: Path, inventory: dict[str, dict]
) -> dict[str, object]:
    inputs = report["inputs"]
    if inputs["target"] != TARGET or report.get("target_libc") != LIBC:
        raise PackageError("standalone build target/libc mismatch")
    if inputs["pex_version"] != PEX_VERSION:
        raise PackageError("standalone PEX version mismatch")
    if inputs["pbs_archive"] != PBS_ARCHIVE or inputs["pbs_sha256"] != PBS_SHA256:
        raise PackageError("standalone PBS provenance mismatch")
    if inputs["science_version"] != SCIENCE_VERSION:
        raise PackageError("standalone Science provenance mismatch")
    jump = report.get("scie_jump")
    if jump != {
        "asset": SCIE_JUMP_ASSET,
        "version": SCIE_JUMP_VERSION,
        "sha256": SCIE_JUMP_SHA256,
    }:
        raise PackageError("standalone scie-jump provenance mismatch")
    wheel = report["wheel"]
    scie = report["scie"]
    manifest_hash = sha256(manifest_path)
    return {
        "schema_version": 1,
        "devlegate": {
            "version": wheel["version"],
            "source_commit": report["source_commit"],
            "wheel_sha256": wheel["sha256"],
            "standalone_sha256": scie["sha256"],
            "standalone_size": scie["size"],
        },
        "target": {"platform": TARGET, "libc": LIBC},
        "pex": {
            "version": PEX_VERSION,
            "wheel_sha256": report["pex"]["wheel_sha256"],
            "source_tag": "v2.103.2",
            "source_commit": "ab31461ceaec1167f60751416dbed0e6b5803b03",
        },
        "science": {
            "version": SCIENCE_VERSION,
            "asset": report["science"]["asset"],
            "sha256": report["science"]["sha256"],
            "role": "build-time only",
        },
        "pbs": {
            "release": inputs["pbs_release"],
            "python_version": inputs["pbs_python_version"],
            "archive": inputs["pbs_archive"],
            "sha256": inputs["pbs_sha256"],
            "source_commit": "4bb01f09aaf362c71e891be4a41cb6d6ddf830b3",
        },
        "scie_jump": {
            **jump,
            "source_tag": "v1.13.0",
            "source_commit": "d45d0d80845220f3f842083d42caba31f35b5290",
        },
        "split_inventory": inventory,
        "wheel_build_toolchain": report["wheel_build_toolchain"],
        "reproducibility": {
            "wheel": report["wheel_reproducible"],
            "scie": report["scie_reproducible"],
            "scope": report["reproducibility_scope"],
        },
        "ptex_runtime_included": False,
        "runtime_network_inputs": [],
        "compliance_manifest": {
            "path": "LICENSES/standalone-compliance-manifest.json",
            "sha256": manifest_hash,
        },
    }


def normalized_tar(source: Path, archive: Path, timestamp: int, top_level: str) -> None:
    paths = [source]
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        paths.extend(Path(current) / name for name in directories)
        paths.extend(Path(current) / name for name in files)
    paths.sort(key=lambda path: "" if path == source else str(path.relative_to(source)))
    with archive.open("wb") as output:
        with gzip.GzipFile(
            fileobj=output, mode="wb", filename="", mtime=0, compresslevel=9
        ) as compressed:
            with tarfile.open(
                fileobj=compressed, mode="w", format=tarfile.PAX_FORMAT
            ) as tar:
                for path in paths:
                    relative = path.relative_to(source)
                    arcname = (
                        top_level
                        if not relative.parts
                        else str(Path(top_level, relative))
                    )
                    info = tar.gettarinfo(str(path), arcname=arcname)
                    info.mtime = timestamp
                    info.uid = 0
                    info.gid = 0
                    info.uname = "root"
                    info.gname = "root"
                    info.pax_headers = {}
                    if info.isdir():
                        info.mode = 0o755
                    elif info.isreg():
                        info.mode = 0o755 if path.stat().st_mode & 0o111 else 0o644
                    if info.isreg():
                        with path.open("rb") as input_file:
                            tar.addfile(info, input_file)
                    else:
                        tar.addfile(info)


def git_timestamp(repo: Path, commit: str) -> int:
    result = run(["git", "-C", str(repo), "show", "-s", "--format=%ct", commit])
    return int(result.stdout.strip())


def package(
    repo: Path,
    artifact: Path,
    report_path: Path,
    output_dir: Path,
    manifest_path: Path | None = None,
    assembly_root: Path | None = None,
) -> tuple[Path, Path]:
    repo = repo.resolve()
    artifact = artifact.resolve()
    report = json.loads(report_path.read_text(encoding="utf-8"))
    manifest_path = (manifest_path or repo / COMPLIANCE_DIR / "manifest.json").resolve()
    records = validate_manifest(manifest_path)
    if not artifact.is_file():
        raise PackageError(f"standalone executable is missing: {artifact}")
    if sha256(artifact) != report["scie"]["sha256"]:
        raise PackageError("standalone executable hash does not match build report")
    commit = report["source_commit"]
    if run(["git", "-C", str(repo), "rev-parse", "HEAD"]).stdout.strip() != commit:
        raise PackageError("source commit does not match selected repository state")
    version = report["wheel"]["version"]
    top_level = f"devlegate-{version}-{TARGET}"
    output_dir.mkdir(parents=True, exist_ok=True)
    archive = output_dir / f"{top_level}.tar.gz"
    sidecar = output_dir / f"{archive.name}.sha256"
    temp_context = None
    if assembly_root is None:
        temp_context = tempfile.TemporaryDirectory(prefix="devlegate-package-")
        assembly_root = Path(temp_context.name)
    else:
        assembly_root.mkdir(parents=True, exist_ok=True)
    try:
        stage = assembly_root / "stage" / top_level
        (stage / "LICENSES").mkdir(parents=True)
        shutil.copy2(artifact, stage / "devlegate")
        (stage / "devlegate").chmod(0o755)
        for record, file_record in records:
            destination = stage / archive_license_path(file_record["path"])
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(manifest_path.parent / file_record["path"], destination)
            destination.chmod(0o644)
        (stage / "LICENSES/standalone-compliance-manifest.json").write_text(
            manifest_path.read_text(encoding="utf-8"), encoding="utf-8"
        )
        (stage / "THIRD_PARTY_NOTICES.md").write_text(
            notice_text(records), encoding="utf-8"
        )
        split_dir = assembly_root / "split"
        inventory = split_inventory(artifact, split_dir)
        provenance = stable_provenance(report, manifest_path, inventory)
        (stage / "BUILD-PROVENANCE.json").write_text(
            json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        normalized_tar(stage, archive, git_timestamp(repo, commit), top_level)
        digest = sha256(archive)
        sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    finally:
        if temp_context is not None:
            temp_context.cleanup()
    return archive, sidecar


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("package",))
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--artifact", type=Path, required=True)
    parser.add_argument("--build-report", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--manifest", type=Path)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        archive, sidecar = package(
            args.repo,
            args.artifact,
            args.build_report,
            args.output_dir,
            args.manifest,
        )
    except (PackageError, OSError, subprocess.SubprocessError) as error:
        print(f"package-standalone: {error}", file=os.sys.stderr)
        return 1
    print(archive)
    print(sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
