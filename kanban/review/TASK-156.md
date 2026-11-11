---
"type": "devlegate.ticket"
"title": "Repair ticket packaging report-generation provenance and always-visible CLI errors"
"depends_on": ["TASK-155"]
---

## Milestone

Devlegate 0.5.7 self-hosted Arch dogfooding; unblock reliable use of `./dev package arch --ticket TASK-089` when identity is provable, without weakening fail-closed source/owner validation or introducing historical data migrations. This is a post-integration defect in the TASK-155 workflow, not a request to rewrite the done TASK-155 or backlog TASK-089 specifications.

## Goal

Make the producer/consumer contract for execution resource-generation identity coherent and enforceable, and make `./dev package` errors visible in an interactive terminal. Correct the defect causing `--ticket` to return 1 without output, then report `latest execution lacks checkpoint identity` when redirected; do not silently accept unproven checkpoints.

## Context

- On a real Ubuntu developer checkout of integrated `release/0.5.7`, `./dev package arch --ticket TASK-089` exited 1 without stdout/stderr both with the Devlegate service running and after stopping it. `./dev package deb` without `--ticket` succeeded. Installing that Debian package did not change `./dev package` behavior, which uses the checkout's `tools/build_distribution.py` under `.venv`.
- On redirecting stdout/stderr, the observed error was `dev: package: latest execution lacks checkpoint identity`.
- `tools/build_distribution.py::main` catches exceptions, but prints them only when `not sys.stdout.isatty()`; interactive errors are therefore hidden.
- `tools/ticket_source.py::resolve` takes a published-report fallback when no local `ResourceBinding` is available, but rejects if `latest_report.resource_generation` or `workspace_head` is absent. Local test `test_immutable_history_fallback_after_integration_and_retirement` creates an artificial report with generation filled in; it does not cover a real producer/consumer life cycle without surviving bindings.
- Immutable published reports for `TASK-089` executions `a52a7fa96143443ea86fa709c8f094a9` and `34878bd12a3844a7ab4057bffb635c8e`, and `TASK-155` executions `abc34ca2c7f64c7684bef5675855a9d6` and `9ced1b7282bc4758a862dbf68932657a`, have `workspace_head` but **omit** `resource_generation`. This is observed committed evidence. Current source `src/devlegate/runtime.py` appears to set this field via `dataclasses.replace` before checkpoint publication; determine whether these reports were produced by a different/older installed runtime, or an actual publication-path defect, rather than assuming one cause.
- The current `TASK-089` checkpoint is `861511704e8c99f280dd1912843aaa98a7477d14`, parent `efd357942dc8b73812e3ecefa1e66ce4c661323e`. Its commit message retains `Devlegate-Execution: a52a7fa96143443ea86fa709c8f094a9` (the prior execution), whereas the most recent report is `34878bd12a3844a7ab4057bffb635c8e`. That trailer alone is **not** proof of the new generation's ownership.

## Required behavior

1. Trace the actual producer to immutable report publication, including running installed-artifact/source identity and version skew where relevant. Verify whether the currently integrated runtime reliably places the actual 64-hex resource generation in every newly committed, checkpoint-bearing report and whether restart/recovery/blocked paths preserve it. Make a narrow producer fix if a reachable path omits it. Do not rewrite, backfill, or remove existing immutable reports; do not add a general migration/compatibility layer.
2. Keep `--ticket` resolution read-only and fail-closed across live/retired worktrees, local resource receipts, published reports and exact Git objects. A generation-less historical report may be used **only if independent durable evidence actually proves equivalent unique generation and checkpoint attribution**; neither matching branch/head SHA, execution name/trailer, a current ticket status nor a guessed generation suffices. If no independent proof exists, refuse with a diagnostic identifying the failed proof and the selected execution, not a fabricated identity or fallback to an older checkpoint/product HEAD.
3. Ensure complete newly produced execution reports and resource receipts permit the existing documented `--ticket` fallback after normal worktree/branch/binding retirement, or explicitly diagnose a genuine missing authority without weakening retirement checks. Avoid silently selecting an older report when a newer one is unprovable. Preserve existing all-source snapshot, submodule, staged/index and selected-toolchain guarantees from TASK-155.
4. Always emit a concise actionable error to **stderr** for `./dev package` exceptions, regardless of TTY state. No silent exit code 1. Keep exit status nonzero for failures and no stack trace by default; distinguish ticket-source proof refusal from other package build errors in messages. Diagnostics must not leak secrets/private state paths or claim success. Do not change normal successful output semantics.
5. Record the operator recovery path for an older immutable report that genuinely lacks sufficient provenance: do not claim `--ticket` will work merely because the installed runtime was upgraded. Document an isolated *clean* checkout at an independently checked exact checkpoint and the already supported `./dev package arch --repo PATH` as a manual dogfooding path, without treating that package as proof of `--ticket` ownership. Do not touch the real TASK-089 runtime resources or commit historical fixes.

## Acceptance criteria

- A fresh real execution report published through the current Devlegate owner contains the exact generation token corresponding to its actual independently recorded generation intent, including `blocked` worker outcome. Publication, restart and retirement maintain consistent identities and never synthesize a token from SHA/branch.
- An isolated genuine live ticket worktree and the same ticket after retirement produce an unambiguous `--ticket` source; output correctly identifies checkpoint, execution and generation, and remains separate from release output. The prior legitimate protections and clean no-selector / explicit `--repo` packaging still pass.
- For genuinely generation-less legacy evidence with no other independent witness, `--ticket` fails clearly and correctly with the latest execution identified; it does not silently package arbitrary code. The documented exact-commit `--repo` path remains usable for manually authorized testing.
- The original silent-failure reproduction returns a visible error when run with or without TTY and with stdout redirected; running a package build with no `--ticket` is unchanged.
- Focused tests, Ruff and complete exact-checkpoint GitHub Actions CI pass. No new CLI selectors, runtime dependencies, broad distribution redesign or historical migration path.

## Required regressions

- From a disposable real-Git project, admit and complete a **real owner-side** worker execution (including a `blocked` claim), verify committed `executions/T/<id>.json` contains the actual generation and checkpoint and matches independent generation receipt; retire the execution worktree/resources through the normal owner protocol, then select/build the same checkpoint through `--ticket`. Avoid using only an artificial `dataclasses.replace` fixture report.
- Simulate a legacy published report missing `resource_generation` with no local binding: verify explicit fail-closed diagnostic and no worktree/ref/runtime mutation. Simulate old/new publications and show that missing latest identity never causes selection of an older successful report. Include a mismatched generation/report and forged execution trailer.
- Exercise incomplete/blocked/crash/recovery paths as applicable to the proven identity-loss mechanism; assert any newly published report still has a verified token or fails safely before claiming provenance.
- In subprocess/CLI tests, make `sys.stdout.isatty()` return both true and false while triggering a known `--ticket` exception; assert one useful stderr diagnostic and exit 1, without accidental output suppression. Assert normal `./dev package deb` behavior is retained.
- Preserve previous staged-only, gitlink, submodule, Git filter and toolchain-provenance regressions.

## Boundaries

Only change the necessary producer/consumer, developer packaging diagnostics, related fixtures and documentation. No runtime data migration or compatibility scaffold, no historical report mutation, no release/branch/ref integration, no manual rebase, no broad version framework, no bypass of worktree/owner authority, no separate TASK-089 implementation changes. If the only root cause is a stale installed artifact, do not invent a source-code workaround; document the operational resolution and close the code gap through executable regressions and error visibility.
