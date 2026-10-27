---
"type": "devlegate.ticket"
"title": "Retire stale failed executions from current status without losing history"
---

## Milestone
Devlegate 0.5.7 UX correctness.

## Incident
TASK-088 remains listed under `Failed executions` in `devlegate status` even after its ticket transitioned back to todo (or could move to backlog, accepted, done, or be removed). The old failure is historically valid but should not be shown as a currently actionable failed ticket.

## Required behavior
1. Audit how persisted `failed_executions`, Kanban state, retry/recovery markers, remote control freshness and CLI/status JSON projections interact; distinguish active actionable failure from historical evidence.
2. After an authoritative ticket transition to todo, backlog, review, accepted, done, or deletion from the active Kanban, do not list its superseded previous failure in the *current* Failed section. Preserve immutable execution reports, failure history and associated evidence. Do not hide a genuinely active recovery/manual intervention solely due to a stale/unfetched control snapshot.
3. Prefer a coherent lifecycle reconciliation or status projection boundary over CLI-only string filtering. Explain behavior for missing or unreadable control state and interrupted transitions; fail closed rather than inventing success or silently dropping provenance.
4. Add real control-state transition tests for failed→todo/backlog/review/accepted/done/deleted, unchanged actionable failure, new failure for a later execution, restart/reload, and divergent/stale control observations. Verify CLI human output and structured JSON agree and no false omission.
5. Keep it scoped to status/lifecycle correctness: no automatic retry policy change, no ticket mutation, no purge of history, no general inspect API.

## Acceptance
A historic failure such as TASK-088 does not persist in active Failed after an authoritative transition, while audit and recovery evidence remain accessible. Exact-checkpoint CI and Ruff green.
