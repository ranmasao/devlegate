---
"type": "devlegate.ticket"
"title": "Recovery capability contract completeness"
"depends_on": ["TASK-110"]
---

## Scope
Audit `WorkflowRecoveryCapabilities` against actual recovery calls, including `recovery_checkpoint_exact`, `recovery_persist_reconciliation`, `recovery_report_matches_state`; correct declarations and focused static/real replay tests. Check forwarding necessity but no facade redesign.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
