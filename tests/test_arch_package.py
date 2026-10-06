# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import hashlib
import importlib.util
import json
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.skipif(
    subprocess.run(
        ["sh", "-c", "command -v zstd"], capture_output=True
    ).returncode
    != 0,
    reason="zstd is required to create Arch packages",
)

ROOT = Path(__file__).parents[1]
TOOLS = ROOT / "tools"
sys.path.insert(0, str(TOOLS))


def load_tool(name):
    spec = importlib.util.spec_from_file_location(name, TOOLS / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


VALIDATOR = load_tool("validate_arch")
BUILDER = load_tool("package_arch")


def standalone_root(tmp_path: Path) -> Path:
    root = tmp_path / "standalone"
    (root / "LICENSES").mkdir(parents=True)
    binary = root / "devlegate"
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    for name in (
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
    ):
        (root / name).write_text(name + "\n", encoding="ascii")
    (root / "LICENSES/manifest.json").write_text("{}\n", encoding="ascii")
    return root


def make_arch_package(tmp_path: Path, *, arch: str = "x86_64") -> tuple[Path, Path]:
    root = tmp_path / "root"
    (root / "usr/bin").mkdir(parents=True)
    (root / "usr/share/doc/devlegate/LICENSES").mkdir(parents=True)
    binary = root / "usr/bin/devlegate"
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    doc = root / "usr/share/doc/devlegate"
    for name in (
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
    ):
        (doc / name).write_text(name + "\n", encoding="ascii")
    (doc / "LICENSES/manifest.json").write_text("{}\n", encoding="ascii")
    (doc / "INSTALLATION-PROVENANCE.json").write_text(
        json.dumps(
            {
                "distribution": "arch",
                "package": "devlegate",
                "version": "0.5.6.dev0",
                "source_commit": "a" * 40,
                "payload_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
        )
        + "\n",
        encoding="ascii",
    )
    (root / ".PKGINFO").write_text(
        "pkgname = devlegate\npkgbase = devlegate\npkgver = 0.5.6.dev0\npkgrel = 1\n"
        f"arch = {arch}\nlicense = EUPL-1.2\n",
        encoding="ascii",
    )
    package = tmp_path / "devlegate.pkg.tar.zst"
    subprocess.run(
        ["tar", "--zstd", "-cf", str(package), "-C", str(root), "."],
        check=True,
        capture_output=True,
    )
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"source_commit": "a" * 40, "wheel": {"version": "0.5.6.dev0"}}),
        encoding="ascii",
    )
    return package, report


def test_arch_validator_accepts_dependency_free_payload(tmp_path):
    package, report = make_arch_package(tmp_path)
    binary = VALIDATOR.validate(package, report, tmp_path / "extract")
    assert binary.is_file()
    assert VALIDATOR.fields(package)["arch"] == ["x86_64"]


def test_arch_validator_rejects_wrong_architecture(tmp_path):
    package, report = make_arch_package(tmp_path, arch="aarch64")
    with pytest.raises(VALIDATOR.PackageError, match="arch"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_production_arch_builder_emits_valid_package(tmp_path, monkeypatch):
    extracted = standalone_root(tmp_path)
    report = tmp_path / "build-report.json"
    report.write_text(
        json.dumps({"source_commit": "a" * 40, "wheel": {"version": "0.5.6.dev0"}}),
        encoding="ascii",
    )
    monkeypatch.setattr(BUILDER, "validate", lambda *args: extracted)
    monkeypatch.setattr(BUILDER, "git_timestamp", lambda *args: 1)
    package = BUILDER.package(
        repo=tmp_path,
        archive=tmp_path / "standalone.tar.gz",
        sidecar=tmp_path / "standalone.tar.gz.sha256",
        build_report=report,
        output_dir=tmp_path / "output",
    )
    assert package.name == "devlegate-0.5.6.dev0-1-x86_64.pkg.tar.zst"
    VALIDATOR.validate(package, report, tmp_path / "validated")
