---
"type": "devlegate.ticket"
"title": "Audit operator admission and receipts boundary"
"depends_on": ["TASK-119"]
---

## Scope
Audit `_submit_operator_command`, `_validate_operator_admission`, `_admit_operator_command`, `_dispatch_operator_command`, receipt locks, drain and scheduler contention. Map typed request, admission, receipt and effects with lock order and at-most-once/uncertain semantics. Diagnostic proposals only, no broad code change.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
