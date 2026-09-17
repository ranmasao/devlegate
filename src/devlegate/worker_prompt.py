# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure construction of the implementation worker prompt."""

import re
from dataclasses import dataclass
from enum import Enum


class WorkDirective(Enum):
    FRESH = "fresh"
    RESUME = "resume"
    REWORK = "rework"


@dataclass(frozen=True)
class WorkerPromptInput:
    assignment: str
    directive: WorkDirective


_CORE_CONTRACT = """You are an implementation worker.

Implement the assigned work in the current repository workspace.
Work only on the assigned task.
Inspect source code and tests as needed.
Preserve unrelated behavior.
Run focused validation as appropriate.
The worker owns implementation edits and the semantic claim only. Do not commit,
push, merge, rebase, checkout, switch branches, create or delete branches,
modify workflow or ticket state, write ExecutionReport artifacts, or integrate
implementation changes into a product branch. Devlegate owns Git lifecycle,
execution reports, ticket movement, and product integration.

Before finishing, call the devlegate_report tool exactly once. Report one of
these worker claims: completed when you believe the assigned work is complete,
incomplete when useful work remains, or blocked when safe progress needs
external information or action."""

_DIRECTIVES = {
    WorkDirective.FRESH: "Implement the assigned work in the current workspace.",
    WorkDirective.RESUME: (
        "Continue the existing implementation in the current workspace. "
        "Inspect the existing workspace and current changes before editing. "
        "Preserve useful existing changes and do not discard valid partial work. "
        "Continue the same assigned work."
    ),
    WorkDirective.REWORK: (
        "Revise the existing implementation according to the supplied feedback "
        "while preserving correct work already present."
    ),
}


def _content(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("prompt content must be text")
    return value.strip("\r\n")


def _fenced_content(value: str) -> str:
    """Keep opaque content from being interpreted as prompt structure."""
    longest = max((len(run) for run in re.findall(r"~+", value)), default=0)
    fence = "~" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}"


def build_worker_prompt(prompt_input: WorkerPromptInput) -> str:
    """Build one deterministic prompt without observing workflow or Git state."""
    if not isinstance(prompt_input, WorkerPromptInput):
        raise TypeError("prompt input must be WorkerPromptInput")
    if not isinstance(prompt_input.directive, WorkDirective):
        raise ValueError("unknown work directive")
    assignment = _content(prompt_input.assignment)
    if not assignment:
        raise ValueError("assigned work must not be empty")

    sections = [
        ("Core Worker Contract", _CORE_CONTRACT, False),
        ("Work Directive", _DIRECTIVES[prompt_input.directive], False),
    ]
    sections.append(("Assigned Work", assignment, True))
    return "\n\n".join(
        f"## {title}\n{_fenced_content(value) if untrusted else value}"
        for title, value, untrusted in sections
    ) + "\n"
