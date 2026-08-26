import io
import json
import subprocess

import pytest

from devlegate.cli import MAX_STDOUT_EVENT_BYTES, _run_opencode


class FakeProcess:
    def __init__(self, stdout=b"", stderr=b"", returncode=0):
        self.stdout = io.BytesIO(stdout)
        self.stderr = io.BytesIO(stderr)
        self.returncode = returncode

    def wait(self):
        return self.returncode


def run_worker(monkeypatch, stdout=b"", stderr=b"", returncode=0):
    process = FakeProcess(stdout, stderr, returncode)
    monkeypatch.setattr(
        subprocess, "Popen", lambda *args, **kwargs: process
    )
    return process, _run_opencode(["fake"], "prompt")


def event(text):
    return (json.dumps({"type": "text", "part": {"text": text}}) + "\n").encode()


def test_worker_protocol_renders_events_and_stderr(capsys, monkeypatch):
    stdout = event("ordinary")
    _, result = run_worker(monkeypatch, stdout, b"stderr\n")
    output = capsys.readouterr()
    assert result == 0
    assert "ordinary" in output.out
    assert "stderr" in output.err


@pytest.mark.parametrize(
    "payload",
    [b"not-json\n", b'{}\n', b'{"type":"text"}\n'],
)
def test_malformed_worker_events_fail_closed(capsys, monkeypatch, payload):
    _, result = run_worker(monkeypatch, payload)
    assert result == 1
    assert "protocol error" in capsys.readouterr().out


def test_worker_event_just_below_limit_is_processed(capsys, monkeypatch):
    raw = event("x" * (MAX_STDOUT_EVENT_BYTES - 1000))
    _, result = run_worker(monkeypatch, raw)
    assert result == 0
    assert "exceeds maximum size" not in capsys.readouterr().out


def test_oversized_event_is_drained_and_not_echoed(capsys, monkeypatch):
    oversized = b"oversized-secret-" + b"x" * (MAX_STDOUT_EVENT_BYTES + 100) + b"\n"
    _, result = run_worker(monkeypatch, oversized + event("after"))
    output = capsys.readouterr().out
    assert result == 1
    assert "exceeds maximum size" in output
    assert "oversized-secret" not in output
    assert "after" in output


def test_tool_and_error_events_are_rendered_inert(capsys, monkeypatch):
    payload = "before\x1b[?1049h"
    lines = [
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "completed", "output": payload},
            },
        },
        {
            "type": "tool_use",
            "part": {
                "tool": "bash",
                "state": {"status": "error", "error": payload},
            },
        },
        {"type": "error", "error": {"message": payload}},
    ]
    raw = b"".join((json.dumps(line) + "\n").encode() for line in lines)
    _, result = run_worker(monkeypatch, raw)
    output = capsys.readouterr().out
    assert result == 0
    assert "OpenCode tool: bash" in output
    assert "OpenCode error" in output
    assert "\\x1b[?1049h" in output
    assert "\x1b" not in output
