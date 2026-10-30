---
"type": "devlegate.ticket"
"title": "Converge reconciliation transaction ownership"
"depends_on": ["TASK-112", "TASK-114", "TASK-123"]
---

## Scope
Inspect `reconciliation_transactions` direct `owner._state` access and temporary execution base override. Either use explicit immutable admission/stage transitions or remove unjustified forwarding; choose safer design with fewer seams. Preserve local/remote pending WAL, CAS, lease, interrupted replay; crash-injection tests. Conditional bounded implementation only.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
