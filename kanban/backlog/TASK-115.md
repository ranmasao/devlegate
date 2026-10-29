---
"type": "devlegate.ticket"
"title": "Extract pure runtime state invariant validator pilot"
"depends_on": ["TASK-112"]
---

## Scope
First prove `_validate_state_invariant` has no mutation or I/O and inspect free dependencies. Then conditionally extract one pure immutable-input validation module preserving error types/text, state schemas, load/save behavior. Differential invalid/current/legacy cases, import and no-cycle checks. If unsafe, report no-change, no migrations.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
