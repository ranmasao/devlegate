"""Bounded read-only Unix IPC client for the foreground CLI."""

from __future__ import annotations

import socket
import uuid
from pathlib import Path

from devlegate.ipc_protocol import (
    IPCProtocolError,
    encode_request,
    parse_response,
    receive_frame,
    send_frame,
)
from devlegate.runtime import (
    ExecutionPlan,
    FailedExecution,
    GitObservation,
    StatusSnapshot,
)


class IPCClientError(Exception):
    """A transport, protocol, or daemon application response error."""

    def __init__(self, message: str, *, application: bool = False) -> None:
        super().__init__(message)
        self.application = application


def request(
    socket_path: Path, method: str, *, timeout: float = 2.0
) -> dict[str, object]:
    """Issue one protocol-v1 read-only request and return its structured result."""
    request_id = uuid.uuid4().hex
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    try:
        try:
            connection.connect(str(socket_path))
            stream = connection.makefile("rwb")
            try:
                send_frame(stream, encode_request(request_id, method, {}))
                payload = receive_frame(stream)
            finally:
                stream.close()
        except (OSError, IPCProtocolError) as error:
            raise IPCClientError(f"daemon IPC unavailable: {error}") from error
    finally:
        connection.close()
    if payload is None:
        raise IPCClientError("daemon IPC returned no response")
    try:
        response = parse_response(payload)
    except IPCProtocolError as error:
        raise IPCClientError(f"daemon IPC protocol error: {error}") from error
    if response.request_id != request_id:
        raise IPCClientError("daemon IPC response id does not match request")
    if not response.ok:
        error = response.error or {}
        raise IPCClientError(
            f"daemon error: {error.get('message', 'unknown application error')}",
            application=True,
        )
    if response.result is None:
        raise IPCClientError("daemon IPC response has no result")
    return response.result


def decode_plan(value: dict[str, object]) -> ExecutionPlan:
    try:
        ticket = _optional_mapping(value["ticket"], "plan ticket")
        observation = _mapping(value["observation"], "plan observation")
        return ExecutionPlan(
            action=_text(value["action"], "plan action"),
            reason=_text(value["reason"], "plan reason"),
            ticket_id=_optional_text(ticket.get("id"), "ticket id")
            if ticket is not None
            else None,
            ticket_title=_optional_text(ticket.get("title"), "ticket title")
            if ticket is not None
            else None,
            ticket_state=_optional_text(ticket.get("state"), "ticket state")
            if ticket is not None
            else None,
            bound=_bool(value["bound"], "plan bound"),
            code=_optional_observation(observation.get("code"), "plan code"),
            control=_optional_observation(observation.get("control"), "plan control"),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IPCClientError(f"daemon IPC returned invalid plan: {error}") from error


def decode_status(value: dict[str, object]) -> StatusSnapshot:
    try:
        execution = _mapping(value["execution"], "execution")
        observation = _mapping(value["observation"], "observation")
        tickets = _mapping(value["tickets"], "tickets")
        return StatusSnapshot(
            phase=_text(execution["phase"], "execution phase"),
            bound_ticket_id=_optional_text(
                execution["bound_ticket"], "bound ticket"
            ),
            persisted_body_present=_bool(
                execution["persisted_body"], "persisted body"
            ),
            code=_observation(observation["code"], "code observation"),
            control=_optional_observation(
                observation.get("control"), "control observation"
            ),
            counts=_counts(tickets["counts"]),
            runnable=_ticket_pairs(tickets["runnable"], "runnable"),
            blocked=_blocked(tickets["blocked"]),
            review=_ticket_pairs(tickets["review"], "review"),
            accepted=_ticket_pairs(tickets["accepted"], "accepted"),
            next_ticket=_optional_pair(tickets["next"], "next ticket"),
            plan=decode_plan(value["plan"] if isinstance(value["plan"], dict) else {}),
            failed_executions=_failures(value["failed_executions"]),
            reconciliation=_optional_dict(
                value.get("reconciliation"), "reconciliation"
            ),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise IPCClientError(f"daemon IPC returned invalid status: {error}") from error


def _mapping(value: object, label: str) -> dict[str, object]:
    if not isinstance(value, dict):
        raise ValueError(f"{label} must be an object")
    return value


def _optional_mapping(value: object, label: str) -> dict[str, object] | None:
    if value is None:
        return None
    return _mapping(value, label)


def _text(value: object, label: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"{label} must be text")
    return value


def _optional_text(value: object, label: str) -> str | None:
    if value is None:
        return None
    return _text(value, label)


def _bool(value: object, label: str) -> bool:
    if not isinstance(value, bool):
        raise ValueError(f"{label} must be boolean")
    return value


def _observation(value: object, label: str) -> GitObservation:
    mapping = _mapping(value, label)
    return GitObservation(
        branch=_optional_text(mapping["branch"], f"{label} branch"),
        detached=_bool(mapping["detached"], f"{label} detached"),
        local_head=_text(mapping["local_head"], f"{label} local head"),
        remote_ref=_text(mapping["remote_ref"], f"{label} remote ref"),
        remote_head=_optional_text(mapping["remote_head"], f"{label} remote head"),
        working_tree_clean=_bool(
            mapping["working_tree_clean"], f"{label} working tree"
        ),
        working_tree_fingerprint=_text(
            mapping["working_tree_fingerprint"], f"{label} fingerprint"
        ),
    )


def _optional_observation(value: object, label: str) -> GitObservation | None:
    if value is None:
        return None
    return _observation(value, label)


def _counts(value: object) -> tuple[tuple[str, int], ...]:
    mapping = _mapping(value, "ticket counts")
    expected = ("backlog", "todo", "review", "accepted", "done")
    if set(mapping) != set(expected):
        raise ValueError("ticket counts must contain exactly the expected states")
    counts = []
    for key in expected:
        number = mapping[key]
        if (
            not isinstance(number, int)
            or isinstance(number, bool)
        ):
            raise ValueError("ticket counts must contain integer values")
        counts.append((key, number))
    return tuple(counts)


def _ticket_pairs(value: object, label: str) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError(f"{label} must be a list")
    pairs = []
    for item in value:
        mapping = _mapping(item, label)
        pairs.append(
            (
                _text(mapping["id"], f"{label} id"),
                _text(mapping["title"], f"{label} title"),
            )
        )
    return tuple(pairs)


def _blocked(value: object) -> tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]:
    if not isinstance(value, list):
        raise ValueError("blocked must be a list")
    entries = []
    for item in value:
        mapping = _mapping(item, "blocked ticket")
        blockers = _dependency_pairs(mapping["blocked_by"])
        entries.append(
            (
                _text(mapping["id"], "blocked id"),
                _text(mapping["title"], "blocked title"),
                blockers,
            )
        )
    return tuple(entries)


def _dependency_pairs(value: object) -> tuple[tuple[str, str], ...]:
    if not isinstance(value, list):
        raise ValueError("blocked dependencies must be a list")
    pairs = []
    for item in value:
        mapping = _mapping(item, "blocked dependency")
        pairs.append(
            (
                _text(mapping["id"], "dependency id"),
                _text(mapping["state"], "dependency state"),
            )
        )
    return tuple(pairs)


def _optional_pair(value: object, label: str) -> tuple[str, str] | None:
    if value is None:
        return None
    pairs = _ticket_pairs([value], label)
    return pairs[0]


def _failures(value: object) -> tuple[FailedExecution, ...]:
    if not isinstance(value, list):
        raise ValueError("failed executions must be a list")
    failures = []
    for item in value:
        mapping = _mapping(item, "failed execution")
        failures.append(
            FailedExecution(
                ticket_id=_text(mapping["id"], "failed id"),
                title=_text(mapping["title"], "failed title"),
                reason=_text(mapping["reason"], "failed reason"),
                retryable=_bool(mapping["retryable"], "failed retryable"),
                nonretryable_reason=_optional_text(
                    mapping["nonretryable_reason"], "nonretryable reason"
                ),
                interruption_kind=_optional_text(
                    mapping["interruption_kind"], "interruption kind"
                ),
            )
        )
    return tuple(failures)


def _optional_dict(value: object, label: str) -> dict[str, object] | None:
    if value is None:
        return None
    return _mapping(value, label)
