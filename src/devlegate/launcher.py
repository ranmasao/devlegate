# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""The argv identity used to relaunch the installed Devlegate product."""

from __future__ import annotations

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
    return LaunchCommand.current_python()
