# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]
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
    assert metadata["project"]["version"] == "0.5.0"
    assert metadata["project"]["license"]["text"] == "EUPL-1.2"
