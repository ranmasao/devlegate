---
"type": "devlegate.ticket"
"title": "Recover interrupted rework after remote predecessor publication without false conflict"
---

## Incident

On 2026-10-22 WSL crashed during TASK-088 rework. On service restart, workflow persistently blocked with:

`workflow blocked: rework preparation conflict: remote execution branch changed after evidence publication`

The previous reviewed checkpoint is `efb852661aa29c8aacade1cefc240c471312c38a`. During preparation at 14:36:02–04, the execution branch was moved to product base `4c236f95592e7e17deb0c85dc56e7f6d4d343bf4` and three cherry-picks created `e4e883f7`, `749a38c0`, `931266f0ef363864bd20f2bd3b4487ba03ec9074`. `git cherry efb8526 931266f 4c236f9` marks all three `-` (patch-equivalent). Local branch, origin branch and registered worktree HEAD all equal `931266f0`. The worktree is dirty with uncommitted changes in `tests/test_control_recovery.py` and `tests/test_control_workspace.py`; these must never be discarded.

## Root cause to investigate

`src/devlegate/runtime.py` calls `_verify_rework_remote_predecessor(ticket_id, previous_base, previous_checkpoint)` unconditionally on rework resume, including after `remote_predecessor_installed` and after worker work began. That verification accepts only old remote checkpoint, rejecting the already-transplanted and published predecessor. `_prepare_rework_remote_predecessor` already has partial idempotent support when remote equals `workspace.head`, but earlier unconditional checks/preparation are not stage-aware. Examine all persisted preparation boundaries and crash windows; distinguish a legitimate already-installed predecessor from a foreign remote update.

## Required behavior

1. Persist and replay a verifiable exact installed remote predecessor identity, its source evidence, and the execution/worktree binding across restart. Never classify a mere arbitrary remote descendant as a legitimate replay.
2. After evidence pin and/or after local transplant, resume safely and idempotently when the remote was already installed, without requiring it to equal the *old* checkpoint.
3. Preserve completed review checkpoint evidence, patch-equivalence proofs, remote publication lease/race guarantees, and fail-closed handling of foreign branch mutations or corrupted state.
4. Preserve uncommitted worker changes; never reset, detach, clean, checkout over, force-push, or re-cherry-pick onto a dirty or active worktree. Clearly distinguish a recoverable worker execution interrupted before checkpoint from an unsafe unknown process/execution state. Never silently relaunch a worker that may still be running or duplicate a delivered mutable request.
5. Add real Git/crash-injection tests at boundaries: evidence publication, branch transplant, remote predecessor publication, worker start with dirty files, restart/adoption and foreign drift. Prove safe re-entrance and forbidden mutations; ensure clean restart and CI.
6. Improve blocking error diagnostics to include observed and expected refs, stage, and actionable operator guidance without destructive steps.
7. Treat TASK-088 worktree/remote `931266f0` plus uncommitted test changes as the motivating evidence, not disposable scratch state. No manual metadata or SQLite editing as a product workaround.

Priority: **high** — service-wide workflow can remain blocked after host crash. Independent of TASK-088's Arch control init regression and TASK-096's dev setup submodule synchronization.
