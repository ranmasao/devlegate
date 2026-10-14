# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2.
"""Small shared wrapper for the installed Git command line client."""

from __future__ import annotations

import subprocess
from pathlib import Path


def run_git(
    cwd: Path, *args: str, check: bool = True, input_text: str | None = None
) -> subprocess.CompletedProcess[str]:
    """Run Git in ``cwd`` while preserving subprocess result semantics."""
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        input=input_text,
        text=True,
        capture_output=True,
        check=check,
    )
