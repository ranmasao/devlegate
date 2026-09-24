# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import importlib.util
import json
import sys
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


def test_builder_python_is_not_coupled_to_bundled_minor():
    BUILDER.validate_builder_version("PyPy", 3, 13)


def test_verified_artifact_rejects_wrong_hash(tmp_path):
    artifact = tmp_path / "artifact"
    artifact.write_bytes(b"actual")

    with pytest.raises(BUILDER.BuildError, match="sha256 mismatch"):
        BUILDER.verify_file(artifact, "0" * 64)


def test_scie_command_pins_runtime_inputs(tmp_path):
    wheel = tmp_path / "devlegate.whl"
    with ZipFile(wheel, "w") as archive:
        archive.writestr(
            "devlegate-0.5.4.dev0.dist-info/METADATA", "Version: 0.5.4.dev0\n"
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
    science_pin = command.index("--scie-science-binary")
    assert ["--scie-science-binary", str(tmp_path / "science")] == command[
        science_pin : science_pin + 2
    ]


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
