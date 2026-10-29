---
"type": "devlegate.ticket"
"title": "Map durable state writers aliases and locks"
"depends_on": ["TASK-111"]
---

## Scope
At exact product HEAD inventory direct and nested `_state` mutations and aliases, SQLite save-failure semantics, locks and acquisition order including callbacks/IPC, publication freshness and ownership. Deliver transition/authority matrix and targeted failure/race tests; no refactor.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
