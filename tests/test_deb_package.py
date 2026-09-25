# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import json
import os
import subprocess
import sys
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


VALIDATOR = load_tool("validate_deb")


def make_package(
    tmp_path: Path,
    *,
    depends: str | None = None,
    maintainer_script: bool = False,
    installed_size: int | None = None,
    include_installed_size: bool = True,
) -> tuple[Path, Path]:
    root = tmp_path / "root"
    control = root / "DEBIAN"
    binary = root / "usr/lib/devlegate/devlegate"
    doc = root / "usr/share/doc/devlegate"
    control.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    doc.mkdir(parents=True)
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/bin/devlegate").symlink_to("../lib/devlegate/devlegate")
    for name in (
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
    ):
        (doc / name).write_text(f"{name}\n", encoding="ascii")
    licenses = doc / "LICENSES"
    licenses.mkdir()
    (licenses / "standalone-compliance-manifest.json").write_text(
        "{}\n", encoding="ascii"
    )
    dependency = f"Depends: {depends}\n" if depends else ""
    size = (
        VALIDATOR.installed_size_kib(root) if installed_size is None else installed_size
    )
    installed_size_field = f"Installed-Size: {size}\n" if include_installed_size else ""
    (control / "control").write_text(
        "Package: devlegate\nVersion: 0.5.4.dev0\nArchitecture: amd64\n"
        f"{installed_size_field}"
        f"{dependency}Description: test\n test\n",
        encoding="ascii",
    )
    if maintainer_script:
        script = control / "postinst"
        script.write_text("#!/bin/sh\n", encoding="ascii")
        script.chmod(0o755)
    package = tmp_path / "devlegate.deb"
    subprocess.run(
        ["dpkg-deb", "--build", "--root-owner-group", str(root), str(package)],
        check=True,
        capture_output=True,
        text=True,
    )
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"wheel": {"version": "0.5.4.dev0"}}))
    return package, report


def test_deb_validator_accepts_dependency_free_payload(tmp_path):
    package, report = make_package(tmp_path)
    binary = VALIDATOR.validate(package, report, tmp_path / "extract")
    assert binary.is_file()
    metadata = VALIDATOR.fields(package)
    assert int(metadata["Installed-Size"]) > 0
    assert not list((tmp_path / "extract/usr/share/doc/devlegate").glob("*.tar.gz*"))


def test_installed_size_rounds_each_tiny_file(tmp_path):
    (tmp_path / "first").write_bytes(b"1")
    (tmp_path / "second").write_bytes(b"2")
    assert VALIDATOR.installed_size_kib(tmp_path) == 2


def test_installed_size_accounts_for_symlink_size(tmp_path):
    (tmp_path / "target").write_bytes(b"x" * 1025)
    (tmp_path / "link").symlink_to("target")
    assert VALIDATOR.installed_size_kib(tmp_path) == 3


def test_installed_size_counts_other_filesystem_objects(tmp_path):
    (tmp_path / "directory").mkdir()
    fifo = tmp_path / "fifo"
    os.mkfifo(fifo)
    assert VALIDATOR.installed_size_kib(tmp_path) == 2


def test_installed_size_excludes_debian_control_from_absolute_root(tmp_path):
    control = tmp_path / "DEBIAN"
    control.mkdir()
    (control / "control").write_bytes(b"x" * 2048)
    (tmp_path / "payload").write_bytes(b"x")
    assert VALIDATOR.installed_size_kib(tmp_path) == 1


def test_deb_validator_rejects_inconsistent_installed_size(tmp_path):
    package, report = make_package(tmp_path, installed_size=9999)
    with pytest.raises(VALIDATOR.PackageError, match="does not match"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_validator_rejects_missing_installed_size(tmp_path):
    package, report = make_package(tmp_path, include_installed_size=False)
    with pytest.raises(VALIDATOR.PackageError, match="invalid Installed-Size"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_validator_rejects_invalid_installed_size(tmp_path):
    package, report = make_package(tmp_path, installed_size=0)
    with pytest.raises(VALIDATOR.PackageError, match="not positive"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_validator_rejects_runtime_dependencies(tmp_path):
    package, report = make_package(tmp_path, depends="python3")
    with pytest.raises(VALIDATOR.PackageError, match="runtime dependencies"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_validator_rejects_maintainer_scripts(tmp_path):
    package, report = make_package(tmp_path, maintainer_script=True)
    with pytest.raises(VALIDATOR.PackageError, match="maintainer scripts"):
        VALIDATOR.validate(package, report, tmp_path / "extract")
