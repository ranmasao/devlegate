---
"type": "devlegate.ticket"
"title": "Support Git-native ticket amendments without changing ticket format"
"depends_on": ["TASK-132"]
---

## Goal

Implement Git-native ticket amendments in the current 0.5.8 iteration, including **deferred application when a worker is already running**. The operator edits canonical Markdown and commits/pushes to `devlegate/control`; Git owns versioning and diffs, while Devlegate owns execution admission, immutable bindings and review. Reuse the existing owner, WAL/CAS, Git synchronization, checkpoint, review and reset mechanisms. Deliver working behavior, not an isolated Git-history verifier.

No new amendment CLI command, YAML fields, custom VCS layer, amendment database, compatibility migration or general-purpose Git audit subsystem.

## Required behavior

### Unbound tickets

Edits to an existing `backlog` or genuinely unbound `todo` ticket at the same canonical ID/path are ordinary Git commits. The existing ticket parser/DAG and scheduler re-evaluate title, body and `depends_on`. At a safe admission, the next **FRESH** worker receives and is durably bound to the exact current committed specification. No SHA or special commit syntax is required from the operator.

### Bound worker: defer B1

A worker admitted to specification **B0** keeps B0 for its entire execution. A curator may commit **B1** while it is running, but that change must **not** stop/restart the worker, hot-swap its instructions, rebind its execution or silently erase progress. The worker may finish, produce checkpoint **C0**, and publish an execution report accurately attributed to B0. B1 applies only at the next safe review/admission boundary. An uncertain Git observation may block unsafe publication/admission, not destroy completed evidence.

### Review and continuation

The independent Reviewer evaluates C0 against the **current specification B1** (with B0 and the change between them available as evidence):

- **ACCEPT:** explicitly establish that C0 satisfies B1; never implicitly treat a B0 decision as acceptance of B1.
- **REWORK:** explain gaps against B1 and preserve C0 as potential input to the next **new execution**. Reuse the preceding verified code checkpoint where safe instead of automatically starting implementation from scratch.
- Reject stale review of B1 after an unreviewed B2; unrelated control commits should not alone invalidate review. Retain the unchanged-spec review behavior and `TASK-073`'s human approval gate.

Devlegate or its trusted deterministic publication boundary must derive and verify Git identities, execution/checkpoint binding and reviewed-spec identity. **Do not require an LLM Reviewer to obtain, calculate, copy or attest Git SHA values.** A future Reviewer may have no Git access. Existing web-chat review may continue using GitHub; tools/API for a limited-access Reviewer and worker-facing diff tools are **out of scope**. Add only the minimal review/provenance adaptation necessary for this task.

### Workflow state and reset

| State | Amendment |
| --- | --- |
| `backlog` | Allowed at stable path |
| `todo`, unbound | Allowed; next admission binds current version |
| `todo`, bound/running | Deferred to review, existing B0 execution untouched |
| `review` | No direct edit; first defer with unchanged blob to backlog |
| `accepted`, `done`, `abandoned` | Protected |

Normal workflow moves preserve the exact ticket blob. After a rejected review, the curator may publish an immutable review artifact and move `review → backlog` without changing the ticket, then amend the backlog file in a **separate** commit and schedule it for a new execution. Old reports/reviews remain immutable evidence; no new execution is falsely identified as the original B0 attempt.

An **explicit** `devlegate reset TASK-XXX` deliberately discards prior execution continuation rights and retires its exact independently bound resources, preserving historical evidence and allowing FRESH work on the currently selected specification. It must **not** be invoked automatically merely because an amendment appeared. Where checkpoint reuse/resource safety is uncertain, fail at that operation with an actionable diagnostic; never silently delete non-done worktrees or refs.

### Minimal trust boundary

Preserve ticket schema and dependency DAG, authorized workflow states, immutable execution/control bindings, owner serialization, existing CAS/recovery/integration checks and review attribution. Reuse Git object identity, diff and ancestry where relevant. Inspect only those transitions and identities actually needed for admission/review safety. Do **not** replicate Git's history engine, expand overlapping per-commit scanners, or add speculative edit/revert/delete/recreate forensics against a curator already authorized to write control. Actual missing/unobservable/rewritten Git authority and ambiguous resource ownership must still fail closed.

## Acceptance and checks

1. Real-Git backlog and unbound-todo edits, including dependency changes, affect the next FRESH prompt; malformed current tickets/dependencies are rejected.
2. A B0 worker continues across a B1 commit, and C0/report remain bound to B0. No automatic reset or lost checkpoint.
3. Reviewer explicitly accepts C0 against B1 or returns REWORK with C0 preserved for the next execution; stale B2 decisions fail, and unchanged-spec review still works.
4. Separately committed unchanged-blob review deferral and backlog amendment work; intentional reset produces a new execution generation, retires only proven old resources and preserves historical reports. Accepted/done protection remains.
5. Focused owner/race/recovery and real-Git integration tests prove the boundaries above. Pass full configured CI and lint. Fix the self-hosting test fixture `tests/test_agent_protocol.py` that still uses `release/0.5.7` while the current rendered Reviewer is for `release/0.5.8`.
6. Document the simple edit/commit/push workflow, state matrix, B0/B1 review/continuation and the distinction between delayed amendment and destructive explicit reset.

Prefer a small extension of existing mechanisms, not a new amendment subsystem. Do not add reviewer harness tools, diff tools, arbitrary Git-forensics requirements, new tickets or migration paths.
