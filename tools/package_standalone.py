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
import tomllib
from pathlib import Path

COMPLIANCE_DIR = "packaging/standalone-compliance"
MANIFEST_SCHEMA = "devlegate.standalone-compliance.v1"
TARGET = "linux-x86_64"
LIBC = "glibc"
PBS_ARCHIVE = "cpython-3.12.14+20260901-x86_64-unknown-linux-gnu-install_only.tar.gz"
PBS_SHA256 = "936c246dfdbbfa7cb22dd01814a21f582a892689fae96b06071a5e433baffa22"
PEX_VERSION = "2.103.2"
PEX_WHEEL = "pex-2.103.2-py3.py312-none-any.whl"
PEX_WHEEL_SHA256 = "f1316f1f6f0e125c44c8d6f49cd6ebc4b294b7582a385b3999e814824e607ec7"
SCIENCE_VERSION = "0.21.0"
SCIE_JUMP_VERSION = "1.13.0"
SCIE_JUMP_ASSET = "scie-jump-gnu-linux-x86_64"
SCIE_JUMP_SPLIT_NAME = "scie-jump"
SCIE_JUMP_SHA256 = "a5afd5cd99ac201865d329980e9856521d394e2780441c4abc2f73add5b7e2d0"
SCIENCE_ASSET = "science-fat-linux-x86_64"
SCIENCE_SHA256 = "2070de7f823033a3b0e8a2ceb56e9fdfa8375fda1762a0d71ba11cd36d5c730f"
PBS_PROVIDER = "PythonBuildStandalone"
PBS_RELEASE = "20260901"
PBS_PYTHON_VERSION = "3.12.14"
EXPECTED_REPRODUCIBILITY_SCOPE = (
    "byte-reproducible on the tested Linux x86_64 build environment "
    "from the pinned immutable inputs"
)
DEVLEGATE_SNAPSHOT_PATHS = {
    "DEVLEGATE-LICENSE": "LICENSE",
    "DEVLEGATE-NOTICE": "NOTICE",
    "DEVLEGATE-LICENSING.md": "LICENSING.md",
    "CC0-1.0.txt": "LICENSES/CC0-1.0.txt",
    "NanoYAML-MIT.txt": "src/devlegate/_vendor/nanoyaml/LICENSE",
}


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


def builder_constants() -> tuple[
    dict[str, str], dict[str, tuple[str, str]], dict[str, tuple[str, str]]
]:
    from build_standalone import INPUTS, PEX_BOOTSTRAP_TOOLS, WHEEL_BUILD_TOOLS

    return (
        {
            key: value
            for key, value in vars(INPUTS).items()
        },
        WHEEL_BUILD_TOOLS,
        PEX_BOOTSTRAP_TOOLS,
    )


def project_version(repo: Path) -> str:
    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
        return data["project"]["version"]
    except (KeyError, OSError, tomllib.TOMLDecodeError) as error:
        raise PackageError(f"cannot read project version: {error}") from error


def pinned_tools(
    tools: dict[str, tuple[str, str]]
) -> dict[str, dict[str, dict[str, str]]]:
    return {
        "versions": {
            name: filename.split("-")[1] for name, (filename, _sha256) in tools.items()
        },
        "artifacts": {
            name: {"filename": filename, "sha256": digest}
            for name, (filename, digest) in tools.items()
        },
    }


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


def manifest_records(manifest: dict[str, object]) -> list[dict]:
    components: set[str] = set()
    for record in manifest["records"]:
        if not isinstance(record, dict):
            raise PackageError("compliance manifest record is not an object")
        component = record.get("component")
        if not isinstance(component, str) or not component or component in components:
            raise PackageError(f"invalid or duplicate manifest component: {component}")
        components.add(component)
        for key in ("version", "role", "license", "source_repository", "source_path"):
            if not record.get(key):
                raise PackageError(f"incomplete compliance record: {key}")
    return manifest["records"]


def validate_devlegate_snapshots(manifest_path: Path, repo: Path) -> None:
    root = manifest_path.parent
    manifest = load_manifest(manifest_path)
    manifest_files = {
        file_record["path"]: file_record
        for _record, file_record in manifest_file_records(manifest)
    }
    for snapshot, source in DEVLEGATE_SNAPSHOT_PATHS.items():
        snapshot_path = root / snapshot
        source_path = repo / source
        if (
            not source_path.is_file()
            or not snapshot_path.is_file()
            or snapshot_path.read_bytes() != source_path.read_bytes()
        ):
            raise PackageError(
                f"Devlegate legal snapshot is out of sync: {snapshot} != {source}"
            )
        file_record = manifest_files.get(snapshot)
        if file_record is None or file_record["sha256"] != sha256(source_path):
            raise PackageError(
                f"Devlegate legal snapshot is absent from manifest: {snapshot}"
            )


def validate_manifest(
    manifest_path: Path, repo: Path | None = None
) -> list[tuple[dict, dict]]:
    manifest = load_manifest(manifest_path)
    manifest_records(manifest)
    if repo is not None:
        validate_devlegate_snapshots(manifest_path, repo)
    records = manifest_file_records(manifest)
    root = manifest_path.parent
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


def notice_text(records: list[dict]) -> str:
    runtime = [
        record for record in records if "not redistributed" not in record["role"]
    ]
    build_only = [record for record in records if "not redistributed" in record["role"]]

    def section(items: list[dict]) -> list[str]:
        lines = [
            "| Component | Version | Role | License | License file(s) | "
            "Source/provenance |",
            "| --- | --- | --- | --- | --- | --- |",
        ]
        for record in items:
            files = ", ".join(
                f"`{archive_license_path(file['path'])}`"
                for file in record["files"]
            ) or "none"
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
    expected_names = {
        SCIE_JUMP_SPLIT_NAME,
        "pex",
        PBS_ARCHIVE,
        "configure-binding.py",
        "lift.json",
    }
    if set(files) != expected_names:
        raise PackageError(
            "embedded scie split inventory mismatch: "
            f"missing={sorted(expected_names - set(files))}, "
            f"unexpected={sorted(set(files) - expected_names)}"
        )
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
    report: dict[str, object],
    manifest_path: Path,
    inventory: dict[str, dict],
    repo: Path | None = None,
) -> dict[str, object]:
    canonical_inputs, wheel_tools, bootstrap_tools = builder_constants()
    inputs = report["inputs"]
    for key, expected in canonical_inputs.items():
        if inputs.get(key) != expected:
            raise PackageError(f"standalone input identity mismatch: {key}")
    if report.get("target_libc") != LIBC:
        raise PackageError("standalone build libc mismatch")
    pex = report.get("pex", {})
    if (
        pex.get("version") != PEX_VERSION
        or pex.get("wheel_filename") != PEX_WHEEL
        or pex.get("wheel_sha256") != PEX_WHEEL_SHA256
        or pex.get("bootstrap_tools") != pinned_tools(bootstrap_tools)["artifacts"]
    ):
        raise PackageError("standalone PEX identity mismatch")
    science = report.get("science", {})
    if (
        science.get("version") != SCIENCE_VERSION
        or science.get("asset") != SCIENCE_ASSET
        or science.get("sha256") != SCIENCE_SHA256
    ):
        raise PackageError("standalone Science identity mismatch")
    jump = report.get("scie_jump")
    if jump != {
        "asset": SCIE_JUMP_ASSET,
        "version": SCIE_JUMP_VERSION,
        "sha256": SCIE_JUMP_SHA256,
    }:
        raise PackageError("standalone scie-jump provenance mismatch")
    if repo is not None and project_version(repo) != report["wheel"]["version"]:
        raise PackageError("standalone source version does not match wheel version")
    if report.get("wheel_build_toolchain") != pinned_tools(wheel_tools):
        raise PackageError("standalone wheel build toolchain mismatch")
    if (
        report.get("wheel_reproducible") is not True
        or report.get("scie_reproducible") is not True
        or report.get("independent_build_roots") is not True
        or report.get("independent_pex_roots") is not True
        or report.get("temporary_build_path_embedded") is not False
        or report.get("reproducibility_scope") != EXPECTED_REPRODUCIBILITY_SCOPE
    ):
        raise PackageError("standalone reproducibility proof is incomplete")
    wheel = report["wheel"]
    scie = report["scie"]
    manifest_hash = sha256(manifest_path)
    return {
            "schema_version": 2,
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
            "wheel_filename": PEX_WHEEL,
            "wheel_sha256": report["pex"]["wheel_sha256"],
            "bootstrap_tools": report["pex"]["bootstrap_tools"],
            "source_tag": "v2.103.2",
            "source_commit": "ab31461ceaec1167f60751416dbed0e6b5803b03",
        },
        "science": {
            "version": SCIENCE_VERSION,
            "asset": SCIENCE_ASSET,
            "sha256": SCIENCE_SHA256,
            "role": "build-time only",
        },
        "pbs": {
            "provider": PBS_PROVIDER,
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
            "independent_build_roots": report["independent_build_roots"],
            "independent_pex_roots": report["independent_pex_roots"],
            "temporary_build_path_embedded": report[
                "temporary_build_path_embedded"
            ],
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
    records = validate_manifest(manifest_path, repo)
    manifest = load_manifest(manifest_path)
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
            notice_text(manifest["records"]), encoding="utf-8"
        )
        split_dir = assembly_root / "split"
        inventory = split_inventory(artifact, split_dir)
        provenance = stable_provenance(report, manifest_path, inventory, repo)
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
