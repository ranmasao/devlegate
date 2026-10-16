---
"type": "devlegate.ticket"
"title": "Resolve accepted provenance from the lifecycle admission, not the latest review edit"
"depends_on": ["TASK-071"]
---

## Incident and objective

On 2026-10-16, TASK-071 had valid exact-checkpoint code evidence and its worker lifecycle atomically added `executions/TASK-071/05f571e2....json` and moved `todo → review` at control commit `fd98d3f3`. A curator then appended review notes at `4f0558ab`, before the `review → accepted` move at `765b2eca`. The running Devlegate (product `507da8f926f9`) blocked with `accepted ticket TASK-071 has ambiguous or missing completed execution evidence` even though the original lifecycle report and checkpoint existed. Its `ServiceEngine._accepted_execution_report()` assumes the *last modification* of the review Markdown is the worker's original review-admission commit.

TASK-071 recovered operationally through a fresh worker lifecycle at `34c27f6f`, not by rewriting reports or history. Fix the underlying provenance resolver so an independent curator note does not masquerade as the lifecycle admission. Preserve the existing fail-closed guarantees.

## Architecture and scope (strict)

1. Implement the admission/evidence resolver in a **small dedicated module**, preferably `src/devlegate/accepted_provenance.py` (or a similarly cohesive existing provenance module). Do **not** put the new resolver algorithm, commit-history traversal, or Markdown interpretation in `src/devlegate/runtime.py`. Replace the existing long `ServiceEngine._accepted_execution_report()` selection algorithm with a thin call/owner-aware runner adaptation and domain error mapping. Keep `ServiceEngine` responsible for admission, owner serialization, workflow decisions, and state transitions.
2. Identify the **latest valid `todo → review` lifecycle event belonging to the current review generation**, not the latest ordinary edit of `kanban/review/<ticket>.md`. Prove the lifecycle commit's exact report/artifact additions, ticket move and unchanged original ticket content using existing exact-lifecycle proof semantics where possible. Reject unrelated or forged commits, ambiguous bindings, report replacement, inconsistent product/feature checkpoint and stale earlier admissions after `review → todo → review`. The immutable execution JSON remains the authority for worker identity; Git history proves its publication. Avoid ref-history heuristics based on merely matching a commit message.
3. Separately validate changes **after** the lifecycle admission. A review-only annotation must not be mistaken for a new worker admission. Accept subsequent edits only if their allowed non-execution character can be proven under a narrow, documented convention (for example, append-only and explicitly marked curator notes with the admission-era ticket text unchanged). If a post-admission change cannot reliably be distinguished from an altered specification or acceptance criteria, **fail closed with an actionable `review content changed after admission` diagnostic**, not `missing execution evidence`. Do not add a general Markdown review language, permit arbitrary appended instructions or implement the future review-artifact subsystem here.
4. Preserve the accepted-stage identity checks, existing review generation/remote-HEAD correspondence, expected-old/CAS and lease guarantees, WAL/recovery behavior, owner/shutdown-aware Git transport, and all negative-vs-failure Git semantics. Keep the resolver *read-only*: no hidden `fetch`, `push`, `update-ref`, worktree modification, network accesses or background subprocesses. Reuse existing observations and avoid history-scanning N+1 loops; disclose added Git commands and their cost.
5. Do **not** alter immutable execution reports, rewrite control/product/work branch history, change report schema, or implement a new ticket-review system. Do not broaden TASK-072 Git mutation scope.

## Mandatory maintenance docstring

Add a clear English docstring to the resolver's main entry point explaining **why this compatibility logic exists**, not just what it returns. The docstring must explicitly record:
- The 2026-10-16 TASK-071 failure mode: curator edits to `review/TASK.md` after the worker's atomic report+`todo → review` lifecycle publication.
- Why a review file's most recent Git modification is **not** the identity of the worker admission; the verified lifecycle event and immutable execution evidence are.
- That this rule bridges the current Kanban-in-Git representation and is a **candidate for simplification/removal** if review annotations are later stored separately and a stable explicit admission identifier replaces history inference.
- Conditions for safe removal: retain immutable report binding, exact admission proof, revision/retry disambiguation, and protection against post-execution specification changes.

This is a maintenance contract, not an invitation to add a future review system now.

## Focused regressions and verification

- Minimal real Git history mirroring `fd98d3f3 → 4f0558ab → 765b2eca`: report+ticket move in one commit, then append-only marked curator review note, then acceptance. Verify that the correct execution binds without treating the note commit as admission **only if** the annotation convention makes the edit provably safe.
- Plain `todo → review → accepted → done` remains unchanged; no false missing-evidence failure.
- Changed original ticket metadata/requirements or arbitrary unclassified body edits after admission are rejected with a specific diagnostic; never silently accept a modified specification.
- `review → todo → review` with a new execution selects only the newest valid generation, even with historical reports from previous attempts.
- Missing, duplicated, tampered, malformed or failed execution reports; wrong execution branch or published checkpoint; non-descendant/ambiguous Git history; unavailable Git observation: retain precise fail-closed behavior and no mutation.
- Integration/recovery regression coverage for accepted transitions, including concurrent control descendants and interrupted accepted-integration replay. Avoid rewriting or duplicating large E2E suites.
- Report `runtime.py` LOC and dependency changes; **no new resolver logic in runtime**. Demonstrate bounded read-only Git command traces (including helper-internal calls) and no unexpected effectful commands. Exact-checkpoint complete pytest, coverage and Ruff must pass.

## Relationship to future review artifacts

Review-history separation is **not** an implementation requirement in this ticket. The goal of that later design is to stop many obsolete curator review iterations from occupying the worker's active ticket context, while retaining their audit history outside the prompt. Do not create new review storage, CLI commands, metadata schema or migration in this bugfix.

## Acceptance

Curator verifies the real historical failure no longer confuses a curator edit with a worker admission, that altered requirements still fail closed, and that source responsibilities remain outside `runtime.py`. No automatic acceptance; report any unsupported post-review edit format explicitly rather than granting broad trust.
