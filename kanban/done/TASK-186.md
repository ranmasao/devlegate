---
"type": "devlegate.ticket"
"title": "Handle broken stdout pipes without Python shutdown diagnostics"
---

## Milestone and scheduling

**Confirmed CLI runtime bug; mandatory fix in the current Devlegate 0.5.8 iteration.** Keep in `backlog` until explicitly scheduled, but do not defer this defect as optional post-0.5.8 polish. This maintenance ticket is independent of TASK-133, TASK-184 and reset generation retirement. Do not modify their resources or runtime state.

## Incident and root cause

On the self-hosted Arch installation, the operator ran:

```sh
devlegate status --json | jq '{client: .client.version, service: .service.version}'
```

The downstream `jq` executable was absent. Bash reported `jq: command not found`, and the Devlegate Python producer emitted:

```text
Exception ignored in: <_io.TextIOWrapper name='<stdout>' mode='w' encoding='utf-8'>
BrokenPipeError: [Errno 32] Broken pipe
```

A missing downstream executable is one reproducible trigger, not a Devlegate dependency or justification for installing `jq`. Early-closing valid consumers (`head`, etc.) can trigger the same problem. In the current product, `src/devlegate/output.py:emit()` calls `print()` and the `src/devlegate/cli.py:main()` entrypoint does not handle `BrokenPipeError`. Buffered stdout can fail only during interpreter finalization, after the command has otherwise returned; simply catching around `print()` does not address that case. Both the console-script entrypoint and `python -m devlegate` use `main()`.

## Required behavior

1. Treat downstream stdout `EPIPE` as a controlled CLI output termination, not a traceback, an `Exception ignored in` shutdown warning or a product/runtime-state failure. Do not claim that the upstream command's result or the entire pipeline was successfully delivered.
2. Establish an explicit, common CLI entrypoint/output boundary that catches `BrokenPipeError` raised during actual output and from an explicit final `sys.stdout.flush()`. Ensure normal finite invocations with valid output preserve their existing exit codes and machine-readable bytes.
3. After detecting a broken stdout pipe, prevent a second `EPIPE` on interpreter shutdown without printing to the closed pipe; limit any stdout redirection/disposal to the affected invocation and avoid masking unrelated I/O failures.
4. Use a deterministic documented process exit status for broken stdout, preferably `141` (`128 + SIGPIPE`), and test it explicitly. Do not install global `SIGPIPE = SIG_DFL` in shared runtime paths, suppress all `OSError` exceptions, swallow stderr failures, or alter worker/service process and signal ownership.
5. Cover CLI commands that use the common formatter (including `status --json`, `version --json`, and plain text output) and both supported entrypoints. Handle early downstream termination without requiring `jq`, Python third-party packages, or external consumer installation. Do not make this a generic logging, shell or IPC redesign.

## Acceptance and regressions

- Subprocess-based regression with a reader that closes its pipe before the command finishes, including (a) an error during output and (b) buffered output whose error otherwise appears only in the interpreter's final flush. No `BrokenPipeError`, `Exception ignored in` or unexpected traceback on stderr; the documented producer exit status is observed.
- Normal piping into a fully consuming reader retains byte-identical JSON/plain text and existing CLI return status. Genuine command errors (e.g. invalid project state) remain nonzero and visible on stderr.
- A missing downstream executable remains a shell error for the missing command; Devlegate handles only its own broken stdout and does not silently treat the pipeline as fully successful.
- Focused CLI/entrypoint tests, `./dev lint`, `git diff --check` and full hosted CI on the exact implementation checkpoint pass. Preserve existing CLI usability, service/worker isolation, stdlib-only runtime and all durable state/provenance rules.

## Scope

Small CLI output/entrypoint bug fix for the current 0.5.8 iteration. No runtime data migrations, new dependencies, change to control-plane records, rework of TASK-133, or broad refactor.
