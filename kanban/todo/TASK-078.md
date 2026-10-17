---
"type": "devlegate.ticket"
"title": "Separate Git transport interruption from domain retry policy"
---

## Milestone

Git architecture hardening: complete TASK-073's transport/ownership findings without redesigning the VCS layer.

## Goal

Make Git execution a predictable owner/shutdown-aware transport, and move command-specific retry decisions to the workflow that can prove whether a previous mutation took effect.

## Context

At integrated product `release/0.5.7`, `ServiceEngine._git_runtime()` runs `_git(repo, *args, check=False)` and, after `ShutdownInterrupted` under persisted `merge_pending`, repeats the same arbitrary command. This is a structural risk, not proof that a particular mutation has already been corrupted. Existing WAL, CAS, shutdown and reconciliation checks are authoritative and must be preserved.

## Required behavior

1. Inventory actual callers and command classes that reach the `merge_pending` interruption branch; distinguish local observations, idempotent effects, conditionally repeatable mutations, and nonrepeatable mutations before changing the code.
2. Restrict `_git_runtime()` to owner authorization, shutdown/interruption observation, execution and transparent result/error propagation. Do not automatically rerun an arbitrary command from this transport seam.
3. Where the previous retry exists for a legitimate `merge_pending` recovery reason, retain the required behavior in the owning domain transaction: re-observe durable WAL stage and Git state, prove exact prior effect or its absence, then choose continuation, explicit safe retry, or fail-closed. Never infer absence solely from an interrupted subprocess.
4. Preserve serialized ServiceEngine authority, worker/process shutdown semantics, mutation admission, evidence-before-publication, expected-old/CAS, remote leases, required re-observation, and explicit operator retry barriers.
5. Keep Git first-class; reuse existing typed operations and domain workflows. Do not introduce a VCS abstraction, parallel executor, hidden process launch, or opportunistic unrelated refactoring.
6. Document before/after full executed-command traces for affected interrupted and normal paths, including nested calls and Tier 1/2/3 effects, identifying each newly added or eliminated invocation.

## Acceptance criteria

- `_git_runtime()` contains no mutation-specific or generic retry after an ambiguous interruption; the only permitted retries are explicit, evidence-backed decisions in the owning workflow.
- Read-only and mutation paths enforce the existing owner and shutdown authority. An ambiguous mutation never silently executes twice.
- Normal control sync, publication and recovery behavior stays compatible, including existing durable replay paths and legitimate `merge_pending` completion.
- Focused tests demonstrate commands actually executed, no hidden network/write effects and preserved CAS/TOCTOU checks; any new Git invocation of any tier is itemized and justified.
- Complete pytest, coverage and Ruff pass on the exact implementation checkpoint.

## Required regressions

- Setup: Git read observation interrupted before completion; action: invoke owner runner; expected: no hidden retry and a correctly classified interruption.
- Setup: ref update interrupted before it can mutate; action: run domain recovery; expected: fresh observation allows only an explicitly proven safe continuation.
- Setup: ref update completed but its result was lost to shutdown; action: replay pending transaction; expected: observe the existing exact effect, without a second arbitrary update.
- Setup: `merge_pending` after uncertain merge/push; action: restart; expected: WAL and Git proofs decide resume/stop, never generic duplicate mutation.
- Setup: mutating call outside the service owner or after shutdown admission closes; action: execute Git; expected: rejected with no mutation.
- Setup: active manual-retry barrier; action: restart; expected: it remains authoritative rather than becoming an automatic retry.
