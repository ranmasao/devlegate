# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Private artifact transport quotas and fail-closed framing."""

import json
import os
import struct
import subprocess

import pytest

from devlegate.abandonment_evidence import (
    MAX_READ_BYTES,
    EvidenceBuilder,
    EvidenceError,
    GitArtifact,
    OwnerGitEvidence,
)
from devlegate.evidence_channel import MAX_REQUEST_BYTES, EvidencePipe
from devlegate.ipc_protocol import encode_frame


@pytest.fixture
def bundle(tmp_path):
    subprocess.run(["git", "init", str(tmp_path)], check=True, capture_output=True)
    data = b"\x01" * MAX_READ_BYTES
    oid = subprocess.run(
        ["git", "-C", str(tmp_path), "hash-object", "-w", "--stdin"],
        input=data, capture_output=True, check=True,
    ).stdout.decode().strip()
    with OwnerGitEvidence(tmp_path, lambda: None) as source:
        builder = EvidenceBuilder(source)
        identity = builder.add(GitArtifact("blob", (oid,)))
        bundle = builder.seal({})
        yield bundle, identity
        bundle.close()


def request(bundle, identity, **changes):
    value = dict(manifest_id=bundle.id, artifact_id=identity, offset=0, length=1)
    value.update(changes)
    return encode_frame(json.dumps(value).encode())


def response(fd):
    size = struct.unpack(">I", os.read(fd, 4))[0]
    return json.loads(os.read(fd, size))


@pytest.mark.parametrize("changes", [
    {"artifact_id": "0" * 64}, {"offset": -1}, {"offset": True},
    {"length": MAX_READ_BYTES + 1}, {"path": "/etc/passwd"},
    {"method": "operator-abandon"},
])
def test_pipe_cannot_escape_manifest_or_invoke_control_methods(bundle, changes):
    bundle, identity = bundle
    with EvidencePipe(bundle) as channel:
        outgoing, incoming = channel.worker_fds
        os.write(outgoing, request(bundle, identity, **changes))
        assert channel.serve_one()
        reply = response(incoming)
        assert reply["ok"] is False
        assert set(reply) == {"ok", "error"}


def test_request_and_aggregate_response_limits_are_shared(bundle):
    bundle, identity = bundle
    with EvidencePipe(bundle, requests=1) as channel:
        outgoing, incoming = channel.worker_fds
        os.write(outgoing, request(bundle, identity))
        assert channel.serve_one()
        assert response(incoming)["ok"]
        with pytest.raises(EvidenceError, match="request/session budget"):
            channel.serve_one()
        assert channel._request_r == -1
    with EvidencePipe(bundle, response_bytes=1) as channel:
        outgoing, _incoming = channel.worker_fds
        os.write(outgoing, request(bundle, identity))
        with pytest.raises(EvidenceError, match="aggregate response budget"):
            channel.serve_one()


def test_oversized_frame_denied_before_reading_payload(bundle):
    bundle, _identity = bundle
    with EvidencePipe(bundle) as channel:
        outgoing, _incoming = channel.worker_fds
        os.write(outgoing, struct.pack(">I", MAX_REQUEST_BYTES + 1))
        with pytest.raises(EvidenceError, match="frame exceeds"):
            channel.serve_one()
        assert channel._request_r == -1


def test_stalled_input_and_stalled_reader_have_deadlines(bundle):
    bundle, identity = bundle
    with EvidencePipe(bundle, io_seconds=0.02) as channel:
        outgoing, _incoming = channel.worker_fds
        os.write(outgoing, struct.pack(">I", 100))
        with pytest.raises(EvidenceError, match="deadline"):
            channel.serve_one()
    with EvidencePipe(bundle, io_seconds=0.02) as channel:
        outgoing, _incoming = channel.worker_fds
        os.write(outgoing, request(bundle, identity, length=MAX_READ_BYTES))
        with pytest.raises(EvidenceError, match="deadline"):
            channel.serve_one()


def test_worker_eof_closes_owner_ends(bundle):
    bundle, _identity = bundle
    with EvidencePipe(bundle) as channel:
        channel.worker_started()
        assert channel.serve_one() is False
        assert channel._request_r == channel._response_w == -1
