---
"type": "devlegate.ticket"
"title": "Atomic reconciliation evidence ref pinning"
---

## Scope
Repair `ServiceEngine._persist_product_reconciliation`: replace observe-then-unconditional update-ref with create-only/expected-old CAS and re-observation. Preserve pin-before-WAL, owner/stop and normal/replay behavior. Test absent, equal, conflicting, racing writer, interrupted/restarted execution with real refs.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
