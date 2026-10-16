---
"type": "devlegate.ticket"
"title": "Extract Git mutation and publication mechanics from runtime"
"depends_on": ["TASK-071", "TASK-077"]
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
- Compare command traces for representative flows (control/product sync, accepted integration, evidence pinning, retry and recovery) before and after. Keep intentional re-observation and CAS checks. Document and cost-classify every extra Git invocation. Modest justified extra local-only reads may be acceptable; additional remote reads require clear latency/round-trip rationale; any new state-changing command requires exact safety justification and curator review. No hidden `N+1` Git calls.
- Route Git CLI launches through the established transport seam (including owner/shutdown-aware variants); do not introduce scattered new direct `subprocess` Git invocations, hidden shell commands or background launches. Use small focused spies/tests instead of a large instrumentation framework.

## Git execution cost and effect classes

Track newly introduced *executed commands* by **actual effects**, not just by Git subcommand name or number of helper calls:

- **Tier 1 — local-only read:** no remote access, no persistent repository/worktree mutation (e.g. `rev-parse`, `rev-list`, `merge-base`, `cat-file` in read-only modes). Lowest priority for elimination relative to network and mutation; nevertheless, an extra invocation is not made acceptable simply by being local. Avoid per-object `N+1` loops and expensive history scans.
- **Tier 2 — remote read:** accesses the remote but does not mutate repository/worktree state, e.g. `ls-remote`. Higher priority for elimination than local reads; extra network round trips are not an acceptable trade-off merely because a helper is cleaner.
- **Tier 3 — state-changing operation:** mutates local or remote Git state, including refs/index/worktree/object store; e.g. `update-ref`, `add`, `commit`, `reset`, `merge`, `push` and ordinary `fetch` (remote read **plus local object/ref writes**). Highest priority for eliminating newly introduced calls due to side effects, durability, and recovery/authorization implications. Identify *local versus remote* writes separately.

A command can have mixed effects; classify by the **highest applicable tier**, not its apparent name. Some observations such as `status` may refresh the index depending on how invoked; check actual options/side effects. **These tiers are an order for eliminating unwanted new calls: state-changing first, remote read second, local-only read third. They are not a cost budget, an allowance to add calls, or numeric weights.** An added invocation of any tier is a potential regression requiring a before/after trace and concrete rationale. Do not accept more local reads simply because they are cheap. If a genuinely indispensable safety verification introduces a command, make it visible for explicit curator review; never hide new network operations or mutations in read helpers. Preserve intentional race/CAS checks; do not sacrifice safety simply to reduce process counts.

## Acceptance

Every original raw Git call in `runtime.py` is accounted for in a before/after inventory. Zero scattered raw Git command calls remain in the main runtime; if one owner-bound executor remains, justify that safety boundary explicitly. Report actual removed duplication, changed module edges, LOC and direct-call counts without mistaking renamed call sites for fewer subprocess executions. No workflow behavior or ownership weakening; per-tier before/after executed-command traces, no hidden effects and no automatic acceptance of extra calls at any tier; exact-checkpoint CI green.
