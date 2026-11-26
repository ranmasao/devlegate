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

The current product at e57f5a79 also has ticket-ID-only predicates in runtime.py _record_worker_identity (queued -> started), the fresh reset admission/configuration binding before workspace preparation, and the post-worker queued launch-failure/fast-exit branches. ticket_reset.admit acknowledges a started reset by fresh execution ID and configuration; ResetStop.kind and the post-worker cancellation path consult active reset and ticket ID. Inspect these together, distinguishing an unbound queued scheduling intent from a bound fresh execution and an admitted prior-execution cancellation. inspection.inspect_object uses ticket_reset.response, which currently presents any non-null blocker as blocked, including historical started records. These are concrete audit locations, not a requirement to rewrite each function or assume every path is independently reachable with wrong identity.

## Required behavior

- Define the narrow operational boundary of an in-progress reset, its queued fresh admission, and its completed started record. Match the operation and the relevant prior/fresh execution or generation at each reset-specific effect, progress update, interruption, acknowledgement and diagnostic. Ticket ID alone is not sufficient authority. Once started is durably established, do not reattach unrelated execution failures or retroactively change its completion outcome.
- Keep the existing stage meanings: stopping-worker/retiring-old-generation/requeue own the reset transaction; queued represents a pending fresh start, initially without a fresh execution identity; started is the completed reset operation, not a statement that the fresh worker completed successfully. Use existing operation, prior execution, fresh execution and generation fields, not a second state machine. Establish the fresh binding only at the legitimate admission boundary; subsequent progress must match it. Never silently overwrite a bound fresh execution/generation merely because the ticket ID matches. Preserve any already-supported legitimate pre-launch continuation using existing ownership proofs; an inconsistent active binding must fail safely, not attach to an unrelated execution. Keep existing configuration-at-admission and lost-ack semantics.
- A worker retirement failure remains a failure of that execution, with its exact ownership and containment evidence. Retain existing safe refusal and recovery behavior. Do not make retirement succeed by clearing a reset record, clearing worker identity, or weakening any resource proof.
- A genuine in-progress reset or queued fresh launch must still receive the appropriate blocker and resume safely after interruption. Preserve idempotent acknowledgement of the reset that started the currently owned fresh worker. A subsequent explicitly admitted reset is a distinct operation, not a reactivation of the old one.
- Check reset-related status/inspection/retry/recovery and cleanup consumers for stale operational authority or misleading attribution. Make the smallest coherent correction; keep historical evidence available and avoid new duplicate state, a new lifecycle framework or broad presentation changes.
- If a retained started record already contains a stale blocker (the observed dogfooding shape), do not present it as an active reset failure or admission barrier. Preserve historical stored evidence and discard boundaries; do not rewrite records on read or implement a migration. Distinguish historical reset information from current execution diagnostics using the existing view where possible. Do not change the actual reset/worker state to make a display assertion pass.
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
4. Distinguish an initially unbound queued reset from an already bound queued reset. Exercise matching fresh identity, mismatched execution/generation for the same ticket, normal PID-capture start and the fast-exit-before-capture path. Assert only legitimate matching progress can transition queued to started or attach a reset-specific blocker; no unrelated identity adoption. Check that completed-reset acknowledgement still works for the actual fresh admission, and a later explicit reset can establish a distinct operation under the existing policy.
5. Include the observed stale started + non-null blocker shape in an inspection/status regression. Verify read-only presentation does not imply an active reset failure, durable evidence is unchanged, and reset_boundaries still exclude discarded reports from continuation. Do not require compatibility migration or special persisted repair code.
6. Cover stale record consumers in status/inspection/recovery/cleanup and existing reset and worker retirement regressions. Preserve durable historical artifacts and ownership proofs. Run relevant focused tests and full hosted CI.
