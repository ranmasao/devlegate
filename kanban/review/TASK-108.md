---
"type": "devlegate.ticket"
"title": "Restore owner/shutdown Git runner boundary for runtime effects"
"depends_on": ["TASK-106"]
---

## Goal

Close the observed Git authority escape hatch in ServiceEngine runtime workflows. `_persist_product_reconciliation` still resolves and pins evidence using `runner=_git` despite an owner-aware `self._git_runtime` seam. The architecture audit also identified direct runners in checkpoint publication, accepted integration, lifecycle and drop cleanup. Audit **all** direct `runner=_git` (including equivalent wrappers and direct `_git(...)`) in `src/devlegate/runtime.py` at current integrated HEAD, not only one example. This is a bounded owner/shutdown contract hardening ticket, **not** broad Git/workflow refactoring.

## Work

1. Build a complete call-site inventory: path/function, command/effect tier, state/owner admission, whether runtime-only or independently allowed preflight, and why it does or does not require `self._git_runtime`. Treat observations and state changes separately; show actual helper-internal subprocesses.
2. Replace unsafe runtime effect/observation bypasses with established owner-aware runner/typed semantic helpers. Preserve standalone inspection/preflight intentionally permitted outside engine authority; explicitly justify any retained direct transport, and never create a second default runtime runner.
3. Specify and preserve interruption semantics for each call and surrounding multi-command transaction. In particular distinguish shutdown *before* a Git command (must prevent unauthorized spawn) from shutdown/interruption *during or after* an ambiguous push, ref pin or cleanup (must preserve WAL/evidence and re-observe on safe re-entry). No blind retry or accidental new `stop` gate after an irreversible effect.
4. Preserve exact evidence ref identity, pin-before-reconciliation-state, existing CAS/lease requirements, worktree proof, control/product publication, recovery, owner-thread enforcement and current domain error mapping. Do not introduce additional Git commands, hidden fetches or mutation side effects without explicit necessity and accounting.
5. Test negative wrong-thread and shutdown-before-command cases through actual ServiceEngine runner seam (assert no Git subprocess/effect); add narrowly focused crash/ambiguous-outcome proof tests for each materially changed publication or cleanup boundary. Use existing tests rather than duplicate entire suites.
6. Produce before/after executed-command/effect ledger by workflow and call-site plus reasoned exceptions. Run targeted pytest and Ruff; full pytest/coverage and Ruff belong to exact checkpoint GitHub CI. No full local suite by default.

## Acceptance

Every runtime-owned Git subprocess (including nested typed helpers) either demonstrably uses owner/shutdown-aware capability or is an explicitly proven safe exception; wrong-thread / pre-command shutdown cannot mutate or invoke Git. Interruption after ambiguous effects remains replayable and fail-closed. No unintended change in command count/effect ordering or WAL/publication safety. All focused regressions and exact-checkpoint CI green. Review against source, not grep count alone.

## Scope restraint

Do not rewrite `_run_iteration_body`, combine checkpoint/recovery state machines, introduce generic VCS abstractions, or perform unrelated runtime decomposition. Report any unfixable architectural exception as `incomplete` with a focused follow-up recommendation; do not weaken safeguards to satisfy a textual zero-count.
