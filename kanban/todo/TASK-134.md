---
"type": "devlegate.ticket"
"title": "Durably bind execution resources to their exact generation"
"depends_on": ["TASK-125"]
---

## Goal
Implement a narrowly scoped, durable generation-specific ownership binding for Devlegate execution worktrees and local/upstream execution refs. The owner must be able to prove whether resources belong to a specific execution ID, rather than relying on branch name, path or coincidentally matching commit SHA. This is a prerequisite for the operator reset preview in TASK-129, not a reset implementation.

## Implementation contract
- Record binding under owner serialization before freshly prepared execution resources are usable; bind exact ticket ID, execution ID/generation, control/admission provenance, expected worktree identity/path/branch and resource creation epoch/nonce or similarly non-reusable identity. Preserve WAL/CAS and crash-ordering invariants across worktree prepare, ref creation/publication, worker launch, retries and recovery. Specify how binding is independently checked against the actual resources without assuming SHA/path proves continuity.
- Never transfer an old binding to recreated resources even when checkpoint SHA and branch/path are identical. Make loss, stale binding, conflicting creation, interrupted setup and uncertain cleanup fail closed. Restart/replay must retain or safely reconcile the binding without fabricating ownership.
- Keep historical executions that lack owner-published binding unsafe for generic reset. Do not retroactively infer a binding from matching branch/SHA. No compatibility migration: historical remediation, if necessary, requires a separate explicit one-shot operator decision.
- Expose a small read-only binding/proof API for TASK-129; avoid adding the reset preview, destructive removal, new general VCS abstractions or unrelated CLI surface in this task.
- Use isolated real-Git fixtures and owner/state tests to prove fresh creation, durable persistence, restart, repeat/recreate on identical SHA, changed path/ref, interruption at each binding boundary, pending ownership and non-interference with previous reports/pins. No effects on actual TASK-073 resources.
- Document exact binding semantics and recovery rules, run focused and configured CI.

## Boundaries
Stdlib-only as practical; no migration scaffolding or customer-compatibility paths. Keep existing automatic cleanup policy unchanged. The ticket is complete only when a new generation can be positively distinguished from identical-looking recreated resources.
