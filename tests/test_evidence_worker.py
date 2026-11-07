# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import hashlib
import json
import socket
import struct
import subprocess
import sys
import threading
import time

import pytest

from devlegate.abandonment_evidence import (
    EvidenceBuilder,
    EvidenceError,
    GitArtifact,
    OwnerGitEvidence,
)
from devlegate.evidence_channel import EvidenceSocket
from devlegate.evidence_worker import (
    REPORT_SECTIONS,
    EvidenceWorkspace,
    analysis_prompt,
    validate_analysis_claim,
)
from devlegate.worker_egress import WorkerClaim
from devlegate.worker_supervisor import WorkerSupervisor


@pytest.fixture
def bundle(tmp_path):
    repo = tmp_path / "owner-repository"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    data = bytes(range(256)) * 4097
    oid = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=data, check=True, capture_output=True,
    ).stdout.decode().strip()
    owner = threading.get_ident()

    def assert_owner():
        assert threading.get_ident() == owner

    with OwnerGitEvidence(repo, assert_owner) as source:
        builder = EvidenceBuilder(source)
        builder.add(GitArtifact("blob", (oid,)))
        yield builder.seal({"target": "DISPOSABLE"}), data


def exchange(channel, payload):
    client = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    client.connect(str(channel.path))
    client.setblocking(False)
    raw = json.dumps(payload).encode()
    client.sendall(struct.pack(">I", len(raw)) + raw)
    received = bytearray()
    deadline = time.monotonic() + 5
    try:
        while time.monotonic() < deadline:
            channel.poll()
            try:
                chunk = client.recv(65536)
            except BlockingIOError:
                continue
            if not chunk:
                break
            received.extend(chunk)
        assert len(received) >= 4
        length = struct.unpack(">I", received[:4])[0]
        assert len(received) == length + 4
        return json.loads(received[4:])
    finally:
        client.close()


def test_scoped_socket_denies_other_operations_and_limits_reads(
    bundle, short_state_dir,
):
    evidence, _data = bundle
    manifest = evidence.manifest()
    artifact = manifest["artifacts"][0]
    with EvidenceSocket(evidence, short_state_dir / "read.sock") as channel:
        valid = dict(manifest_id=manifest["manifest_id"], artifact_id=artifact["id"],
                     offset=0, length=7)
        assert exchange(channel, valid)["ok"]
        for invalid in (
            {**valid, "length": 262145}, {**valid, "offset": -1},
            {**valid, "artifact_id": "0" * 64},
            {**valid, "manifest_id": "0" * 64},
            {**valid, "path": "/etc/passwd"},
            {"method": "cleanup-workspaces"},
        ):
            assert not exchange(channel, invalid)["ok"]
    assert not channel.path.exists()


def test_socket_request_quota_and_existing_paths_fail_closed(bundle, short_state_dir):
    evidence, _data = bundle
    path = short_state_dir / "read.sock"
    path.write_text("foreign")
    with pytest.raises(OSError):
        EvidenceSocket(evidence, path)
    assert path.read_text() == "foreign"
    path.unlink()
    with EvidenceSocket(evidence, path, requests=1) as channel:
        assert not exchange(channel, {})["ok"]
        with pytest.raises(EvidenceError, match="request budget"):
            exchange(channel, {})


def test_evidence_workspace_rejects_git_and_substituted_manifest(
    bundle, short_state_dir,
):
    evidence, _data = bundle
    workspace = EvidenceWorkspace.prepare(short_state_dir, "REQUEST", evidence)
    workspace.validate()
    (workspace.path / ".git").mkdir()
    with pytest.raises(EvidenceError, match=".git"):
        workspace.validate()
    (workspace.path / ".git").rmdir()
    manifest = workspace.path / "manifest.json"
    manifest.unlink()
    manifest.symlink_to(short_state_dir / "elsewhere")
    with pytest.raises(EvidenceError, match="regular file"):
        workspace.validate()


def test_supervised_evidence_only_worker_pages_binary_and_reports(
    bundle, short_state_dir, tmp_path, monkeypatch,
):
    monkeypatch.setenv("GIT_DIR", "/owner/git")
    monkeypatch.setenv("SSH_AUTH_SOCK", "/owner/ssh.sock")
    monkeypatch.setenv("DEVLEGATE_PRIVATE_SOCKET", "/owner/privileged.sock")
    evidence, data = bundle
    workspace = EvidenceWorkspace.prepare(short_state_dir, "REQUEST", evidence)
    worker = tmp_path / "trusted-analysis-worker"
    # The disposable worker receives no repository path, ref or owner object.
    # Its protocol read exercises the same socket framing as the scoped tool.
    worker.write_text(f"#!{sys.executable}\n" + '''
import base64, hashlib, json, os, socket, struct, sys
from pathlib import Path
sys.stdin.read()
assert not Path('.git').exists()
assert not any(key.startswith(('GIT_', 'SSH_', 'DEVLEGATE_')) for key in os.environ)
manifest = json.loads(Path('manifest.json').read_text())
tool_dir = Path(os.environ['OPENCODE_CONFIG_DIR']) / 'tools'
tool = (tool_dir / 'devlegate_evidence_read.ts').read_text()
assert manifest['manifest_id'] in tool
artifact = manifest['artifacts'][0]
assert artifact['omission'] and artifact['text'] == ''
digest = hashlib.sha256()
def exact(stream, length):
    value = b''
    while len(value) < length:
        part = stream.recv(length - len(value))
        assert part
        value += part
    return value
for offset in range(0, artifact['size'], 262144):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as stream:
        stream.settimeout(10)
        stream.connect(str(Path.cwd() / 'read.sock'))
        raw = json.dumps(dict(manifest_id=manifest['manifest_id'],
            artifact_id=artifact['id'], offset=offset,
            length=min(262144, artifact['size']-offset))).encode()
        stream.sendall(struct.pack('>I', len(raw)) + raw)
        length = struct.unpack('>I', exact(stream, 4))[0]
        assert length <= 1048576
        reply = json.loads(exact(stream, length))
        assert reply['ok'], reply
        result = reply['result']
        content = base64.b64decode(result['content'])
        assert hashlib.sha256(content).hexdigest() == result['chunk_sha256']
        digest.update(content)
assert digest.hexdigest() == artifact['sha256']
report = '\\n'.join('## ' + title + '\\nDisposable analysis.' for title in (
    'Abandonment reason', 'Executions and reviews', 'Meaningful changes and research',
    'Integrated and discarded work', 'Unfinished work', 'Known gaps',
    'Future conclusions', 'Evidence identities'))
report += '\\n' + manifest['manifest_id'] + '\\n' + digest.hexdigest()
print(json.dumps({'type': 'tool_use', 'part': {
    'type': 'tool', 'tool': 'devlegate_report',
    'state': {'status': 'completed', 'input': {'outcome': 'completed',
    'summary': 'Analyzed the indexed evidence.', 'remaining': [], 'questions': [],
    'report': report}}}}))
''')
    worker.chmod(0o700)
    supervisor = WorkerSupervisor(str(worker), "test", "", show_worker_output=False)
    identities = []
    with EvidenceSocket(evidence, workspace.path / "read.sock") as channel:
        result = supervisor.run(
            workspace, analysis_prompt("Analyze this disposable target.",
                                       workspace.manifest_id),
            execution_id="a" * 32, evidence=channel,
            identity_handler=identities.append,
        )
    assert result.process_returncode == 0
    assert result.transport_error is None
    assert result.egress_error is None
    assert result.worker_group_retired
    assert result.claim is not None
    assert result.claim.report.endswith(hashlib.sha256(data).hexdigest())
    assert len(identities) == 1
    assert not supervisor.active_count


def test_evidence_deadline_cancels_supervised_worker_and_retires_group(
    bundle, short_state_dir, tmp_path,
):
    evidence, _data = bundle
    workspace = EvidenceWorkspace.prepare(short_state_dir, "REQUEST", evidence)
    worker = tmp_path / "stalled-analysis-worker"
    worker.write_text(
        f"#!{sys.executable}\nimport sys, time\nsys.stdin.read()\ntime.sleep(60)\n"
    )
    worker.chmod(0o700)
    supervisor = WorkerSupervisor(str(worker), "test", "", show_worker_output=False)
    identities = []
    with EvidenceSocket(
        evidence, workspace.path / "read.sock", session_seconds=0.2,
    ) as channel:
        result = supervisor.run(
            workspace, "Inspect evidence only.", execution_id="b" * 32,
            evidence=channel, identity_handler=identities.append,
        )
    assert "session deadline" in result.transport_error
    assert result.process_returncode != 0
    assert result.worker_group_retired
    assert len(identities) == 1
    assert not supervisor.active_count


def test_complete_analysis_report_is_bounded_and_requires_identities(bundle):
    evidence, _data = bundle
    text = "\n".join(f"## {title}\nDisposable analysis." for title in REPORT_SECTIONS)
    claim = WorkerClaim("completed", "Analyzed work.", (), (), text)
    with pytest.raises(EvidenceError, match="identities"):
        validate_analysis_claim(claim, evidence)
    text += "\n" + evidence.id
    validate_analysis_claim(WorkerClaim("completed", "Analyzed work.", (), (), text),
                            evidence)
    with pytest.raises(EvidenceError, match="sections"):
        validate_analysis_claim(WorkerClaim("completed", "", (), (), evidence.id),
                                evidence)
    with pytest.raises(EvidenceError, match="65536"):
        validate_analysis_claim(WorkerClaim("blocked", "", (), (), "a" * 65537),
                                evidence)
