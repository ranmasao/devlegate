---
"type": "devlegate.ticket"
"title": "Fail closed on recovery Git observation and evidence races"
"depends_on": ["TASK-078"]
---

## Milestone

Git architecture hardening: recovery semantics and evidence durability for TASK-073.

## Goal

Ensure recovery distinguishes absent refs/objects from Git observation failures, and preserves evidence creation/CAS authority when racing or replaying control-lineage repair.

## Context

`recovery_transactions.reconcile_control_lineage()` currently treats every nonzero `rev-parse --verify` of its evidence ref as absence before attempting `update-ref`. `recovery_workspace.py` also performs direct Git observations and mutations. A direct `run_git` call is not intrinsically unsafe under an authorized transaction; prove any owner/shutdown gap before fixing it.

## Required behavior

1. Inventory direct Git call paths in `recovery_transactions.py` and `recovery_workspace.py`, including upstream authorization, lock, runner and shutdown behavior; distinguish proven risks from safely owner-held operations.
2. Use typed ref/object observations or narrowly extend `git.py` so valid absence, malformed result, corrupt/unreadable repository, OS/transport failure and wrong object identity are distinct outcomes.
3. Preserve distinct create-if-absent, expected-old/CAS, and unconditional-update primitives; no replacement of guarded updates by unconditional writes.
4. On evidence creation race, re-observe exact ref identity under the existing admission constraints. Accept an already-present exact pin only when proof matches; fail closed on conflict, unreadability or ambiguous outcomes.
5. Repeat required lineage/remote observations at the established mutation boundaries, preserve the evidence-before-control-reset ordering, and avoid repair effects after failed admission/observation.
6. Use owner-bound transport where the actual runtime path requires it, without introducing speculative new frameworks or changing standalone inspection contracts. Trace full executed commands and classify changed effects.

## Acceptance criteria

- An observation error cannot authorize evidence creation, reset, repair or worktree removal; absent ref and repository failure are distinguishable at the domain boundary.
- Replay and ref races preserve displaced evidence and never replace a conflicting evidence ref.
- No owner/shutdown bypass is introduced or left unexamined in the scoped mutation paths; transaction locking, WAL, leases and CAS remain effective.
- All new Git invocations (including local reads) have explicit before/after trace and justification, with no hidden Tier 3 write or remote round trip.
- Exact-checkpoint complete pytest, coverage and Ruff succeed.

## Required regressions

- Setup: evidence ref truly absent; action: reconcile control lineage; expected: guarded create-if-absent followed by required revalidation and adoption.
- Setup: evidence ref already contains exact displaced head; action: replay; expected: idempotent proof without overwriting evidence.
- Setup: evidence ref points elsewhere; action: replay; expected: fail closed without control reset.
- Setup: repository unreadable, corrupt, malformed, or Git execution fails during evidence lookup; action: reconcile; expected: observation failure, no create/reset.
- Setup: another writer creates evidence concurrently; action: guarded create loses the race; expected: re-observe exact ref and either prove identical success or stop.
- Setup: evidence pinned but worktree reset interrupted or failed; action: restart recovery; expected: preserve pin, reprove exact target and recover or block safely.
- Setup: unauthorized or interrupted recovery mutation; action: execute; expected: no authority bypass or unproven replay.
