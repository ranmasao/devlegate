# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import gzip
import hashlib
import importlib.util
import io
import json
import os
import sys
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


ARCH = load("arch_format")


def test_real_arch_assembly_retains_manifest_license_tree_and_rejects_extra(
    tmp_path, monkeypatch
):
    package_arch = load("package_arch")
    validate_arch = load("validate_arch")
    manifest_path = ROOT / "packaging/standalone-compliance/manifest.json"
    standalone = load("package_standalone")
    manifest = standalone.load_manifest(manifest_path)
    extracted = tmp_path / "standalone"
    extracted.mkdir()
    (extracted / "devlegate").write_bytes(b"standalone payload")
    for name in (
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
    ):
        source = {
            "LICENSE": ROOT / "LICENSE",
            "NOTICE": ROOT / "NOTICE",
            "LICENSING.md": ROOT / "LICENSING.md",
        }.get(name)
        if source is None:
            if name == "THIRD_PARTY_NOTICES.md":
                content = standalone.notice_text(manifest["records"])
            else:
                content = json.dumps(
                    {
                        "devlegate": {
                            "standalone_sha256": hashlib.sha256(
                                b"standalone payload"
                            ).hexdigest(),
                            "standalone_size": len(b"standalone payload"),
                        }
                    }
                )
            (extracted / name).write_text(content, encoding="ascii")
        else:
            (extracted / name).write_bytes(source.read_bytes())
    licenses = extracted / "LICENSES"
    licenses.mkdir()
    (licenses / "standalone-compliance-manifest.json").write_bytes(
        manifest_path.read_bytes()
    )
    for _record, file_record in standalone.manifest_file_records(manifest):
        relative = standalone.archive_license_path(file_record["path"])
        if not relative.startswith("LICENSES/"):
            continue
        destination = licenses / relative.removeprefix("LICENSES/")
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(
            (manifest_path.parent / file_record["path"]).read_bytes()
        )

    report = tmp_path / "build-report.json"
    source_commit = package_arch.package_run(
        ["git", "-C", str(ROOT), "rev-parse", "HEAD"]
    ).stdout.strip()
    report_data = {
        "source_commit": source_commit,
        "wheel": {"version": "0.5.7.dev0"},
        "scie": {"sha256": hashlib.sha256(b"standalone payload").hexdigest()},
        "inputs": {},
        "target_libc": "synthetic",
        "pex": {},
        "science": {},
        "scie_jump": {},
        "wheel_build_toolchain": {},
        "wheel_reproducible": True,
        "scie_reproducible": True,
        "independent_build_roots": True,
        "independent_pex_roots": True,
        "temporary_build_path_embedded": False,
        "reproducibility_scope": "synthetic",
    }
    report.write_text(json.dumps(report_data), encoding="ascii")
    monkeypatch.setattr(
        standalone,
        "stable_provenance",
        lambda *_args: json.loads((extracted / "BUILD-PROVENANCE.json").read_text()),
    )
    monkeypatch.setattr(standalone, "split_inventory", lambda *_args: {})
    monkeypatch.setattr(package_arch, "validate", lambda *_args: extracted)
    outputs = []
    for umask in (0o0002, 0o0022):
        old_umask = os.umask(umask)
        try:
            outputs.append(
                package_arch._package(
                    repo=ROOT,
                    archive=tmp_path / "standalone.tar.gz",
                    sidecar=tmp_path / "standalone.tar.gz.sha256",
                    build_report=report,
                    output_dir=tmp_path / f"output-{umask:o}",
                )
            )
        finally:
            os.umask(old_umask)
    output = outputs[0]
    assert output.read_bytes() == outputs[1].read_bytes()

    validated = tmp_path / "validated"
    validate_arch.validate(output, report, validated, ROOT, manifest_path)
    members = ARCH.members(output)
    extracted_package = tmp_path / "package-root"
    ARCH.extract(output, extracted_package)
    assert (extracted_package / ".PKGINFO").stat().st_mode & 0o7777 == 0o644
    assert (extracted_package / ".MTREE").stat().st_mode & 0o7777 == 0o644
    assert (
        "usr/share/doc/devlegate/LICENSES/pex-vendored/ansicolors-1.1.8-ISC.txt"
        in members
    )
    assert "usr/share/doc/devlegate/LICENSES/python-runtime/LICENSE.zlib.txt" in members

    native = load("native_payload")
    custom_root = tmp_path / "custom-root"
    custom_layout = native.PayloadLayout(licenses="usr/share/licenses/devlegate")
    native.assemble(
        extracted,
        custom_root,
        distribution="arch",
        format_tool=package_arch.TOOL_IDENTITY,
        repo=ROOT,
        manifest=manifest_path,
        layout=custom_layout,
    )
    native.validate(
        custom_root,
        distribution="arch",
        format_tool=package_arch.TOOL_IDENTITY,
        repo=ROOT,
        manifest=manifest_path,
        expected_binary_sha256=report_data["scie"]["sha256"],
        standalone_report=report_data,
        layout=custom_layout,
    )
    assert (custom_root / "usr/share/licenses/devlegate").is_dir()
    assert not (custom_root / "usr/share/doc/devlegate/LICENSES").exists()

    dependency_root = tmp_path / "dependency-root"
    ARCH.extract(output, dependency_root)
    (dependency_root / ".PKGINFO").write_text(
        (dependency_root / ".PKGINFO").read_text(encoding="ascii")
        + "depend = python\n",
        encoding="ascii",
    )
    dependency_package = tmp_path / "dependency.pkg.tar.zst"
    ARCH.build(dependency_root, dependency_package, timestamp=0)
    with pytest.raises(validate_arch.PackageError, match="mandatory PKGINFO"):
        validate_arch.validate(
            dependency_package,
            report,
            tmp_path / "dependency-validated",
            ROOT,
            manifest_path,
        )

    extra = extracted_package / "usr/share/doc/devlegate/LICENSES/extra.txt"
    extra.write_text("unexpected", encoding="ascii")
    tampered = tmp_path / "tampered.pkg.tar.zst"
    ARCH.build(extracted_package, tampered, timestamp=0)
    with pytest.raises(
        validate_arch.PackageError, match="native package contains unexpected files"
    ):
        validate_arch.validate(
            tampered, report, tmp_path / "tampered-validated", ROOT, manifest_path
        )


def test_arch_owned_builder_is_reproducible_and_has_real_source_digest(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".PKGINFO").write_text("pkgname = devlegate\n", encoding="ascii")
    (root / "usr").mkdir()
    (root / "usr/devlegate").write_bytes(b"payload")
    first = tmp_path / "first.pkg.tar.zst"
    second = tmp_path / "second.pkg.tar.zst"
    ARCH.build(root, first, timestamp=123)
    ARCH.build(root, second, timestamp=123)
    assert first.read_bytes() == second.read_bytes()
    assert len(ARCH.TOOL_IDENTITY["digest"]) == 64
    assert ARCH.members(first)["usr/devlegate"] == b"payload"
    extracted = tmp_path / "extracted"
    ARCH.extract(first, extracted)
    assert (extracted / ".MTREE").is_file()
    assert (extracted / "usr").stat().st_mode & 0o7777 == 0o755
    assert (extracted / "usr").is_dir()
    ARCH.validate_mtree(extracted)


@pytest.mark.parametrize("umask", (0o0002, 0o0022))
def test_arch_directory_modes_and_mtree_are_umask_independent(tmp_path, umask):
    root = tmp_path / f"root-{umask:o}"
    root.mkdir()
    (root / ".PKGINFO").write_text("pkgname = devlegate\n", encoding="ascii")
    nested = root / "usr/share/doc/devlegate/LICENSES/nested"
    nested.mkdir(parents=True)
    (nested / "NOTICE.txt").write_text("notice\n", encoding="ascii")
    old_umask = os.umask(umask)
    try:
        output = tmp_path / f"package-{umask:o}.pkg.tar.zst"
        ARCH.build(root, output, timestamp=123)
    finally:
        os.umask(old_umask)
    extracted = tmp_path / f"extracted-{umask:o}"
    ARCH.extract(output, extracted)
    for directory in extracted.rglob("*"):
        if directory.is_dir():
            assert directory.stat().st_mode & 0o7777 == 0o755
    ARCH.validate_mtree(extracted)


def test_arch_rejects_tampered_mtree(tmp_path):
    root = tmp_path / "root"
    root.mkdir()
    (root / ".PKGINFO").write_text("pkgname = devlegate\n", encoding="ascii")
    (root / "usr").mkdir()
    (root / "usr/devlegate").write_bytes(b"payload")
    package = tmp_path / "package.pkg.tar.zst"
    ARCH.build(root, package, timestamp=123)
    extracted = tmp_path / "extracted"
    ARCH.extract(package, extracted)
    mtree = extracted / ".MTREE"
    mtree.write_bytes(
        gzip.compress(
            gzip.decompress(mtree.read_bytes()).replace(
                b"sha256digest=", b"sha256digest=0"
            ),
            mtime=0,
        )
    )
    tampered = tmp_path / "tampered.pkg.tar.zst"
    ARCH.build(extracted, tampered, timestamp=123)
    with pytest.raises(ARCH.ArchFormatError, match="MTREE"):
        ARCH.validate_mtree(extracted)


def test_arch_archive_is_reproducible_across_umask(tmp_path):
    outputs = []
    for umask in (0o0002, 0o0022):
        root = tmp_path / f"root-{umask:o}"
        root.mkdir()
        (root / ".PKGINFO").write_text("pkgname = devlegate\n", encoding="ascii")
        (root / "usr").mkdir()
        (root / "usr/devlegate").write_bytes(b"payload")
        output = tmp_path / f"package-{umask:o}.pkg.tar.zst"
        old_umask = os.umask(umask)
        try:
            ARCH.build(root, output, timestamp=123)
        finally:
            os.umask(old_umask)
        outputs.append(output.read_bytes())
    assert outputs[0] == outputs[1]


def test_arch_rejects_duplicate_and_traversal_members(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in (".PKGINFO", "../escape"):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    package = tmp_path / "bad.pkg.tar.zst"
    package.write_bytes(ARCH._zstd_encode(output.getvalue()))
    with pytest.raises(ARCH.ArchFormatError, match="unsafe"):
        ARCH.members(package)


def test_arch_rejects_duplicate_and_link_members(tmp_path):
    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        for name in (".PKGINFO", ".PKGINFO"):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    duplicate = tmp_path / "duplicate.pkg.tar.zst"
    duplicate.write_bytes(ARCH._zstd_encode(output.getvalue()))
    with pytest.raises(ARCH.ArchFormatError, match="duplicate"):
        ARCH.members(duplicate)

    output = io.BytesIO()
    with tarfile.open(fileobj=output, mode="w") as archive:
        archive.addfile(tarfile.TarInfo("usr"))
        link = tarfile.TarInfo("usr/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "../../escape"
        archive.addfile(link)
    link_package = tmp_path / "link.pkg.tar.zst"
    link_package.write_bytes(ARCH._zstd_encode(output.getvalue()))
    with pytest.raises(ARCH.ArchFormatError, match="unsupported"):
        ARCH.members(link_package)


def test_arch_rejects_malformed_tar_stream(tmp_path):
    package = tmp_path / "malformed.pkg.tar.zst"
    package.write_bytes(ARCH._zstd_encode(b"not a tar stream"))
    with pytest.raises(ARCH.ArchFormatError, match="invalid Arch tar stream"):
        ARCH.members(package)


def test_native_payload_custom_license_root_is_used_for_every_license_member():
    native = load("native_payload")
    standalone = load("package_standalone")
    manifest_path = ROOT / "packaging/standalone-compliance/manifest.json"
    manifest = standalone.load_manifest(manifest_path)
    layout = native.PayloadLayout(licenses="legal/snapshots")

    entries = native._inventory(manifest, layout, ROOT)
    license_destinations = {
        entry.destination
        for entry in entries
        if entry.role in {"manifest", "license"}
    }
    assert license_destinations
    assert all(
        path.startswith("legal/snapshots/") for path in license_destinations
    )
    assert not any("/usr/share/doc/devlegate/" in path for path in license_destinations)


@pytest.mark.parametrize(
    "field",
    ("executable", "documentation", "licenses", "provenance"),
)
@pytest.mark.parametrize("value", ("usr//bin/devlegate", "usr/bin/./devlegate"))
def test_native_payload_rejects_noncanonical_layout_destinations(field, value):
    native = load("native_payload")
    with pytest.raises(native.PackageError, match="layout path is unsafe"):
        native.PayloadLayout(**{field: value})
