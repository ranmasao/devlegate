"""Bounded Unix IPC client for read-only views and service commands."""

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
    """A transport, protocol, or service application response error."""

    def __init__(
        self,
        message: str,
        *,
        application: bool = False,
        uncertain: bool = False,
    ) -> None:
        super().__init__(message)
        self.application = application
        self.uncertain = uncertain


def _uncertain_message(method: str) -> str:
    if method == "retry":
        return "retry request outcome is uncertain; inspect status before retrying"
    return f"{method} request outcome is uncertain; inspect status before retrying"


def request(
    socket_path: Path,
    method: str,
    payload: dict[str, object] | None = None,
    *,
    timeout: float = 2.0,
    mutable: bool = False,
    request_id: str | None = None,
) -> dict[str, object]:
    """Issue one bounded request, replaying an ambiguous mutable delivery once."""
    request_id = request_id or uuid.uuid4().hex
    attempts = 2 if mutable else 1
    for attempt in range(attempts):
        try:
            return _request_once(
                socket_path,
                method,
                payload,
                request_id=request_id,
                timeout=timeout,
                mutable=mutable,
            )
        except IPCClientError as error:
            if mutable and attempt > 0 and not error.application:
                raise IPCClientError(
                    _uncertain_message(method),
                    uncertain=True,
                ) from error
            if not mutable or not error.uncertain or attempt + 1 == attempts:
                raise


def _request_once(
    socket_path: Path,
    method: str,
    payload: dict[str, object] | None,
    *,
    request_id: str,
    timeout: float,
    mutable: bool,
) -> dict[str, object]:
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(timeout)
    connected = False
    try:
        try:
            connection.connect(str(socket_path))
            connected = True
            stream = connection.makefile("rwb")
            try:
                send_frame(stream, encode_request(request_id, method, payload or {}))
                payload = receive_frame(stream)
            finally:
                stream.close()
        except (OSError, IPCProtocolError) as error:
            if mutable and connected:
                raise IPCClientError(
                    _uncertain_message(method),
                    uncertain=True,
                ) from error
            raise IPCClientError(f"service IPC unavailable: {error}") from error
    finally:
        connection.close()
    if payload is None:
        if mutable:
            raise IPCClientError(
                _uncertain_message(method),
                uncertain=True,
            )
        raise IPCClientError("service IPC returned no response")
    try:
        response = parse_response(payload)
    except IPCProtocolError as error:
        if mutable:
            raise IPCClientError(
                _uncertain_message(method),
                uncertain=True,
            ) from error
        raise IPCClientError(f"service IPC protocol error: {error}") from error
    if response.request_id != request_id:
        if mutable:
            raise IPCClientError(
                _uncertain_message(method),
                uncertain=True,
            )
        raise IPCClientError("service IPC response id does not match request")
    if not response.ok:
        error = response.error or {}
        raise IPCClientError(
            f"service error: {error.get('message', 'unknown application error')}",
            application=True,
        )
    if response.result is None:
        if mutable:
            raise IPCClientError(
                _uncertain_message(method),
                uncertain=True,
            )
        raise IPCClientError("service IPC response has no result")
    return response.result


def decode_retry_candidates(value: dict[str, object]) -> tuple[dict[str, str], ...]:
    try:
        candidates = value["candidates"]
        if not isinstance(candidates, list):
            raise ValueError("candidates must be a list")
        result = []
        for item in candidates:
            if not isinstance(item, dict) or set(item) != {
                "id",
                "title",
                "reason",
                "kind",
            }:
                raise ValueError("retry candidate shape is invalid")
            if not all(isinstance(item[key], str) and item[key] for key in item):
                raise ValueError("retry candidate fields must be non-empty text")
            result.append({key: item[key] for key in item})
        if set(value) != {"candidates"}:
            raise ValueError("retry candidate response fields are invalid")
        return tuple(result)
    except (KeyError, TypeError, ValueError) as error:
        raise IPCClientError(
            f"service IPC returned invalid retry candidates: {error}"
        ) from error


def decode_retry_ack(value: dict[str, object], ticket_id: str) -> None:
    if set(value) != {"accepted", "ticket_id"}:
        raise IPCClientError("service IPC returned invalid retry acknowledgement")
    if value["accepted"] is not True or value["ticket_id"] != ticket_id:
        raise IPCClientError("service IPC returned invalid retry acknowledgement")


def decode_reconcile_ack(
    value: dict[str, object], ticket_id: str, onto: str
) -> None:
    if set(value) != {"accepted", "ticket_id", "onto"}:
        raise IPCClientError(
            "service IPC returned invalid reconciliation acknowledgement"
        )
    if (
        value["accepted"] is not True
        or value["ticket_id"] != ticket_id
        or value["onto"] != onto
    ):
        raise IPCClientError(
            "service IPC returned invalid reconciliation acknowledgement"
        )


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
        raise IPCClientError(f"service IPC returned invalid plan: {error}") from error


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
        raise IPCClientError(f"service IPC returned invalid status: {error}") from error


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
