---
"type": "devlegate.ticket"
"title": "Finish reconciliation extraction: remove obsolete runtime transaction and verify replay"
"depends_on": ["TASK-057"]
---

## Current review — REQUEST CHANGES

Execution `81deaef70a3e400383c2c829409cec0a`, checkpoint `478acf371e5cb3542c80f457c657034fdd24ca6a`, outcome `incomplete`.

[Exact-checkpoint CI #38620039814](https://github.com/ranmasao/devlegate/actions/runs/38620039814): **FAILURE at Ruff only**. Full tests completed: **1203 passed, 1 skipped**; `src/devlegate/reconciliation_transactions.py` has **29 E501 long-line violations**. Preserve the working test baseline; do not introduce blanket suppression.

## What is already completed — do not redo

- Stranded execution, workspace recovery, checkpoint/publication replay, lifecycle and accepted integration recovery are already extracted into `recovery_transactions.py`, `recovery_workspace.py`, `recovery_workflow.py`, and helpers. Prior obsolete implementations in `runtime.py` were removed.
- Admission, linear lineage proof, published rewrite authorization, evidence-before-publication and lock ownership boundaries already exist.
- **This checkpoint performed the main requested extraction.** `reconciliation_transactions.py` now dispatches on persisted `rewrite_stage` and directly implements `recover_local_pending()`, `recover_remote_pending()`, `start_fresh_rewrite()`, associated lineage checks and `_resolve()`. The production path no longer delegates the entire operation back to `ServiceEngine._reconciliation_start_update_base()`.
- The unused linear `execute_update_base()` sequencer and obsolete mock-only tests were removed. **Do not rebuild it.**
- Current real-engine tests are green; the remaining work is finite cleanup and validation, not another design/extraction exercise.

## Exact remaining implementation work

1. **Physically delete the obsolete large `ServiceEngine._reconciliation_start_update_base()` body** from `src/devlegate/runtime.py`. It remains ~400 lines after its callers were replaced. Verify call sites first; remove only unreachable aggregate code, along with truly unused `_reconciliation_recover_local_rewrite()` and `_reconciliation_recover_remote_rewrite()` forwarding wrappers. Preserve short `_reconcile_update_base_owned()` and existing narrow owner methods.
2. **Fix the 29 Ruff E501 errors** in the new `src/devlegate/reconciliation_transactions.py` using formatting only; do not add E501 ignores. Avoid unrelated refactors.
3. **Verify semantic equivalence at the new state boundaries, not merely test count.** Focus specifically on:
   - `remote-pending` where remote is unchanged vs already published: no second push; exact evidence/lineage proof; fail closed for unexpected remote head.
   - `local-pending` safe abort/reset and appropriate fresh retry; verify workspace state and exact count before destructive reset.
   - Existing WAL transition and authorization rules, pin-before-publish, lease/CAS conflicts, idempotent resolution, accurate `rewrite_stage` cleanup.
   - Compare the extracted implementation against the just-deleted legacy body for **behavioral changes**, especially WAL field cleanup and rollback paths. In particular, `_clear_rewrite()` currently clears the union of stage-specific fields; check against previous per-stage cleanup to avoid inadvertently changing durable semantics.
   - If a specific uncovered branch changed, add **one focused real-engine replay test** for it; avoid new mocked sequencing frameworks or duplicating tests already present.
4. Run full authoritative pytest/coverage and Ruff on the next exact checkpoint SHA. Acceptance requires both success.

## Constraints

- KISS/DRY: plain cohesive recovery/fresh-rewrite functions are sufficient. No extra Protocol hierarchies, stage wrappers, compatibility fallbacks, general state-machine engine, or another 400-line callback.
- One serialized ServiceEngine retains state and Git mutation ownership. Keep intentional repeat admission/TOCTOU checks, durable intent ordering, exact evidence and lease/CAS proof, crash restart behavior, operator stop vs continuation (TASK-057), and established public errors/CLI semantics.
- Preserve existing recovery module extractions. Leave `_run_iteration_body()` (~1173 lines) for separate follow-up; **do not change it in TASK-061**.
- TASK-066, logging unrelated to this operation, published 0.5.6 and product release integration are out of scope.

## Acceptance criteria

- Legacy `_reconciliation_start_update_base()` and unused forwarded recovery bodies are absent; the extracted WAL-stage dispatcher and stage procedures are the *only* active implementation.
- No inadvertent semantic changes in recovery, rollback, WAL cleanup or publication/authorization, demonstrated by existing and targeted regression tests.
- Exact checkpoint CI green (full tests/coverage and Ruff).
- Report any remaining blocker precisely; **do not append old reviews** or spend an iteration on further architecture analysis.
