---
"type": "devlegate.ticket"
"title": "Restore stale failed-execution status suppression across unrelated active workers"
---

## Milestone
Devlegate 0.5.7 operator-facing status correctness; regression of completed TASK-102 after TASK-137. Keep in backlog until explicitly scheduled; no dependency on the unfinished TASK-151.

## Observed incident (2026-11-08)
On the running 0.5.7.dev0 service, `devlegate @devlegate status` reported:
- Active execution: TASK-151, `worker-running`.
- Failed executions: TASK-088, `retryable: no`, `ticket is no longer todo (done)`.
- Code and control checkouts clean, local and remote HEADs matching, control `89305e24951d`; TASK-088 is indeed published under `kanban/done/`.

TASK-102 explicitly required old failures to disappear from the **current** Failed section after authoritative lifecycle transitions while retaining history. TASK-137 later unified failed-generation supersession with retry/admission and introduced an overly broad guard in `ServiceEngine._failure_superseded()`:
```python
if state.get("worker_identity") is not None:
    return False
```
This disqualifies suppression for **every** ticket whenever an unrelated worker is running. The status evaluator then emits the stale TASK-088 record as nonretryable. Exact live-service state was not fetched; reproduce in a disposable fixture to verify this causal path independently.

## Goal
Restore a truthful, current, actionable `Failed executions` view independently of unrelated activity, without weakening fail-closed scheduler/retry/admission/ownership rules or erasing immutable historical evidence.

## Required behavior
1. Separate **current failure visibility** from **authorization to retry or admit a worker**. A proven obsolete failure may be hidden in status even when another unrelated ticket is actively executing. Apply recovery/worker-identity guards per matching ticket/execution generation, not as a global veto to history projection. Retain all state/evidence for audit.
2. For a failure of ticket X, check whether the original failed generation is still active or holds an unresolved matching retry/recovery/worker/resource intent. Never hide a genuinely actionable current failure or a matching unresolved process. A worker for ticket Y must not revive X's historic error.
3. Audit TASK-137's changes to TASK-102's original contract for terminal `done`, `review`, `accepted`, `backlog`, proven `todo → backlog → todo`, and removed tickets. Reassess its currently enforced same-blob and removed-ticket behaviors: the **status view** need not impose the same proof as safe re-execution. Suppress only where authoritative first-parent control evidence proves the historical failure superseded; fail closed on ambiguous deletion, rewrites, changes of identity, unpublished/dirty/divergent or inaccessible control history. Do not silently interpret an arbitrary changed ticket as the same execution generation.
4. Maintain consistency between `status`, `status --json`, `plan`, explicit `retry` candidates, and actual scheduler admission. Where status suppression is more permissive than admission authorization, present an accurate admission blocker rather than fabricating `run-worker` eligibility. No broad changes to retry policy, remote lease/CAS or owner process isolation.
5. Reuse a shared proven per-ticket supersession result when practical; avoid excessive repeated full-history Git scans on every status poll. Do not persist or mutate historic failures just to make the table look clean. No automatic database migration or manual SQLite repair.

## Focused acceptance regressions
- **Exact incident:** T-OLD has a retained failure, a published intact `todo → done` transition and clean synchronized Git; T-NEW is in `agent_running` with its own worker identity. Human/JSON status omit T-OLD from current failures; the active T-NEW remains visible. History stays untouched. Repeat after restart.
- Matching live/uncertain T-OLD ownership or pending recovery retains the appropriate current warning. A new genuine T-OLD failure is visible and is not hidden by its old transition.
- Proved deferral/requeue, proven removal and content changes are checked with explicit status-vs-admission distinction; uncertain/divergent/dirty/unfetched observations refuse proof rather than claiming success.
- No accidental scheduler retry/unblock or loss of audit records. Real-Git disposable fixtures, plus one CLI human/JSON contract test. Avoid adding redundant tests of private helpers for each syntactic variation.

## Boundaries
Fix this in the status/lifecycle layer; do not use an output-only text filter. Do not alter historical TASK-088, TASK-102, TASK-134 or TASK-137 resources, control history or current TASK-151 execution. Avoid hypothetical migrations and third-party runtime dependencies. Ruff and exact-checkpoint CI must pass.
