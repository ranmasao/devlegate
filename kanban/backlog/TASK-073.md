---
"type": "devlegate.ticket"
"title": "STORY acceptance gate: isolate Git implementation from ServiceEngine"
"depends_on": ["TASK-071", "TASK-072", "TASK-078", "TASK-079", "TASK-080", "TASK-081"]
---

## Purpose: parent story and final curator acceptance

This is the **parent story and final acceptance gate** for the Git-layer refactor. It is intentionally represented as an ordinary Devlegate ticket: the current workflow does not support native Epic/Story metadata. The completed milestones TASK-061 and TASK-067 through TASK-070, plus the completed TASK-071 and TASK-072 and follow-up implementation tasks TASK-078 through TASK-081, are child work toward **one architectural outcome**. Passing individual ticket tests or reducing a command counter is not proof this story is done.

This ticket runs only after TASK-071 and TASK-072 reach `done`. It is an **audit/report task**, not permission to silently redesign the system. Return any remaining gaps for focused follow-up work. **Final acceptance requires an explicit human curator decision, not an automated self-approval or merely green CI.**

## Curator's intended architecture (normative)

Git is Devlegate's supported first-class VCS backend. **Do not build a generic VersionControlSystemInterface**, plugin framework, or speculative backend portability layer.

Separate concerns without increasing indirection gratuitously:

1. **CLI transport**: the installed Git command runner, process lifecycle, captured output and OS/execution failures.
2. **Typed Git semantics**: commit/ref/object resolution; ancestry and history; worktree/branch inspection; blob/tree reads; ref creation/CAS and other command-level effects. Explicitly distinguish missing/negative results from Git observation errors.
3. **Domain Git workflows**: control/product synchronization, checkpoint publication, evidence pinning, recovery, reconciliation and lifecycle effects under their distinct constraints.
4. **ServiceEngine**: admission, serialized ownership/state transitions, scheduling, WAL orchestration and policy decisions. It uses semantic operations, not embedded raw Git command strings.

## Strict story-level acceptance criteria

All conditions must be verified on the **integrated product HEAD**, not inferred from individual worker reports:

1. **No scattered raw Git subcommand calls in `src/devlegate/runtime.py`.** No direct `_git(repo, "rev-parse", ...)`, `_git(..., "update-ref", ...)`, `self._git_runtime(..., "fetch", ...)`, `subprocess` invocation of Git, or equivalents constructed indirectly throughout ServiceEngine. At most a single *owner/shutdown-aware command transport seam* may remain, with no command-specific business logic. Any other exception is a **failure pending explicit curator approval**; workers cannot exempt their own calls.
2. **No fake decomposition.** Replacing raw commands by equally low-level one-line forwarding methods does not qualify. Git command construction and result parsing belong in cohesive infrastructure/domain modules; `runtime.py` must express business operations. Demonstrate real responsibility boundaries and no avoidable circular imports or Protocol inflation.
3. **Errors are meaningful and fail closed.** Distinguish absent ref, valid negative ancestry, unreachable/corrupt repository, malformed Git output and execution/OS errors. `run_git()` transport behavior must be explicit and stable; semantic helpers must not silently turn arbitrary Git failures into absence or permission to repair/create. Handle root commits correctly. Map typed Git failures to appropriate Devlegate domain errors at entrypoints; retain owner-bound shutdown interruption semantics.
4. **Transactional invariants preserved.** Owner lock/authorization and mutation admission; WAL ordering; evidence pin before publication; expected-old ref CAS; remote lease checks; re-observation before effects; crash/retry/reconciliation continuation; control/product/workspace provenance and isolation. Do not weaken these to achieve a counter target.
5. **Behavior and compatibility unchanged** except narrowly justified correctness fixes. Existing focused and full CI proof must be green on the final exact SHA, including pytest and Ruff. Add only focused tests for newly factored semantics or discovered gaps; do not duplicate broad E2E suites.
6. **Quantitative, traceable audit.** Inventory *all* prior direct Git call sites, grouped into read/mutation/transport, and map them to new semantic operations or domain modules. Count remaining raw command construction in `runtime.py` (target **zero** outside the one narrowly justified transport seam), direct subprocess executions, source LOC and module dependency edges before/after. Explain unchanged operations and any extra layers. Do not misrepresent fewer source call sites as fewer Git processes.
7. **No hidden Git executions or effects; tiered cost.** For representative read, sync, publication and recovery paths, compare *executed Git command traces* before and after, including every invocation internal to newly factored helpers and any subprocess spawned outside `run_git`. Each new helper must have a documented command/effect contract; a semantic read helper may not silently fetch or mutate. Classify every executed command as (1) local-only read, (2) remote read without mutation, or (3) local/remote state-changing operation, using the highest applicable tier for mixed-effect commands (ordinary `fetch` counts as a write because it updates local Git state). The tiers define **priority of removing newly introduced invocations**, not permission to create a priced number of them: eliminate new tier-3 writes first, tier-2 remote reads next, tier-1 local reads last. Every new invocation, including a cheap local read, is a potential regression requiring a command-level explanation. Neither latency arguments nor a clean abstraction automatically justify increases. Only truly necessary new safety checks may remain, explicitly disclosed for the curator's decision; do not remove required CAS/TOCTOU checks to force a lower count. Track local vs remote mutations separately. No hidden subprocesses, network/write effects, background operations, or repeated `N+1` Git reads. Preserve intentional TOCTOU re-observation and CAS. Tier labels represent priorities, not fixed numerical performance weights.
8. **No premature closure.** The story cannot be accepted merely because TASK-071/072 are accepted, because CI is green, or because individual subprocess patterns were deduplicated. If ANY criterion is unmet, report precisely which, cite relevant paths/functions, mark worker result `incomplete` with actionable remaining work, and leave final acceptance to the curator.

## Audit verification scope — reuse CI, avoid duplicate full suite

This parent story is an **audit/report task** after TASK-078 through TASK-081 are `done`, not an implementation worker pass. Read `tests/TEST_BOUNDARIES.md`, `tests/TEST_PLACEMENT.md`, `tests/FOCUSED_TEST_AUDIT.md` and the integrated code and CI evidence. Verify the full-suite GitHub Actions coverage and Ruff result for the **exact integrated product SHA**; do not rerun all pytest merely to restate an already-proven CI result.

For a missing Git command trace or a disputed isolated invariant, prefer narrowly selected checks such as:

```sh
./dev test -q tests/test_git.py tests/test_control_git_workflow.py
./dev test -q tests/test_daemon_cross_layer.py -k "control_sync_trace or merge_pending"
./dev test -q tests/test_recovery_boundaries.py -k "lineage or evidence or rewrite"
```

Do not run unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, or `./dev coverage` as routine audit work; do not rerun unrelated production-topology and packaging suites. Targeted tests may supplement the requested executed-command accounting but **cannot substitute for the complete audit table or green exact-SHA CI**. A failed/missing CI run is a curator-visible blocker, not a mandate for an agent to exhaust its time budget on repeated full local suites. Human curator `APPROVE` remains mandatory.

## Acceptance audit deliverable

Prepare a concise evidence-based table for criteria 1–8: PASS/FAIL, source references and numeric before/after inventory, including executed-command counts **per effect tier** and a justification for **every added command, even local read-only**. Include a short remaining-risk section, especially around `_git_runtime`, `resolve_commit`, CAS and publication. Audit only; do not self-authorize acceptance or introduce broad code changes.

**Human curator's explicit APPROVE is the only completion signal for this parent story.**

## Reviewer decision — 2026-10-16: STORY AUDIT INCOMPLETE; return to todo

**Reviewed execution:** `9ada49c1cd904f18b90e63c0b5be7b02`, zero-delta checkpoint `a30447295d6423da442aab23dbb00623090e91ad`, exact integrated product HEAD `release/0.5.7`. Atomic report-and-review admission: control commit `657165952213c5500d3eb0e2ef8fc38ba15bfb68`. Execution result is `incomplete` with five remaining items and one curator question. No adjacent MD audit artifact was published (`report_artifact=null`); the execution claim is NOT a complete criteria 1–8 evidence table.

### Correct audit scope (resolve worker question deterministically)

Audit the exact **integrated product HEAD**, including previously integrated TASK-077 provenance hardening and TASK-071/072. Do **not** audit an invented TASK-071/TASK-072-only branch/range or exclude an integrated fix. This follows the normative story gate and requires no separate curator choice.

### Independent acceptance matrix

| # | Finding | Source / evidence |
| --- | --- | --- |
| 1 | **PASS** for the narrow runtime argv criterion. No scattered raw Git argv construction remains in `runtime.py`; the one `_git_runtime()` seam calls `_git(repo, *args)` and contains a retry. | `src/devlegate/runtime.py::_git_runtime`, lines ~2568–2588 |
| 2 | **PARTIAL / NOT PROVEN story-wide.** Cohesive `control_git_workflow` and recovery modules are real architectural improvements, but several domain helpers still construct raw Git commands and have no demonstrated unified runner/error ownership. | `src/devlegate/execution_workspace.py`, `recovery_transactions.py`, `recovery_workspace.py`, `reconciliation_transactions.py` |
| 3 | **PARTIAL.** `git.py` has typed ancestry/observation helpers, but remaining raw calls need individual error-semantic review. For instance, `recovery_transactions.reconcile_control_lineage` treats any nonzero `run_git(..., "rev-parse", ..., check=False)` as an absent evidence ref without distinguishing an unreadable/corrupt repository. | `src/devlegate/recovery_transactions.py` lines ~60–76; `git.py` |
| 4 | **UNVERIFIED** across remaining direct Git entrypoints. Some transactions enforce locks and CAS themselves, but their direct unbound `run_git` paths have not been traced for full owner/shutdown/interrupt semantics. Do not assert that all invariant failures are real bugs without focused reproduction. | `recovery_transactions.py`, `recovery_workspace.py`, `reconciliation_transactions.py`, `execution_workspace.py` |
| 5 | **PASS** for existing exact-SHA CI, not a waiver of architectural proof. Pytest+coverage and Ruff succeeded on the integrated HEAD. | [GitHub Actions #38965620567](https://github.com/ranmasao/devlegate/actions/runs/38965620567) |
| 6 | **FAIL** as an acceptance deliverable. The audit did not supply a complete baseline→integrated call-site and module-edge inventory or the required numeric executed-command comparison. Textual `_git(...)`/`run_git(...)` occurrences in `runtime.py` are ~183 at `release/0.5.5` versus 2 in the current product; **this is a source-text count, not subprocess execution cost**. The module's size is ~8606 vs ~8578 lines. | `src/devlegate/runtime.py` at `release/0.5.5` and integrated `release/0.5.7`; `docs/GIT_COMMAND_INVENTORY.md` |
| 7 | **FAIL** for complete trace/effect proof. `docs/GIT_COMMAND_INVENTORY.md` has validated full runner traces for current/fast-forward control sync and control attach/bootstrap; publication, evidence pin, accepted integration replay and interrupted recovery are explicitly labeled **operation subsets**, not complete executed-command traces. More downstream direct-Git processes are not yet inventoried, nor is the `merge_pending` retry's conditional extra invocation. | `docs/GIT_COMMAND_INVENTORY.md`; `tests/test_daemon_cross_layer.py`; `runtime.py::_git_runtime` |
| 8 | **NOT SATISFIED.** This is a curator-only architecture gate, and there is no explicit human `APPROVE`. Do not promote a worker's audit or green CI to approval. | This ticket's normative acceptance policy |

### Specific architectural blockers for curator decision

1. **Transport policy:** `_git_runtime()` mixes owner/shutdown checks with a `merge_pending`-dependent retry of the *same arbitrary Git command* after `ShutdownInterrupted`. This can execute a mutating command twice and must be classified and justified or factored into the appropriate domain policy without weakening interruption safety.
2. **Semantics and ownership:** Audit remaining direct `run_git`/`_git` sites in `recovery_transactions.py`, `reconciliation_transactions.py`, `recovery_workspace.py`, and `execution_workspace.py`. Distinguish cases safely covered by the caller's owner/lock from any actual bypass; do not infer that every raw call is automatically a security defect. Identify concrete fixes for unchecked error/absence distinctions.
3. **Evidence:** Provide complete executed-command traces, including nested invocation counts and Tier 1/2/3 effect classifications, for representative read, publication, evidence pin, recovery and reconciliation paths, not just partial operation sequences. Any additional Tier-1, Tier-2 or Tier-3 commands require explicit justification. Clearly separate observed counts from inferred parity.
4. **Human authority:** Only an explicit curator `APPROVE` can close TASK-073. Curator must decide whether the architecture gaps should be addressed by focused implementation work before repeating the final story acceptance audit.

**Scope control:** TASK-073 remains an **audit/report task**, not authorization for a worker to implement the above refactors silently. Its next attempt must produce the missing referenced criteria matrix and a concrete, bounded implementation-gap handoff, or remain `incomplete`. Do not edit existing execution evidence, rewrite product history, or automatically self-approve. Preserve `TASK-073` ID and all earlier accepted child work.

**Reviewer disposition:** move this same ticket `kanban/review/TASK-073.md → kanban/todo/TASK-073.md` with these audit findings. No accepted/done transition.

## Curator scheduling decision — 2026-10-17: deferred follow-up gate

The prior incomplete execution and reviewer audit above are retained as historical evidence, not deleted or rewritten. The curator has deferred this same TASK-073 to `kanban/backlog` while TASK-078 (transport interruption), TASK-079 (recovery observations), TASK-080 (execution workspace) and TASK-081 (reconciliation) are implemented. These tasks are strict prerequisites for any new final audit; their accepted/review states do not satisfy dependencies until `done`.

When this story is explicitly promoted to `todo` after dependencies are complete, start a **new audit execution/generation**, bind it to the then-integrated product HEAD, and independently verify criteria 1–8. Never reuse or edit the prior execution result `9ada49c1cd904f18b90e63c0b5be7b02` as acceptance evidence for the new gate; only an explicit human curator APPROVE can close TASK-073. This scheduling deferral is not an implementation specification for automatic stale-execution invalidation or retry authorization.

## Supplemental architectural audit context (not a new acceptance criterion)

A reproducible **static** scan of a 0.5.7 snapshot reported `runtime.py` at 11,351 physical LOC (481,365 bytes, ~46% of analyzed package bytes), 22 internal import targets, and a 1,292-line `_run_iteration_body` with 49 distinct direct `self` callees. Already-extracted `git.py` (~995 LOC), `execution_workspace.py` (~828) and `reconciliation_transactions.py` (~568) show that moving functions out of `runtime.py` has **not** by itself proven transfer of semantic Git responsibility.

Use this only when interpreting the existing **no fake decomposition** and final-audit criteria. Require evidence of semantic ownership and full executed-command effects rather than using file LOC, import fan-out, or source-text command counts as proxies. Static self-call edges omit inner helper Git executions, callbacks and mutable state aliases. This note does not change the story's dependencies, mandatory human curator approval, or quantitative tiered command audit requirements.

## Final audit context update — 2026-10-26

TASK-099 and TASK-100 are now integrated and observed in production-like use: both Devlegate and rslab2 retired historical published worktrees while preserving non-`done` tickets and reconciliation evidence. They become mandatory current baseline for acceptance criteria 3, 4 and 7, especially exact ownership, fresh published ancestry, lease/CAS, partial retirement replay and rewritten evidence identity. These observations are operational evidence, **not** a substitute for full Git command/effect traces.

Explicitly measure repeated Git fetch and remote observations in the bounded 32-candidate screening and full diagnostic inventory (up to 10,000), distinguishing clean steady state from backlog catch-up. For each representative cleanup pass account for all nested Git executions; identify N+1 effects and whether one-candidate retirement unnecessarily forces repeated product fetches. Evaluate proposed cached/batched read-only inventory separately from authoritative pre-mutation fresh proof, preserving single-owner serialized lifecycle and no hidden mutation. This extends the existing command-accounting criterion, not a new architecture mandate. TASK-073 remains an audit-only human acceptance gate after dependencies; do not admit it early.
