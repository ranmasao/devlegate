---
"type": "devlegate.ticket"
"title": "Map execution phases and extract at most one"
"depends_on": ["TASK-117", "TASK-118"]
---

## Scope
First map `_run_iteration_body` phase predicates, owner/proof/Git effects, WAL boundaries, stop and re-entry paths. Extract at most one independently justified closed phase with focused failure/restart tests; otherwise report no-change. No parallel FSM or line-range splitting.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
