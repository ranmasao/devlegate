# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import json
import os
import shutil
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files

import pytest

from devlegate.worker_supervisor import _run_opencode

PLUGIN = files("devlegate").joinpath("diagnostics_plugin.mjs")
NODE = shutil.which("node")


def run_hook(tool, args, result):
    assert NODE is not None, "Node is required to exercise the diagnostics plugin"
    script = """
const {default: plugin} = await import(process.argv[1]);
let data = ''; for await (const c of process.stdin) data += c;
const [input, result] = JSON.parse(data);
await (await plugin({directory: '/work'}))['tool.execute.after'](input, result);
process.stdout.write(JSON.stringify(result));
"""
    proc = subprocess.run(
        [NODE, "--input-type=module", "-e", script, str(PLUGIN)],
        input=json.dumps([{"tool": tool, "args": args}, result]),
        text=True,
        capture_output=True,
        check=True,
    )
    return json.loads(proc.stdout)


def diagnostics():
    return {
        f"/work/file{i}.py": [
            {"severity": 1 + j % 2, "message": f"error {j} " + "x" * 320}
            for j in range(1000)
        ]
        for i in range(5)
    }


def byte_size(value):
    return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode())


@pytest.mark.parametrize("count", [2, 5])
def test_large_diagnostics_bounded(count):
    source = diagnostics()
    assert byte_size(source) > 1048576
    result = {
        "output": "LSP errors detected: visible feedback",
        "title": "title",
        "metadata": {
            "diagnostics": source,
            "diff": "diff",
            "files": [{"filePath": f"/work/file{i}.py"} for i in range(count)],
        },
    }
    actual = run_hook("apply_patch", {}, result)
    output = actual["metadata"]["diagnostics"]
    assert byte_size(output) <= 65536
    assert actual["metadata"]["devlegateDiagnosticsTruncated"] is True
    assert set(output) <= set(list(source)[:count])
    assert all(d["severity"] == 1 for v in output.values() for d in v)
    assert actual["output"] == result["output"]
    assert actual["title"] == result["title"]
    assert actual["metadata"]["diff"] == "diff"
    assert actual["metadata"]["files"] == result["metadata"]["files"]


def test_moves_deletions_unknown_and_unicode():
    source = {
        f"/work/{n}": [{"severity": 1, "message": "\u00e9" * 40000}]
        for n in ("old", "new", "deleted", "other")
    }
    source["/work/deleted"] = [{"severity": 2, "message": "warning"}]
    result = {
        "metadata": {
            "diagnostics": source,
            "files": [
                {"filePath": "/work/old", "movePath": "/work/new", "type": "move"},
                {"filePath": "/work/deleted", "type": "delete"},
            ],
        }
    }
    metadata = run_hook("apply_patch", {}, result)["metadata"]
    output = metadata["diagnostics"]
    assert byte_size(output) <= 65536
    assert metadata["devlegateDiagnosticsTruncated"] is True
    assert output == {
        "/work/deleted": source["/work/deleted"],
    }
    result["metadata"]["files"] = [{"filePath": "relative"}]
    metadata = run_hook("apply_patch", {}, result)["metadata"]
    assert metadata["diagnostics"] == {}
    assert metadata["devlegateDiagnosticsTruncated"] is True


def test_exact_utf8_budget_and_severity_priority():
    diagnostic = {"severity": 1, "message": '\u00e9"\\\n'}
    source = {"/work/\u00e9": [diagnostic]}
    diagnostic["message"] += "x" * (65536 - byte_size(source))
    warning = {"severity": 2, "message": "warning"}
    result = {"metadata": {"diagnostics": {"/work/\u00e9": [warning, diagnostic]}}}
    metadata = run_hook("write", {"filePath": "/work/\u00e9"}, result)["metadata"]
    assert metadata["diagnostics"] == source
    assert byte_size(metadata["diagnostics"]) == 65536
    assert metadata["devlegateDiagnosticsTruncated"] is True


@pytest.mark.parametrize("tool", ["edit", "write"])
def test_single_file_and_malformed(tool):
    result = {
        "metadata": {
            "diagnostics": {"/work/a": [{"severity": 1}], "/work/b": []},
            "other": 3,
        }
    }
    assert run_hook(tool, {"filePath": "a"}, result)["metadata"] == {
        "diagnostics": {"/work/a": [{"severity": 1}]},
        "other": 3,
    }
    assert run_hook(tool, {}, {"metadata": {"diagnostics": None}})["metadata"] == {
        "diagnostics": {},
        "devlegateDiagnosticsTruncated": True,
    }
    assert run_hook(tool, {}, {"metadata": None}) == {"metadata": None}
    assert run_hook("devlegate_report", {}, result) == result


@pytest.mark.skipif(not os.environ.get("DEVLEGATE_LIVE_OPENCODE"), reason="opt-in")
def test_live_opencode_loads_hook(tmp_path):
    config_dir = tmp_path / "worker-config"
    plugins = config_dir / "plugins"
    plugins.mkdir(parents=True)
    (plugins / "z-diagnostics.js").write_bytes(PLUGIN.read_bytes())

    # Explicit ordering puts the test injector before the production plugin.
    class Handler(BaseHTTPRequestHandler):
        calls = 0
        requests = []

        def log_message(self, *args):
            pass

        def do_POST(self):
            request = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            Handler.requests.append(request)
            if request.get("tools"):
                Handler.calls += 1
            delta = (
                {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": "call1",
                            "type": "function",
                            "function": {
                                "name": "write",
                                "arguments": json.dumps(
                                    {
                                        "filePath": str(tmp_path / "hello.py"),
                                        "content": 'value: int = "wrong"\n',
                                    }
                                ),
                            },
                        }
                    ]
                }
                if Handler.calls == 1 and request.get("tools")
                else {"content": "done"}
            )
            chunk = {
                "id": "test",
                "object": "chat.completion.chunk",
                "created": 1,
                "model": "test",
                "choices": [
                    {
                        "index": 0,
                        "delta": delta,
                        "finish_reason": "tool_calls"
                        if "tool_calls" in delta
                        else "stop",
                    }
                ],
            }
            body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            values = list(diagnostics().values())
            snapshot = {str(tmp_path / "hello.py"): values[0]}
            snapshot.update(
                {str(tmp_path / f"other{i}"): v for i, v in enumerate(values[1:])}
            )
            body = json.dumps(snapshot).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    (plugins / "a-inject.js").write_text(f"""export default async () => ({{
      'tool.execute.after': async (input, result) => {{
        if (input.tool !== 'write') return;
        result.metadata.diagnostics = await (await fetch('http://127.0.0.1:{server.server_port}/')).json();
      }}
    }})""")
    config = {
        "provider": {
            "test": {
                "npm": "@ai-sdk/openai-compatible",
                "name": "test",
                "options": {"baseURL": f"http://127.0.0.1:{server.server_port}/v1"},
                "models": {"test": {"name": "test"}},
            }
        },
    }
    if os.environ.get("DEVLEGATE_LSP_BIN"):
        config["lsp"] = {
            "test-lsp": {
                "command": [os.environ["DEVLEGATE_LSP_BIN"], "--stdio"],
                "extensions": [".py"],
            }
        }
    env = {
        **os.environ,
        "OPENCODE_CONFIG_DIR": str(config_dir),
        "OPENCODE_CONFIG_CONTENT": json.dumps(config),
    }
    events = []
    result = _run_opencode(
        [
            "opencode",
            "run",
            "--dir",
            str(tmp_path),
            "--format",
            "json",
            "--model",
            "test/test",
        ],
        "Use the write tool to create hello.py. Then stop.",
        cwd=tmp_path,
        env=env,
        event_handler=events.append,
        show_worker_output=False,
    )
    server.shutdown()
    assert result.process_returncode == 0
    assert result.transport_ok, result.transport_error
    writes = [
        event["part"]
        for event in events
        if event.get("type") == "tool_use"
        and event.get("part", {}).get("tool") == "write"
    ]
    assert writes
    filtered = writes[-1]["state"]["metadata"]["diagnostics"]
    assert writes[-1]["state"]["metadata"]["devlegateDiagnosticsTruncated"]
    assert set(filtered) == {str(tmp_path / "hello.py")}
    assert byte_size(filtered) <= 65536
    assert all(byte_size(event) < 1048576 for event in events)
    print(
        "synthetic snapshot bytes:",
        byte_size(diagnostics()),
        "filtered bytes:",
        byte_size(filtered),
        "largest JSONL event bytes:",
        max(byte_size(event) for event in events),
    )
    if os.environ.get("DEVLEGATE_LSP_BIN"):
        assert "LSP errors detected" in writes[-1]["state"]["output"]
        assert "LSP errors detected" in json.dumps(Handler.requests)
