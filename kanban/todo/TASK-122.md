---
"type": "devlegate.ticket"
"title": "Prevent forced-stop durable intent from stale concurrent state save"
"depends_on": ["TASK-113"]
---

## Problem and evidence
TASK-112 audit `docs/architecture/state-authority-audit-112.md` demonstrates with `test_forced_stop_can_be_overwritten_by_inflight_owner_save` that an in-flight `_save_state` prepares a full replacement payload, another thread commits forced stop with `execution_interruption_kind=operator_abort` and `manual_retry_required`, then the stale replacement removes those durable markers. SQLite serializes writes but not construction of the payload. Observed in isolated concurrent test; production timing and downstream worker safety require validation.

## Required change
- Trace `request_lifecycle(force=True)`, owner `_save_state`, IPC direct callback, stop/drain and SQLite store revision/transaction ownership. Prove the precise possible interleaving, not merely test existence.
- Implement one narrow, explicit synchronization or generation-checked state transition boundary that prevents a stale full-state writer from erasing accepted forced-stop intent. If using CAS/revision checks, retry only with fresh verified state and preserve all competing updates; no silent loss on retry. Evaluate lock order and callback reentry before choosing a lock.
- Preserve operator request acknowledgement, durable-before-signal ordering, owner-thread rules, WAL fault handling, shutdown/restart/replay semantics and existing error behavior. Do not introduce migrations, generic transaction abstractions, or rework unrelated state storage.
- Add deterministic real SQLite tests for stale-save race, failed save, forced-stop vs normal completion, reentry and restart; ensure unrelated fields are not lost. Validate with focused suite and report effective exact product SHA.
- Fail closed and document any unresolved post-commit ambiguity. This ticket repairs the lost-update defect only; keep cleanup/status publication findings separate.
