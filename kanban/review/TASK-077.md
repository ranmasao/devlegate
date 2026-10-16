---
"type": "devlegate.ticket"
"title": "Resolve accepted provenance from the lifecycle admission, not the latest review edit"
"depends_on": ["TASK-071"]
---

## Incident and objective

On 2026-10-16, TASK-071 had valid exact-checkpoint code evidence and its worker lifecycle atomically added `executions/TASK-071/05f571e2....json` and moved `todo → review` at control commit `fd98d3f3`. A curator then appended review notes at `4f0558ab`, before the `review → accepted` move at `765b2eca`. The running Devlegate (product `507da8f926f9`) blocked with `accepted ticket TASK-071 has ambiguous or missing completed execution evidence` even though the original lifecycle report and checkpoint existed. Its `ServiceEngine._accepted_execution_report()` assumes the *last modification* of the review Markdown is the worker's original review-admission commit.

TASK-071 recovered operationally through a fresh worker lifecycle at `34c27f6f`, not by rewriting reports or history. Fix the underlying provenance resolver so an independent curator note does not masquerade as the lifecycle admission. Preserve the existing fail-closed guarantees.

## Architecture and scope (strict)

1. **Choose placement by existing ownership, not a mandatory new file.** First assess `execution_result.py`, which already owns `ExecutionReport` identity, validation and durable report storage. If the resolver remains a small, cohesive extension of that domain, implement it there. If correct reuse requires substantial extraction of lifecycle Git-history proofs shared by accepted integration and recovery (especially `ServiceEngine._lifecycle_commit_is_exact()` / `_control_report_at()`), a focused `execution_provenance.py` is justified instead; do not create an `accepted_provenance.py` merely to move one method. Preserve the separate responsibilities of `git.py` (Git-level semantics), `tickets.py` (ticket loading/scheduling), and `recovery_workflow.py` (crash replay), and avoid circular dependencies or unnecessary Protocols. **Do not put the new resolver algorithm, commit-history traversal, or Markdown interpretation into `runtime.py`.** Make `ServiceEngine._accepted_execution_report()` a thin delegation/error-mapping seam; preserve owner serialization, workflow policy, and state transitions. State the chosen placement, reused proof logic, and module LOC/dependency impact in the worker report.
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

## Reviewer decision — 2026-10-16: REWORK REQUIRED (return to todo)

**Reviewed execution:** `4a2852be423842bda3a3ae4ac77b1d1d`, checkpoint `2f5de60094cf553774e52a6b260ea6cd45d36076`. Worker concluded `completed` and [exact-checkpoint CI #38923462350](https://github.com/ranmasao/devlegate/actions/runs/38923462350) succeeded. The dedicated `execution_provenance.py`, reuse of lifecycle proof, maintenance docstring and thin runtime boundary are **accepted as implementation direction**, but the current implementation does not yet satisfy all TASK-077 requirements. This is a same-ticket hardening iteration, **not acceptance**.

### Blocking defect A — select the current admission generation

In `resolve_accepted_execution_report()`, `history_commits(..., "executions/<ticket>")` is traversed against **every** stored report, and all qualifying executions are appended to `matches`; `len(matches) != 1` rejects an otherwise valid latest generation.

**Required deterministic regression:**
1. Execution A publishes exact report+ `todo → review` at checkpoint H.
2. Curator returns the same ticket `review → todo` without changing the executed specification.
3. Execution B publishes a second exact report+`todo → review` at the **same checkpoint H** (no new product delta).
4. Curator performs `review → accepted`.
5. Only B, the execution bound to the current review-admission generation, can authorize integration; A remains immutable historical evidence and must not cause spurious ambiguity.

Identify the **latest valid admission belonging to the currently accepted review generation**, not merely the newest report by timestamp, the last edit to `review/T.md`, or all reports sharing a SHA. A tampered/invalid latest generation must fail closed; do not silently fall back to an earlier matching execution. Also cover a newer generation with a different checkpoint. Preserve the original exact-commit, report/artifact, branch/remote-checkpoint, ticket specification and curator-annotation checks.

### Blocking defect B — eliminate the history × reports Git-process explosion

The nested `for commit in history: for report in reports:` invokes `lifecycle_commit_is_exact()` for cross-products of unrelated history entries and execution reports. Each exact proof performs multiple Git observations, so static loop complexity can produce quadratic *executed Git subprocess calls* as a ticket accumulates retries.

Resolve candidate commit/report association without this cross-product: use an indexed report lookup and proof of the relevant admission/generation, or another narrowly justified bounded approach. Do not add caching frameworks, a general history API, hidden network calls, or new mutation paths.

**Required regression/measurement:** build Git histories with multiple prior executions (including at least two report+review generations), instrument the actual Git runner, and show command counts per candidate/history size and effect tier before/after. Prove no quadratic amplification, no hidden `N+1` calls, and no new `fetch`/`push`/`update-ref` or remote observations. Preserve necessary TOCTOU checks and fail-closed errors; document any unavoidable extra read command for curator review.

### Completion of this hardening pass

- Keep `execution_provenance.py` as the owner if it remains cohesive; do **not** move resolver logic into `runtime.py` or add a separate review-artifact system. Retain the explanatory docstring and the established review-note convention.
- Add focused real-Git regressions for both defects, including stale generation versus current generation, identical checkpoint across two executions, tampering/invalid latest evidence, and command-count bounds. Retain existing tests for marked curator notes and rejected unclassified edits.
- Verify accepted integration and recovery still use correct lifecycle proof semantics; full exact-checkpoint pytest with coverage and Ruff must pass. Report changed LOC, dependency edges, and actual Git subprocess traces, not only helper call counts.
- Do not change historical `executions/` artifacts, rewrite existing feature/product/control history, or self-promote `todo → review`. Let the normal Devlegate worker lifecycle publish the new report and transition.

**Reviewer disposition:** `kanban/review/TASK-077.md → kanban/todo/TASK-077.md`, preserving the same ticket ID and original specification.
