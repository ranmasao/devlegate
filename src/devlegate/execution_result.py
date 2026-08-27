"""Devlegate-owned interpretation and durable reports for worker executions."""

from __future__ import annotations

import json
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path

from devlegate.worker_egress import WorkerClaim, WorkerRunResult


class ExecutionReportError(ValueError):
    """Raised when an execution result or report is invalid."""


_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_CONCLUSIONS = {"completed", "incomplete", "blocked", "failed"}
_REPORT_SCHEMA = "devlegate.execution-report.v1"


@dataclass(frozen=True)
class ExecutionResult:
    process_returncode: int
    transport_error: str | None
    egress_error: str | None
    claim: WorkerClaim | None
    conclusion: str
    questions: tuple[str, ...]
    remaining: tuple[str, ...]
    reason: str

    @property
    def egress_ok(self) -> bool:
        return self.egress_error is None and self.claim is not None


@dataclass(frozen=True)
class ExecutionReport:
    execution_id: str
    ticket_id: str
    code_base_head: str
    control_head: str
    execution_branch: str
    execution_path: str
    workspace_head: str | None
    result: ExecutionResult

    @property
    def schema(self) -> str:
        return _REPORT_SCHEMA

    def as_dict(self) -> dict[str, object]:
        claim = self.result.claim
        return {
            "schema": self.schema,
            "execution_id": self.execution_id,
            "ticket_id": self.ticket_id,
            "code_base_head": self.code_base_head,
            "control_head": self.control_head,
            "execution_branch": self.execution_branch,
            "execution_path": self.execution_path,
            "workspace_head": self.workspace_head,
            "process_returncode": self.result.process_returncode,
            "transport_error": self.result.transport_error,
            "transport_ok": self.result.transport_error is None,
            "egress_error": self.result.egress_error,
            "egress_ok": self.result.egress_ok,
            "worker_claim": (
                {
                    "outcome": claim.outcome,
                    "summary": claim.summary,
                    "remaining": list(claim.remaining),
                    "questions": list(claim.questions),
                }
                if claim is not None
                else None
            ),
            "conclusion": self.result.conclusion,
            "questions": list(self.result.questions),
            "remaining": list(self.result.remaining),
            "reason": self.result.reason,
        }

    def to_json(self) -> str:
        return json.dumps(self.as_dict(), indent=2, sort_keys=True) + "\n"

    @classmethod
    def from_dict(cls, payload: object) -> "ExecutionReport":
        if not isinstance(payload, dict):
            raise ExecutionReportError("execution report must be an object")
        expected = {
            "schema",
            "execution_id",
            "ticket_id",
            "code_base_head",
            "control_head",
            "execution_branch",
            "execution_path",
            "workspace_head",
            "process_returncode",
            "transport_error",
            "transport_ok",
            "egress_error",
            "egress_ok",
            "worker_claim",
            "conclusion",
            "questions",
            "remaining",
            "reason",
        }
        if set(payload) != expected:
            raise ExecutionReportError("execution report fields are invalid")
        if payload["schema"] != _REPORT_SCHEMA:
            raise ExecutionReportError("unsupported execution report schema")
        strings = (
            "execution_id",
            "ticket_id",
            "code_base_head",
            "control_head",
            "execution_branch",
            "execution_path",
            "reason",
        )
        if not all(isinstance(payload[field], str) for field in strings):
            raise ExecutionReportError("execution report identity fields are invalid")
        for field in ("execution_id", "ticket_id"):
            if not _IDENTIFIER.fullmatch(payload[field]):
                raise ExecutionReportError(f"invalid execution report {field}")
        if payload["workspace_head"] is not None and not isinstance(
            payload["workspace_head"], str
        ):
            raise ExecutionReportError("execution report workspace head is invalid")
        if not isinstance(payload["process_returncode"], int) or isinstance(
            payload["process_returncode"], bool
        ):
            raise ExecutionReportError("execution report process status is invalid")
        if not isinstance(payload["transport_error"], (str, type(None))):
            raise ExecutionReportError("execution report transport error is invalid")
        if not isinstance(payload["egress_error"], (str, type(None))):
            raise ExecutionReportError("execution report egress error is invalid")
        if not isinstance(payload["transport_ok"], bool) or payload["transport_ok"] != (
            payload["transport_error"] is None
        ):
            raise ExecutionReportError(
                "execution report transport status is inconsistent"
            )
        claim_payload = payload["worker_claim"]
        claim = _parse_claim(claim_payload) if claim_payload is not None else None
        if not isinstance(payload["egress_ok"], bool) or payload["egress_ok"] != (
            claim is not None and payload["egress_error"] is None
        ):
            raise ExecutionReportError("execution report egress status is inconsistent")
        conclusion = payload["conclusion"]
        if not isinstance(conclusion, str) or conclusion not in _CONCLUSIONS:
            raise ExecutionReportError("execution report conclusion is invalid")
        questions = _strings(payload["questions"], "questions")
        remaining = _strings(payload["remaining"], "remaining")
        if claim is None and (questions or remaining):
            raise ExecutionReportError(
                "execution report has semantic data without claim"
            )
        if claim is not None and (
            questions != claim.questions or remaining != claim.remaining
        ):
            raise ExecutionReportError("execution report claim data is inconsistent")
        expected_conclusion = (
            claim.outcome
            if claim is not None
            and payload["process_returncode"] == 0
            and payload["transport_error"] is None
            and payload["egress_error"] is None
            else "failed"
        )
        if conclusion != expected_conclusion:
            raise ExecutionReportError("execution report conclusion is inconsistent")
        result = ExecutionResult(
            payload["process_returncode"],
            payload["transport_error"],
            payload["egress_error"],
            claim,
            conclusion,
            questions,
            remaining,
            payload["reason"],
        )
        return cls(
            payload["execution_id"],
            payload["ticket_id"],
            payload["code_base_head"],
            payload["control_head"],
            payload["execution_branch"],
            payload["execution_path"],
            payload["workspace_head"],
            result,
        )


def _strings(value: object, field: str) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        raise ExecutionReportError(f"execution report {field} is invalid")
    return tuple(value)


def _parse_claim(value: object) -> WorkerClaim:
    if not isinstance(value, dict) or set(value) != {
        "outcome",
        "summary",
        "remaining",
        "questions",
    }:
        raise ExecutionReportError("execution report worker claim is invalid")
    if not isinstance(value["outcome"], str) or value["outcome"] not in {
        "completed",
        "incomplete",
        "blocked",
    }:
        raise ExecutionReportError("execution report worker claim outcome is invalid")
    if not isinstance(value["summary"], str):
        raise ExecutionReportError("execution report worker claim summary is invalid")
    return WorkerClaim(
        value["outcome"],
        value["summary"],
        _strings(value["remaining"], "worker claim remaining"),
        _strings(value["questions"], "worker claim questions"),
    )


def build_execution_result(run: WorkerRunResult) -> ExecutionResult:
    """Interpret one worker observation without treating claims as truth."""
    if not isinstance(run, WorkerRunResult):
        raise TypeError("execution input must be WorkerRunResult")
    claim = run.claim
    questions = claim.questions if claim is not None else ()
    remaining = claim.remaining if claim is not None else ()
    if run.process_returncode != 0:
        conclusion = "failed"
        reason = f"worker exited with status {run.process_returncode}"
    elif run.transport_error is not None:
        conclusion = "failed"
        reason = run.transport_error
    elif run.egress_error is not None or claim is None:
        conclusion = "failed"
        reason = run.egress_error or "worker claim is unavailable"
    else:
        conclusion = claim.outcome
        reason = "valid worker claim"
    return ExecutionResult(
        run.process_returncode,
        run.transport_error,
        run.egress_error,
        claim,
        conclusion,
        questions,
        remaining,
        reason,
    )


def build_execution_report(
    *,
    execution_id: str,
    ticket_id: str,
    code_base_head: str,
    control_head: str,
    execution_branch: str,
    execution_path: str,
    workspace_head: str | None,
    run: WorkerRunResult,
) -> ExecutionReport:
    """Build a report from Devlegate-owned identity and worker observations."""
    return ExecutionReport(
        execution_id,
        ticket_id,
        code_base_head,
        control_head,
        execution_branch,
        execution_path,
        workspace_head,
        build_execution_result(run),
    )


def new_execution_id() -> str:
    """Generate an opaque Devlegate-owned identifier for one attempt."""
    return uuid.uuid4().hex


class ExecutionReportStore:
    """Persist reports below one Devlegate-owned control-plane directory."""

    def __init__(self, control_worktree: Path) -> None:
        self.root = control_worktree / "executions"

    def _path(self, ticket_id: str, execution_id: str) -> Path:
        if not _IDENTIFIER.fullmatch(ticket_id) or not _IDENTIFIER.fullmatch(
            execution_id
        ):
            raise ExecutionReportError("invalid execution report identity")
        return self.root / ticket_id / f"{execution_id}.json"

    def write(self, report: ExecutionReport) -> Path:
        if not isinstance(report, ExecutionReport):
            raise TypeError("execution report must be ExecutionReport")
        ExecutionReport.from_dict(report.as_dict())
        path = self._path(report.ticket_id, report.execution_id)
        temporary_name = None
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            if self.root.is_symlink():
                raise ExecutionReportError("execution report root is a symlink")
            if path.parent.exists() and path.parent.is_symlink():
                raise ExecutionReportError(
                    "execution report ticket directory is a symlink"
                )
            path.parent.mkdir(parents=True, exist_ok=True)
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=path.parent,
                prefix=f".{report.execution_id}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                temporary.write(report.to_json())
                temporary.flush()
                os.fsync(temporary.fileno())
            os.link(temporary_name, path)
            directory_fd = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
            return path
        except FileExistsError as error:
            raise ExecutionReportError("execution report already exists") from error
        except OSError as error:
            raise ExecutionReportError(
                f"cannot persist execution report: {error}"
            ) from error
        finally:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass

    def read(self, ticket_id: str, execution_id: str) -> ExecutionReport:
        path = self._path(ticket_id, execution_id)
        if path.is_symlink() or path.parent.is_symlink() or self.root.is_symlink():
            raise ExecutionReportError("execution report path is a symlink")
        try:
            payload = json.loads(path.read_text())
        except (OSError, json.JSONDecodeError) as error:
            raise ExecutionReportError(
                f"cannot read execution report: {path}"
            ) from error
        report = ExecutionReport.from_dict(payload)
        if (report.ticket_id, report.execution_id) != (ticket_id, execution_id):
            raise ExecutionReportError("execution report identity does not match path")
        return report
