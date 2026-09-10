import socket
import threading
from copy import deepcopy

import pytest

from devlegate.cli import _render_status_text
from devlegate.ipc_client import IPCClientError, decode_plan, decode_status, request
from devlegate.ipc_protocol import (
    encode_error_response,
    encode_success_response,
    parse_request,
    receive_frame,
    send_frame,
)
from devlegate.runtime import (
    ExecutionPlan,
    FailedExecution,
    GitObservation,
    StatusSnapshot,
)


def serve_once(path, response):
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def serve():
        connection, _ = listener.accept()
        stream = connection.makefile("rwb")
        try:
            request_message = parse_request(receive_frame(stream))
            send_frame(stream, response(request_message))
        finally:
            stream.close()
            connection.close()
            listener.close()

    thread = threading.Thread(target=serve)
    thread.start()
    return thread


def test_request_returns_structured_success(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(message):
        return encode_success_response(message.request_id, {"value": "ok"})

    thread = serve_once(path, response)
    assert request(path, "ping") == {"value": "ok"}
    thread.join(timeout=2)
    assert not thread.is_alive()


def test_request_rejects_mismatched_response_id(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(_message):
        return encode_success_response("different", {"value": "ok"})

    thread = serve_once(path, response)
    with pytest.raises(IPCClientError, match="response id does not match"):
        request(path, "ping")
    thread.join(timeout=2)


def test_request_surfaces_application_error(tmp_path):
    path = tmp_path / "devlegate.sock"

    def response(message):
        return encode_error_response(message.request_id, "blocked", "not ready")

    thread = serve_once(path, response)
    with pytest.raises(IPCClientError, match="not ready") as raised:
        request(path, "status")
    assert raised.value.application is True
    thread.join(timeout=2)


def test_mutable_transport_failure_is_uncertain_and_not_replayed(tmp_path):
    path = tmp_path / "ipc.sock"
    listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    listener.bind(str(path))
    listener.listen(1)

    def close_after_request():
        connection, _address = listener.accept()
        connection.recv(4096)
        connection.close()

    thread = threading.Thread(target=close_after_request)
    thread.start()
    try:
        with pytest.raises(IPCClientError) as raised:
            request(path, "retry", {"ticket_id": "T-1"}, mutable=True)
        assert raised.value.uncertain
    finally:
        listener.close()
        thread.join(timeout=2)


def representative_observation(branch="main"):
    return GitObservation(
        branch,
        False,
        "local-head",
        "origin/main",
        "remote-head",
        True,
        "fingerprint",
    )


def representative_plan():
    observation = representative_observation()
    return ExecutionPlan(
        "run-worker",
        "deterministic runnable ticket selection",
        "T-1",
        "Ticket",
        "todo",
        True,
        observation,
        representative_observation("devlegate/control"),
    )


def test_status_round_trip_preserves_complete_view():
    plan = representative_plan()
    snapshot = StatusSnapshot(
        "agent_running",
        "T-1",
        True,
        representative_observation(),
        representative_observation("devlegate/control"),
        (("backlog", 1), ("todo", 2), ("review", 3), ("accepted", 4), ("done", 5)),
        (("T-1", "Ticket"),),
        (("T-2", "Waiting", (("D-1", "review"),)),),
        (("R-1", "Review"),),
        (("A-1", "Accepted"),),
        ("T-1", "Ticket"),
        plan,
        (FailedExecution("F-1", "Failed", "broken", True, None, "operator_abort"),),
        {"ticket": "T-1", "onto": "main"},
    )

    assert decode_status(snapshot.as_dict()) == snapshot


def test_plan_round_trip_preserves_complete_view():
    plan = representative_plan()

    assert decode_plan(plan.as_dict()) == plan


def test_status_text_is_identical_after_ipc_round_trip():
    snapshot = StatusSnapshot(
        "idle",
        None,
        False,
        representative_observation(),
        representative_observation("devlegate/control"),
        (("backlog", 1), ("todo", 2), ("review", 3), ("accepted", 4), ("done", 5)),
        (("T-1", "Ticket"),),
        (("T-2", "Waiting", (("D-1", "review"),)),),
        (("R-1", "Review"),),
        (("A-1", "Accepted"),),
        ("T-1", "Ticket"),
        representative_plan(),
    )

    ipc_text = _render_status_text(decode_status(snapshot.as_dict()))
    direct_text = _render_status_text(snapshot)
    assert ipc_text == direct_text


@pytest.mark.parametrize("status", ["pending", "resolved"])
def test_reconciliation_text_distinguishes_actionable_history(status):
    reconciliation = {
        "status": status,
        "ticket_id": "T-1",
        "original_base": "base-1",
        "observed_product": "product-1",
        "worker_checkpoint": "checkpoint-1",
        "product_branch": "main",
        "product_local_head": "local-1",
        "product_dirty": False,
        "product_target_eligible": True,
        "product_observation": "clean",
    }
    snapshot = StatusSnapshot(
        "idle",
        None,
        False,
        representative_observation(),
        representative_observation("devlegate/control"),
        (("backlog", 1), ("todo", 2), ("review", 3), ("accepted", 4), ("done", 5)),
        (),
        (),
        (),
        (),
        None,
        representative_plan(),
        reconciliation=reconciliation,
    )

    text = _render_status_text(snapshot)
    payload = snapshot.as_dict()
    assert payload["reconciliation"]["status"] == status
    if status == "pending":
        assert "Reconciliation required:" in text
        assert "  update-base eligible: True" in text
    else:
        assert "Reconciliation required:" not in text
        assert "  ticket: T-1" not in text
    assert snapshot.plan.action == "run-worker"


def test_counts_decode_in_canonical_order():
    value = StatusSnapshot(
        "idle",
        None,
        False,
        representative_observation(),
        None,
        (("backlog", 1), ("todo", 2), ("review", 3), ("accepted", 4), ("done", 5)),
        (),
        (),
        (),
        (),
        None,
        ExecutionPlan("none", "none", code=representative_observation()),
    ).as_dict()
    value["tickets"]["counts"] = {
        "done": 5,
        "accepted": 4,
        "review": 3,
        "todo": 2,
        "backlog": 1,
    }

    assert decode_status(value).counts == (
        ("backlog", 1),
        ("todo", 2),
        ("review", 3),
        ("accepted", 4),
        ("done", 5),
    )


@pytest.mark.parametrize(
    "mutate",
    [
        lambda value: value["tickets"]["blocked"][0]["blocked_by"][0].pop("state"),
        lambda value: value["tickets"]["counts"].pop("todo"),
        lambda value: value["tickets"]["counts"].update({"unexpected": 1}),
        lambda value: value["tickets"]["counts"].update({"todo": True}),
    ],
)
def test_malformed_status_nested_shapes_raise_ipc_error(mutate):
    snapshot = StatusSnapshot(
        "idle",
        None,
        False,
        representative_observation(),
        None,
        (("backlog", 1), ("todo", 2), ("review", 3), ("accepted", 4), ("done", 5)),
        (),
        (("T-2", "Waiting", (("D-1", "review"),)),),
        (),
        (),
        None,
        ExecutionPlan("none", "none", code=representative_observation()),
    )
    value = deepcopy(snapshot.as_dict())
    mutate(value)

    with pytest.raises(IPCClientError):
        decode_status(value)
