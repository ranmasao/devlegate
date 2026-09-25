# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import shutil
import sys
import tarfile
from pathlib import Path

import pytest


def load_tool(name):
    path = Path(__file__).parents[1] / "tools" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKAGE = load_tool("package_standalone")
VALIDATOR = load_tool("validate_standalone_package")
MANIFEST = Path(__file__).parents[1] / "packaging/standalone-compliance/manifest.json"


def test_manifest_snapshot_hashes_and_target():
    records = PACKAGE.validate_manifest(MANIFEST)
    manifest = PACKAGE.load_manifest(MANIFEST)

    assert len(records) >= 50
    assert manifest["target"] == {"platform": "linux-x86_64", "libc": "glibc"}
    assert any(
        record["component"] == "PEX vendored runtime libraries"
        for record, _file in records
    )


def test_modified_snapshot_fails_closed(tmp_path):
    snapshot = tmp_path / "standalone-compliance"
    shutil.copytree(MANIFEST.parent, snapshot)
    changed = snapshot / "pex/Apache-2.0.txt"
    changed.write_text(changed.read_text() + "changed\n")

    with pytest.raises(PACKAGE.PackageError, match="hash mismatch"):
        PACKAGE.validate_manifest(snapshot / "manifest.json")


def test_notices_are_deterministic_and_classify_build_only():
    records = PACKAGE.validate_manifest(MANIFEST)

    first = PACKAGE.notice_text(records)
    second = PACKAGE.notice_text(records)

    assert first == second
    assert "PEX vendored runtime libraries" in first
    assert "Science" in first
    assert "Build Provenance Not Redistributed" in first
    assert "`LICENSES/pex-vendored/ansicolors-1.1.8-ISC.txt`" in first


def test_unknown_license_path_fails_closed():
    with pytest.raises(PACKAGE.PackageError):
        PACKAGE.archive_license_path("unknown/license.txt")


def test_normalized_archives_are_byte_identical(tmp_path):
    first_root = tmp_path / "assembly-a" / "stage"
    second_root = tmp_path / "assembly-b" / "stage"
    first_root.mkdir(parents=True)
    second_root.mkdir(parents=True)
    (first_root / "devlegate").write_bytes(b"payload")
    (second_root / "devlegate").write_bytes(b"payload")
    (first_root / "devlegate").chmod(0o755)
    (second_root / "devlegate").chmod(0o700)
    first_archive = tmp_path / "a.tar.gz"
    second_archive = tmp_path / "b.tar.gz"

    PACKAGE.normalized_tar(first_root, first_archive, 123, "package")
    PACKAGE.normalized_tar(second_root, second_archive, 123, "package")

    assert first_archive.read_bytes() == second_archive.read_bytes()


def test_safe_extract_rejects_path_traversal(tmp_path):
    archive = tmp_path / "unsafe.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("package")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        member = tarfile.TarInfo("package/../escape")
        member.size = 1
        tar.addfile(member, __import__("io").BytesIO(b"x"))

    with pytest.raises(PACKAGE.PackageError, match="unsafe"):
        VALIDATOR.safe_extract(archive, tmp_path / "extract", "package")
