---
"type": "devlegate.ticket"
"title": "Measure Git effects and cleanup repeated commands"
"depends_on": ["TASK-112"]
---

## Scope
Instrument complete Git argv and nested effect traces in sync, publication, recovery, evidence pin and cleanup (0/1/32 candidates, 10000 inventory bound). Count local read, remote read and write incl fetch; separate measurements and estimates, preserve fresh pre-mutation proof. Audit only.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
