# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
# ruff: noqa: F401, I001

"""Shared IPC fixtures and dispatch/server implementation-seam source.

Semantic ``test_ipc_*`` modules own fake dispatch, transport/ownership,
owner-thread handoff, and recovery-bridge selections. Real owner-thread tests
stay separate from fake dispatch and retain their IPC proof boundary.
"""

import json

import os

import socket

import stat

import struct

import sys

import threading

import time

from concurrent.futures import ThreadPoolExecutor

from pathlib import Path

from types import SimpleNamespace

import pytest

from resource_helpers import owned_short_state_dir

from runtime_helpers import run_test_iteration

from _control_support import control_fixture, git, invoke, persist_agent_running

import devlegate.cli as cli

import devlegate.daemon as daemon

import devlegate.ipc_server as ipc_server

from devlegate.ipc_protocol import (
    MAX_PAYLOAD_BYTES,
    IPCProtocolError,
    encode_frame,
    encode_request,
    parse_response,
    receive_frame,
    send_frame,
)

from devlegate.ipc_server import (
    UNIX_SOCKET_PATH_MAX_BYTES,
    UnixIPCServer,
    dispatch_mutation,
    dispatch_read_only,
)

from devlegate.runtime import (
    DevlegateError,
    OperatorCommand,
    RetryCandidate,
    _reconcile_resume_request_fingerprint,
)

from devlegate.service import ServiceEngine

from devlegate.worker_egress import WorkerClaim, WorkerRunResult

def make_engine(tmp_path, monkeypatch, state_override=None):
    working, config, state = control_fixture(tmp_path)
    if state_override is not None:
        config.write_text(config.read_text().replace(str(state), str(state_override)))
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), state

def request(socket_path, request_id, method, payload=None):
    connection = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    connection.settimeout(5)
    connection.connect(str(socket_path))
    stream = connection.makefile("rwb")
    send_frame(stream, encode_request(request_id, method, payload or {}))
    response = parse_response(receive_frame(stream))
    stream.close()
    connection.close()
    return response

@pytest.fixture
def running_server(tmp_path, monkeypatch, short_state_dir):
    engine, state = make_engine(tmp_path, monkeypatch, short_state_dir)
    engine.ensure_hosted_views()
    server = UnixIPCServer(engine, engine.ipc_socket_path)
    server.start()
    try:
        yield engine, state, server
    finally:
        server.stop()
        engine.end_hosted_owner()

def _request(method, payload=None):
    from devlegate.ipc_protocol import IPCRequest

    return IPCRequest(1, "id", method, payload or {})

# Export all support names, including private helper functions used by domain tests.
__all__ = [name for name in globals() if not name.startswith("__")]
