# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tarfile
import tomllib
import zipfile
from email.parser import Parser
from pathlib import Path

from devlegate import __version__

ROOT = Path(__file__).parents[1]
DEFAULT_TEMPLATE_FILES = {
    "default_templates/.env.example",
    "default_templates/artifacts.toml",
    "default_templates/generated_marker.txt",
    "default_templates/project_context.md",
    "default_templates/prompts/core_contract.txt",
    "default_templates/prompts/fresh.txt",
    "default_templates/prompts/resume.txt",
    "default_templates/prompts/rework.txt",
    "default_templates/skills/architect/SKILL.md.tmpl",
    "default_templates/skills/reviewer/SKILL.md.tmpl",
}
HEADER = (
    "# Copyright (c) 2026 Daniil Romanov\n"
    "# Licensed under the EUPL-1.2.\n"
    "# SPDX-License-Identifier: EUPL-1.2\n"
)


def source_files() -> list[Path]:
    return [
        *sorted(
            path
            for path in (ROOT / "src/devlegate").rglob("*.py")
            if "_vendor/nanoyaml" not in path.as_posix()
        ),
        *sorted((ROOT / "tests").glob("*.py")),
        *sorted((ROOT / "tools").glob("*.py")),
        ROOT / "dev",
        ROOT / "tools/build-full-source",
    ]


def test_devlegate_owned_source_has_exact_eupl_header() -> None:
    for path in source_files():
        text = path.read_text(encoding="utf-8")
        expected = HEADER
        if text.startswith("#!"):
            expected = text.splitlines(keepends=True)[0] + HEADER
        assert text.startswith(expected), path


def test_external_and_copyable_scopes_are_excluded_from_eupl_header_check() -> None:
    vendor = ROOT / "src/devlegate/_vendor/nanoyaml"
    assert not list(vendor.rglob("*.py")) or all(
        not path.read_text(encoding="utf-8").startswith(HEADER)
        for path in vendor.rglob("*.py")
    )
    templates = list((ROOT / "src/devlegate/default_templates").rglob("*"))
    assert templates
    assert all(
        "SPDX-License-Identifier: EUPL-1.2" not in path.read_text(encoding="utf-8")
        for path in templates
        if path.is_file()
    )
    guide = (ROOT / "LICENSING.md").read_text(encoding="utf-8")
    assert "`src/devlegate/default_templates/**`" in guide
    assert "`src/devlegate/_vendor/nanoyaml/**`" in guide
    assert "CC0-1.0" in guide


def test_required_legal_files_and_version_exist() -> None:
    assert (ROOT / "LICENSE").is_file()
    assert (ROOT / "LICENSES/CC0-1.0.txt").is_file()
    assert (ROOT / "LICENSING.md").is_file()
    assert (ROOT / "NOTICE").is_file()
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    assert metadata["project"]["version"] == "0.5.4.dev0"
    assert __version__ == metadata["project"]["version"]
    assert metadata["project"]["license"] == "EUPL-1.2 AND CC0-1.0 AND MIT"
    assert metadata["project"]["license-files"] == [
        "LICENSE",
        "NOTICE",
        "LICENSING.md",
        "LICENSES/*",
        "src/devlegate/_vendor/nanoyaml/LICENSE",
    ]
    assert "license = {text" not in (ROOT / "pyproject.toml").read_text()
    assert tomllib.loads((ROOT / "pyproject.toml").read_text())["build-system"][
        "requires"
    ] == ["setuptools>=77.0.3"]


def test_copyable_static_material_is_not_embedded_in_eupl_source() -> None:
    project_context = (ROOT / "src/devlegate/project_context.py").read_text()
    agent_protocol = (ROOT / "src/devlegate/agent_protocol.py").read_text()
    worker_prompt = (ROOT / "src/devlegate/worker_prompt.py").read_text()
    assert "# Devlegate project context" not in project_context
    assert "GENERATED FILE. DO NOT EDIT DIRECTLY." not in agent_protocol
    assert "You are an implementation worker." not in worker_prompt
    assert "Implement the assigned work in the current workspace." not in worker_prompt

    templates = ROOT / "src/devlegate/default_templates"
    assert (templates / "project_context.md").is_file()
    assert (templates / "generated_marker.txt").is_file()
    assert (templates / "prompts/core_contract.txt").is_file()
    assert (templates / "prompts/fresh.txt").is_file()
    assert (templates / "prompts/resume.txt").is_file()
    assert (templates / "prompts/rework.txt").is_file()


def _build_distribution_artifacts(root: Path) -> tuple[Path, Path]:
    output = root / "artifacts"
    output.mkdir()
    backend = root / "backend"
    backend.mkdir()
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-cache-dir",
            "--target",
            str(backend),
            "setuptools==77.0.3",
            "packaging>=24.2",
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    environment = os.environ.copy()
    environment["PYTHONPATH"] = os.pathsep.join(
        [str(backend), environment.get("PYTHONPATH", "")]
    )
    subprocess.run(
        [
            sys.executable,
            "-m",
            "pip",
            "wheel",
            "--no-build-isolation",
            "--no-deps",
            "--disable-pip-version-check",
            "--wheel-dir",
            str(output),
            str(ROOT),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from setuptools.build_meta import build_sdist; import sys; "
            "build_sdist(sys.argv[1])",
            str(output),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        env=environment,
    )
    shutil.rmtree(ROOT / "src/devlegate.egg-info", ignore_errors=True)
    return next(output.glob("*.whl")), next(output.glob("*.tar.gz"))


def test_built_wheel_and_sdist_carry_complete_license_boundaries(tmp_path) -> None:
    wheel, sdist = _build_distribution_artifacts(tmp_path)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        dist_info = next(name for name in names if name.endswith(".dist-info/METADATA"))
        metadata = Parser().parsestr(archive.read(dist_info).decode("utf-8"))
        expected_license_files = {
            "LICENSE",
            "NOTICE",
            "LICENSING.md",
            "LICENSES/CC0-1.0.txt",
            "src/devlegate/_vendor/nanoyaml/LICENSE",
        }
        assert metadata.get("License-Expression") == (
            "EUPL-1.2 AND CC0-1.0 AND MIT"
        )
        assert set(metadata.get_all("License-File")) == expected_license_files
        assert all(
            f"{dist_info.removesuffix('/METADATA')}/licenses/{path}" in names
            for path in expected_license_files
        )
        assert "devlegate/_vendor/nanoyaml/LICENSE" in names
        assert "MIT License" in archive.read(
            "devlegate/_vendor/nanoyaml/LICENSE"
        ).decode()
        template_names = {
            name.removeprefix("devlegate/")
            for name in names
            if name.startswith("devlegate/default_templates/")
        }
        assert template_names == DEFAULT_TEMPLATE_FILES
        assert not any(
            "SPDX-License-Identifier: EUPL-1.2" in archive.read(name).decode("utf-8")
            for name in names
            if name.startswith("devlegate/default_templates/")
            if not name.endswith("/")
        )

    with tarfile.open(sdist) as archive:
        names = set(archive.getnames())
        root = next(name for name in names if name.endswith("/pyproject.toml")).rsplit(
            "/", 1
        )[0]
        expected = {
            f"{root}/LICENSE",
            f"{root}/NOTICE",
            f"{root}/LICENSING.md",
            f"{root}/LICENSES/CC0-1.0.txt",
            f"{root}/CONTRIBUTING.md",
            f"{root}/DCO",
            f"{root}/src/devlegate/_vendor/nanoyaml/LICENSE",
            f"{root}/src/devlegate/default_templates/project_context.md",
            f"{root}/src/devlegate/default_templates/prompts/core_contract.txt",
        }
        assert expected <= names
        assert {
            member.name.removeprefix(f"{root}/src/devlegate/")
            for member in archive.getmembers()
            if member.isfile()
            and member.name.startswith(f"{root}/src/devlegate/default_templates/")
        } == DEFAULT_TEMPLATE_FILES
        member = archive.extractfile(
            f"{root}/src/devlegate/_vendor/nanoyaml/LICENSE"
        )
        assert member is not None and b"MIT License" in member.read()


def test_licensing_and_contribution_docs_cover_current_scopes() -> None:
    guide = (ROOT / "LICENSING.md").read_text()
    contributing = (ROOT / "CONTRIBUTING.md").read_text()
    dco = (ROOT / "DCO").read_text()
    for text in (guide, contributing):
        assert "EUPL-1.2" in text
        assert "CC0-1.0" in text
        assert "MIT" in text
    assert "30-second overview" in guide
    assert "documented external boundaries" in guide
    assert "Ordinary factual or operational" in guide
    assert "project and runtime output" in guide
    assert "Developer Certificate of Origin" in contributing
    assert "git commit -s" in contributing
    assert "Signed-off-by: Name <email>" in contributing
    assert "no CLA" in contributing
    assert "Developer Certificate of Origin\nVersion 1.1" in dco
