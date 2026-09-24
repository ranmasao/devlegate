# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from __future__ import annotations

import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).parents[1]


def git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)


def test_bundled_parser_is_private_and_not_top_level() -> None:
    assert not (ROOT / "src/nanoyaml").exists()
    assert (ROOT / "src/devlegate/_vendor/nanoyaml/__init__.py").is_file()

    result = subprocess.run(
        [
            os.fspath(ROOT / ".venv/bin/python"),
            "-c",
            "import sys; import devlegate.tickets; "
            "assert 'nanoyaml' not in sys.modules; "
            "assert 'devlegate._vendor.nanoyaml' in sys.modules",
        ],
        cwd=ROOT,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr


def test_console_check_ignores_hostile_consumer_nanoyaml(git_fixture) -> None:
    working = git_fixture["working"]
    control = git_fixture["control"]
    hostile = working / "nanoyaml/__init__.py"
    hostile.parent.mkdir()
    hostile.write_text(
        'raise RuntimeError("consumer nanoyaml must never be imported")\n'
    )
    git(working, "add", "nanoyaml")
    git(working, "commit", "-m", "add hostile consumer package")
    git(working, "push", "origin", "HEAD:main")
    git(working, "fetch", "origin", "main")

    (control / "kanban/done/ED-10.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Dependency"\n---\n'
        "completed\n"
    )
    (control / "kanban/todo/DRAFT-001.md").write_text(
        "---\n"
        '"type": "devlegate.ticket"\n'
        '"title": "Flow dependency"\n'
        '"depends_on": ["ED-10"]\n'
        "---\n"
        "ticket body\n"
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "add flow dependency fixture")
    git(control, "push", "origin", "HEAD:devlegate/control")

    result = subprocess.run(
        [
            os.fspath(ROOT / ".venv/bin/devlegate"),
            "--env",
            os.fspath(git_fixture["config"]),
            "check",
        ],
        cwd=working,
        env={key: value for key, value in os.environ.items() if key != "PYTHONPATH"},
        text=True,
        capture_output=True,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Ready." in result.stdout
