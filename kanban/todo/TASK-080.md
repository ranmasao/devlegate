---
"type": "devlegate.ticket"
"title": "Unify execution workspace Git authority and semantics"
"depends_on": ["TASK-079"]
---

## Milestone

Git architecture hardening: execution worktree boundaries for TASK-073.

## Goal

Finish the execution-workspace Git boundary without weakening worktree identity, ownership, topology, or preflight safety.

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
- Full pytest, coverage and Ruff succeed on the integrated implementation checkpoint.

## Required regressions

- Setup: execution branch missing; action: prepare workspace; expected: only admitted guarded creation.
- Setup: branch bound to an unexpected, dirty, detached, occupied or conflicting worktree; action: prepare or clean up; expected: refuse unsafe mutation.
- Setup: symlink/path substitution at managed worktree; action: inspect/repair; expected: reject unsafe identity.
- Setup: Git returns malformed worktree output or repo is unreadable; action: inspect; expected: explicit observation failure rather than absent workspace.
- Setup: interrupt after branch creation but before worktree registration; action: recover; expected: exact-identity reconciliation or safe stop.
- Setup: checkpoint commit fails or submodule metadata is inconsistent; action: execute lifecycle; expected: no invalid checkpoint publication.
- Setup: service owner is lost or shutdown starts; action: attempt workspace mutation; expected: no unauthorized operation.
