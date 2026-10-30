---
"type": "devlegate.ticket"
"title": "Restore reconciliation temporary execution base on every exceptional exit"
"depends_on": ["TASK-122"]
---

## Problem and evidence
TASK-112 audit `docs/architecture/state-authority-audit-112.md` and `test_rewrite_observation_failure_leaves_temporary_base_in_memory` show that `reconciliation_transactions.start_fresh_rewrite` can persist `local-pending` with the original `execution_base_head`, temporarily change `owner._state["execution_base_head"]` to a target, and exit on workspace observation failure without restoring the original in-memory base. Durable and live state then diverge.

## Required change
- Trace all temporary-base assignments and exits in fresh and resumed reconciliation rewrite, including observation, lineage refusal, publication, checkpoint and Git errors.
- Remove the temporary owner-state override if a bounded explicit argument suffices; otherwise implement exception-safe restoration preserving identity and explicit local-pending/reentry semantics. Do not simply restore in a way that discards a concurrently committed state generation.
- Preserve owner authority, valid reconciliation evidence, WAL phase ordering, local/remote pending, restart/replay, CAS/lease checks and error classification.
- Extend deterministic failure injection across each post-override operation; assert durable/live consistency, replay and successful completion, with focused real-Git coverage where effects occur. Keep the TASK-112 reproduction as regression and identify any unproven failure path.
- Narrow correctness fix, no broad reconciliation ownership redesign; that remains TASK-118. No migrations or generic compatibility layer.
