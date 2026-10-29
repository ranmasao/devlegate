---
"type": "devlegate.ticket"
"title": "Narrow one family of Ruff wildcard suppressions"
"depends_on": ["TASK-111"]
---

## Scope
Use TASK-086 Ruff inventory: first IPC support/test family only. Replace wildcard with explicit imports/exports, remove targeted F403/F405/I001 where safe, preserve fixture/monkeypatch import bindings. Focused Ruff, import collection and tests. Other families remain separate follow-up proposals; no bulk edit.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
