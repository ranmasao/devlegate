# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

import subprocess
from pathlib import Path


def git(cwd: Path, *arguments: str) -> None:
    subprocess.run(
        ["git", *arguments], cwd=cwd, check=True, capture_output=True, text=True
    )


def project(tmp_path: Path, name: str) -> Path:
    repo = tmp_path / name
    repo.mkdir()
    git(repo, "init", "-b", "main")
    (repo / ".env").write_text("STATE_DIR=" + str(tmp_path / (name + "-state")))
    return repo / ".env"
