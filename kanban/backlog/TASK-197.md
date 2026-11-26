---
"type": "devlegate.ticket"
"title": "Keep completed ticket-reset operations separate from later worker failures"
"depends_on": ["TASK-192"]
---

## Milestone

Devlegate 0.5.8 — deterministic recovery and operation ownership.

## Goal

A completed ticket reset must remain historical evidence, not acquire new authority or a new blocker when a later execution of the same ticket encounters a worker launch, retirement or recovery problem.

## Context

During TASK-056 self-hosting a previously completed reset operation 51e04ffd237ca31ab244816331345cf9 (fresh execution f30172c711324233955998961827bb1d) was retained in runtime state as status started, retirement completed. A later worker retirement failure attached the blocker "worker retirement is unproven" to that old operation. The stale record remained present during the subsequent TASK-194 REWORK recovery on 2026-11-26. The record's historical admission, control transaction, retirement and fresh execution identity are meaningful evidence; retaining it is not itself a defect.

In runtime.py, the post-worker branch for not worker_run.worker_group_retired updates ticket_reset and emits reset diagnostics on ticket ID equality alone. ticket_reset.active() already excludes queued and started, but the post-worker branch does not use a stage- and execution-appropriate predicate. Audit other readers/writers of ticket_reset for the same identity/lifecycle confusion. TASK-192 improves worker retirement itself; an actual unproved retirement must still refuse unsafe continuation regardless of reset history.

## Required behavior

- Define the narrow operational boundary of an in-progress reset, its queued fresh admission, and its completed started record. Match the operation and the relevant prior/fresh execution or generation at each reset-specific effect, progress update, interruption, acknowledgement and diagnostic. Ticket ID alone is not sufficient authority. Once started is durably established, do not reattach unrelated execution failures or retroactively change its completion outcome.
- A worker retirement failure remains a failure of that execution, with its exact ownership and containment evidence. Retain existing safe refusal and recovery behavior. Do not make retirement succeed by clearing a reset record, clearing worker identity, or weakening any resource proof.
- A genuine in-progress reset or queued fresh launch must still receive the appropriate blocker and resume safely after interruption. Preserve idempotent acknowledgement of the reset that started the currently owned fresh worker. A subsequent explicitly admitted reset is a distinct operation, not a reactivation of the old one.
- Check reset-related status/inspection/retry/recovery and cleanup consumers for stale operational authority or misleading attribution. Make the smallest coherent correction; keep historical evidence available and avoid new duplicate state, a new lifecycle framework or broad presentation changes.
- No new CLI commands, migration paths, repair of actual self-hosting state, reconciliation redesign or changes to reset destination policy (TASK-189). Do not alter already published reset artifacts. If local historical correction is necessary, describe it separately, not as a product migration.

## Acceptance criteria

- A terminal started reset cannot receive a blocker or trigger reset-specific handling solely because a subsequent worker has the same ticket ID.
- A later failed or unproved worker retirement is reported and handled as that worker's own problem; no unsafe admission, cleanup or false retirement.
- Legitimate pending reset and queued launch behavior, durable operation identity, restart and acknowledgement semantics remain correct.
- Focused regressions, lint, diff check and exact-checkpoint hosted CI pass.

## Required regressions

1. Exercise worker retirement refusal without any reset record and with a historical started reset of the same ticket but different execution/generation. Compare ordinary failure/ownership behavior, assert historical record unchanged and no misleading reset-blocked event.
2. Establish a reset through real lifecycle transitions, including fresh execution start, then fail retirement of that execution and of a later retry/new execution. Assert completed reset evidence is not mutated; current failure remains visible and safe. Include restart with a retained started record and a different ticket's worker.
3. Exercise genuine reset in progress and queued fresh launch failure/retirement ambiguity, including interruption/restart and repeated command acknowledgement. Assert the correct operation receives a blocker, and no duplicate reset or premature worker admission occurs.
4. Cover stale record consumers in status/inspection/recovery/cleanup and existing reset and worker retirement regressions. Preserve durable historical artifacts and ownership proofs. Run relevant focused tests and full hosted CI.
