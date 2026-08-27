"""Pure construction of the implementation worker prompt."""

from dataclasses import dataclass
from enum import Enum


class WorkDirective(Enum):
    FRESH = "fresh"
    RESUME = "resume"
    REWORK = "rework"
    RECOVERY = "recovery"


@dataclass(frozen=True)
class WorkerPromptInput:
    assignment: str
    directive: WorkDirective
    previous_work: str | None = None
    architect_feedback: str | None = None


_CORE_CONTRACT = """You are an implementation worker.

Implement the assigned work in the current repository workspace.
Work only on the assigned task.
Inspect source code and tests as needed.
Preserve unrelated behavior.
Run focused validation as appropriate.
Do not manage workflow state or orchestration."""

_DIRECTIVES = {
    WorkDirective.FRESH: "Implement the assigned work in the current workspace.",
    WorkDirective.RESUME: (
        "Continue the existing implementation in the current workspace. "
        "Preserve useful existing changes and complete the assigned work."
    ),
    WorkDirective.REWORK: (
        "Revise the existing implementation according to the supplied feedback "
        "while preserving correct work already present."
    ),
    WorkDirective.RECOVERY: (
        "Inspect the existing partial implementation and continue safely from "
        "the work already present."
    ),
}


def _content(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("prompt content must be text")
    return value.strip("\r\n")


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
        ("Core Worker Contract", _CORE_CONTRACT),
        ("Work Directive", _DIRECTIVES[prompt_input.directive]),
    ]
    for title, value in (
        ("Relevant Previous Work", prompt_input.previous_work),
        ("Architect Feedback", prompt_input.architect_feedback),
    ):
        if value is not None and _content(value):
            sections.append((title, _content(value)))
    sections.append(("Assigned Work", assignment))
    return "\n\n".join(f"## {title}\n{value}" for title, value in sections) + "\n"
