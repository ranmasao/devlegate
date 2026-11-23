---
"type": "devlegate.ticket"
"title": "Clarify reset disposition and post-reset worker scheduling"
---

## Milestone and scheduling

Devlegate 0.5.7 — self-hosting operator workflow refinement, to be revisited during the current iteration. **Backlog until an explicit operator/Architect decision on semantics.** Do not schedule, automatically requeue or implement this ticket before that decision; creating it must not unblock or start any worker.

## Problem and observed incident

On 2026-11-23 the operator ran `devlegate reset TASK-056` to discard the accumulated execution and planned to refine the canonical TASK-056 text before permitting another worker. Reset successfully retired the prior generation, then published a control update, moved TASK-056 to `kanban/todo` and immediately activated scheduling. The service started another execution before the operator could request the ticket edit.

In the current implementation, `ticket_reset.py` always publishes the destination `kanban/todo/<ticket>.md` during the `requeue` stage (unless already there), then records `queued` and calls `owner.wake()`. The behavior is consistent with the existing implementation, but it conflates two distinct operator intentions:

1. **Reset the ticket's execution**: safely cancel/retire the old worker and owned resources, discard the prior attempt's continuation authority, preserve the canonical ticket and historical audit evidence, and make the ticket eligible for a later fresh start.
2. **Authorize immediate scheduling** of a new execution.

A reset operation does not always imply that the operator wants the second action. Unwanted early admission can bind a stale specification and require another destructive reset. This is a lifecycle/control-policy issue, **not merely a CLI or journal wording defect**.

## Design decision required before implementation

Determine the simplest operator-facing semantics that let the operator reset a ticket and, when needed, modify or inspect it *before* another worker is admitted. Avoid introducing mandatory options or complicated control flags without demonstrating that simpler behavior is insufficient.

Evaluate at least these alternatives against actual user workflows:

- **Existing behavior:** every reset returns the ticket to `todo` and makes it immediately runnable.
- **Reset into backlog:** explicitly or by policy, leave the ticket in `backlog` until the operator moves it to `todo`.
- **Preserve prior workflow state:** distinguish resetting an execution from changing ticket scheduling; establish which prior states are meaningful/safe to retain.
- **Hold / deferred activation:** separate resetting the old execution from allowing a fresh one, without an additional ticket move; assess the extra persistent state, crash recovery and operator complexity.
- **Optional CLI destination/mode:** e.g. `--to todo|backlog`, only if justified. Decide whether it is necessary at all; **do not choose its default yet**.

Compare outcomes for a reset initiated from `review`, `todo` or `backlog`, both with and without an active worker, and the common case where the operator simply wants a new execution immediately. Define the default using the least surprising operator behavior and smallest adequate interface, and document any genuinely necessary explicit control.

The chosen policy must distinguish a reset request/acknowledgement, proven retirement of the old execution and authorization for a new worker. The requeue/disposition and any scheduling hold must remain durable and replayable after restart; no transient in-memory or CLI-only "do not start yet" flag that is lost at the next owner cycle.

## Functional boundaries for the eventual implementation

- Retain existing owner-controlled reset admission, exact old-generation/containment retirement, fail-closed handling of ambiguous ownership and runtime-store writes, reset audit artifacts and historical execution/report boundaries.
- A requested **non-runnable** post-reset state must prevent a fresh worker across the control-publication, runtime-state and scheduler boundary, including normal wake-ups, service restart and reset replay. Never introduce a window where a ticket can be automatically admitted before the non-runnable disposition becomes authoritative.
- A requested **runnable** outcome must permit the normal existing scheduler without an unnecessary second operator action, if that is the selected policy.
- Reject or reconcile competing reset, stop, retry and ticket-stage changes deterministically; do not overwrite an independently updated canonical ticket or silently switch the chosen disposition.
- Do not turn reset into ordinary REWORK/RESUME, lose provenance, create concurrent workers, or imply that the abandoned attempt completed normally.
- Preserve the existing ticket ID and canonical content unless the operator separately requests an edit. Do not add data migrations or a compatibility architecture; there is no external customer base.
- Keep reset UI/journal wording changes in the existing **TASK-157 / UX-002** backlog observation. This ticket owns post-reset disposition/admission semantics only; it may coordinate with UX-002 after the policy is decided.

## Acceptance and regressions (after policy selection)

1. Show an explicit documented decision table for source state, chosen operation/mode, resulting canonical state and whether a worker is immediately runnable. The default must be justified from operator workflows, not arbitrarily chosen for implementation convenience.
2. Reproduce the TASK-056 incident in a disposable service: reset a ticket that needs editing, ensure the selected non-runnable path leaves time for the operator's change and **does not launch a new worker**, even with an already running scheduler.
3. Verify a normal "reset and immediately rerun" workflow remains simple and correct.
4. Exercise resets from `todo`, `review` and `backlog`, including active and absent workers, unknown ticket IDs, rejected admissions and control-lineage changes.
5. Inject crashes/restarts before and after old containment retirement, control ticket-state publication, reset-state persistence and scheduler wake. Assert no premature/duplicate new worker, no lost intended disposition, no unproven retirement and idempotent replay.
6. Run existing reset/worker/retry/lifecycle suites and authoritative CI/Ruff without weakening the owner and resource-generation safety guarantees.

## Current status

**Decision backlog, not an implementation authorization.** The original default policy, necessity and spelling of any new CLI option, and any persistent scheduling hold are deliberately unresolved. Revisit with the operator in this iteration before moving to `todo`. TASK-056 is a motivating incident, not a dependency or part of this ticket's scope.
