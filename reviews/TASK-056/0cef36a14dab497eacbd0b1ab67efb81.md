<!-- devlegate-review-v1
ticket: TASK-056
execution: 0cef36a14dab497eacbd0b1ab67efb81
checkpoint: 0c6f3dd0469c23d6ef7f52aa8a3ee5f17035e44f
control: fccd089ab3510466990069fc7fa65505bb77a0f6
disposition: rework
-->

# TASK-056 — REWORK: finish owner-mediated watchdog, not another observation helper

## Evidence and decision

The execution `0cef36a14dab497eacbd0b1ab67efb81` has a valid durable `incomplete` report, with working transport/egress, no questions, code base `a48ed50a5b27c380b23e25494a4378fc1f8450ff`, and checkpoint `0c6f3dd0469c23d6ef7f52aa8a3ee5f17035e44f`. This cumulative checkpoint contains the previous stdout/stderr activity callback plus this attempt's `ExecutionContainment.has_other_processes(worker_pid)` and associated tests. The newly added method observes other PIDs in a cgroup tree and returns unknown (`None`) when observation is insufficient; the method is not yet connected to watchdog decision-making.

**The acceptance-blocking behavior remains absent:** there is no integrated inactivity timer, warning/state reporting, owner-mediated stalled classification, exact containment retirement, durable replacement handoff, or deterministic recovery-context prompt. Both worker claims themselves acknowledge this. A proven helper does not satisfy the complete watchdog assignment. Keep the cumulative changes; do not reset or rebuild from scratch.

CI run https://github.com/ranmasao/devlegate/actions/runs/44995417554 completed **2406 passed, 3 skipped**, but **Ruff failed** with E501 in `src/devlegate/execution_containment.py` lines 421 (94 > 88) and 428 (90 > 88). The missing local development venv reported by the worker does not make those CI findings unknown; CI is available as independent evidence.

## Primary blocker: next verifiable action

Implement a **minimal functional vertical slice** through the existing service owner and recovery boundary, **before adding more independent primitives or polishing peripheral tests**:

1. Connect the existing stdout/stderr `activity_handler` to an execution-scoped monotonic idle clock. On service monitoring, distinguish recent activity, warning and recovery thresholds (default 5/10 minutes, machine-local configurable). Revalidate immediately before acting and suppress agent-idle retirement when the existing owned-cgroup observation detects an active descendant; unknown ownership/membership fails closed.
2. Record `worker_stalled` against the old exact attempt and route recovery through existing owner/transaction/reconciliation mechanisms. Retire the exact proven containment, preserve workspace, checkpoint and existing execution evidence, and only then admit **one** new execution. Ensure process-exit, current activity, concurrent restart and uncertain retirement cannot produce duplicate replacements.
3. Construct and inject a bounded deterministic continuation fragment from preserved evidence: stable relative changed paths, bounded verbatim stdout/stderr tail, truncation markers and neutral guidance to inspect existing changes. No LLM summarization, provider cause guesses, process/execution IDs, internal worktree/state paths, cgroup details or sandbox disclosure.

## Mandatory dependent verification

- Exercise activity on both streams (including partial-line output), threshold boundary and last-moment activity, warning without mutation, cancellation upon normal completion, active owned descendant, absent/unknown cgroup membership, durable stalled reason, and retirement-before-replacement ordering.
- Exercise restart during warning and retirement/relaunch, uncertain ownership fail-closed, preservation of edited workspace, no duplicated worker, bounded deterministic prompt from identical evidence, redaction of private identities, and accurate operator status.
- Preserve existing stop/retry/process-loss/reconciliation regressions, including safe restart behavior. Complete focused tests and full authoritative CI. Fix the two Ruff E501 violations and verify lint.

## Optional / non-blocking observations

The stdout/stderr observation hook and `has_other_processes` are legitimate building blocks, not reasons to discard work. Avoid converting this into a general process-monitoring or independent recovery framework. The real blocker is end-to-end service integration, and the remaining ticket work is still substantial; an intermediate execution may truthfully report incomplete, but the next implementation attempt should prioritize actual watchdog behavior over another standalone helper.

## Disposition

REWORK `TASK-056` without changing its canonical ticket blob. Publish this immutable, attributable review with the move `kanban/review/TASK-056.md` → `kanban/todo/TASK-056.md` in one control commit. Do not change the retained work checkpoint, reports, TASK-148, TASK-154 or other workflow items.
