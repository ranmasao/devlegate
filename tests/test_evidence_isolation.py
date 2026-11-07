# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Kernel-enforced evidence isolation, exclusively in disposable child processes."""

import ctypes
import json
import os
import socket
import subprocess
import sys
import sysconfig
from pathlib import Path

import pytest

from devlegate import evidence_launcher
from devlegate.abandonment_evidence import (
    EvidenceBuilder,
    GitArtifact,
    OwnerGitEvidence,
)
from devlegate.evidence_channel import EvidencePipe


@pytest.fixture
def kernel_boundary():
    if not sys.platform.startswith("linux"):
        pytest.skip("Linux Landlock ABI 6+ is required for the kernel boundary test")
    if ctypes.CDLL(None).syscall(444, 0, 0, 1) < 6:
        pytest.skip("host lacks Landlock ABI 6+; production launcher refuses admission")


def launch(tmp_path, code, *, pipes=(), extra_spec=None, extra_inherited=()):
    evidence = tmp_path / "evidence"
    evidence.mkdir(exist_ok=True)
    scratch = tmp_path / "scratch"
    scratch.mkdir(exist_ok=True)
    python = str(Path(sys.executable).resolve())
    executables = {python}
    for path in ("/lib64/ld-linux-x86-64.so.2", "/lib/ld-linux-aarch64.so.1"):
        if Path(path).exists():
            executables.add(str(Path(path).resolve()))
    runtime = {
        str(Path(sysconfig.get_path("stdlib")).resolve()),
        str(Path("/usr/lib").resolve()),
    }
    # Dynamic libraries are data, not executable tool roots.
    for path in ("/lib", "/lib64"):
        if Path(path).exists():
            runtime.add(str(Path(path).resolve()))
    spec = {
        "readonly": sorted(runtime | {str(evidence.resolve())}),
        "executable": sorted(executables), "scratch": str(scratch.resolve()),
        "environment": {"HOME": str(scratch), "TMPDIR": str(scratch), "LANG": "C"},
        "pipes": list(pipes),
    }
    spec.update(extra_spec or {})
    read_fd, write_fd = os.pipe()
    try:
        os.write(write_fd, json.dumps(spec).encode())
        os.close(write_fd)
        write_fd = -1
        process = subprocess.Popen(
            [sys.executable, "-I", "-S", evidence_launcher.__file__,
             "--spec-fd", str(read_fd), "--", python, "-I", "-S", "-c", code],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            pass_fds=(read_fd, *pipes, *extra_inherited),
            env={**os.environ, "GIT_CONFIG_GLOBAL": "/must/not/leak",
                 "SSH_AUTH_SOCK": "/must/not/leak", "DEVLEGATE_CONTROL": "secret"},
        )
    finally:
        os.close(read_fd)
        if write_fd != -1:
            os.close(write_fd)
    return process


def test_kernel_denies_source_credentials_ipc_and_evidence_mutation(
    tmp_path, kernel_boundary,
):
    private = tmp_path / "owner"
    private.mkdir()
    source = private / "project"
    source.mkdir()
    (source / ".git").mkdir()
    secret = private / "credentials"
    secret.write_text("owner secret")
    evidence = tmp_path / "evidence"
    evidence.mkdir()
    manifest = evidence / "manifest.json"
    manifest.write_text('{"bounded": true}')
    (evidence / "escape").symlink_to(secret)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as control:
        control.bind(str(private / "control.sock"))
        control.listen()
        code = f"""
import ctypes, errno, json, os, platform, socket, subprocess, sys
def denied(operation):
    try:
        operation()
    except OSError as error:
        assert error.errno in (errno.EACCES, errno.EPERM), error
    else:
        raise AssertionError('sandbox escape succeeded')
assert not any(key.startswith(('GIT_', 'SSH_', 'DEVLEGATE_')) for key in os.environ)
assert open({str(manifest)!r}).read() == '{{"bounded": true}}'
denied(lambda: open({str(secret)!r}).read())
denied(lambda: open({str(evidence / 'escape')!r}).read())
denied(lambda: os.listdir({str(source / '.git')!r}))
denied(lambda: open({str(source / 'modified')!r}, 'w'))
denied(lambda: open({str(manifest)!r}, 'w'))
denied(lambda: open('/proc/self/environ').read())
denied(lambda: socket.socket(socket.AF_UNIX))
denied(lambda: socket.socketpair())
denied(lambda: os.execv('/usr/bin/git', ['git', '--version']))
denied(lambda: os.kill(os.getppid(), 0))
# Neither process-memory APIs nor an io_uring can bypass the private pipes.
libc = ctypes.CDLL(None, use_errno=True)
calls = (310, 438, 425) if platform.machine() == 'x86_64' else (270, 438, 425)
for call in calls:
    assert libc.syscall(call, 0, 0, 0, 0, 0, 0) == -1
    assert ctypes.get_errno() == errno.EPERM
descendant = subprocess.run([sys.executable, '-I', '-S', '-c',
    'open(' + repr({str(secret)!r}) + ').read()'], capture_output=True)
assert descendant.returncode != 0 and b'PermissionError' in descendant.stderr
open('scratch-result', 'w').write('isolated')
print('isolated')
"""
        process = launch(tmp_path, code)
        stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr.decode()
    assert stdout == b"isolated\n"
    assert manifest.read_text() == '{"bounded": true}'
    assert secret.read_text() == "owner secret"
    assert not (source / "modified").exists()


def test_unexpected_inherited_source_descriptor_is_closed(tmp_path, kernel_boundary):
    private = tmp_path / "credential"
    private.write_bytes(b"must never escape")
    with private.open("rb") as stream:
        descriptor = stream.fileno()
        code = f"""
import errno, os
try:
    os.read({descriptor}, 100)
except OSError as error:
    assert error.errno == errno.EBADF
else:
    raise AssertionError('unexpected source descriptor survived')
print('closed')
"""
        process = launch(tmp_path, code, extra_inherited=(descriptor,))
        stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 0, stderr.decode()
    assert stdout == b"closed\n"


def test_git_free_child_reads_manifest_authorized_binary_ranges_over_pipes(
    tmp_path, kernel_boundary,
):
    repo = tmp_path / "owner-repository"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    data = bytes(range(256)) * 4096
    oid = subprocess.run(
        ["git", "-C", str(repo), "hash-object", "-w", "--stdin"],
        input=data, check=True, capture_output=True,
    ).stdout.decode().strip()
    with OwnerGitEvidence(repo, lambda: None) as source:
        builder = EvidenceBuilder(source)
        identity = builder.add(GitArtifact("blob", (oid,)))
        bundle = builder.seal({"target": "DISPOSABLE"})
        evidence = tmp_path / "evidence"
        evidence.mkdir()
        manifest = evidence / "manifest.json"
        manifest.write_text(json.dumps(bundle.manifest()))
        channel = EvidencePipe(bundle)
        request_w, response_r = channel.worker_fds
        code = f"""
import base64, hashlib, json, os, struct
manifest = json.load(open({str(manifest)!r}))
record = manifest['artifacts'][0]
assert record['id'] == {identity!r} and record['text'] == ''
requests = os.fdopen({request_w}, 'wb', buffering=0)
responses = os.fdopen({response_r}, 'rb')
digest = hashlib.sha256()
for offset in range(0, record['size'], 262144):
    request = json.dumps(dict(manifest_id=manifest['manifest_id'],
                              artifact_id=record['id'], offset=offset,
                              length=262144)).encode()
    requests.write(struct.pack('>I', len(request)) + request)
    length = struct.unpack('>I', responses.read(4))[0]
    assert length < 1048576
    reply = json.loads(responses.read(length))
    assert reply['ok'], reply
    response = reply['result']
    value = base64.b64decode(response['content'])
    assert response['chunk_sha256'] == hashlib.sha256(value).hexdigest()
    digest.update(value)
assert digest.hexdigest() == record['sha256']
print('verified binary without Git')
"""
        try:
            process = launch(tmp_path, code, pipes=(request_w, response_r))
            channel.worker_started()
            for _page in range(4):
                assert channel.serve_one()
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 0, stderr.decode()
            assert stdout == b"verified binary without Git\n"
        finally:
            channel.close()
            bundle.close()


def test_unprovable_boundary_refuses_before_worker_exec(tmp_path):
    process = launch(tmp_path, "print('must not run')", extra_spec={"readonly": ["/"]})
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 126
    assert stdout == b""
    assert b"isolation refused" in stderr


def test_credentials_cannot_be_injected_through_launch_environment(tmp_path):
    process = launch(
        tmp_path, "print('must not run')",
        extra_spec={"environment": {"SSH_AUTH_SOCK": "/owner/agent"}},
    )
    stdout, stderr = process.communicate(timeout=15)
    assert process.returncode == 126
    assert stdout == b""
    assert b"environment allowlist" in stderr
