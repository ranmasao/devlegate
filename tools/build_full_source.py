#!/usr/bin/env python3
# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Build a deterministic, self-contained source archive for a Git ref."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
from pathlib import Path

VERSION_RE = re.compile(r"^v[0-9]+\.[0-9]+\.[0-9]+$")
MANIFEST_NAME = "SOURCE-MANIFEST"


class BuildError(RuntimeError):
    """Raised when the selected source cannot be packaged safely."""


def public_version(version: str) -> str:
    if not VERSION_RE.fullmatch(version):
        raise BuildError("version must match vX.Y.Z")
    return version[1:]


def git(repo: Path, *args: str, check: bool = True) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), *args],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if check and result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise BuildError(f"git {' '.join(args)} failed: {detail}")
    return result.stdout


def resolve_commit(repo: Path, ref: str) -> str:
    result = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--verify", f"{ref}^{{commit}}"],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise BuildError(f"cannot resolve source ref {ref!r}")
    return result.stdout.strip()


def commit_timestamp(repo: Path, commit: str) -> int:
    return int(git(repo, "show", "-s", "--format=%ct", commit).strip())


def gitlinks(repo: Path, commit: str) -> list[tuple[str, str]]:
    raw = subprocess.check_output(
        ["git", "-C", str(repo), "ls-tree", "-r", "-z", commit]
    )
    found = []
    for entry in raw.rstrip(b"\0").split(b"\0"):
        header, path = entry.split(b"\t", 1)
        mode, _kind, object_id = header.split()
        if mode == b"160000":
            found.append((path.decode(), object_id.decode()))
    return found


def checkout_source(repo: Path, commit: str, destination: Path) -> None:
    source_origin = git(
        repo, "config", "--get", "remote.origin.url", check=False
    ).strip()
    result = subprocess.run(
        [
            "git",
            "-c",
            "protocol.file.allow=always",
            "clone",
            "--quiet",
            "--no-checkout",
            str(repo),
            str(destination),
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise BuildError(f"cannot clone source repository: {result.stderr.strip()}")
    if source_origin:
        git(destination, "remote", "set-url", "origin", source_origin)
    git(destination, "checkout", "--quiet", "--detach", commit)
    subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "sync",
            "--recursive",
        ],
        check=True,
    )
    result = subprocess.run(
        [
            "git",
            "-C",
            str(destination),
            "-c",
            "protocol.file.allow=always",
            "submodule",
            "update",
            "--init",
            "--recursive",
        ],
        check=False,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode:
        raise BuildError(
            f"cannot initialize source submodules: {result.stderr.strip()}"
        )


def verify_materialized_submodules(repo: Path, commit: str) -> list[tuple[str, str]]:
    materialized: list[tuple[str, str]] = []

    def visit(current_repo: Path, current_commit: str, prefix: str) -> None:
        for relative_path, expected in gitlinks(current_repo, current_commit):
            path = current_repo / relative_path
            full_path = f"{prefix}/{relative_path}" if prefix else relative_path
            if not path.is_dir():
                raise BuildError(f"gitlink {full_path} is not materialized")
            actual = git(path, "rev-parse", "--verify", "HEAD").strip()
            if actual != expected:
                raise BuildError(
                    f"gitlink {full_path} expected {expected}, found {actual}"
                )
            materialized.append((full_path, expected))
            visit(path, expected, full_path)

    visit(repo, commit, "")
    return materialized


def remove_git_metadata(root: Path) -> None:
    for current, directories, files in os.walk(root, topdown=True):
        directories[:] = sorted(directories)
        files.sort()
        if ".git" in directories:
            shutil.rmtree(Path(current) / ".git")
            directories.remove(".git")
        if ".git" in files:
            (Path(current) / ".git").unlink()


def write_manifest(
    root: Path, version: str, commit: str, submodules: list[tuple[str, str]]
) -> None:
    lines = [
        "format: devlegate-full-source-v1",
        f"release: {version}",
        f"devlegate-commit: {commit}",
        "submodules:",
    ]
    if submodules:
        lines.extend(f"  - path: {path}\n    commit: {sha}" for path, sha in submodules)
    else:
        lines.append("  - none")
    (root / MANIFEST_NAME).write_text("\n".join(lines) + "\n", encoding="utf-8")


def normalized_tar(source: Path, archive: Path, timestamp: int, top_level: str) -> None:
    paths = [source]
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        directories.sort()
        files.sort()
        paths.extend(Path(current) / name for name in directories)
        paths.extend(Path(current) / name for name in files)
    paths.sort(
        key=lambda path: "" if path == source else str(path.relative_to(source))
    )

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
                    info.pax_headers = dict(info.pax_headers)
                    info.pax_headers.pop("atime", None)
                    info.pax_headers.pop("ctime", None)
                    if info.isreg():
                        with path.open("rb") as input_file:
                            tar.addfile(info, input_file)
                    else:
                        tar.addfile(info)


def build(repo: Path, ref: str, version: str, output_dir: Path) -> tuple[Path, Path]:
    archive_version = public_version(version)
    repo = repo.resolve()
    if not repo.exists():
        raise BuildError(f"repository does not exist: {repo}")
    commit = resolve_commit(repo, ref)
    timestamp = commit_timestamp(repo, commit)
    output_dir.mkdir(parents=True, exist_ok=True)
    archive_name = f"devlegate-{archive_version}-full-source.tar.gz"
    archive = output_dir / archive_name
    sidecar = output_dir / f"{archive_name}.sha256"

    with tempfile.TemporaryDirectory(prefix="devlegate-full-source-") as temporary:
        checkout = Path(temporary) / "checkout"
        checkout_source(repo, commit, checkout)
        submodules = verify_materialized_submodules(checkout, commit)
        write_manifest(checkout, version, commit, submodules)
        remove_git_metadata(checkout)
        normalized_tar(checkout, archive, timestamp, f"devlegate-{archive_version}")

    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    sidecar.write_text(f"{digest}  {archive.name}\n", encoding="ascii")
    return archive, sidecar


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--repo", type=Path, required=True, help="source Git repository"
    )
    parser.add_argument("--ref", required=True, help="source ref or commit")
    parser.add_argument(
        "--version", required=True, help="release version, such as v0.5.0"
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    try:
        archive, sidecar = build(args.repo, args.ref, args.version, args.output_dir)
    except (BuildError, OSError, subprocess.CalledProcessError) as error:
        print(f"build-full-source: {error}", file=os.sys.stderr)
        return 1
    print(archive)
    print(sidecar)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
