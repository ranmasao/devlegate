# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Durable evidence for process-level lifecycle requests."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

from devlegate.runtime_locator import RuntimeLocator


def path_for(locator: RuntimeLocator) -> Path:
    return locator.state_dir / "lifecycle" / f"{locator.state_key}.json"


def write(locator: RuntimeLocator, payload: dict[str, object]) -> None:
    target = path_for(locator)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read(locator: RuntimeLocator) -> dict[str, object] | None:
    try:
        value = json.loads(path_for(locator).read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
