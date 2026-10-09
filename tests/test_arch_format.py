# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import hashlib
import importlib.util
import io
import json
import tarfile
from pathlib import Path

import pytest

ROOT = Path(__file__).parents[1]


def load(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "tools" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
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
    report.write_text(
        json.dumps(
            {
                "source_commit": source_commit,
                "wheel": {"version": "0.5.6.dev0"},
                "scie": {
                    "sha256": hashlib.sha256(b"standalone payload").hexdigest()
                },
            }
        ),
        encoding="ascii",
    )
    monkeypatch.setattr(package_arch, "validate", lambda *_args: extracted)
    output = package_arch._package(
        repo=ROOT,
        archive=tmp_path / "standalone.tar.gz",
        sidecar=tmp_path / "standalone.tar.gz.sha256",
        build_report=report,
        output_dir=tmp_path / "output",
    )

    validated = tmp_path / "validated"
    validate_arch.validate(output, report, validated, ROOT, manifest_path)
    members = ARCH.members(output)
    assert (
        "usr/share/doc/devlegate/LICENSES/pex-vendored/ansicolors-1.1.8-ISC.txt"
        in members
    )
    assert "usr/share/doc/devlegate/LICENSES/python-runtime/LICENSE.zlib.txt" in members

    extracted_package = tmp_path / "package-root"
    ARCH.extract(output, extracted_package)
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
