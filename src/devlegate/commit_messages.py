# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Human-facing Git messages for Devlegate-owned commits."""

from __future__ import annotations

import re
import textwrap


def _summary(summary: str) -> str:
    """Normalize the worker's already-semantic summary for Git display."""
    value = re.sub(r"\s+", " ", summary).strip()
    if not value:
        return "Worker reported no summary."
    return "\n".join(
        textwrap.wrap(value, width=72, break_long_words=False, break_on_hyphens=False)
    )


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


def integration_message(
    ticket_id: str,
    title: str,
    execution_id: str,
    summary: str,
    checkpoint: str,
) -> str:
    return (
        f"{ticket_id}: {title} (integrated)\n\n"
        "Worker result:\n"
        f"{_summary(summary)}\n\n"
        f"Devlegate-Execution: {execution_id}\n"
        f"Devlegate-Checkpoint: {checkpoint}"
    )


__all__ = ["checkpoint_message", "integration_message", "lifecycle_message"]
