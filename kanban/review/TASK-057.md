---
"type": "devlegate.ticket"
"title": "Preserve continuation intent across forced service restart"
---

## Milestone

Lifecycle continuation semantics.

## Goal

Distinguish the mechanism used to interrupt a running worker from the operator or
system intent that determines whether work should continue automatically afterward.

A forced service restart must be able to retire the current worker, restart the
service, prove the old worker is gone, and continue the interrupted ticket
automatically when the preserved state is safe.

A deliberate operator stop/abort must continue to require explicit retry.

## Motivation

Current behavior after:

    devlegate @project restart --force

is:

- lifecycle restart is accepted;
- active worker is interrupted;
- execution checkpoint/workspace is preserved;
- interruption is normalized as `operator_abort`;
- service restarts successfully;
- the interrupted execution is marked retryable;
- automatic resume is suppressed because all `operator_abort` cases require
  explicit retry.

Observed result:

    interrupted execution for LAB-169 is not eligible for automatic resume;
    explicit retry is required

This loses the higher-level continuation intent.

The same low-level worker interruption mechanism can correspond to different
operator/system intentions:

    stop --force
        -> stop work; do not restart automatically

    restart --force
        -> retire current worker so the service can restart; continue afterward

    watchdog recovery
        -> retire a stalled worker; continue afterward

Therefore:

    interruption mechanism != continuation intent

## Required model

Introduce an explicit durable distinction between:

1. interruption mechanism/reason; and
2. continuation intent/policy.

The exact names are implementation-defined, but the model must be able to
represent at least:

### Explicit operator abort/stop

    mechanism: forced/operator interruption
    continuation: explicit-retry-required

### Forced service restart

    mechanism: forced/operator interruption
    continuation: resume-after-restart

### Orderly service shutdown/process loss

Preserve the existing safe automatic-resume semantics where already supported.

### Watchdog recovery

TASK-056 will require a continuation intent equivalent to:

    resume-after-stall-recovery

Do not force watchdog recovery to masquerade as a generic `operator_abort`.

## Required behavior

- `stop --force` must retain current semantics: terminate the active execution,
  preserve evidence/state, and require explicit operator retry if work remains.
- `restart --force` must preserve a durable continuation intent saying that the
  interrupted eligible ticket should continue after the service restarts.
- After restart, Devlegate must prove the prior worker ownership boundary is no
  longer active before starting a replacement worker.
- Automatic continuation must reuse the preserved execution/workspace state
  according to the existing safe recovery rules; do not discard partial work.
- A replacement worker must never overlap the prior worker.
- Unsafe/ambiguous ownership or recovery state must still fail closed.
- Ordinary execution failure must not become automatically retryable merely
  because this continuation-intent model exists.
- Existing `service_shutdown` and `process_loss` recovery semantics must remain
  intact unless a concrete inconsistency is found.
- Persist enough continuation intent to survive daemon/systemd restart.
- Clear/consume continuation intent exactly once when the intended continuation
  is admitted or conclusively cannot be admitted.
- Stale continuation intent must never revive an already completed or superseded
  execution.

## Interaction with current interruption kinds

Current persisted interruption kinds include:

    operator_abort
    service_shutdown
    process_loss

Do not add compatibility/migration machinery for old internal state. This project
is pre-1.0 and may use a one-shot local cleanup if dogfood state needs adjustment.

Prefer a clean current model over preserving obsolete internal distinctions.

## Status and provenance

Internal execution/lifecycle evidence should make it possible to distinguish:

- why the old worker was interrupted;
- whether continuation was requested;
- whether continuation was admitted;
- whether it was blocked and why.

User-facing status should not mislabel a forced restart continuation as a plain
operator abort requiring manual retry.

## Acceptance criteria

- `stop --force` still leaves interrupted work requiring explicit retry.
- `restart --force` automatically continues an interrupted eligible ticket after
  successful service restart.
- The old worker is proven retired before replacement admission.
- Preserved partial repository state is used by the continuation attempt.
- The continuation decision is durable across the actual service restart.
- No duplicate replacement is spawned if recovery logic runs more than once.
- Ambiguous ownership/recovery remains fail closed.
- Ordinary failed executions remain explicit-retry unless governed by another
  explicit policy.
- Existing graceful restart/shutdown/process-loss tests remain green.
- Full authoritative CI and Ruff are green.

## Required regressions

At minimum:

1. active worker + `stop --force` -> interrupted/retryable, no automatic resume;
2. active worker + `restart --force` -> worker retired, service restarted,
   automatic continuation admitted;
3. preserved workspace changes survive forced restart continuation;
4. replacement starts only after old ownership is proven gone;
5. service restart during continuation admission does not create duplicate
   replacement workers;
6. stale continuation intent cannot revive a completed execution;
7. continuation intent is consumed exactly once;
8. ambiguous worker ownership blocks automatic continuation;
9. ordinary execution failure still requires explicit retry;
10. existing `service_shutdown` and `process_loss` automatic recovery behavior
    remains unchanged;
11. status/provenance distinguishes interruption mechanism from continuation
    intent;
12. forced restart no longer reports a misleading generic
    `operator_abort -> explicit retry required` outcome when continuation was
    requested.

## Relationship to other work

- TASK-055 defines the exact execution ownership/retirement boundary.
- TASK-056 defines inactivity detection and watchdog-driven recovery.
- This ticket provides the lifecycle continuation-intent semantics that both
  forced restart and future watchdog recovery can reuse.

## Non-goals

This ticket does not:

- implement the worker inactivity watchdog;
- redesign process ownership/cgroups;
- change ordinary retry policy for unrelated failures;
- add prompt-recovery context;
- add compatibility/version migration layers.
