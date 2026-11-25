---
"type": "devlegate.ticket"
"title": "Unify workflow DAG consumers on the canonical cached projection"
"depends_on": ["TASK-195"]
---

## Milestone

Devlegate 0.5.8 — workflow read-path consolidation.

## Goal

Replace redundant materializations of the committed ticket DAG in product consumers with the shared projection established by TASK-195, preserving existing workflow semantics and live mutation safeguards. Reduce repeated Git subprocesses and graph parsing rather than compensate with longer IPC timeouts.

## Context

Current ServiceEngine._ticket_store() loads the control working tree through load_ticket_store(); inspection._ticket() independently reads canonical committed blobs and builds its own graph. Published status and selection also consume ticket data. Other consumers may include recovery, admission, reset, cleanup and abandonment. They must be inventoried rather than blindly redirected: some intentionally observe working/staged or historical states, not the current committed graph. TASK-194 is deferred and its work branch is not part of the product baseline.

## Required behavior

- Inventory all production uses of ticket/DAG loading, graph construction, dependency validation and runnable computation, including direct callers and equivalent independent reconstruction. Classify each as committed current/historical observation, transient working/index observation, or live mutation/provenance check. Record the resulting consumer map and justified exceptions; do not merely migrate the two known callers.
- Route committed workflow consumers through the shared projection, using exact requested revision and configured paths. Reuse it across scheduler/status/inspection and other applicable consumers; avoid duplicate graph parsers or separate caches. Keep exact ticket text, path/blob, dependency diagnostics, runnable ordering and public schemas unchanged. History/provenance and runtime observations remain separately attributed. Do not cache the entire inspect response with stale local or history data.
- Preserve legitimate working-tree/index observations and all existing control cleanliness, canonical state, ref/CAS, ownership and admission checks. Where scheduler reads committed graph data, establish the existing required relationship to the working control state before acting; never replace a required fresh validation of mutable evidence by a cached DAG. Handle staged/intermediate transitions explicitly, using existing validation semantics rather than silently choosing committed or uncommitted data. Historical inspection uses its requested commit, not the current projection.
- Define when the owner materializes/reuses the graph during normal synchronization/iteration and publishes read-only status. Avoid expensive full reconstruction in a read request when the required projection is already available. Maintain current owner serialization, no extra writer or thread, no SQLite writes caused by inspection, no new cache persistence or runtime migrations.
- Do not implement the pending TASK-194 read-only admission changes; work against the integrated product base. The resulting committed inspection path must be usable by TASK-194 when it is reconciled/rescheduled. No general refactor of reset/recovery, log following, Git transport, or CLI rendering.
- Compare existing and consolidated behavior on representative real-sized Git histories (>=200 tickets), including cold/warm repeated inspection, scheduling, independent control changes, and ticket transitions. Record elapsed times and Git subprocess counts. The repeated graph-reading portion must no longer scale in per-ticket subprocess launches for an unchanged workflow. Distinguish separate history/report/prompt/live observation costs; do not merely increase timeouts.

## Acceptance criteria

- All production DAG access sites are inventoried and each committed-graph consumer uses the shared projection, with explicit reason for any retained mutable/independent observation path. No duplicate committed DAG reconstruction remains without a demonstrated separate semantic purpose.
- Scheduler, dependency checks, status and inspection preserve observable behavior and exact attribution; invalid/dirty/conflicting control state still prevents unsafe mutation. Cached valid state cannot hide later errors, transitions or amendments.
- Warm repeated inspection and normal scheduler observations reuse the shared graph; measured real-size results and subprocess counts are available for subsequent TASK-194 review. No persistent cache or new mutation path.
- Focused regression tests, lint, diff check and exact-checkpoint hosted CI pass.

## Required regressions

1. Real Git-backed scheduler/status/inspection fixture with dependencies, abandonment reservations and terminal states: compare pre-existing contracts across idle, review, accepted, done, scheduling, amendments and unrelated control commits. Assert exact committed revision and state in inspection.
2. Dirty/staged/untracked or conflicting control evidence, ref advance during observation, malformed/duplicate/missing dependency and cycle cases. Previously cached clean graph must not authorize a new mutation or replace actual invalid evidence; preserve distinct committed inspection versus working-state refusal.
3. Historical report/review/prompt inspection, reset/cleanup/recovery and integration tests exercising affected call paths. Preserve exact provenance and fresh mutation-time safety checks, and ensure no inspection writes runtime or Git state.
4. >=200-ticket disposable project: instrument repeated warm ticket inspection and scheduler/status materialization; verify no redundant per-ticket blob reads or parsing for unchanged workflow; demonstrate changed tree invalidation and reuse. Record actual timings and counts, without timing-sensitive CI thresholds.
