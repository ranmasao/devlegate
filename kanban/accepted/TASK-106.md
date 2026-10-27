---
"type": "devlegate.ticket"
"title": "Deduplicate normal and recovery reconciliation persistence"
"depends_on": ["TASK-105"]
---

## Goal
Begin the bounded implementation of the TASK-105 runtime decomposition findings. Remove the demonstrated duplicate reconciliation evidence/payload/state-persistence algorithm in `src/devlegate/runtime.py`: inline normal execution around lines 5094–5137 at the TASK-105 snapshot and existing `_persist_product_reconciliation` around 8402–8460. Use the **current integrated HEAD**, not stale line numbers.

## Required behavior
1. Make normal changed-product processing reuse one shared **engine-owned** persistence implementation also used by recovery. Preserve the same evidence identity, checkpoint, product observations, execution report, status and exact persisted fields. Handle the normal path's existing fallback/remote cases deliberately; do not assume the two call sites are already semantically identical.
2. Preserve pin/prove evidence before the durable reconciliation state save. Do not silently convert `update-ref` to an unsafe unconditional overwrite or change CAS/identity policy. Fail closed on inconsistent, absent or unreadable Git evidence.
3. Keep owner serialization and shutdown/runner contract; inventory existing direct `runner=_git` use in the affected seam and do not introduce new unchecked subprocess calls. Avoid broad unrelated Git runner migration.
4. Keep `_save_state` authority and exception mapping in ServiceEngine. No broad Protocol, new manager or parallel state machine; remove genuine duplication rather than adding forwarding wrappers.
5. Add focused normal and recovery regressions for exact reconciliation payload, evidence-before-state ordering, changed remote, conflicting ref, restart/replay. Check current tests before adding duplicate coverage. Document exact old/new Git subprocess and effect counts; no unexplained new commands.
6. Run targeted pytest and Ruff; use exact checkpoint GitHub CI for full suite and coverage. Do not run a full local suite by default.

## Acceptance
The normal path visibly uses the same reconciliation persistence implementation as recovery, with proven unchanged WAL and Git effects, no new global state mutations and focused fail-closed test evidence. Document any unavoidable remaining difference. Do not implement other TASK-105 candidates in this ticket.
