---
"type": "devlegate.ticket"
"title": "Owner-serialized operator reset confirmation and durable intent"
"depends_on": ["TASK-129"]
---

## Goal
Build on TASK-129 preview/proof. Introduce explicit operator-only apply request with plan/generation identity confirmation, serialized with ServiceEngine owner/admission and receipt handling. Before any destructive action validate current ticket state/blob/control generation, owner and pending integration/forced retry/recovery/reconciliation/retirement intents, worktree and exact ref identities. Persist identity-bound reset authorization and durable WAL/state intent before effects; no implicit authorization from backlog, age or worker. Reject stale previews and uncertain writes. This ticket implements authorization/intent and failure-safe pre-effect boundary only; do NOT perform resource deletion until TASK-131. Tests must exercise concurrent/contended operator requests, denied active/pending state, stale plan/ticket SHA, crash before intent, unknown outcome and restart restoration. No real TASK-073 effects; document confirmation contract.

## Boundaries
No migration/compatibility scaffolding, no generic VCS abstraction, no TASK-073-specific production branches, and no destructive mutation of live operator resources. Preserve WAL/CAS, exact provenance, and fail-closed semantics. Changes must be reviewed independently before downstream tasks proceed.
