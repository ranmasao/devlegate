#!/usr/bin/env python3
"""Validate an extracted deterministic Devlegate full-source archive."""

from __future__ import annotations

import argparse
import hashlib
import shutil
import sys
import tarfile
from pathlib import Path

from build_full_source import (
    MANIFEST_NAME,
    BuildError,
    gitlinks,
    public_version,
    resolve_commit,
)


def parse_manifest(path: Path) -> tuple[str, str, list[tuple[str, str]]]:
    lines = path.read_text(encoding="utf-8").splitlines()
    if len(lines) < 4 or lines[0] != "format: devlegate-full-source-v1":
        raise BuildError("invalid SOURCE-MANIFEST format")
    release = lines[1].removeprefix("release: ")
    commit = lines[2].removeprefix("devlegate-commit: ")
    if not release or not commit or lines[3] != "submodules:":
        raise BuildError("incomplete SOURCE-MANIFEST")
    if lines[4:] == ["  - none"]:
        return release, commit, []
    submodules = []
    for index in range(4, len(lines), 2):
        if not lines[index].startswith("  - path: ") or not lines[index + 1].startswith(
            "    commit: "
        ):
            raise BuildError("invalid submodule entry in SOURCE-MANIFEST")
        submodules.append(
            (
                lines[index].removeprefix("  - path: "),
                lines[index + 1].removeprefix("    commit: "),
            )
        )
    return release, commit, submodules


def safe_extract(archive: Path, destination: Path) -> Path:
    if destination.exists():
        shutil.rmtree(destination)
    destination.mkdir(parents=True)
    with tarfile.open(archive, "r:gz") as tar:
        members = tar.getmembers()
        if not members:
            raise BuildError("archive is empty")
        root = members[0].name
        if "/" in root or root in {".", ".."}:
            raise BuildError("archive has an invalid top-level directory")
        if any(
            not (member.name == root or member.name.startswith(root + "/"))
            for member in members
        ):
            raise BuildError("archive contains paths outside its top-level directory")
        tar.extractall(destination, filter="data")
    return destination / root


def validate_archive(
    archive: Path,
    sidecar: Path,
    source_repo: Path,
    ref: str,
    version: str,
    extract_dir: Path,
) -> Path:
    archive_version = public_version(version)
    expected_root = f"devlegate-{archive_version}"
    expected_commit = resolve_commit(source_repo, ref)
    expected_gitlinks = gitlinks(source_repo, expected_commit)
    expected_digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar_text = sidecar.read_text(encoding="ascii").strip()
    if sidecar_text != f"{expected_digest}  {archive.name}":
        raise BuildError("SHA-256 sidecar does not match archive")

    root = safe_extract(archive, extract_dir)
    if root.name != expected_root:
        raise BuildError(f"expected archive root {expected_root}, found {root.name}")
    if any(path.name == ".git" for path in root.rglob("*")):
        raise BuildError("archive contains Git metadata")
    manifest_path = root / MANIFEST_NAME
    if not manifest_path.is_file():
        raise BuildError("archive is missing SOURCE-MANIFEST")
    release, manifest_commit, manifest_gitlinks = parse_manifest(manifest_path)
    if release != version or manifest_commit != expected_commit:
        raise BuildError("SOURCE-MANIFEST source identity does not match selected ref")
    if manifest_gitlinks != expected_gitlinks:
        raise BuildError("SOURCE-MANIFEST gitlinks do not match selected source tree")
    for path, _commit in expected_gitlinks:
        materialized = root / path
        if not materialized.is_dir():
            raise BuildError(f"gitlink {path} is not materialized as a directory")
    return root


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", type=Path, required=True)
    parser.add_argument("--sidecar", type=Path, required=True)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--ref", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--extract-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        root = validate_archive(
            args.archive,
            args.sidecar,
            args.repo,
            args.ref,
            args.version,
            args.extract_dir,
        )
    except (BuildError, OSError, tarfile.TarError) as error:
        print(f"validate-full-source: {error}", file=sys.stderr)
        return 1
    print(root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
