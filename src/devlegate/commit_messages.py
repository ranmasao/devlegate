# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Human-facing Git messages for Devlegate-owned commits."""

from __future__ import annotations

import re


def _summary(summary: str) -> str:
    """Keep worker output concise without copying questions or raw output."""
    value = re.sub(r"\s+", " ", summary).strip()
    return value[:240] if value else "Worker reported no summary."


def checkpoint_message(
    ticket_id: str, title: str, execution_id: str, summary: str
) -> str:
    return (
        f"{ticket_id}: {title}\n\n"
        "Worker result:\n"
        f"{_summary(summary)}\n\n"
        f"Devlegate-Execution: {execution_id}"
    )


def lifecycle_message(
    ticket_id: str,
    title: str,
    execution_id: str,
    conclusion: str,
    summary: str,
) -> str:
    return (
        f"{ticket_id}: {title} ({conclusion})\n\n"
        "Worker result:\n"
        f"{_summary(summary)}\n\n"
        f"Devlegate-Execution: {execution_id}"
    )


__all__ = ["checkpoint_message", "lifecycle_message"]
