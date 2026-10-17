---
"type": "devlegate.ticket"
"title": "Separate Git transport interruption from domain retry policy"
---

## Milestone

Git architecture hardening: complete TASK-073's transport/ownership findings without redesigning the VCS layer.

## Goal

Make Git execution a predictable owner/shutdown-aware transport, and move command-specific retry decisions to the workflow that can prove whether a previous mutation took effect.

## Context

At integrated product `release/0.5.7`, `ServiceEngine._git_runtime()` runs `_git(repo, *args, check=False)` and, after `ShutdownInterrupted` under persisted `merge_pending`, repeats the same arbitrary command. This is a structural risk, not proof that a particular mutation has already been corrupted. Existing WAL, CAS, shutdown and reconciliation checks are authoritative and must be preserved.

## Required behavior

1. Inventory actual callers and command classes that reach the `merge_pending` interruption branch; distinguish local observations, idempotent effects, conditionally repeatable mutations, and nonrepeatable mutations before changing the code.
2. Restrict `_git_runtime()` to owner authorization, shutdown/interruption observation, execution and transparent result/error propagation. Do not automatically rerun an arbitrary command from this transport seam.
3. Where the previous retry exists for a legitimate `merge_pending` recovery reason, retain the required behavior in the owning domain transaction: re-observe durable WAL stage and Git state, prove exact prior effect or its absence, then choose continuation, explicit safe retry, or fail-closed. Never infer absence solely from an interrupted subprocess.
4. Preserve serialized ServiceEngine authority, worker/process shutdown semantics, mutation admission, evidence-before-publication, expected-old/CAS, remote leases, required re-observation, and explicit operator retry barriers.
5. Keep Git first-class; reuse existing typed operations and domain workflows. Do not introduce a VCS abstraction, parallel executor, hidden process launch, or opportunistic unrelated refactoring.
6. Document before/after full executed-command traces for affected interrupted and normal paths, including nested calls and Tier 1/2/3 effects, identifying each newly added or eliminated invocation.

## Acceptance criteria

- `_git_runtime()` contains no mutation-specific or generic retry after an ambiguous interruption; the only permitted retries are explicit, evidence-backed decisions in the owning workflow.
- Read-only and mutation paths enforce the existing owner and shutdown authority. An ambiguous mutation never silently executes twice.
- Normal control sync, publication and recovery behavior stays compatible, including existing durable replay paths and legitimate `merge_pending` completion.
- Focused tests demonstrate commands actually executed, no hidden network/write effects and preserved CAS/TOCTOU checks; any new Git invocation of any tier is itemized and justified.
- Complete pytest, coverage and Ruff pass on the exact implementation checkpoint.

## Required regressions

- Setup: Git read observation interrupted before completion; action: invoke owner runner; expected: no hidden retry and a correctly classified interruption.
- Setup: ref update interrupted before it can mutate; action: run domain recovery; expected: fresh observation allows only an explicitly proven safe continuation.
- Setup: ref update completed but its result was lost to shutdown; action: replay pending transaction; expected: observe the existing exact effect, without a second arbitrary update.
- Setup: `merge_pending` after uncertain merge/push; action: restart; expected: WAL and Git proofs decide resume/stop, never generic duplicate mutation.
- Setup: mutating call outside the service owner or after shutdown admission closes; action: execute Git; expected: rejected with no mutation.
- Setup: active manual-retry barrier; action: restart; expected: it remains authoritative rather than becoming an automatic retry.

## Reviewer decision — 2026-10-17: REWORK REQUIRED (same ticket)

**Reviewed execution:** `60adf60d92a94577b0c43e9e9de9f1f3` (worker claim `completed`); exact feature checkpoint `d151f67b1327bfd8b4640fa489bdd6e166d20969`; integrated product base `a30447295d6423da442aab23dbb00623090e91ad`. Atomic lifecycle admission: control `e98fc89229ae4d92b340686ae05dff18f6c2fb0a`. CI [#39002212722](https://github.com/ranmasao/devlegate/actions/runs/39002212722) succeeded at the exact feature SHA. The adjacent worker report is empty, and the claim alone is not acceptance evidence.

### Verified work to retain

- `ServiceEngine._git_runtime()` no longer repeats arbitrary commands after signal interruption under `merge_pending`. The interrupted fetch test now correctly observes one attempt, not two.
- The fast-forward retry was restricted to `_merge_fast_forward_runtime()`, with an exact `HEAD` observation after interruption and a bounded second `merge --ff-only` when the old `HEAD` is still observed.
- Existing control/product merge regressions pass, and the inventory documents the intended new interruption trace. Preserve these improvements and their test coverage.

### Blocking defect 1: retry merges a mutable ref rather than the proved target

In `src/devlegate/runtime.py::_merge_fast_forward_runtime(repo, remote_ref, expected_old, expected_new)`, when the first `merge --ff-only remote_ref` raises `ShutdownInterrupted`, the handler observes only `HEAD`. If `HEAD == expected_old` and phase is `merge_pending`, it reruns `merge --ff-only remote_ref` **without verifying that `remote_ref` still resolves to `expected_new`**. A concurrent tracking-ref update can make the second merge move the checkout to a third revision, which was never admitted by the original WAL stage. The later post-merge equality check does not undo that mutation.

This is reproducible using real local Git: create linear A → B → C; admit `expected_old=A` / `expected_new=B`; simulate an interrupted merge before any effect; advance `origin/master` to C by a separate fetch; rerun `git merge --ff-only origin/master`. Git exits successfully with `HEAD=C`, **not B**. No external concurrency beyond Git's normal mutable ref semantics is needed.

**Required hardening:** make the authorized exact target immutable throughout interrupted continuation (e.g. merge the verified expected commit object, or fail closed if the tracking ref changed before retry). Validate correct branch, target and required safety conditions *before* any second mutation. Assert exact target after effect as well. Avoid adding remote fetches or reads unless their safety role is explicit and accounted for.

### Blocking defect 2: product `merge_pending` does not authorize control merge retry

`_sync_control()` runs before product synchronization; its merge callback now also calls `_merge_fast_forward_runtime()`. The check `self._state.get("phase") == "merge_pending"` identifies the persisted **product** sync stage, not an admitted **control** merge to the passed `expected_new`. The comment that this phase proves the *exact* fast-forward mutation is therefore untrue for the control call path. A signal-interrupted control merge must not be silently retried on the strength of unrelated WAL state.

**Required hardening:** separate product merge recovery from control-sync interruption policy. For control merge, either propagate interruption and let a subsequent safe, fresh control sync handle it, or establish an independently proved control transaction admission. Do not add a broad retry authorization merely because the operation is a fast-forward.

### Blocking gap 3: ambiguous post-interruption worktree/index state and missing negative proofs

A matching `HEAD` alone does not prove that the interrupted Git merge left the index/worktree untouched, or that branch/worktree identity stayed constant. `expected_new` as observed `HEAD` likewise needs the existing cleanliness/identity contracts before declaring the domain transaction complete. Prove safe recovery or fail closed when partial effects cannot be ruled out. Do not reset/clean unknown state speculatively.

### Mandatory focused regressions and evidence

1. Real Git or realistic injected command trace: A → B → C and tracking ref advances B → C between first interrupted merge and retry. **No mutation past the admitted B**; either exactly B or a safe block.
2. Product merge: SIGINT before mutation, after exact target reached, HEAD moved unexpectedly, and index/worktree dirty/ambiguous; prove correct continuation or safe stop, with no duplicate arbitrary mutation.
3. Control merge interrupted while product phase is `merge_pending`: no reuse of product WAL to justify a control mutation.
4. Unrelated read/fetch/ref-update/push interruptions still execute only once; owner/shutdown authority and explicit manual-retry barrier remain unchanged.
5. Document complete before/after affected executed Git command traces (including helper-internal observations and mutations) with Tier 1/2/3, local/remote write distinctions and justification of every added invocation; correct any claim that HEAD-only observation proves non-occurrence of the interrupted mutation.
6. Full exact-checkpoint pytest, coverage and Ruff green after hardening.

**Reviewer disposition:** move the SAME `TASK-078` from `kanban/review` to `kanban/todo`; do not change work branch, execution report, product history or checkpoint. This is rework of the existing execution, not a new ticket. TASK-079 remains dependent on TASK-078 reaching `done`.

## Reviewer decision — 2026-10-17, execution 7a767939: REWORK REQUIRED (CI + evidence)

**Exact execution:** `7a767939284e45bebb550f41e0178daa`; checkpoint `6ca7c6097f2786f54a2b1d376df85b2dc685f116`; base `a30447295d6423da442aab23dbb00623090e91ad`; lifecycle control `d091ccaf7be483d45dc29d088b75ae7ebde3018c`. Worker claim is `incomplete`, with one remaining item: full pytest did not finish during worker runs. **Independent completed GitHub CI [#39007001890](https://github.com/ranmasao/devlegate/actions/runs/39007001890)** provides the exact failure: **1239 passed, 1 skipped, 1 failed** in 279.85 seconds. Ruff was **skipped**, not proven green.

### Verified and retained

- `_merge_fast_forward_runtime()` now validates the expected tracking ref, merges the immutable admitted `expected_new` commit object (not a mutable tracking ref), re-observes the product branch, worktree identity, clean status and exact HEAD, and only retries under matching product `merge_pending` WAL coordinates.
- `_sync_control()` no longer injects the product fast-forward retry callback; an interrupted control merge propagates rather than borrowing product WAL authorization.
- Focused new tests cover target drift between interruption and retry, dirty/ambiguous product worktree, and absence of hidden control workflow retries. Do not undo this work or implement a separate retry framework.

### Blocking requirement 1 — update stale cross-layer test, preserving new semantics

The only failing GitHub CI test is `tests/test_daemon_cross_layer.py::test_merge_pending_retries_matching_control_merge`, at line ~529. The test still asserts `merge_calls == 2`, but the correct new behavior for an interrupted control merge without a control-specific admission is **one call**, then propagate/retain safe recovery state. CI observed `merge_calls == 1` and failed exactly on the outdated assertion.

**Required:** replace the old test's assertions, name and scenario expectations with the new safety invariant. Assert no second control merge in the same interrupted transaction; ensure the previous control HEAD is retained for a pre-effect interruption; prove the next *fresh* control sync can safely complete after normal shutdown/restart and re-observation, without silently continuing on borrowed product WAL authority. Do not simply change `2` to `1` without checking terminal state and recovery behavior.

### Blocking requirement 2 — fix effect-tier documentation and honest trace claims

`tests/RECOVERY_MATRIX.md` newly states that a local `merge --ff-only` is **Tier 2**, while `docs/GIT_COMMAND_INVENTORY.md` and TASK-078/TASK-073 define **Tier 3 for any repository/worktree mutation**, including local merges. Tier 2 means remote read with no mutation, *not* local mutation. Fix the classification and all adjacent text/tables; a fast-forward merge is **Tier 3 local ref/worktree write**, and fetch is Tier 3 remote read **plus local object/ref writes**. Distinguish a genuine full before/after executed-command trace from an illustrative per-operation command sequence. Itemize actual added local safety observations and verify no unintended remote/mutating calls.

### Required verification

1. Complete exact-checkpoint CI must run `./dev coverage -n 4 --dist=worksteal` (or the repository's equivalent full suite), report full pass with coverage, and proceed to **Ruff pass**. Do not infer success from the worker's focused tests or from a partial local timed-out run.
2. Keep focused target-drift, dirty checkout, SIGINT, product WAL and control no-retry regressions. Include a positive *fresh subsequent control sync* proof and negative unexpected-HEAD/identity proof where missing; tests must preserve the deliberate no-duplicate-mutation contract.
3. Preserve all prior WORKER reports, execution checkpoint and lifecycle evidence. This is **same-ticket review hardening**, not a new ticket or authorization to merge or change product/control history manually.

**Reviewer disposition:** return `kanban/review/TASK-078.md` to `kanban/todo/TASK-078.md` with this specification. The proof is nearly complete, but exact-checkpoint green CI and correct effect accounting are mandatory. TASK-079 remains blocked until TASK-078 is integrated to `done`.
