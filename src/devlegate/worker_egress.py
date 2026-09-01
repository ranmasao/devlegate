"""Typed, untrusted semantic egress from one worker process."""

from dataclasses import dataclass
from typing import Any


class WorkerProtocolError(ValueError):
    """Raised when the reserved worker reporting protocol is violated."""


@dataclass(frozen=True)
class WorkerClaim:
    outcome: str
    summary: str
    remaining: tuple[str, ...]
    questions: tuple[str, ...]


@dataclass(frozen=True)
class OpenCodeRunResult:
    process_returncode: int
    transport_error: str | None = None

    @property
    def transport_ok(self) -> bool:
        return self.transport_error is None


@dataclass(frozen=True)
class WorkerRunResult:
    process_returncode: int
    transport_error: str | None
    claim: WorkerClaim | None
    egress_error: str | None

    @property
    def transport_ok(self) -> bool:
        return self.transport_error is None

    @property
    def egress_ok(self) -> bool:
        return self.egress_error is None and self.claim is not None


_OUTCOMES = {"completed", "incomplete", "blocked"}
_CLAIM_FIELDS = {"outcome", "summary", "remaining", "questions"}


def _claim(arguments: Any) -> WorkerClaim:
    if not isinstance(arguments, dict) or set(arguments) != _CLAIM_FIELDS:
        raise WorkerProtocolError("devlegate_report arguments must have exact fields")
    outcome = arguments["outcome"]
    summary = arguments["summary"]
    remaining = arguments["remaining"]
    questions = arguments["questions"]
    if not isinstance(outcome, str) or outcome not in _OUTCOMES:
        raise WorkerProtocolError("devlegate_report outcome is invalid")
    if not isinstance(summary, str):
        raise WorkerProtocolError("devlegate_report summary must be a string")
    if not isinstance(remaining, list) or not all(
        isinstance(item, str) for item in remaining
    ):
        raise WorkerProtocolError("devlegate_report remaining must be list[string]")
    if not isinstance(questions, list) or not all(
        isinstance(item, str) for item in questions
    ):
        raise WorkerProtocolError("devlegate_report questions must be list[string]")
    return WorkerClaim(outcome, summary, tuple(remaining), tuple(questions))


class WorkerEgressParser:
    """Consume raw decoded events and accept exactly one reserved claim."""

    def __init__(self) -> None:
        self.claim: WorkerClaim | None = None
        self.error: str | None = None

    def consume(self, event: object) -> None:
        if self.error is not None:
            return
        if not isinstance(event, dict) or event.get("type") != "tool_use":
            return
        part = event.get("part")
        if not isinstance(part, dict) or part.get("tool") != "devlegate_report":
            return
        if part.get("type") != "tool":
            self.error = "devlegate_report event has invalid tool type"
            return
        state = part.get("state")
        if not isinstance(state, dict) or state.get("status") != "completed":
            self.error = "devlegate_report event is not completed"
            return
        if self.claim is not None:
            self.error = "duplicate devlegate_report event"
            return
        try:
            self.claim = _claim(state.get("input"))
        except WorkerProtocolError as error:
            self.error = str(error)

    def finish(self) -> tuple[WorkerClaim | None, str | None]:
        if self.error is not None:
            return None, self.error
        if self.claim is None:
            return None, "exactly one devlegate_report event is required"
        return self.claim, None
