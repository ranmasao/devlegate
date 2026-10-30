---
"type": "devlegate.ticket"
"title": "Schedule historical cleanup by real cadence and skip non-done retirement proofs"
"depends_on": ["TASK-113"]
---

## Problem
The idle scheduler invokes `ServiceEngine._reconcile_workspace_backlog()` on successive iterations. Its `time.monotonic() - _cleanup_last_pass_log >= 300` check throttles only logging, **not** discovery, Git calls, inventory publication or persistence. Non-done historical resources (notably TASK-073 in backlog) get repeatedly examined although no destructive eligibility is possible. Keep immediate post-acceptance cleanup separate.

## Scope
- Add real monotonic scheduling of the **historical** cleanup work, using a conservative initial five-minute cadence (300 s), with explicit startup and restart behavior. No busy rechecking on each idle scheduler iteration; use existing wake/shutdown/lifecycle mechanisms, avoid unbounded sleeping/blocking the scheduler and do not add external dependencies.
- Preserve immediate cleanup on accepted integration, independent of the historical interval. Control-state changes or restart must cause a suitable new discovery/eligibility pass rather than permanently caching an obsolete negative.
- Before expensive per-candidate deletion proofs, short-circuit resources whose ticket is authoritatively **not durably done**; retain them in the visible diagnostic inventory as `blocked` with reason, and keep ownership/provenance uncertain states fail-closed. Do not treat mere absence from a directory or stale in-memory plan as durable ticket state.
- Never cache positive eligibility to authorize mutation: immediately before worktree removal/ref deletion/remote lease, re-observe full required proof with exact SHA, fresh owner checks and CAS/lease. Preserve cursor fairness, cohort=32, apply<=1 and inventory limit semantics (including documented incomplete scans and observation failures).
- Separate actual work-cadence measurement from logging throttle. Add deterministic clock/wake tests, real-Git call counters for repeated idle iterations, non-done TASK-073-like resources, later transition to done, restart, immediate integration, interrupted partial cleanup and foreign writer. State precisely when inventory is refreshed and how age/staleness is reported; don't invent an always-fresh snapshot.

## Boundaries
No migration, compatibility machinery, generic scheduler framework, unsafe forced cleanup or manual deletion of TASK-073. Preserve single-owner and durable safety invariants, state-write lock, supervisor and shutdown behavior. Keep TASK-073 final human acceptance gate; its existing resources are handled by a separate evidence-based disposition task. Run focused tests and full configured CI.
