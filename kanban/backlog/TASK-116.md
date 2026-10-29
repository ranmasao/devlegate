---
"type": "devlegate.ticket"
"title": "Separate one status projection from observation"
"depends_on": ["TASK-115"]
---

## Scope
Select ONE status/plan projection and distinguish owner-acquired Git/store snapshot from pure shaping. Conditionally extract immutable observation-to-model logic; preserve admission, freshness, CLI/IPC schemas, missing remote, concurrent mutation and monkeypatch semantics. No broad extraction.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
