---
"type": "devlegate.ticket"
"title": "Unify execution workspace Git authority and semantics"
"depends_on": ["TASK-079"]
---

## Milestone

Git architecture hardening: execution worktree boundaries for TASK-073.

## Goal

Finish the execution-workspace Git boundary without weakening worktree identity, ownership, topology, or preflight safety.

## Worker test scope — targeted verification, full suite in CI

Follow `tests/TEST_BOUNDARIES.md` and `tests/TEST_PLACEMENT.md`; select workspace-specific semantic tests and only the stronger host/recovery cases changed by the implementation:

```sh
./dev test -q tests/test_execution_workspace_submodules.py tests/test_git.py
./dev test -q tests/test_control_workspace.py -k "worktree or workspace or branch or checkpoint"
./dev test -q tests/test_daemon_recovery.py -k "workspace or interruption"
./dev lint
```

If the changed behavior reaches a production-process crash/restart invariant, select a matching named `tests/test_cli_topology.py` test by `-k`; do not run that entire module by habit. Test both positive worktree reuse and negative wrong/dirty/occupied identity with actual Git worktrees in the focused selection.

**Do not run by default:** unfiltered `./dev test`, `./dev test-parallel`, `./dev check`, `./dev coverage`, the complete production-topology suite, or unrelated distribution tests. The worker reports focused test and Ruff results; GitHub Actions performs complete exact-SHA pytest/coverage and Ruff and the reviewer verifies that evidence.

## Context

`execution_workspace.py` legitimately owns execution branch/worktree lifecycle but still constructs many raw `_git()` calls. Raw command count alone does not establish a bug; the task is to prove which calls need typed error semantics or owner/shutdown-aware routing and to correct those specific gaps.

## Required behavior

1. Inventory every direct Git command in `execution_workspace.py`, grouped into branch/ref observation, worktree registration/identity, lifecycle mutation, checkpoint and submodule validation. For each, record runner, caller authority, expected failure semantics and executed subprocess/effect counts.
2. Keep `ExecutionWorkspaceManager` responsible for workspace lifecycle. Reuse `git.py` typed operations when they encode a real shared semantic contract; preserve specialized commands in the domain module where extracting them would be cosmetic.
3. Make absent/detached/occupied/dirty/conflicting/unreadable/malformed states distinguishable; unexpected Git errors must not become permission to create or remove workspace resources.
4. Route runtime-invoked Git commands through appropriate owner/shutdown-aware capabilities where required. Preserve separate safe standalone inspection/preflight paths and do not allow them to gain mutation authority.
5. Preserve execution worktree and branch identity proofs, symlink/path protections, checkpoint provenance, WAL handling and safe cleanup of partially prepared workspaces.
6. Compare before/after executed-command traces (including helper-internal calls) and tiered local/remote effects. No unaccounted N+1 subprocess loop or extra mutation just to satisfy API uniformity.

## Acceptance criteria

- Scoped worktree mutations run under the established service authorization and interruption contract; inspection cannot escalate into mutation.
- Correct absence and valid negative topology findings remain distinct from Git observation or OS failures.
- Existing execution preparation, checkpoint creation, cleanup and interrupted recovery preserve exact branch/worktree ownership guarantees.
- The report maps old raw commands to justified retained domain operations or typed helpers and includes quantitative subprocess accounting; all extra invocations are explicitly justified.
- Full pytest with coverage and Ruff succeeds in GitHub Actions on the exact implementation checkpoint; focused workspace and authority regressions are run by the worker.

## Required regressions

- Setup: execution branch missing; action: prepare workspace; expected: only admitted guarded creation.
- Setup: branch bound to an unexpected, dirty, detached, occupied or conflicting worktree; action: prepare or clean up; expected: refuse unsafe mutation.
- Setup: symlink/path substitution at managed worktree; action: inspect/repair; expected: reject unsafe identity.
- Setup: Git returns malformed worktree output or repo is unreadable; action: inspect; expected: explicit observation failure rather than absent workspace.
- Setup: interrupt after branch creation but before worktree registration; action: recover; expected: exact-identity reconciliation or safe stop.
- Setup: checkpoint commit fails or submodule metadata is inconsistent; action: execute lifecycle; expected: no invalid checkpoint publication.
- Setup: service owner is lost or shutdown starts; action: attempt workspace mutation; expected: no unauthorized operation.

## Supplemental architecture focus (measured 0.5.7 baseline)

The measured module sizes were `execution_workspace.py` **828 physical lines**, `recovery_workspace.py` **283**, and `runtime.py` **11,351**. `ServiceEngine._prepare_execution_workspace` was ~98 LOC, while `cleanup_workspaces` was ~343 LOC, `_assert_cleanup_has_no_execution_dependency` ~218, `_workspace_cleanup_cohort` ~144, `_retire_execution_refs` ~106 and `_revalidate_cleanup_retirement` ~36. These counts are navigation clues, not refactoring quotas or proof of defects.

For worktree lifecycle, separate **read-only observation/ownership proof**, **admitted preparation/reuse**, **rework predecessor publication**, and **cleanup/ref retirement** in the required inventory. Specifically record when dirty worktrees may be preserved versus when they are unsafe, what evidence protects a previously completed side effect, and which checks must be revalidated immediately before destructive mutation. Analyze cleanup as a distinct **high-risk proof boundary**; do not extract an indiscriminate cleanup manager, move ownership to a background service, or introduce implicit Git reads/writes through helpers.

Keep actual Git effects, persisted state and safety-critical order measurable. The static call graph does not capture inner Git subprocesses or indirect state writes, so the existing executed-command and negative real-worktree tests remain the deciding evidence. Broader cleanup decomposition should be recommended separately after proof review, not silently added to this ticket's scope.

## Integrated baseline and scope clarification — 2026-10-26

TASK-099 and TASK-100 have been integrated and exercised by the installed runtime on Devlegate and rslab2. Treat their current product implementation, **not the earlier architecture snapshot**, as the safety baseline: bounded auto-cleanup, publication ancestry, managed worktree ownership, cleanliness/submodules, re-observation before every destructive stage, local exact-ref deletion, remote SHA lease, crash replay, and resolved rewritten reconciliation evidence. Preserve these invariants and existing regressions. TASK-080 must *audit* cleanup’s Git ownership/error semantics and actual subprocess/effect cost; do not silently redesign cleanup scheduling, add concurrency, or replace proven safeguards. Separate cleanup performance work if measurements justify it.
