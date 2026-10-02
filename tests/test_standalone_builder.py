# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import hashlib
import importlib.util
import json
import os
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from zipfile import ZipFile

import pytest

SPEC = importlib.util.spec_from_file_location(
    "build_standalone", Path(__file__).parents[1] / "tools" / "build_standalone.py"
)
BUILDER = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = BUILDER
SPEC.loader.exec_module(BUILDER)


@pytest.mark.parametrize(
    ("major", "minor", "accepted"),
    ((3, 11, False), (3, 12, True), (3, 13, True), (4, 0, True)),
)
def test_builder_python_version_floor(major, minor, accepted):
    if accepted:
        BUILDER.validate_builder_version("CPython", major, minor)
    else:
        with pytest.raises(BUILDER.BuildError):
            BUILDER.validate_builder_version("CPython", major, minor)


def test_verified_artifact_rejects_wrong_hash(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"actual")

    with pytest.raises(BUILDER.BuildError, match="sha256 mismatch"):
        BUILDER.verify_file(artifact, "0" * 64)


def test_cached_input_uses_verified_blob_without_download(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    destination = tmp_path / "tools" / "input.bin"
    payload = b"immutable input"
    digest = hashlib.sha256(payload).hexdigest()
    (cache / digest).parent.mkdir(parents=True)
    (cache / digest).write_bytes(payload)
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))

    def fail_download(_path):
        pytest.fail("cache hit attempted a download")

    assert BUILDER.cached_input("input.bin", digest, destination, fail_download)
    assert destination.read_bytes() == payload


def test_cached_input_replaces_corrupt_blob_atomically(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    destination = tmp_path / "input.bin"
    payload = b"correct input"
    digest = hashlib.sha256(payload).hexdigest()
    cache.mkdir()
    (cache / digest).write_bytes(b"corrupt")
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))

    def download(path):
        path.write_bytes(payload)

    BUILDER.cached_input("input.bin", digest, destination, download)
    assert destination.read_bytes() == payload
    assert (cache / digest).read_bytes() == payload


def test_cached_input_does_not_publish_failed_download(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    destination = tmp_path / "input.bin"
    payload = b"partial input"
    digest = hashlib.sha256(b"complete input").hexdigest()
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))

    def fail_download(path):
        path.write_bytes(payload)
        raise OSError("interrupted")

    with pytest.raises(OSError):
        BUILDER.cached_input("input.bin", digest, destination, fail_download)
    assert not (cache / digest).exists()
    assert not destination.exists()


def test_standalone_assets_use_cached_pbs_and_scie_jump(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))
    pbs = b"pbs"
    jump = b"jump"
    pbs_digest = hashlib.sha256(pbs).hexdigest()
    jump_digest = hashlib.sha256(jump).hexdigest()
    (cache / pbs_digest).parent.mkdir(parents=True)
    (cache / pbs_digest).write_bytes(pbs)
    (cache / jump_digest).write_bytes(jump)
    monkeypatch.setattr(BUILDER, "PBS_SHA256", pbs_digest)
    monkeypatch.setattr(BUILDER, "SCIE_JUMP_SHA256", jump_digest)

    def fail_download(_path, _url):
        pytest.fail("warm standalone asset cache attempted a download")

    monkeypatch.setattr(BUILDER, "download_url_to", fail_download)
    assets_url = BUILDER.materialize_standalone_assets(tmp_path / "tools")
    assets = Path(assets_url.removeprefix("file://"))
    assert (
        assets
        / "jump"
        / "download"
        / f"v{BUILDER.SCIE_JUMP_VERSION}"
        / BUILDER.SCIE_JUMP_ASSET
    ).read_bytes() == jump
    assert (
        assets
        / "providers"
        / BUILDER.PBS_PROVIDER
        / "download"
        / BUILDER.PBS_RELEASE
        / BUILDER.PBS_ARCHIVE
    ).read_bytes() == pbs
    metadata = (
        assets
        / "providers"
        / BUILDER.PBS_PROVIDER
        / "download"
        / BUILDER.PBS_RELEASE
        / f"distributions-{BUILDER.PBS_PYTHON_VERSION}-install_only_stripped.json"
    )
    assert (
        json.loads(metadata.read_text())["assets"][0]["digest"]["fingerprint"]
        == pbs_digest
    )


def test_standalone_assets_reject_corrupt_cache_and_redownload(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))
    pbs = b"pbs"
    jump = b"jump"
    pbs_digest = hashlib.sha256(pbs).hexdigest()
    jump_digest = hashlib.sha256(jump).hexdigest()
    cache.mkdir()
    (cache / pbs_digest).write_bytes(b"corrupt pbs")
    (cache / jump_digest).write_bytes(b"corrupt jump")
    monkeypatch.setattr(BUILDER, "PBS_SHA256", pbs_digest)
    monkeypatch.setattr(BUILDER, "SCIE_JUMP_SHA256", jump_digest)
    downloads = []

    def download(path, url):
        downloads.append(url)
        path.write_bytes(pbs if path.name == BUILDER.PBS_ARCHIVE else jump)

    monkeypatch.setattr(BUILDER, "download_url_to", download)
    BUILDER.materialize_standalone_assets(tmp_path / "tools")
    assert len(downloads) == 2
    assert (cache / pbs_digest).read_bytes() == pbs
    assert (cache / jump_digest).read_bytes() == jump


def test_standalone_assets_match_science_download_mirror_layout(tmp_path, monkeypatch):
    cache = tmp_path / "cache"
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))
    pbs = b"pbs"
    jump = b"jump"
    pbs_digest = hashlib.sha256(pbs).hexdigest()
    jump_digest = hashlib.sha256(jump).hexdigest()
    cache.mkdir()
    (cache / pbs_digest).write_bytes(pbs)
    (cache / jump_digest).write_bytes(jump)
    monkeypatch.setattr(BUILDER, "PBS_SHA256", pbs_digest)
    monkeypatch.setattr(BUILDER, "SCIE_JUMP_SHA256", jump_digest)
    monkeypatch.setattr(BUILDER, "download_url_to", lambda *_: pytest.fail("download"))

    assets = Path(
        BUILDER.materialize_standalone_assets(tmp_path / "tools").removeprefix("file://")
    )
    jump_path = (
        assets
        / "jump"
        / "download"
        / f"v{BUILDER.SCIE_JUMP_VERSION}"
        / BUILDER.SCIE_JUMP_ASSET
    )
    provider_path = (
        assets
        / "providers"
        / BUILDER.PBS_PROVIDER
        / "download"
        / BUILDER.PBS_RELEASE
        / BUILDER.PBS_ARCHIVE
    )
    metadata = json.loads(
        provider_path.with_name(
            f"distributions-{BUILDER.PBS_PYTHON_VERSION}-install_only_stripped.json"
        ).read_text()
    )
    assert jump_path.is_file()
    assert provider_path.is_file()
    assert metadata["assets"][0]["rel_path"] == (
        f"download/{BUILDER.PBS_RELEASE}/{BUILDER.PBS_ARCHIVE}"
    )
    assert metadata["assets"][0]["target_triple"] == BUILDER.PBS_TARGET_TRIPLE


def test_real_pex_science_consumes_warm_asset_mirror(tmp_path, monkeypatch):
    cache_name = os.environ.get("DEVLEGATE_STANDALONE_INTEGRATION_CACHE")
    if not cache_name:
        pytest.skip(
            "set DEVLEGATE_STANDALONE_INTEGRATION_CACHE for toolchain integration"
        )
    cache = Path(cache_name)
    expected_digests = [
        BUILDER.PEX_SHA256,
        BUILDER.SCIENCE_SHA256,
        BUILDER.PBS_SHA256,
        BUILDER.SCIE_JUMP_SHA256,
        *(digest for _, digest in BUILDER.WHEEL_BUILD_TOOLS.values()),
        *(digest for _, digest in BUILDER.PEX_BOOTSTRAP_TOOLS.values()),
    ]
    if any(not (cache / digest).is_file() for digest in expected_digests):
        pytest.skip("the pinned standalone integration cache is not warm")
    for digest in expected_digests:
        try:
            BUILDER.verify_file(cache / digest, digest)
        except BUILDER.BuildError:
            pytest.skip("the pinned standalone integration cache is not warm")
    monkeypatch.setenv("DEVLEGATE_STANDALONE_INPUT_CACHE", str(cache))

    requests = []

    class UpstreamDenied(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            self.send_error(503, "upstream access is forbidden in this test")

        def log_message(self, _format, *_args):
            pass

    server = ThreadingHTTPServer(("127.0.0.1", 0), UpstreamDenied)
    server_url = f"http://127.0.0.1:{server.server_port}"
    try:
        for variable in ("ALL_PROXY", "HTTPS_PROXY", "HTTP_PROXY"):
            monkeypatch.setenv(variable, server_url)
        monkeypatch.setenv("NO_PROXY", "")
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        tools = tmp_path / "tools"
        _pex_wheel, pex_runtime, _tool_wheels, assets_url = (
            BUILDER.download_packaging_tools(sys.executable, tools)
        )
        assets = Path(assets_url.removeprefix("file://"))
        metadata = next(assets.glob("providers/*/download/*/distributions-*.json"))
        provider_metadata = json.loads(metadata.read_text())
        provider_metadata["base_url"] = server_url
        metadata.write_text(json.dumps(provider_metadata, indent=2) + "\n")

        wheel = tmp_path / "devlegate-0.5.6.dev0-py3-none-any.whl"
        with ZipFile(wheel, "w") as archive:
            archive.writestr("devlegate/__init__.py", "")
            archive.writestr(
                "devlegate/cli.py", "def main():\n    return 0\n"
            )
            archive.writestr(
                "devlegate-0.5.6.dev0.dist-info/METADATA",
                "Metadata-Version: 2.1\nName: devlegate\nVersion: 0.5.6.dev0\n",
            )
            archive.writestr(
                "devlegate-0.5.6.dev0.dist-info/WHEEL",
                "Wheel-Version: 1.0\nGenerator: test\n"
                "Root-Is-Purelib: true\nTag: py3-none-any\n",
            )

        artifact = tmp_path / "devlegate.scie"
        BUILDER.build_scie(
            wheel,
            tmp_path,
            tools,
            pex_runtime,
            sys.executable,
            artifact,
            tools / BUILDER.SCIENCE_ASSET,
            assets_url,
            tmp_path / "build",
            "0",
        )
        assert artifact.is_file()
        assert requests == []
    finally:
        server.shutdown()


def test_scie_command_pins_runtime_inputs(tmp_path):
    wheel = tmp_path / "devlegate.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "devlegate-0.5.6.dev0.dist-info/METADATA", "Version: 0.5.6.dev0\n"
        )
    command = BUILDER.scie_command(
        "python",
        wheel,
        tmp_path,
        tmp_path / "tools",
        tmp_path / "devlegate",
        tmp_path / "science",
    )

    assert ["--scie-pbs-release", "20260901"] == command[
        command.index("--scie-pbs-release") : command.index("--scie-pbs-release") + 2
    ]
    python_pin = command.index("--scie-python-version")
    assert ["--scie-python-version", "3.12.14"] == command[
        python_pin : python_pin + 2
    ]
    assert "--runtime-pex-root" not in command
    assert "--scie-pbs-stripped" in command
    science_pin = command.index("--scie-science-binary")
    assert ["--scie-science-binary", str(tmp_path / "science")] == command[
        science_pin : science_pin + 2
    ]


def test_scie_command_uses_local_standalone_asset_mirror(tmp_path):
    wheel = tmp_path / "devlegate.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "devlegate-0.5.6.dev0.dist-info/METADATA", "Version: 0.5.6.dev0\n"
        )
    command = BUILDER.scie_command(
        "python", wheel, tmp_path, tmp_path / "tools", tmp_path / "devlegate",
        tmp_path / "science", "file:///standalone-assets"
    )
    assets_pin = command.index("--scie-assets-base-url")
    assert command[assets_pin : assets_pin + 2] == [
        "--scie-assets-base-url", "file:///standalone-assets"
    ]


def test_build_environment_uses_isolated_pex_root(tmp_path):
    first = BUILDER.build_environment(tmp_path / "build-a", "123")
    second = BUILDER.build_environment(tmp_path / "build-b", "123")

    assert first["PEX_ROOT"] != second["PEX_ROOT"]
    assert first["PEX_ROOT"] == str(tmp_path / "build-a" / "PEX_ROOT")
    assert first["PYTHONHASHSEED"] == second["PYTHONHASHSEED"] == "0"


def test_scie_builds_share_assets_but_not_pex_roots(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(
        BUILDER, "project_version_from_wheel", lambda _wheel: "0.5.6.dev0"
    )

    def fake_run(command, *, env=None, cwd=None):
        calls.append((command, env))
        artifact = Path(command[command.index("--output-file") + 1])
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_bytes(b"scie")
        artifact.chmod(0o700)
        return subprocess.CompletedProcess(command, 0, stdout="", stderr="")

    monkeypatch.setattr(BUILDER, "run", fake_run)
    common = (
        tmp_path / "wheel",
        tmp_path / "wheel-input",
        tmp_path / "tools",
        tmp_path / "pex-runtime",
        "python",
        "file:///standalone-assets",
        "123",
    )
    BUILDER.build_scie(
        common[0], common[1], common[2], common[3], common[4],
        tmp_path / "build-a" / "scie", tmp_path / "science", common[5],
        tmp_path / "build-a", common[6]
    )
    BUILDER.build_scie(
        common[0], common[1], common[2], common[3], common[4],
        tmp_path / "build-b" / "scie", tmp_path / "science", common[5],
        tmp_path / "build-b", common[6]
    )
    assert calls[0][0][calls[0][0].index("--scie-assets-base-url") + 1] == calls[1][0][
        calls[1][0].index("--scie-assets-base-url") + 1
    ]
    assert calls[0][1]["PEX_ROOT"] != calls[1][1]["PEX_ROOT"]


def test_scie_inspection_rejects_custom_runtime_base(tmp_path):
    inspection = {
        "scie": {
            "lift": {
                "base": str(tmp_path),
                "files": [
                    {"name": BUILDER.PBS_ARCHIVE, "hash": BUILDER.PBS_SHA256}
                ],
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="custom runtime base"):
        BUILDER.validate_scie_inspection(inspection, forbidden_build_root=tmp_path)


def test_scie_inspection_rejects_build_root_in_raw_metadata(tmp_path):
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {
                        "name": BUILDER.PBS_ARCHIVE,
                        "hash": BUILDER.PBS_SHA256,
                        "path": str(tmp_path),
                    }
                ]
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="temporary build root"):
        BUILDER.validate_scie_inspection(inspection, forbidden_build_root=tmp_path)


def test_scie_inspection_rejects_common_outer_build_root(tmp_path):
    outer = tmp_path / "outer"
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {
                        "name": BUILDER.PBS_ARCHIVE,
                        "hash": BUILDER.PBS_SHA256,
                        "path": str(outer / "build-a"),
                    }
                ]
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="temporary build root"):
        BUILDER.validate_scie_inspection(inspection, forbidden_build_root=outer)


def test_split_scie_verifies_gnu_jump_hash(tmp_path, monkeypatch):
    names = {
        BUILDER.SCIE_JUMP_SPLIT_NAME,
        "pex",
        BUILDER.PBS_ARCHIVE,
        "configure-binding.py",
        "lift.json",
    }
    for name in names:
        (tmp_path / name).write_bytes(b"payload")

    def fake_run(command, *, env=None, cwd=None):
        stdout = BUILDER.SCIE_JUMP_VERSION if command[-1] == "--version" else ""
        return subprocess.CompletedProcess(command, 0, stdout=stdout, stderr="")

    original_sha256 = BUILDER.sha256

    def fake_sha256(path):
        if path.name == BUILDER.SCIE_JUMP_SPLIT_NAME:
            return BUILDER.SCIE_JUMP_SHA256
        if path.name == BUILDER.PBS_ARCHIVE:
            return BUILDER.PBS_SHA256
        return original_sha256(path)

    monkeypatch.setattr(BUILDER, "run", fake_run)
    monkeypatch.setattr(BUILDER, "sha256", fake_sha256)

    split = BUILDER.split_scie(tmp_path / "artifact", tmp_path)

    assert sorted(split) == sorted(names)


def test_scie_inspection_rejects_pbs_hash_mismatch():
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {
                        "name": BUILDER.PBS_ARCHIVE,
                        "hash": "0" * 64,
                    }
                ]
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="PBS archive hash mismatch"):
        BUILDER.validate_scie_inspection(inspection)


def test_scie_inspection_rejects_scie_jump_mismatch():
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {"name": BUILDER.PBS_ARCHIVE, "hash": BUILDER.PBS_SHA256}
                ]
            },
            "jump": {"version": "0.0.0"},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="scie-jump version mismatch"):
        BUILDER.validate_scie_inspection(inspection)


def test_scie_inspection_rejects_ptex():
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {"name": BUILDER.PBS_ARCHIVE, "hash": BUILDER.PBS_SHA256},
                    {"name": "ptex", "hash": "0" * 64},
                ]
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    with pytest.raises(BUILDER.BuildError, match="ptex"):
        BUILDER.validate_scie_inspection(inspection)


def test_scie_inspection_records_observed_pins():
    inspection = {
        "scie": {
            "lift": {
                "files": [
                    {"name": BUILDER.PBS_ARCHIVE, "hash": BUILDER.PBS_SHA256}
                ]
            },
            "jump": {"version": BUILDER.SCIE_JUMP_VERSION},
        }
    }

    observed = BUILDER.validate_scie_inspection(inspection)

    assert observed["pbs_release"] == BUILDER.PBS_RELEASE
    assert observed["pbs_sha256"] == BUILDER.PBS_SHA256
    assert observed["scie_jump_version"] == BUILDER.SCIE_JUMP_VERSION
    assert observed["ptex_runtime_included"] is False


def test_provenance_constants_are_json_serializable():
    assert json.dumps(BUILDER.asdict(BUILDER.INPUTS))
