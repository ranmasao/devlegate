# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure construction of the implementation worker prompt."""

import re
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class WorkDirective(Enum):
    FRESH = "fresh"
    RESUME = "resume"
    REWORK = "rework"


@dataclass(frozen=True)
class WorkerPromptInput:
    assignment: str
    directive: WorkDirective


_PROMPT_ROOT = Path(__file__).parent / "default_templates/prompts"


def _prompt_text(name: str) -> str:
    path = _PROMPT_ROOT / name
    if not path.is_file():
        raise RuntimeError(f"packaged worker prompt is missing: {path}")
    return path.read_text().rstrip("\r\n")


_CORE_CONTRACT = _prompt_text("core_contract.txt")
_DIRECTIVES = {
    WorkDirective.FRESH: _prompt_text("fresh.txt"),
    WorkDirective.RESUME: _prompt_text("resume.txt"),
    WorkDirective.REWORK: _prompt_text("rework.txt"),
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
