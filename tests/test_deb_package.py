# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import gzip
import hashlib
import importlib.util
import io
import json
import os
import shutil
import subprocess
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


VALIDATOR = load_tool("validate_deb")
BUILDER = load_tool("package_deb")
DEB = load_tool("deb_format")
ARCH_BUILDER = load_tool("package_arch")
ARCH = load_tool("arch_format")
ARCH_VALIDATOR = load_tool("validate_arch")


def make_package(
    tmp_path: Path,
    *,
    depends: str | None = None,
    maintainer_script: bool = False,
    installed_size: int | None = None,
    include_installed_size: bool = True,
    legacy_layout: bool = False,
    repository_integration: bool = False,
) -> tuple[Path, Path]:
    root = tmp_path / "root"
    control = root / "DEBIAN"
    binary = (
        root / "usr/lib/devlegate/devlegate"
        if legacy_layout
        else root / "usr/bin/devlegate"
    )
    doc = root / "usr/share/doc/devlegate"
    control.mkdir(parents=True)
    binary.parent.mkdir(parents=True)
    doc.mkdir(parents=True)
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    if legacy_layout:
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
    (doc / "INSTALLATION-PROVENANCE.json").write_text(
        json.dumps(
            {
                "distribution": "debian",
                "package": "devlegate",
                "payload_sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            }
        )
        + "\n",
        encoding="ascii",
    )
    licenses = doc / "LICENSES"
    licenses.mkdir()
    (licenses / "standalone-compliance-manifest.json").write_text(
        "{}\n", encoding="ascii"
    )
    if repository_integration:
        integration = root / "skills/architect/SKILL.md"
        integration.parent.mkdir(parents=True)
        integration.write_text("generated\n", encoding="ascii")
    dependency = f"Depends: {depends}\n" if depends else ""
    size = (
        VALIDATOR.installed_size_kib(root) if installed_size is None else installed_size
    )
    installed_size_field = f"Installed-Size: {size}\n" if include_installed_size else ""
    (control / "control").write_text(
        "Package: devlegate\nVersion: 0.5.6.dev0\nArchitecture: amd64\n"
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
    report.write_text(json.dumps({"wheel": {"version": "0.5.6.dev0"}}))
    return package, report


def make_standalone_payload(root: Path) -> Path:
    extracted = root / "standalone"
    extracted.mkdir()
    binary = extracted / "devlegate"
    binary.write_bytes(b"standalone")
    binary.chmod(0o755)
    for name in (
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "THIRD_PARTY_NOTICES.md",
        "BUILD-PROVENANCE.json",
    ):
        (extracted / name).write_text(f"{name}\n", encoding="ascii")
    licenses = extracted / "LICENSES"
    licenses.mkdir()
    (licenses / "standalone-compliance-manifest.json").write_text(
        "{}\n", encoding="ascii"
    )
    return extracted


def complete_report(standalone, repo, manifest_path, extracted, monkeypatch):
    """Build complete standalone authority for strict native validation."""
    builder = load_tool("build_standalone")
    binary = extracted / "devlegate"
    report = {
        "source_commit": "a" * 40,
        "inputs": {
            "target": "linux-x86_64",
            "pex_version": "2.103.2",
            "pex_wheel": builder.PEX_WHEEL,
            "pex_sha256": builder.PEX_SHA256,
            "pbs_archive": standalone.PBS_ARCHIVE,
            "pbs_sha256": standalone.PBS_SHA256,
            "science_version": "0.21.0",
            "science_asset": standalone.SCIENCE_ASSET,
            "science_sha256": standalone.SCIENCE_SHA256,
            "pbs_provider": standalone.PBS_PROVIDER,
            "pbs_release": "20260901",
            "pbs_python_version": "3.12.14",
            "scie_jump_version": standalone.SCIE_JUMP_VERSION,
        },
        "target_libc": "glibc",
        "pex": {
            "version": "2.103.2",
            "wheel_filename": builder.PEX_WHEEL,
            "wheel_sha256": builder.PEX_SHA256,
            "bootstrap_tools": standalone.pinned_tools(
                builder.PEX_BOOTSTRAP_TOOLS
            )["artifacts"],
        },
        "science": {
            "version": "0.21.0",
            "asset": standalone.SCIENCE_ASSET,
            "sha256": standalone.SCIENCE_SHA256,
        },
        "scie_jump": {
            "asset": standalone.SCIE_JUMP_ASSET,
            "version": standalone.SCIE_JUMP_VERSION,
            "sha256": standalone.SCIE_JUMP_SHA256,
        },
        "wheel": {"version": "0.5.6.dev0", "sha256": "b" * 64},
        "scie": {
            "sha256": hashlib.sha256(binary.read_bytes()).hexdigest(),
            "size": binary.stat().st_size,
        },
        "wheel_build_toolchain": standalone.pinned_tools(builder.WHEEL_BUILD_TOOLS),
        "wheel_reproducible": True,
        "scie_reproducible": True,
        "independent_build_roots": True,
        "independent_pex_roots": True,
        "temporary_build_path_embedded": False,
        "reproducibility_scope": standalone.EXPECTED_REPRODUCIBILITY_SCOPE,
    }
    inventory = {
        standalone.SCIE_JUMP_SPLIT_NAME: {
            "size": 1,
            "sha256": standalone.SCIE_JUMP_SHA256,
        },
        standalone.PBS_ARCHIVE: {"size": 1, "sha256": standalone.PBS_SHA256},
        "pex": {"size": 1, "sha256": "d" * 64},
        "configure-binding.py": {"size": 1, "sha256": "e" * 64},
        "lift.json": {"size": 1, "sha256": "f" * 64},
    }
    (extracted / "BUILD-PROVENANCE.json").write_text(
        json.dumps(
            standalone.stable_provenance(report, manifest_path, inventory, repo)
        ),
        encoding="ascii",
    )
    monkeypatch.setattr(standalone, "split_inventory", lambda *_args: inventory)
    return report


def make_verified_package(
    tmp_path: Path, monkeypatch, *, legacy_layout: bool = False
):
    """Build a package against a real checked-out compliance authority."""
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("LICENSE", "NOTICE", "LICENSING.md"):
        shutil.copy2(ROOT / name, repo / name)
    shutil.copy2(ROOT / "pyproject.toml", repo / "pyproject.toml")
    shutil.copytree(ROOT / "LICENSES", repo / "LICENSES")
    shutil.copytree(ROOT / "src", repo / "src")
    compliance = repo / "packaging/standalone-compliance"
    shutil.copytree(ROOT / "packaging/standalone-compliance", compliance)
    manifest_path = compliance / "manifest.json"
    standalone = load_tool("package_standalone")
    manifest = standalone.load_manifest(manifest_path)
    extracted = make_standalone_payload(tmp_path)
    for name in ("LICENSE", "NOTICE", "LICENSING.md"):
        shutil.copy2(repo / name, extracted / name)
    (extracted / "THIRD_PARTY_NOTICES.md").write_text(
        standalone.notice_text(manifest["records"]), encoding="ascii"
    )
    licenses = extracted / "LICENSES"
    (licenses / "standalone-compliance-manifest.json").write_bytes(
        manifest_path.read_bytes()
    )
    for _record, file_record in standalone.manifest_file_records(manifest):
        relative = standalone.archive_license_path(file_record["path"])
        target = licenses / relative.removeprefix("LICENSES/")
        if relative in {"LICENSE", "NOTICE", "LICENSING.md"}:
            target = extracted / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((compliance / file_record["path"]).read_bytes())
    report_data = complete_report(
        standalone, repo, manifest_path, extracted, monkeypatch
    )
    root = tmp_path / "verified-root"
    root.mkdir()
    BUILDER.assemble(
        extracted,
        root,
        distribution="debian",
        format_tool=BUILDER.TOOL_IDENTITY,
        repo=repo,
        manifest=manifest_path,
    )
    if legacy_layout:
        binary = root / "usr/bin/devlegate"
        binary.unlink()
        binary.symlink_to("../lib/devlegate/devlegate")
    control = root / "DEBIAN"
    control.mkdir()
    size = VALIDATOR.installed_size_kib(root)
    (control / "control").write_text(
        "Package: devlegate\nVersion: 0.5.6.dev0\nArchitecture: amd64\n"
        f"Installed-Size: {size}\nDescription: test\n test\n",
        encoding="ascii",
    )
    package = tmp_path / "verified.deb"
    DEB.build(root, package, timestamp=0)
    report = tmp_path / "verified-report.json"
    report.write_text(json.dumps(report_data), encoding="ascii")
    return package, report, repo, manifest_path, extracted


def test_production_deb_builder_sets_maintainer_and_passes_validator(
    tmp_path, monkeypatch
):
    extracted = make_standalone_payload(tmp_path)
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("LICENSE", "NOTICE", "LICENSING.md"):
        shutil.copy2(ROOT / name, repo / name)
    shutil.copy2(ROOT / "pyproject.toml", repo / "pyproject.toml")
    shutil.copytree(ROOT / "LICENSES", repo / "LICENSES")
    shutil.copytree(ROOT / "src", repo / "src")
    shutil.copytree(
        ROOT / "packaging/standalone-compliance",
        repo / "packaging/standalone-compliance",
    )
    standalone = load_tool("package_standalone")
    manifest_path = repo / "packaging/standalone-compliance/manifest.json"
    manifest = standalone.load_manifest(manifest_path)
    for name in ("LICENSE", "NOTICE", "LICENSING.md"):
        shutil.copy2(repo / name, extracted / name)
    (extracted / "THIRD_PARTY_NOTICES.md").write_text(
        standalone.notice_text(manifest["records"]), encoding="ascii"
    )
    licenses = extracted / "LICENSES"
    (licenses / "standalone-compliance-manifest.json").write_bytes(
        manifest_path.read_bytes()
    )
    for _record, file_record in standalone.manifest_file_records(manifest):
        relative = standalone.archive_license_path(file_record["path"])
        target = licenses / relative.removeprefix("LICENSES/")
        if relative in {"LICENSE", "NOTICE", "LICENSING.md"}:
            target = extracted / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((manifest_path.parent / file_record["path"]).read_bytes())
    report_data = complete_report(
        standalone, repo, manifest_path, extracted, monkeypatch
    )
    report = tmp_path / "build-report.json"
    report.write_text(json.dumps(report_data), encoding="ascii")
    monkeypatch.setattr(BUILDER, "validate", lambda *args: extracted)
    monkeypatch.setattr(BUILDER, "git_timestamp", lambda *args: 1)

    package = BUILDER.package(
        repo=repo,
        archive=tmp_path / "standalone.tar.gz",
        sidecar=tmp_path / "standalone.tar.gz.sha256",
        build_report=report,
        output_dir=tmp_path / "output",
    )

    fields = VALIDATOR.fields(package)
    assert fields["Maintainer"] == "Daniil Romanov <romanov.at.bg@gmail.com>"
    assert "devlegate.invalid" not in subprocess.run(
        ["dpkg-deb", "--info", str(package)],
        check=True,
        capture_output=True,
        text=True,
    ).stdout
    VALIDATOR.validate(
        package,
        report,
        tmp_path / "validated",
        repo,
        repo / "packaging/standalone-compliance/manifest.json",
    )
    marker = (
        tmp_path / "validated/usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json"
    )
    assert json.loads(marker.read_text())[
        "format_tool"
    ] == BUILDER.TOOL_IDENTITY


def test_deb_and_arch_preserve_one_verified_payload_and_reject_tampering(
    tmp_path, monkeypatch
):
    """Both native decoders enforce the same trusted standalone payload."""
    deb_package, report_path, repo, manifest_path, extracted = make_verified_package(
        tmp_path, monkeypatch
    )
    report = json.loads(report_path.read_text(encoding="ascii"))

    arch_root = tmp_path / "arch-root"
    native = load_tool("native_payload")
    native.assemble(
        extracted,
        arch_root,
        distribution="arch",
        format_tool=ARCH_BUILDER.TOOL_IDENTITY,
        repo=repo,
        manifest=manifest_path,
    )
    size = sum(
        path.stat().st_size
        for path in arch_root.rglob("*")
        if path.is_file()
    )
    (arch_root / ".PKGINFO").write_text(
        "pkgname = devlegate\n"
        "pkgbase = devlegate\n"
        f"pkgver = {report['wheel']['version']}-1\n"
        f"size = {size}\n"
        "pkgdesc = deterministic local agent orchestrator\n"
        "url = https://github.com/ranmasao/devlegate\n"
        "builddate = 0\n"
        "packager = Devlegate maintainers\n"
        "arch = x86_64\n"
        "license = EUPL-1.2\n"
        "xdata = pkgtype=pkg\n",
        encoding="ascii",
    )
    arch_package = tmp_path / "verified.pkg.tar.zst"
    ARCH.build(arch_root, arch_package, timestamp=0)

    deb_root = tmp_path / "deb-extracted"
    VALIDATOR.validate(
        deb_package, report_path, deb_root, repo, manifest_path
    )
    arch_extracted = tmp_path / "arch-extracted"
    ARCH_VALIDATOR.validate(
        arch_package, report_path, arch_extracted, repo, manifest_path
    )
    logical = {
        path.relative_to(deb_root).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o7777,
        )
        for path in deb_root.rglob("*")
        if path.is_file()
        and path.name != "INSTALLATION-PROVENANCE.json"
    }
    arch_logical = {
        path.relative_to(arch_extracted).as_posix(): (
            hashlib.sha256(path.read_bytes()).hexdigest(),
            path.stat().st_mode & 0o7777,
        )
        for path in arch_extracted.rglob("*")
        if path.is_file()
        and path.name
        not in {".PKGINFO", ".MTREE", "INSTALLATION-PROVENANCE.json"}
    }
    assert logical == arch_logical
    assert any(
        path.relative_to(arch_extracted).as_posix().startswith(
            "usr/share/doc/devlegate/LICENSES/"
        )
        for path in arch_extracted.rglob("*")
        if path.is_file()
    )

    def tampered_package(name, mutate):
        root = tmp_path / f"tampered-{name}"
        shutil.copytree(arch_extracted, root)
        mutate(root)
        package = tmp_path / f"tampered-{name}.pkg.tar.zst"
        ARCH.build(root, package, timestamp=0)
        return package

    license_package = tampered_package(
        "license",
        lambda root: (
            root
            / "usr/share/doc/devlegate/LICENSES/pex-vendored/ansicolors-1.1.8-ISC.txt"
        ).write_text("tampered\n", encoding="ascii"),
    )
    with pytest.raises(ARCH_VALIDATOR.PackageError, match="snapshot differs"):
        ARCH_VALIDATOR.validate(
            license_package,
            report_path,
            tmp_path / "license-validation",
            repo,
            manifest_path,
        )

    def change_provenance(root):
        path = root / "usr/share/doc/devlegate/BUILD-PROVENANCE.json"
        value = json.loads(path.read_text(encoding="utf-8"))
        value["source_commit"] = "changed"
        path.write_text(json.dumps(value), encoding="utf-8")

    provenance_package = tampered_package("provenance", change_provenance)
    with pytest.raises(ARCH_VALIDATOR.PackageError, match="build provenance"):
        ARCH_VALIDATOR.validate(
            provenance_package,
            report_path,
            tmp_path / "provenance-validation",
            repo,
            manifest_path,
        )

    mode_package = tampered_package(
        "mode",
        lambda root: (root / "usr/share/doc/devlegate/LICENSE").chmod(0o755),
    )
    with pytest.raises(ARCH_VALIDATOR.PackageError, match="unexpected mode"):
        ARCH_VALIDATOR.validate(
            mode_package,
            report_path,
            tmp_path / "mode-validation",
            repo,
            manifest_path,
        )
def test_owned_deb_builder_is_byte_reproducible_without_package_manager(tmp_path):
    root = tmp_path / "root"
    (root / "DEBIAN").mkdir(parents=True)
    (root / "usr/bin").mkdir(parents=True)
    (root / "DEBIAN/control").write_text(
        "Package: devlegate\nVersion: 1\nArchitecture: amd64\n"
        "Description: test\n test\n",
        encoding="ascii",
    )
    (root / "usr/bin/devlegate").write_bytes(b"payload")
    first = tmp_path / "first.deb"
    second = tmp_path / "second.deb"
    BUILDER.build_deb(root, first, timestamp=123)
    BUILDER.build_deb(root, second, timestamp=123)
    assert first.read_bytes() == second.read_bytes()


def test_deb_validator_accepts_dependency_free_payload(tmp_path, monkeypatch):
    package, report, repo, manifest, _extracted = make_verified_package(
        tmp_path, monkeypatch
    )
    binary = VALIDATOR.validate(package, report, tmp_path / "extract", repo, manifest)
    assert binary.is_file()
    metadata = VALIDATOR.fields(package)
    assert int(metadata["Installed-Size"]) > 0
    assert not list((tmp_path / "extract/usr/share/doc/devlegate").glob("*.tar.gz*"))


def test_deb_and_arch_adapters_preserve_one_verified_logical_payload(
    tmp_path, monkeypatch
):
    deb_package, report, repo, manifest, extracted = make_verified_package(
        tmp_path, monkeypatch
    )
    arch_builder = ARCH_BUILDER
    monkeypatch.setattr(arch_builder, "validate", lambda *args: extracted)
    monkeypatch.setattr(arch_builder, "git_timestamp", lambda *_args: 0)
    arch_package = arch_builder._package(
        repo=repo,
        archive=tmp_path / "standalone.tar.gz",
        sidecar=tmp_path / "standalone.tar.gz.sha256",
        build_report=report,
        output_dir=tmp_path / "arch-output",
    )

    deb_root = tmp_path / "deb-extracted"
    deb_binary = VALIDATOR.validate(deb_package, report, deb_root, repo, manifest)
    arch_root = tmp_path / "arch-extracted"
    arch_binary = ARCH_VALIDATOR.validate(
        arch_package, report, arch_root, repo, manifest
    )

    def logical_files(root: Path) -> dict[str, tuple[str, int]]:
        return {
            path.relative_to(root).as_posix(): (
                hashlib.sha256(path.read_bytes()).hexdigest(),
                path.stat().st_mode & 0o7777,
            )
            for path in root.rglob("*")
            if path.is_file()
        }

    deb_files = logical_files(deb_root)
    arch_files = {
        name: value
        for name, value in logical_files(arch_root).items()
        if name not in {".PKGINFO", ".MTREE"}
    }
    provenance = "usr/share/doc/devlegate/INSTALLATION-PROVENANCE.json"
    assert deb_files.keys() - {provenance} == arch_files.keys() - {provenance}
    assert {
        name: value for name, value in deb_files.items() if name != provenance
    } == {
        name: value for name, value in arch_files.items() if name != provenance
    }
    assert deb_binary.read_bytes() == arch_binary.read_bytes()
    assert deb_binary.stat().st_mode & 0o7777 == arch_binary.stat().st_mode & 0o7777

    tampered_root = tmp_path / "arch-tampered"
    ARCH.extract(arch_package, tampered_root)
    (tampered_root / "usr/share/doc/devlegate/LICENSES/extra.txt").write_text(
        "unexpected", encoding="ascii"
    )
    tampered_package = tmp_path / "arch-tampered.pkg.tar.zst"
    ARCH.build(tampered_root, tampered_package, timestamp=0)
    with pytest.raises(ARCH_VALIDATOR.PackageError, match="unexpected files"):
        ARCH_VALIDATOR.validate(
            tampered_package,
            report,
            tmp_path / "arch-tampered-extracted",
            repo,
            manifest,
        )


def test_deb_validator_rejects_reduced_standalone_report(tmp_path, monkeypatch):
    package, report, repo, manifest, _extracted = make_verified_package(
        tmp_path, monkeypatch
    )
    reduced = tmp_path / "reduced-report.json"
    reduced.write_text(
        json.dumps({"wheel": {"version": "0.5.6.dev0"}, "scie": {"sha256": "x"}}),
        encoding="ascii",
    )
    with pytest.raises(VALIDATOR.PackageError, match="complete standalone evidence"):
        VALIDATOR.validate(
            package, reduced, tmp_path / "reduced-extract", repo, manifest
        )


def test_deb_validator_rejects_former_private_payload_and_launcher_symlink(
    tmp_path, monkeypatch
):
    package, report, repo, manifest, _extracted = make_verified_package(
        tmp_path, monkeypatch, legacy_layout=True
    )
    with pytest.raises(VALIDATOR.PackageError, match="regular file"):
        VALIDATOR.validate(package, report, tmp_path / "extract", repo, manifest)


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


def test_deb_validator_rejects_repository_integration(tmp_path):
    package, report = make_package(tmp_path, repository_integration=True)
    with pytest.raises(VALIDATOR.PackageError, match="repository integration"):
        VALIDATOR.validate(package, report, tmp_path / "extract")


def test_deb_extractor_rejects_duplicate_and_link_escape_members(tmp_path):
    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.GNU_FORMAT) as archive:
        for name in ("usr/bin/devlegate", "usr/bin/devlegate"):
            info = tarfile.TarInfo(name)
            info.size = 1
            archive.addfile(info, io.BytesIO(b"x"))
    package = tmp_path / "duplicate.deb"
    package.write_bytes(
        b"!<arch>\n"
        + DEB._ar_member("debian-binary", b"2.0\n", 0)
        + DEB._ar_member("data.tar.gz", gzip.compress(payload.getvalue(), mtime=0), 0)
    )
    with pytest.raises(DEB.DebFormatError, match="duplicate"):
        DEB.extract(package, tmp_path / "extract")

    payload = io.BytesIO()
    with tarfile.open(fileobj=payload, mode="w", format=tarfile.GNU_FORMAT) as archive:
        link = tarfile.TarInfo("usr/bin/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/tmp/escape"
        archive.addfile(link)
    absolute = tmp_path / "absolute-link.deb"
    absolute.write_bytes(
        b"!<arch>\n"
        + DEB._ar_member("debian-binary", b"2.0\n", 0)
        + DEB._ar_member("data.tar.gz", gzip.compress(payload.getvalue(), mtime=0), 0)
    )
    with pytest.raises(DEB.DebFormatError, match="unsafe link"):
        DEB.extract(absolute, tmp_path / "absolute-extract")
