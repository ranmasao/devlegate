---
"type": "devlegate.ticket"
"title": "Read-only operator reset inventory and identity-bound preview"
"depends_on": ["TASK-125"]
---

## Goal
Implement read-only inspection for deferred-ticket execution resources. Reuse existing operator CLI/IPC and owner-side Git semantics, not a separate cleanup engine. Identify ticket state/blob/control HEAD, execution-generation report/checkpoint, registered worktree/path/branch/HEAD/cleanliness/locked metadata, local ref SHA, exact upstream ref SHA or proven absence, pending ownership/recovery intents and pinned evidence. Fail closed on ambiguity or errors. Preview must be mutation-free (no fetch or other Git state writes); return deterministic structured plan and identity digest suitable for later confirmation. Do not remove resources. Preserve existing non-done retention and TASK-073 resources. Focused real-Git tests: normal preview, absent resources, dirty/locked/foreign worktrees, unobservable remote, stale/ambiguous generations, no writes. Document operator preview usage. Expose a narrowly reusable plan/proof API for TASK-130.

## Boundaries
No migration/compatibility scaffolding, no generic VCS abstraction, no TASK-073-specific production branches, and no destructive mutation of live operator resources. Preserve WAL/CAS, exact provenance, and fail-closed semantics. Changes must be reviewed independently before downstream tasks proceed.
