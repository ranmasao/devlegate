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

## Review decision — 2026-10-13 (REQUEST CHANGES: policy simplification)

Execution `45079153cb024185b44bd756b7c442b7` completed at
`0c9f9ee1fd8e79e05a103698bb6b90a23f0b2620`.
The original implementation provides `resume-after-restart` continuation
intent for forced restart and retains explicit retry for `stop --force`.
Its CI passed, but the operator has revised the continuation policy before
acceptance. This is a requirements change, not a claim that the worker's
previous implementation failed its original specification.

### Revised authoritative policy

**Automatic safe interruption recovery is the default.** Only an explicit,
Devlegate-admitted `stop --force` sets a durable, execution-scoped
`manual_retry_required` marker (name is illustrative), which prevents
automatic continuation until an explicit authorized retry. A deliberate
`restart --force` does not set the marker, nor do normal service stop,
Linux/Windows shutdown or reboot, WSL shutdown/restart, service process loss,
or a future watchdog interruption. Do not infer operator-abort policy solely
from SIGTERM, SIGKILL, systemd shutdown or the low-level interruption kind.

- Persist the explicit stop marker **before** signalling/retiring the worker,
  subject to existing durable owner/command admission and crash consistency.
- Scope the marker to the exact execution identity and generation; a stale
  marker must not affect another ticket or a superseding execution.
- A manual retry clears/consumes that marker only through a durable,
  authorized transition; restart must not accidentally clear an explicit stop.
- On recovery, absent that marker, attempt **safe automatic continuation**
  only for genuinely interrupted work, using existing worker-retirement,
  provenance, Git/workspace, generation and single-owner proofs. Ambiguous
  state stays blocked/recovery-required. Normal worker failure remains manual
  retry; do not create unbounded retries or blanket `DevlegateError` catches.
- Preserve diagnostics for interruption mechanism and the explicit stop marker
  without a matrix of durable `resume-after-restart` and
  `resume-after-stall-recovery` intents. Remove/refactor the just-added
  restart-specific continuation machinery instead of stacking a second policy
  on top of it.
- A possible future machine-local override permitting auto-retry even after
  `stop --force` is **not implemented in this ticket**; keep the policy
  boundary capable of supporting it later.

### Required regressions

1. `stop --force` durably marks the exact execution before terminating its
   worker, survives service/WSL restart, and suppresses automatic retry.
2. Explicit manual retry consumes the marker without leaking to another
   generation/ticket; repeated recovery cannot revive stale work.
3. `restart --force` resumes safely after ownership retirement without a
   special resume-after-restart intent; no overlapping workers.
4. Normal stop followed by later start, external shutdown/reboot/WSL shutdown,
   and unexpected process loss use the same safe interrupted-work recovery
   policy when supported, without interpreting them as explicit forced stop.
5. Ordinary completed/failed workers are not retried automatically; ambiguous
   ownership or workspace provenance stays fail-closed.
6. Existing foreground, graceful lifecycle and recovery regressions remain
   green. Run authoritative full CI and Ruff on the **new exact checkpoint**.

Keep changes within TASK-057 scope; do not implement TASK-056 or modify
release/product refs or historical execution worktrees during review.

**Disposition: REQUEST CHANGES; move `review` -> `todo`.**
Resume from `0c9f9ee1fd8e79e05a103698bb6b90a23f0b2620`.
