# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Durable evidence for process-level lifecycle requests."""

from __future__ import annotations

import json
import os
import tempfile
from hashlib import sha256
from pathlib import Path

from devlegate.runtime_locator import RuntimeLocator


def path_for(locator: RuntimeLocator) -> Path:
    return locator.state_dir / "lifecycle" / f"{locator.state_key}.json"


def _exact_path(locator: RuntimeLocator, instance_id: str, request_id: str) -> Path:
    key = sha256(json.dumps([instance_id, request_id]).encode()).hexdigest()
    return path_for(locator).with_name(f"{locator.state_key}.{key}.json")


def write(locator: RuntimeLocator, payload: dict[str, object]) -> None:
    instance_id, request_id = payload["instance_id"], payload["request_id"]
    if not isinstance(instance_id, str) or not isinstance(request_id, str):
        raise ValueError("lifecycle outcome has no exact identity")
    exact = _exact_path(locator, instance_id, request_id)
    prior = _read(exact)
    # Writers are serialized by the same authority fd, including across exec.
    # Only the old owner's restart handoff can advance to a new outcome.
    if prior is not None and prior.get("state") != "handoff":
        return
    _write(exact, payload)
    _write(path_for(locator), payload)


def _write(target: Path, payload: dict[str, object]) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{target.name}.", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, target)
        dir_fd = os.open(target.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass


def read(
    locator: RuntimeLocator, *, instance_id: str | None = None,
    request_id: str | None = None,
) -> dict[str, object] | None:
    if instance_id is not None and request_id is not None:
        return _read(_exact_path(locator, instance_id, request_id))
    return _read(path_for(locator))


def _read(path: Path) -> dict[str, object] | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None
