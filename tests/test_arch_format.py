# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import io
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
