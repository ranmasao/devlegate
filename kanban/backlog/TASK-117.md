---
"type": "devlegate.ticket"
"title": "Extract one cleanup dependency proof pilot"
"depends_on": ["TASK-116"]
---

## Scope
Study `_assert_cleanup_has_no_execution_dependency` and nested `_state` inputs. Conditionally isolate pure proof only with immutable inputs and equivalent diagnostics; keep owner authorization, fresh revalidation, destructive worktree/ref CAS, WAL untouched. Test dependency, drift, restart; no bulk cleanup extraction.

## Evidence and boundaries
Use TASK-086/109 reports and supplied architecture.json, engine_calls.dot, imports.dot and static_report.md as leads, not proof of dynamic effects. Exact-SHA verification; preserve single-owner admission, WAL, CAS, evidence, provenance, interruption and replay. No compatibility migrations or generic VCS framework. Existing TASK-085 is separate recovery-boundary audit; TASK-073 remains human-only final story gate.

## Deliverable
Complete only the named bounded scope, supply concrete source locations, regression proof and remaining gaps; do not silently expand scope or claim final architecture approval.
