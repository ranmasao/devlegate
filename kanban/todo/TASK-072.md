---
"type": "devlegate.ticket"
"title": "Extract Git mutation and publication mechanics from runtime"
"depends_on": ["TASK-071"]
---

## Goal

Finish the layered Git-specific extraction started by TASK-070/071: `ServiceEngine` orchestrates workflow policy, state and ownership, while Git command construction and output decoding live in cohesive Git infrastructure or domain workflow modules. Do not design a hypothetical multi-VCS interface.

## Work

1. Inventory all remaining direct Git mutation/transport calls in `runtime.py`: `update-ref`, fetch/push, branch/worktree management, commit/add/reset/merge, evidence pinning and publication/recovery sequences. Document each call's preconditions, owner lock, WAL stage, lease/expected-old, re-observation and error semantics.
2. Consolidate actual common Git mutations as *explicit* typed operations, especially create-if-absent ref vs compare-and-swap vs unconditional update. Do not hide an unguarded update behind an innocuous generic name. Keep distinct domain validation and policy outside low-level Git helpers.
3. Extract cohesive control/product synchronization, publication, evidence, or lifecycle Git command sequences into existing specialized modules or minimal new modules only where doing so clarifies responsibility. Preserve serialized state mutation in ServiceEngine and owner admission, provenance/evidence-before-publish, interruption handling, crash replay and CAS boundaries.
4. Remove raw Git subcommand construction from `runtime.py`; callers should use semantic Git operations or domain functions. Do not just create one-line forwarding methods, broaden Protocols or increase indirection to meet a textual target. A minimal owner-bound execution seam may remain solely for owner/shutdown enforcement, but it must not host scattered Git command construction.
5. Reuse existing regression coverage; add only focused tests for newly factored primitives and previously untested invariants. Validate full pytest and Ruff on the exact checkpoint.

## Explicit effects and subprocess accounting

- Each Git mutation helper must declare its actual effects and the Git subprocesses it executes. In particular, an apparently read-only method must never conceal `fetch`, `update-ref`, `push`, branch changes, cleanup, or worktree mutation.
- Compare command traces for representative flows (control/product sync, accepted integration, evidence pinning, retry and recovery) before and after. Keep intentional re-observation and CAS checks. Any extra Git invocation, network access or side effect requires a concrete safety rationale and explicit curator review; no unreported `N+1` Git calls hidden behind helpers.
- Route Git CLI launches through the established transport seam (including owner/shutdown-aware variants); do not introduce scattered new direct `subprocess` Git invocations, hidden shell commands or background launches. Use small focused spies/tests instead of a large instrumentation framework.

## Acceptance

Every original raw Git call in `runtime.py` is accounted for in a before/after inventory. Zero scattered raw Git command calls remain in the main runtime; if one owner-bound executor remains, justify that safety boundary explicitly. Report actual removed duplication, changed module edges, LOC and direct-call counts without mistaking renamed call sites for fewer subprocess executions. No workflow behavior or ownership weakening; no unaccounted increases in executed Git commands or side effects; exact-checkpoint CI green.
