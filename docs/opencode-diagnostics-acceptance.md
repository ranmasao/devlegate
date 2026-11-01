# Manual OpenCode diagnostics acceptance

This high-volume check is intentionally outside CI: it needs an external
OpenCode process, a local model endpoint, and LSP protocol orchestration.
The small Node hook tests remain mandatory in CI.

Use a disposable workspace and configuration, not a Devlegate execution
workspace. The following describes the local acceptance run with OpenCode
1.19.5, Python 3.12.3, and Node 24.21.0:

1. Start a Python stdlib HTTP server implementing an OpenAI-compatible
   streaming chat completion. On the first request with tools, return a
   `write` tool call for an absolute `file0.py` path; on the next request
   return `done`. Save requests in memory to inspect tool feedback.
2. Start a stdlib LSP server over stdio from OpenCode. Read JSON-RPC messages
   using `Content-Length` framing, answer `initialize` with
   `{"capabilities":{"textDocumentSync":1}}`, and on `didOpen` or
   `didChange` publish diagnostics for five absolute file URIs. Generate each
   file's diagnostics in memory with this recipe:

   ```python
   diagnostics = [
       {"range": {"start": {"line": 0, "character": 0},
                  "end": {"line": 0, "character": 1}},
        "severity": 1 + j % 2, "message": f"diagnostic {j} " + "x" * 320}
       for j in range(1000)
   ]
   ```

3. Set `OPENCODE_CONFIG_DIR` to an empty temporary directory and
   `OPENCODE_CONFIG_CONTENT` to a JSON config with an OpenAI-compatible
   provider `test/test` pointing at the HTTP server and an `lsp` entry with
   `command: [python, server_script, "lsp"]` and `extensions: [".py"]`.
   Run `opencode run --dir "$WORK" --format json --model test/test` with a
   prompt requesting the write. Capture and parse every stdout JSONL line.
   Repeat with the installed `diagnostics_plugin.mjs` bytes copied to
   `$OPENCODE_CONFIG_DIR/plugins/diagnostics.js`. Compare the completed
   tool's `state.output` byte-for-byte and inspect the model requests.
4. Repeat each run using `devlegate.worker_supervisor._run_opencode` with
   the same command and environment to exercise the actual transport cap.
   Check every raw line is below 1,048,576 bytes in the filtered run, and
   serialize `state.metadata.diagnostics` as compact UTF-8 JSON to check the
   65,536-byte limit.

Observed on the stated versions: five LSP publications contained 5,000
diagnostics; the baseline diagnostic map was 2,184,651 bytes and the largest
of six JSONL events was 2,220,729 bytes. The transport rejected that event.
The filtered map was 65,536 bytes and the largest of six events was 101,651
bytes; transport succeeded. The 35,339-byte agent-visible tool output was
identical, included LSP errors, and was present in the model's next request.
These numbers depend on temporary path lengths and OpenCode version.

For distribution verification, build using `python -m build --outdir DIST`,
install the resulting wheel and sdist into separate clean virtual environments,
and compare `importlib.resources.files("devlegate").joinpath(
"diagnostics_plugin.mjs").read_bytes()` with the source resource. Intercept
the worker's `_run_opencode` call and compare those bytes with the generated
`OPENCODE_CONFIG_DIR/plugins/diagnostics.js` before the temporary directory is
removed. Both installation forms produced identical 3,124-byte resources and
worker plugin files in the local acceptance run.
