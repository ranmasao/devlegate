---
"type": "devlegate.ticket"
"title": "Log the persisted worker prompt artifact path at delivery"
---

## Milestone
Devlegate 0.5.7 operator UX.

## Incident
Service journal currently logs `worker prompt constructed: execution=... directive=... sha256=...` and `worker prompt sent: execution=... sha256=...`. A digest is useful for integrity, but the operator primarily needs the path to the saved prompt file.

## Required behavior
1. Trace `write_worker_prompt_artifact`, the returned artifact path, and `_record_prompt_delivery`. Log the actual confirmed persisted prompt artifact path with the execution identity once the write succeeds, and retain sensible integrity identity if useful. Do not invent or log a file path before successful durable creation.
2. Distinguish constructed-in-memory, persisted-on-disk, and successfully sent-to-worker events. Failure to persist or mark sent must not be logged as successful persistence/delivery. Preserve existing audit/metadata/provenance semantics and exactly-once delivery evidence.
3. Ensure the path is the actual prompt artifact, not the worktree, execution log or report; avoid leaking prompt content, secrets or unrelated private paths in diagnostics. Logging a user-operable path is intentional, but scope it to operator journal and follow existing privacy conventions.
4. Add focused tests for real artifact path and file existence, event ordering, failed write/failed delivery, stable execution binding and preservation of sha256 metadata where relevant. No new inspect API or prompt storage schema.

## Acceptance
Operators can find the saved prompt directly from a correct journal path; no misleading event claim, no hidden changes to worker execution/recovery. Exact-checkpoint CI and Ruff green.
