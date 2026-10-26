---
"type": "devlegate.ticket"
"title": "Align reconciliation rewrite evidence validation with workspace cleanup"
---

# Incident / priority

**High-priority functional bugfix, self-hosted reproduction (2026-10-26).** After integrating TASK-099 and installing/restarting standalone Debian Devlegate `0.5.7.dev0` at product SHA `99ca4d27a2ebc8accb5e43653ad793ee583cba01`, self-hosted rslab2 reported **76 cleanup candidates, 75 blocked** with identical reason `reconciliation dependency identity is invalid`; the remaining LAB-187 is correctly retained because it is not durably `done`. This prevents cleanup of unrelated DOC and LAB worktrees (~513 MiB at observation). Service was ready/idle with 0 workers and no failed executions. Do not mistake this for a stale binary or one-off corrupt record: the **current source tree is capable of producing this state again**.

Real durable reconciliation identity (read-only diagnostic, **do not copy host paths into fixtures**):

- `ticket_id=LAB-158`, `execution_id=c68282b27e6244f0a7260233a3a9f7c0`, `status=resolved`, `resolution=update-base`;
- `execution_branch=devlegate/work/LAB-158`, managed `execution_path=<managed-root>/work/LAB-158`;
- `displaced_evidence_ref=refs/devlegate/reconciliation/LAB-158/c68282b27e6244f0a7260233a3a9f7c0`;
- `evidence_ref=refs/devlegate/reconciliation/LAB-158/c68282b27e6244f0a7260233a3a9f7c0-rewritten`;
- `worker_checkpoint=b7180586c9b85fb2dbb8933e6f8aa348199362c9`, `execution_remote_head` the same commit, `effective_base=d697f4e3a727477324ecaf578f85e9fa46cb5d52`; all mandatory identity fields present.

Both reconciliation refs exist: displaced ref at `99b15a7e88006c142b01ac59160b86f28fd3a6a4`, rewritten ref at `b7180586c9b85fb2dbb8933e6f8aa348199362c9`. LAB-158 is `done`; registered clean worktree/local/remote execution branches all at `e6816416f659faa26daff20eb42e4279fddb249a`. This HEAD and the rewritten checkpoint are both ancestors of published `master` (`6e601522ad0aad3d8545f54d9e8b7a82e03b3b4e` at observation). **These facts do not alone authorize retirement of LAB-158**: its reconciliation binding and any pending recovery/resume contracts must still be respected.

# Confirmed producer/consumer contract defect

In **current** `src/devlegate/reconciliation_transactions.py`, `start_fresh_rewrite()` (or its relevant rewrite-init function) constructs `rewritten_evidence_ref = refs/devlegate/reconciliation/<ticket>/<execution>-rewritten` while retaining `displaced_evidence_ref`. `_resolve(..., evidence=rewritten_ref)` persists a `resolved / update-base` reconciliation with that rewritten ref as `evidence_ref`, and also establishes `resume_required`.

However, `src/devlegate/runtime.py::_assert_cleanup_has_no_execution_dependency()` compares `reconciliation["evidence_ref"]` **only** against `_reconciliation_evidence_ref(ticket_id, execution_id)` without the suffix. On mismatch it raises `WorkflowBlockedError("reconciliation dependency identity is invalid")` **before determining whether the reconciliation belongs to the cleanup candidate**. Consequently one valid rewritten LAB-158 record globally blocks cleanup of 75 unrelated done tickets. Ordinary canonical evidence refs must remain supported. This is reproducible via current supported rewrite flow, not a legacy compatibility migration.

# Desired small implementation

Fix the mismatch at the **shared reconciliation identity/validation boundary**, not by mutating SQLite, resetting reconciliation, deleting refs, or introducing a one-off LAB-158 migration.

Suggested low-complexity approach:

1. Locate and reuse existing reconciliation ref constructors, transaction/recovery validators, and dependency checks. Prefer a tiny **single** canonical helper that derives/validates the expected original and rewritten ref for a given ticket and execution (and, if needed, `displaced_evidence_ref` and rewrite state). Have cleanup and the relevant producer/recovery paths share it where practical. Avoid parallel suffix parsers or duplicating the large reconciliation schema validator.
2. Accept `evidence_ref == canonical` for ordinary reconciliation. Accept `evidence_ref == canonical + "-rewritten"` **only for a coherent, explicitly supported rewrite case**, with exact canonical `displaced_evidence_ref`, proper `resolution` / rewrite lifecycle information, and consistent ticket/execution identity. Do not accept arbitrary suffixes, cross-ticket refs, missing/inconsistent displacement provenance, or unrelated resolved records carrying a rewritten ref. Check existing recovery/transaction semantics first; no unverified new schema fields.
3. Preserve **fail-closed** behavior for malformed or ambiguous ownership and all genuinely related resource bindings. Once a reconciliation identity is verified, it must *not* hold an unrelated done-ticket workspace hostage. Do not blindly ignore invalid reconciliation in unrelated candidates if it may obscure ownership. For LAB-158 itself preserve any needed reconciliation/rewrite/resume resources and evidence; do not automatically clear `resolved` or assume it means dispensable.
4. Keep TASK-099's unified cleanup eligibility (done, exact registration/head, cleanliness including submodules, published product ancestry, no active dependencies), ref leases, idempotent partial-retirement replay, and single-owner scheduling unchanged. Changes should be narrow, preferably confined to an existing reconciliation/identity helper plus minimal runtime wiring and focused tests; do **not** refactor the entire recovery engine, add user-configurable modes, or create a migration framework.

# Acceptance and regression tests

- Construct a **real-Git** rewrite reconciliation by invoking the current producer/update-base-with-published-lineage-rewrite flow where practicable, not only by hand-writing a JSON dict; inspect persisted ref names and state, then evaluate cleanup of a *different* durably done, clean, ancestry-published worktree. It must be **eligible and retire successfully** despite LAB-158-equivalent resolved rewrite reconciliation; all original/rewritten reconciliation evidence refs must remain untouched.
- Include a precise regression fixture for the incident's resolved record shape as a secondary focused test, so the historical real-world case stays covered.
- A genuinely related execution worktree remains blocked while reconciliation or `resume_required` can still need it. Verify that unrelated workers/retries are not unintentionally blocked. Include ordinary (non-rewritten) `evidence_ref` behavior.
- Negative tests reject tampered suffixes/other-ticket identities, missing or contradictory displaced refs, invalid lifecycle combinations, and incomplete/ambiguous evidence, without deleting resources. Distinguish an expected `ticket is not durably done` rejection from global bad-reconciliation rejection.
- Preserve existing TASK-099 tests, especially crash-between-retirement-stage replay, remote-ref CAS/lease, foreign/unregistered worktree protection and bounded background progress. Run focused checks, Ruff and full exact-SHA CI. Provide before/after diagnostic evidence: unrelated cleanup no longer reports 75 global `reconciliation dependency identity is invalid` blocks. **Do not run destructive tests against the operator's actual rslab2 workspace.**

# Scope boundary

This ticket addresses a presently reproducible **producer/consumer inconsistency**, not cleanup log observability (TASK-093), global historical evidence migration, or deletion of LAB-158's saved reconciliation. Preserve durable records and provenance refs. Minimize code growth and prefer a shared proof over special-casing `-rewritten` directly in the workspace cleanup loop.
