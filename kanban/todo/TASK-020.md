---
"type": "devlegate.ticket"
"title": "Preserve durable standalone launch identity"
---

## Milestone

Devlegate 0.5.5 standalone/runtime hardening.

## Goal

Ensure that a Devlegate standalone executable always relaunches the installed
outer product artifact, never the extracted/bundled CPython interpreter used
internally by PEX/scie.

Systemd units and every other persisted relaunch boundary must serialize a durable
Devlegate product identity. Internal cache/extraction paths are implementation
details and must never become the installed service command.

## Context

Dogfooding after TASK-011 exposed a regression in the current standalone launcher
selection.

A newly rendered managed systemd unit contained an ExecStart equivalent to:

```text
/home/daniil/.cache/nce/.../python/bin/python3.12 -P -m devlegate --env ... foreground
```

Starting the unit failed immediately:

```text
python3.12: No module named devlegate
```

The path is wrong even independently of the import failure: it points into the
scie/NCE extraction cache rather than to the durable installed standalone
executable.

The regression is related to launch-identity hardening added while fixing worker
environment contamination. `product_launcher()` now accepts the outer standalone
identity only when PEX/scie metadata also satisfies an extra
`SCIE_ARGV0 == sys.argv[0]`-style identity check. That assumption is not valid
for all PEX/scie execution modes. A real standalone process can therefore fall
through to `LaunchCommand.current_python()`, whose `sys.executable` is the
bundled cached CPython.

Worker-boundary sanitation added by TASK-011 is still required. A nested/source
Devlegate started by a worker must not inherit the parent standalone's PEX/scie
identity and relaunch the wrong outer executable.

The distribution proof currently records the generated systemd ExecStart but does
not fail when it resolves to the bundled Python instead of the outer artifact.

## Required behavior

- When running from the supported standalone artifact, `product_launcher()`
  returns the durable outer Devlegate executable as the relaunch command.
- The standalone relaunch identity is independent of the extracted CPython path,
  PEX_ROOT/NCE cache location, and the value of `sys.executable`.
- A managed systemd unit rendered by a standalone Devlegate uses the installed
  outer Devlegate executable directly in `ExecStart`.
- No generated persistent relaunch command for a standalone installation may
  contain the bundled/extracted Python interpreter path followed by
  `-m devlegate`.
- Source/wheel/development execution continues to use an appropriate Python
  relaunch form such as `<python> -P -m devlegate` where that is the actual
  product form.
- Worker subprocesses continue to remove inherited PEX/scie/service-host metadata
  so nested Devlegate processes cannot mistake a parent standalone executable for
  their own launch identity.
- Fix the launcher identity model itself. Do not special-case the observed cache
  path or add a version-specific migration.
- The selected relaunch command must remain stable enough to persist in systemd
  and other durable host configuration until the installed product location
  itself changes.

## Acceptance criteria

- Running the current standalone executable and rendering its systemd unit yields
  an `ExecStart` whose executable is the exact outer standalone artifact used to
  invoke Devlegate.
- Starting that generated unit succeeds without host Python, PYTHONPATH, a venv,
  or access to a Devlegate module outside the standalone artifact.
- Removing or changing the NCE/PEX extraction cache does not invalidate the
  persisted systemd command as long as the installed outer executable remains.
- A source/development invocation still produces a valid Python/module relaunch
  command and does not falsely claim a standalone identity.
- A nested Devlegate launched under WorkerSupervisor does not inherit the outer
  standalone launch identity.
- Full tests, lint, standalone proof, and distribution validation are green.

## Required regressions

- Standalone launcher selection returns the outer executable even when
  `sys.argv[0]` does not equal the outer scie path in an interpreter/tool mode
  representative of the supported artifact.
- Standalone systemd rendering asserts the exact outer executable in
  `ExecStart`; merely recording the rendered value is insufficient.
- Starting a generated standalone systemd command (or an equivalent isolated
  subprocess proof) loads Devlegate successfully with no host-installed package.
- Source/wheel launcher selection retains the Python `-P -m devlegate` form.
- WorkerSupervisor sanitation prevents inherited `PEX`, `SCIE`,
  `SCIE_ARGV0`, and related packaging metadata from selecting a parent
  standalone executable.


## Review continuation after execution 0427f7cd6c264097b0899efca9d5d83e

Checkpoint `03058e7f5927cc144d6a1744cee5c5c317e3abd5` is semantically
promising and should be continued without redesign.

Confirmed:

- launcher selection no longer depends on `sys.argv[0]` when validated
  `PEX == SCIE` metadata identifies an executable ELF outer artifact;
- source/development fallback remains `<python> -P -m devlegate`;
- WorkerSupervisor now strips the broader inherited PEX/SCIE metadata families;
- the standalone proof now fails unless rendered systemd `ExecStart` starts with
  the exact outer artifact path;
- exact-head CI ran the full test suite successfully: `993 passed, 1 skipped`.

Two completion gates remain.

### 1. Fix the exact-head Ruff failure

CI failed only at lint:

```text
E501 Line too long (89 > 88)
tests/test_launcher.py:41:89
```

Wrap/rename that test declaration without changing its semantics and rerun exact-head
CI.

### 2. Execute the authentic standalone proof/distribution validation

TASK-020 explicitly requires the standalone proof, not only unit-level launcher and
systemd tests. The normal CI workflow does not run
`tools/build_standalone.py build/prove`, and the execution report mentions focused
launcher/worker/systemd tests only.

Build the current standalone artifact and run the existing standalone proof against
it, including the strengthened exact-outer-`ExecStart` assertion. Run the relevant
standalone package/distribution validation required by the ticket. Record the exact
result in the worker report.

Do not change the launcher design merely to address these review comments. If the
authentic standalone proof exposes a real defect, fix that defect narrowly; otherwise
retain the current implementation.

Once Ruff is green, exact-head CI is green, and the authentic standalone
proof/distribution validation succeeds, TASK-020 should be ready for acceptance.


## Review continuation after execution e807ba8e05404011a17cc97ec4996a3e

This incomplete handoff is healthy and should continue from checkpoint
`8db5589c35b646b35d216a285f8061b5c2277553` without redesign.

Confirmed progress:

- the previous E501 failure is fixed without changing launcher semantics;
- exact-head CI is now fully green: `993 passed, 1 skipped`, coverage generated at
  79%, and Ruff reports `All checks passed!`;
- a real standalone artifact was built successfully;
- the real artifact exposes the expected standalone identity metadata;
- the strengthened standalone proof verifies that generated systemd `ExecStart`
  selects the exact outer standalone executable rather than bundled CPython.

The task remains incomplete because the authentic lifecycle proof does not complete.

### 1. Diagnose and finish the generated-service lifecycle proof

`tools/build_standalone.py prove` currently reaches the generated standalone service
start and then hangs in the worker environment.

Do not bypass, mock away, or remove this lifecycle part: TASK-020 explicitly requires
proof that the persisted outer-artifact command can actually relaunch Devlegate
without host Python/module installation.

Determine whether the hang is:

- a real standalone/service-launch defect introduced or exposed by the launcher
  identity change; or
- a defect/assumption in the proof harness or its isolated environment.

Fix only the responsible layer. Preserve the current launcher design unless the
authentic proof demonstrates that it is wrong.

The completed proof must demonstrate start/readiness and subsequent lifecycle
operation using the generated outer-artifact launch command, not merely successful
unit rendering.

### 2. Finish standalone package/distribution validation

The execution also reports that the Python `build` module is unavailable in its
local command environment. That is a build-environment issue, not a reason to weaken
the distribution gate.

Use the repository-supported build/development environment (or another isolated
build environment that satisfies the repository's declared build dependencies) and
run the relevant standalone package/distribution validation on the same candidate.
Do not add `build` or other packaging tools as Devlegate runtime dependencies.

Record the exact successful standalone proof and package/distribution validation
results in the next worker report.

No additional ordinary unit-test or lint work is currently known to be needed;
exact-head CI at this checkpoint is already green.
