# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Fake dispatch and validation; this layer does not prove owner serialization."""

from _ipc_support import (
    IPCProtocolError,
    _request,
    dispatch_mutation,
    dispatch_read_only,
    pytest,
    request,
)
from _ipc_support import running_server as running_server


def test_unknown_method_returns_structured_error(running_server):
    _engine, _state, _server = running_server
    response = request(_server.path, "1", "unknown")

    assert not response.ok
    assert response.error == {
        "code": "unknown_method",
        "message": "unsupported method: unknown",
    }

def test_stop_dispatch_requests_service_lifecycle(running_server):
    engine, _state, server = running_server
    called = []
    response = dispatch_mutation(
        engine,
        _request("stop"),
        lifecycle=lambda intent, request_id, force: called.append(
            (intent, request_id, force)
        )
        or {"phase": "draining", "request_id": request_id},
    )
    assert response["accepted"] is True
    assert response["phase"] == "draining"
    assert isinstance(response["instance_id"], str)
    assert called == [("stop", "id", False)]

def test_force_restart_dispatch_requests_service_lifecycle(running_server):
    engine, _state, _server = running_server
    called = []
    response = dispatch_mutation(
        engine,
        _request("restart", {"force": True}),
        lifecycle=lambda intent, request_id, force: called.append(
            (intent, request_id, force)
        )
        or {"phase": "draining", "request_id": request_id},
    )
    assert response["accepted"] is True
    assert called == [("restart", "id", True)]

def test_control_reconciliation_dispatch_preserves_both_identities(running_server):
    engine, _state, _server = running_server
    from_head = "a" * 40
    to_head = "b" * 40
    observed = []
    engine.submit_reconcile_control = lambda actual_from, actual_to, request_id: (
        observed.append((actual_from, actual_to, request_id))
        or {"accepted": True, "from": actual_from, "to": actual_to}
    )
    response = dispatch_mutation(
        engine,
        _request(
            "reconcile-control",
            {"from": from_head, "to": to_head},
        ),
    )
    assert response == {"accepted": True, "from": from_head, "to": to_head}
    assert observed == [(from_head, to_head, "id")]


def test_clean_sweep_is_not_a_public_mutation(running_server):
    engine, _state, _server = running_server

    with pytest.raises(IPCProtocolError, match="unsupported method"):
        dispatch_mutation(engine, _request("clean-sweep", {}))


def test_workspace_ls_is_owner_serialized_and_unbounded(running_server):
    engine, _state, _server = running_server
    engine.submit_workspace_ls = lambda **_kwargs: {
        "status": "blocked",
        "decisions": [{"ticket_id": "T-1", "status": "blocked"}],
    }

    response = dispatch_read_only(engine, _request("workspace-ls", {}))

    assert response["status"] == "blocked"
    assert response["decisions"][0]["ticket_id"] == "T-1"


def test_workspace_ls_rejects_options(running_server):
    engine, _state, _server = running_server

    with pytest.raises(IPCProtocolError, match="does not accept options"):
        dispatch_read_only(engine, _request("workspace-ls", {"limit": 4}))


@pytest.mark.parametrize("payload", [
    {"force": True}, {"apply": "yes"}, {"limit": True}, {"limit": 0},
    {"limit": 10001},
])
def test_prune_rejects_invalid_admission(running_server, payload):
    engine, _state, _server = running_server
    with pytest.raises(IPCProtocolError, match="prune"):
        dispatch_mutation(engine, _request("cleanup-workspaces", payload))


def test_clean_status_is_removed(running_server):
    engine, _state, _server = running_server
    with pytest.raises(IPCProtocolError, match="unsupported method"):
        dispatch_read_only(engine, _request("clean-status"))

def test_dispatch_uses_service_views_without_persistence_access():
    class View:
        def as_dict(self):
            return {"view": True}

    class FakeEngine:
        def published_status_payload(self):
            return {"view": True}

        def published_plan_view(self):
            return View()

    status = dispatch_read_only(FakeEngine(), _request("status"))
    assert status["view"] is True
    assert status["service"]["service"] == "devlegate"
    assert dispatch_read_only(FakeEngine(), _request("plan")) == {"view": True}

def test_status_ipc_keeps_raw_worker_identity_private():
    class View:
        def as_dict(self):
            return {"view": True}

    class FakeEngine:
        def published_status_payload(self):
            return {
                "view": True,
                "live_execution": {
                    "ticket_id": "T-1",
                    "execution_id": "exec-1",
                    "stage": "worker-running",
                    "ownership": "current-service",
                    "identity_state": "matching-live",
                },
            }

    result = dispatch_read_only(FakeEngine(), _request("status"))

    assert result["live_execution"] == {
        "ticket_id": "T-1",
        "execution_id": "exec-1",
        "stage": "worker-running",
        "ownership": "current-service",
        "identity_state": "matching-live",
    }
