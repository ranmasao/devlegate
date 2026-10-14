---
"type": "devlegate.ticket"
"title": "Consolidate low-level Git operations and remove duplicated runtime wrappers"
"depends_on": ["TASK-061"]
---

## Milestone

Devlegate 0.5.7 — KISS/DRY infrastructure refactor. TASK-061 is already completed. This is **not** another workflow/reconciliation redesign.

## Goal

Consolidate *repeated, genuinely low-level* Git command execution and primitives into one small internal Git infrastructure module (for example `src/devlegate/git.py` or `git_ops.py`). Reduce boilerplate and unnecessary owner-proxy wrappers while **preserving every existing state-transition, Git safety and public behavior**.

Do not create a separate Python distribution, adopt GitPython/pygit2, or build a generic Git framework. Use stdlib and the installed `git` CLI.

## First: inventory existing code; implement the small proven overlap

Inspect Git invocations and existing helpers in `src/devlegate`, notably `runtime.py`, `execution_workspace.py`, `recovery_workspace.py`, `recovery_transactions.py`, `recovery_workflow.py`, and `reconciliation_transactions.py`. Include `_git()`, `_git_runtime()`, `recovery_git()`, `reconciliation_git()`, and direct Git subprocess invocation where present.

Identify:
- duplicated subprocess invocation, cwd/env/output/return-code handling;
- duplicated **read-only** primitives: HEAD/ref resolution, commit parents, rev-list, ancestry, clean/status checks;
- duplicated **mutating** primitives: `update-ref` expected-old/CAS, Git worktree operations, rebase/reset/push, **only where the low-level behavior and preconditions really match**;
- owner methods that are trivial forwards to a common Git executor, vs methods enforcing substantial locking, authorization, WAL, provenance, evidence or publication rules.

Document a *brief* actual inventory and intended consolidations in the implementation report; avoid a long speculative design analysis. Start by reusing an existing canonical executor/helper if one already serves the need. **Do not migrate one-off commands solely to grow the API.**

## Responsibilities and boundaries

1. **Low-level Git infrastructure** owns execution of `git` commands, path/cwd handling, output/exit-status mechanics and small shared operations over commits, refs and worktrees. It knows nothing about Devlegate tickets, lifecycle phases, workers, reconciliation, evidence policies or WAL.
2. **Higher-level Git helpers**, where genuinely reused, may compose low-level operations for domain-independent questions such as proving a linear parent chain. These may live in the same small module initially; avoid creating many one-function files.
3. **Devlegate workflow/recovery/reconciliation** retains domain decisions: what ref may move, permissible worktree ownership, expected checkpoint/lineage, when authorization and evidence are required, WAL order, safe rollback and retry. Do **not** move these transactions into a generic Git layer.

Read-only commands may be accessed through shared helpers. **Mutations must remain under the existing serialized ServiceEngine owner and its required guards**. A shared `git.update_ref(...expected_old...)` or lease-push helper is only a mechanism; it must not bypass admission/TOCTOU verification, durable intent, single-owner authorization, worktree provenance or expected-old Git CAS. Keep the ordering of evidence pinning before publication.

For direct methods such as `owner.recovery_git` and `owner.reconciliation_git`, eliminate wrappers **only if** their implementation merely forwards to common Git execution and removing them does not bypass any enforced invariant. Preserve effectful owner capabilities with real semantics; avoid replacing them with unsafe direct calls just to reduce LOC.

Preserve exact error handling and Git exit-status interpretation (including `check=False`, missing refs, races and retries). Do not silently turn a failed Git probe into an exception or a missing ref into a successful empty result.

## Implementation scope and KISS/DRY

- Extract the **smallest coherent** set of reusable primitives observed in actual code, convert their call sites, and delete genuinely redundant wrappers/implementations.
- Prefer plain functions and existing result types to new classes/protocol hierarchies. No generic command registry, dynamic dispatch, new object model or multiple competing Git backends.
- If two similar-looking command sequences have distinct preconditions, preserve them separately; DRY does not override correctness.
- Keep any remaining one-off Git command plainly readable through the common command runner rather than inventing a thin named helper for each.
- Do not expand into `_run_iteration_body()` decomposition, TASK-063 worktree retirement, TASK-066 logging, packaging, CLI changes, or unrelated architectural refactoring.
- Preserve the public API, state schema, existing command semantics and published 0.5.6 artifacts.

## Tests: minimal additions

**No new broad integration or end-to-end suites are required solely because code moved.** Reuse the existing Git/CAS/workspace/recovery/reconciliation and IPC regression suites as authoritative proof that behavior is unchanged.

Add **only focused unit tests for newly extracted primitives when not already adequately covered**, especially their handling of missing refs, nonzero exit codes, expected-old CAS conflicts and command-result semantics. Existing tests may be adjusted for import/mocking boundaries without weakening assertions. If the migration exposes a *specific previously untested safety regression*, add the smallest direct regression for that case; do not create redundant scenario tests or perform a test-framework rewrite.

Run full existing pytest/coverage and Ruff on the exact checkpoint (CI); do not skip prior integration tests.

## Acceptance criteria

- One obvious low-level Git command execution path (or a clearly justified small number of distinct paths) with duplicated mechanics consolidated.
- Actual repeated primitives are centralized without unnecessarily wrapping every command; redundant owner passthroughs removed where safe.
- Domain-level Git decisions and all owner/WAL/lease-CAS/provenance/evidence protections remain in their existing owners and ordering.
- No new framework/dependency/distribution, no unrelated changes.
- Existing full test suite and Ruff pass on the exact checkpoint. Report actual deleted duplication and any intentionally retained higher-level Git operations; do not optimize merely for lower LOC.
