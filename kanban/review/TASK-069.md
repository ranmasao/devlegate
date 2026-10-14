---
"type": "devlegate.ticket"
"title": "Consolidate repeated Git history and reference primitives"
"depends_on": ["TASK-068"]
---

## Goal

Continue the TASK-067/068 Git infrastructure refactor. Reduce repetitive low-level Git command construction and result parsing in `runtime.py`, while keeping service workflow decisions, authorization, and durable state management in their existing modules.

## Work

- Inventory actual repeated patterns in `runtime.py` and existing Git/recovery/reconciliation helpers. The previous checkpoint had approximately 68 `rev-parse`, 19 `merge-base`, 8 `rev-list`, and 10 `update-ref` calls (approximate source scan; recount before modifying).
- Prioritize reusable read-only mechanisms: resolving references and commits, checking ancestry, and enumerating parents or history. Reuse `git.resolve_commit`. Distinguish missing objects, negative ancestry results, malformed input, and Git execution failures.
- Factor only genuinely shared, domain-neutral behavior into the existing `git.py` or another already suitable module. Migrate equivalent call sites; leave commands with different semantics explicit.
- Evaluate repeated conditional `update-ref` mechanics. Factor only when expected-old conditions and error semantics coincide. Leave decisions regarding evidence, publication, retries, recovery, and authorization in the current domain modules.
- Avoid broad refactoring of fetch/push/rebase/reset/worktrees, service iteration or worker lifecycle. Do not create generic Git frameworks, additional Protocol hierarchies, external dependencies, or one-line helper catalogs.
- Preserve all Git result and exception semantics, owner-held mutation safeguards, WAL ordering, evidence preservation, and optimistic concurrency checks. Repeated observations at mutation boundaries are intentional.

## Validation

Keep existing end-to-end and regression coverage. Add only focused unit tests for new primitives where not already covered, or one narrow test for a newly found safety gap. Run the full test suite and Ruff on the exact checkpoint.

Report before/after raw Git call counts, actual duplicate implementations removed, and deliberately retained operations with different semantics. Completion means substantive consolidation of repeated low-level operations, not just renaming the Git executor.
