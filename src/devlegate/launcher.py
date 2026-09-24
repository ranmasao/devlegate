# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""The argv identity used to relaunch the installed Devlegate product."""

from __future__ import annotations

import os
import sys
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class LaunchCommand:
    """An executable argv prefix that can be extended with product arguments."""

    executable_argv: tuple[str, ...]

    def argv(self, *arguments: str) -> list[str]:
        return [*self.executable_argv, *arguments]

    @classmethod
    def current_python(cls, executable: str | Path | None = None) -> LaunchCommand:
        return cls((str(executable or sys.executable), "-P", "-m", "devlegate"))

    @classmethod
    def executable(cls, executable: str | Path) -> LaunchCommand:
        return cls((str(executable),))


def product_launcher() -> LaunchCommand:
    """Return the launcher for the currently installed product form."""
    pex = os.environ.get("PEX")
    scie = os.environ.get("SCIE")
    if pex and scie and pex == scie:
        candidate = Path(pex)
        try:
            if not candidate.is_file() or candidate.stat().st_mode & 0o111 == 0:
                is_executable_elf = False
            else:
                with candidate.open("rb") as executable:
                    is_executable_elf = executable.read(4) == b"\x7fELF"
        except OSError:
            is_executable_elf = False
        if is_executable_elf:
            return LaunchCommand.executable(candidate)
    return LaunchCommand.current_python()
