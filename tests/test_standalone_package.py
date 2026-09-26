# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import io
import shutil
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load_tool(name):
    path = TOOLS / f"{name}.py"
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


PACKAGE = load_tool("package_standalone")
VALIDATOR = load_tool("validate_standalone_package")
MANIFEST = Path(__file__).parents[1] / "packaging/standalone-compliance/manifest.json"
BUILDER = load_tool("build_standalone")


def provenance_report():
    return {
        "source_commit": "a" * 40,
        "inputs": {
            "target": "linux-x86_64",
            "pex_version": "2.103.2",
            "pex_wheel": BUILDER.PEX_WHEEL,
            "pex_sha256": BUILDER.PEX_SHA256,
            "pbs_archive": PACKAGE.PBS_ARCHIVE,
            "pbs_sha256": PACKAGE.PBS_SHA256,
            "science_version": "0.21.0",
            "science_asset": PACKAGE.SCIENCE_ASSET,
            "science_sha256": PACKAGE.SCIENCE_SHA256,
            "pbs_provider": BUILDER.PBS_PROVIDER,
            "pbs_release": "20260901",
            "pbs_python_version": "3.12.14",
            "scie_jump_version": PACKAGE.SCIE_JUMP_VERSION,
        },
        "target_libc": "glibc",
        "pex": {
            "version": "2.103.2",
            "wheel_filename": BUILDER.PEX_WHEEL,
            "wheel_sha256": PACKAGE.PEX_WHEEL_SHA256,
            "bootstrap_tools": PACKAGE.pinned_tools(
                BUILDER.PEX_BOOTSTRAP_TOOLS
            )["artifacts"],
        },
        "science": {
            "version": "0.21.0",
            "asset": PACKAGE.SCIENCE_ASSET,
            "sha256": PACKAGE.SCIENCE_SHA256,
        },
        "scie_jump": {
            "asset": PACKAGE.SCIE_JUMP_ASSET,
            "version": PACKAGE.SCIE_JUMP_VERSION,
            "sha256": PACKAGE.SCIE_JUMP_SHA256,
        },
        "wheel": {"version": "0.5.5.dev0", "sha256": "b" * 64},
        "scie": {"sha256": "c" * 64, "size": 123},
        "wheel_build_toolchain": PACKAGE.pinned_tools(BUILDER.WHEEL_BUILD_TOOLS),
        "wheel_reproducible": True,
        "scie_reproducible": True,
        "independent_build_roots": True,
        "independent_pex_roots": True,
        "temporary_build_path_embedded": False,
        "reproducibility_scope": PACKAGE.EXPECTED_REPRODUCIBILITY_SCOPE,
    }


def split_inventory():
    return {
        PACKAGE.SCIE_JUMP_SPLIT_NAME: {
            "size": 1,
            "sha256": PACKAGE.SCIE_JUMP_SHA256,
        },
        PACKAGE.PBS_ARCHIVE: {"size": 1, "sha256": PACKAGE.PBS_SHA256},
        "pex": {"size": 1, "sha256": "d" * 64},
        "configure-binding.py": {"size": 1, "sha256": "e" * 64},
        "lift.json": {"size": 1, "sha256": "f" * 64},
    }


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


def test_devlegate_snapshot_must_match_repository_source(tmp_path):
    snapshot = tmp_path / "standalone-compliance"
    shutil.copytree(MANIFEST.parent, snapshot)
    (snapshot / "DEVLEGATE-NOTICE").write_text("tampered\n")

    with pytest.raises(PACKAGE.PackageError, match="out of sync"):
        PACKAGE.validate_manifest(snapshot / "manifest.json", MANIFEST.parents[2])


def test_notices_are_deterministic_and_classify_build_only():
    records = PACKAGE.load_manifest(MANIFEST)["records"]

    first = PACKAGE.notice_text(records)
    second = PACKAGE.notice_text(records)

    assert first == second
    assert "PEX vendored runtime libraries" in first
    assert "Science" in first
    assert (
        "| python-build-standalone build machinery | 20260901 | "
        "build provenance not redistributed |"
    ) in first
    assert "| Science | 0.21.0 | build provenance not redistributed |" in first
    assert "Build Provenance Not Redistributed" in first
    assert "`LICENSES/pex-vendored/ansicolors-1.1.8-ISC.txt`" in first


@pytest.mark.parametrize(
    ("path", "value"),
    [
        (("inputs", "pbs_provider"), "wrong"),
        (("inputs", "pbs_release"), "20991231"),
        (("inputs", "pbs_python_version"), "9.9.9"),
        (("inputs", "pex_wheel"), "wrong.whl"),
        (("inputs", "pex_sha256"), "wrong"),
        (("inputs", "science_asset"), "wrong"),
        (("inputs", "science_sha256"), "wrong"),
        (("wheel_build_toolchain", "versions", "pip"), "0.0.0"),
        (("wheel_build_toolchain", "artifacts", "setuptools", "sha256"), "wrong"),
        (("pex", "bootstrap_tools", "pip", "sha256"), "wrong"),
        (("wheel_reproducible",), False),
        (("scie_reproducible",), False),
        (("independent_build_roots",), False),
        (("independent_pex_roots",), False),
        (("temporary_build_path_embedded",), True),
        (("reproducibility_scope",), "wrong"),
    ],
)
def test_canonical_identity_fails_closed(path, value):
    report = provenance_report()
    target = report
    for key in path[:-1]:
        target = target[key]
    target[path[-1]] = value

    with pytest.raises(PACKAGE.PackageError):
        PACKAGE.stable_provenance(report, MANIFEST, split_inventory())


def test_source_version_must_match_report(tmp_path):
    report = provenance_report()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "devlegate"\nversion = "0.5.4"\n'
    )

    with pytest.raises(PACKAGE.PackageError, match="source version"):
        PACKAGE.stable_provenance(report, MANIFEST, split_inventory(), tmp_path)


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


def test_safe_extract_rejects_symlinks(tmp_path):
    archive = tmp_path / "symlink.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("package")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        member = tarfile.TarInfo("package/devlegate")
        member.type = tarfile.SYMTYPE
        member.mode = 0o755
        member.linkname = "/etc/passwd"
        tar.addfile(member)

    with pytest.raises(PACKAGE.PackageError, match="unsupported archive member"):
        VALIDATOR.safe_extract(archive, tmp_path / "extract", "package")


@pytest.mark.parametrize(
    "member_type", [tarfile.LNKTYPE, tarfile.FIFOTYPE, tarfile.CHRTYPE]
)
def test_safe_extract_rejects_special_members(tmp_path, member_type):
    archive = tmp_path / "special.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("package")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        member = tarfile.TarInfo("package/special")
        member.type = member_type
        member.mode = 0o644
        member.linkname = "package/devlegate"
        tar.addfile(member)

    with pytest.raises(PACKAGE.PackageError, match="unsupported archive member"):
        VALIDATOR.safe_extract(archive, tmp_path / "extract", "package")


def test_extracted_content_rejects_ephemeral_paths(tmp_path):
    payload = tmp_path / "metadata"
    payload.write_text("/tmp/devlegate-package-secret\n")

    with pytest.raises(PACKAGE.PackageError, match="ephemeral path"):
        VALIDATOR.reject_ephemeral_paths(tmp_path)


def test_safe_extract_rejects_wrong_mode(tmp_path):
    archive = tmp_path / "mode.tar.gz"
    with tarfile.open(archive, "w:gz") as tar:
        root = tarfile.TarInfo("package")
        root.type = tarfile.DIRTYPE
        root.mode = 0o755
        tar.addfile(root)
        member = tarfile.TarInfo("package/devlegate")
        member.mode = 0o644
        member.size = 1
        tar.addfile(member, io.BytesIO(b"x"))

    with pytest.raises(PACKAGE.PackageError, match="unexpected archive mode"):
        VALIDATOR.safe_extract(archive, tmp_path / "extract", "package")
