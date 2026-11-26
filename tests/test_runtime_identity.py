# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import hashlib
import json
import platform
from pathlib import Path
from types import SimpleNamespace

import pytest

from devlegate import cli, runtime_identity


@pytest.fixture
def native_layout(tmp_path, monkeypatch):
    executable = tmp_path / "usr/bin/devlegate"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"\x7fELFpayload")
    executable.chmod(0o755)
    documentation = tmp_path / "usr/share/doc/devlegate"
    documentation.mkdir(parents=True)
    monkeypatch.setenv("PEX", str(executable))
    monkeypatch.setenv("SCIE", str(executable))
    return executable, documentation / "INSTALLATION-PROVENANCE.json"


@pytest.mark.parametrize("distribution", ["debian", "arch"])
def test_native_version_requires_exact_payload_digest(
    native_layout, monkeypatch, capsys, distribution
):
    executable, marker = native_layout
    marker.write_text(
        json.dumps({
            "distribution": distribution,
            "package": "devlegate",
            "payload_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }),
        encoding="ascii",
    )
    monkeypatch.setattr("sys.argv", ["devlegate", "version"])
    assert cli.main() == 0
    lines = capsys.readouterr().out.splitlines()
    assert "Distribution: standalone" in lines
    assert f"Installation: {distribution}" in lines

    monkeypatch.setattr("sys.argv", ["devlegate", "version", "--json"])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["distribution"] == {
        "form": "standalone",
        "installer": distribution,
        "editable": "no",
        "direct_url": "unknown",
    }

    executable.write_bytes(b"\x7fELFreplaced")
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out)["distribution"]["installer"] == "unknown"


@pytest.mark.parametrize(
    "invalid",
    [
        None,
        "{",
        "[]",
        {"distribution": "unsupported"},
        {"distribution": ["arch"]},
        {"package": "unrelated"},
        {"payload_sha256": None},
        {"payload_sha256": "bad digest"},
        {"payload_sha256": "0" * 64},
    ],
)
def test_invalid_native_provenance_is_unknown(native_layout, invalid):
    executable, marker = native_layout
    if isinstance(invalid, dict):
        value = {
            "distribution": "arch",
            "package": "devlegate",
            "payload_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        }
        for field, entry in invalid.items():
            if entry is None:
                del value[field]
            else:
                value[field] = entry
        invalid = json.dumps(value)
    if invalid is not None:
        marker.write_text(invalid, encoding="ascii")
    value = runtime_identity.identity()
    assert value["distribution"]["form"] == "standalone"
    assert value["distribution"]["installer"] == "unknown"
    assert "Installation: unknown" in runtime_identity.banner(value).splitlines()


def test_metadata_must_resolve_to_imported_package(tmp_path, monkeypatch):
    current = tmp_path / "source/devlegate/__init__.py"
    current.parent.mkdir(parents=True)
    current.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(current))
    unrelated = SimpleNamespace(
        files=[Path("devlegate/__init__.py")],
        locate_file=lambda _path: tmp_path / "other/devlegate/__init__.py",
        read_text=lambda _name: "pip",
    )
    assert not runtime_identity._metadata_matches_package(unrelated)


def test_bound_python_distribution_accepts_installer(tmp_path, monkeypatch):
    package_file = tmp_path / "site/devlegate/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(package_file))
    distribution = SimpleNamespace(
        files=[Path("devlegate/__init__.py")],
        locate_file=lambda _path: package_file,
        read_text=lambda name: "pip\n" if name == "INSTALLER" else None,
    )
    monkeypatch.setattr(
        runtime_identity.metadata, "distribution", lambda _name: distribution
    )
    monkeypatch.delenv("PEX", raising=False)
    result = runtime_identity.identity()["distribution"]
    assert result["form"] == "python-package"
    assert result["installer"] == "pip"
    assert result["editable"] == "unknown"
    assert result["direct_url"] == "unknown"


def test_distribution_without_owned_package_file_is_not_bound(tmp_path, monkeypatch):
    package_file = tmp_path / "site/devlegate/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(package_file))
    distribution = SimpleNamespace(
        files=[Path("other/__init__.py")],
        locate_file=lambda _path: package_file,
        read_text=lambda _name: "pip",
    )
    assert not runtime_identity._metadata_matches_package(distribution)


def test_bound_python_distribution_without_optional_metadata_is_unknown(
    tmp_path, monkeypatch
):
    package_file = tmp_path / "site/devlegate/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(package_file))
    distribution = SimpleNamespace(
        files=[Path("devlegate/__init__.py")],
        locate_file=lambda _path: package_file,
        read_text=lambda _name: None,
    )
    monkeypatch.setattr(
        runtime_identity.metadata, "distribution", lambda _name: distribution
    )
    result = runtime_identity._package_identity()
    assert result["installer"] == "unknown"
    assert result["editable"] == "unknown"
    assert result["direct_url"] == "unknown"


def test_editable_python_distribution_requires_matching_source_root(
    tmp_path, monkeypatch
):
    source_root = tmp_path / "source"
    package_file = source_root / "devlegate/__init__.py"
    package_file.parent.mkdir(parents=True)
    package_file.write_text("", encoding="ascii")
    monkeypatch.setattr(runtime_identity.devlegate, "__file__", str(package_file))
    direct_url = json.dumps(
        {"url": source_root.as_uri(), "dir_info": {"editable": True}}
    )
    distribution = SimpleNamespace(
        files=None,
        locate_file=lambda _path: tmp_path / "unused",
        read_text=lambda name: (
            direct_url if name == "direct_url.json" else None
        ),
    )
    assert runtime_identity._metadata_matches_package(distribution)

    other_root = tmp_path / "other"
    distribution.read_text = lambda name: (
        json.dumps({"url": other_root.as_uri(), "dir_info": {"editable": True}})
        if name == "direct_url.json"
        else None
    )
    assert not runtime_identity._metadata_matches_package(distribution)


def test_standalone_requires_matching_pex_scie_elf_executable(tmp_path, monkeypatch):
    executable = tmp_path / "devlegate"
    executable.write_bytes(b"\x7fELFpayload")
    executable.chmod(0o755)
    monkeypatch.setenv("PEX", str(executable))
    monkeypatch.setenv("SCIE", str(executable))
    assert runtime_identity._standalone_executable() == executable.resolve()

    monkeypatch.delenv("SCIE")
    assert runtime_identity._standalone_executable() is None
    monkeypatch.setenv("SCIE", str(executable))
    monkeypatch.setenv("SCIE", str(tmp_path / "other"))
    assert runtime_identity._standalone_executable() is None
    monkeypatch.setenv("SCIE", str(executable))
    executable.chmod(0o644)
    assert runtime_identity._standalone_executable() is None
    executable.chmod(0o755)
    executable.write_bytes(b"not-elf")
    assert runtime_identity._standalone_executable() is None


def test_os_identity_falls_back_without_os_release(monkeypatch):
    monkeypatch.setattr(
        platform,
        "freedesktop_os_release",
        lambda: (_ for _ in ()).throw(OSError("unavailable")),
    )
    expected = {
        "name": platform.system() or "unknown",
        "version": platform.release() or "unknown",
    }
    assert runtime_identity._os_identity() == expected
