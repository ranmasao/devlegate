# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Persistence for the latest observed terminal service failure."""

from __future__ import annotations

import dataclasses
import datetime
import json
import os
import tempfile
from pathlib import Path

from devlegate.runtime_locator import RuntimeLocator

SCHEMA = "devlegate.service-failure.v1"


@dataclasses.dataclass(frozen=True)
class ServiceFailureDiagnostic:
    stage: str
    exception_type: str
    message: str
    occurred_at: str
    pid: int

    def as_dict(self) -> dict[str, object]:
        return {
            "schema": SCHEMA,
            "stage": self.stage,
            "exception_type": self.exception_type,
            "message": self.message,
            "occurred_at": self.occurred_at,
            "pid": self.pid,
        }

    def as_public_dict(self) -> dict[str, str]:
        return {
            "stage": self.stage,
            "exception_type": self.exception_type,
            "message": self.message,
            "occurred_at": self.occurred_at,
        }


def path_for(locator: RuntimeLocator) -> Path:
    return locator.state_dir / "diagnostics" / f"{locator.state_key}.json"


def create(stage: str, error: BaseException) -> ServiceFailureDiagnostic:
    return ServiceFailureDiagnostic(
        stage=stage,
        exception_type=type(error).__name__,
        message=str(error) or type(error).__name__,
        occurred_at=datetime.datetime.now(datetime.UTC).isoformat(),
        pid=os.getpid(),
    )


def write(locator: RuntimeLocator, diagnostic: ServiceFailureDiagnostic) -> None:
    target = path_for(locator)
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(diagnostic.as_dict(), sort_keys=True).encode("utf-8")
    descriptor, temporary_name = tempfile.mkstemp(
        prefix=f".{target.name}.", suffix=".tmp", dir=target.parent
    )
    try:
        with os.fdopen(descriptor, "wb") as temporary:
            descriptor = -1
            temporary.write(payload)
            temporary.write(b"\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, target)
    finally:
        if descriptor != -1:
            os.close(descriptor)
        try:
            os.unlink(temporary_name)
        except FileNotFoundError:
            pass


def clear(locator: RuntimeLocator) -> None:
    try:
        path_for(locator).unlink()
    except FileNotFoundError:
        pass


def read(locator: RuntimeLocator) -> tuple[ServiceFailureDiagnostic | None, bool]:
    target = path_for(locator)
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None, False
    except (OSError, UnicodeError, json.JSONDecodeError):
        return None, True
    if not isinstance(raw, dict):
        return None, True
    expected = {
        "schema": str,
        "stage": str,
        "exception_type": str,
        "message": str,
        "occurred_at": str,
        "pid": int,
    }
    if set(raw) != set(expected) or any(
        not isinstance(raw[key], value_type) or not raw[key]
        for key, value_type in expected.items()
    ):
        return None, True
    if raw["schema"] != SCHEMA or raw["stage"] not in {"startup", "runtime"}:
        return None, True
    if isinstance(raw["pid"], bool) or raw["pid"] <= 0:
        return None, True
    return (
        ServiceFailureDiagnostic(
            stage=raw["stage"],
            exception_type=raw["exception_type"],
            message=raw["message"],
            occurred_at=raw["occurred_at"],
            pid=raw["pid"],
        ),
        False,
    )
