---
"type": "devlegate.ticket"
"title": "Audit and safely retire orphaned TASK-073 execution resources"
"depends_on": ["TASK-125"]
---

## Purpose
TASK-073 is an intentionally deferred **human-only final story acceptance gate**, currently in `kanban/backlog`, not durably done. Historical worktree, local execution ref and upstream execution ref exist; automatic historical cleanup must **not** delete them merely because the ticket is non-done. Two durable incomplete reports exist: `9ada49c1cd904f18b90e63c0b5be7b02` and `b69e17a85bd04c25b0348a6db8fb77b5`. A prior review/backlog/todo transition affected ticket blob continuity, so do not try to replay these generations as normal rework without proving provenance.

## Required investigation and outcome
- Reconcile exact current Git identities for `devlegate/work/TASK-073`: worktree registration/path/HEAD/dirty status, local and remote branch SHAs, product ancestry, both execution reports and their `control_head`, ticket revisions, relevant refs and evidence/pin generations, control history, current `backlog` location, runtime owner/state. Distinguish obsolete generations, uncertain resources and any currently active ownership; no claims based on ticket ID alone.
- Propose and verify a **one-shot, explicit operator-controlled** disposition for superseded TASK-073 execution worktree/refs if all are proved safely retired. Use exact-old SHA preconditions, worktree identity and clean-state checks, upstream lease, re-observation before each effect, fail-closed partial replay and postcondition verification. Preserve durable reports, original ticket, evidence refs and human gate. If proof is insufficient, **do not delete**; leave a concrete blocker and safe operator procedure.
- Do not convert TASK-073 to `done`, self-APPROVE, rewrite historical control/report artifacts, or manufacture provenance continuity. A fresh later final audit must bind to then-integrated product HEAD as a new valid generation. Do not add migration or permanent special-case behavior keyed to TASK-073.
- Document evidence, exact commands/script if needed, preconditions, failure modes, operator authorization boundary and resulting inventory status. A destructive one-shot operation must never be run automatically by this ticket's worker merely on the basis of discovery; deliver the checked procedure for explicit curator authorization.

## Tests
Focused real-Git fixture with obsolete non-done resource, live owner and dirty workspace refusals, changed local/remote SHA, missing/ambiguous evidence, interrupted partial retirement and rerun idempotence. Preserve TASK-073 backlog blob and previous reports. No general-purpose VCS abstraction.
