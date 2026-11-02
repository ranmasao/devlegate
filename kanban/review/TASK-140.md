---
"type": "devlegate.ticket"
"title": "Read-only historical failed-execution repair inspection"
"depends_on": ["TASK-137", "TASK-138"]
---

## Milestone

TASK-139 historical repair decomposition: 1/5 — independently observable candidate proof.

## Goal

Expose one real read-only CLI/IPC/ServiceEngine operation that inspects the exact deferred failed-after-reviewed execution candidate and returns a structured fail-closed evidence assessment. No authorization, runtime mutation, Git ref mutation, or worker scheduling belongs here.

## Context

TASK-139 was deferred after six incomplete executions. Its unaccepted work-branch code is **not part of the product base**: do not assume `_observe_historical_repair_review()` exists in the integrated product; implement the necessary history/provenance observer in this ticket's own product delta. The existing product `resolve_rework_review()`, `ExecutionReportStore`, `ExecutionWorkspaceManager`, Git observation helpers, and read-only IPC dispatch are starting points. This must not inspect or change actual TASK-134 resources in tests.

## Required behavior

1. Provide an explicit read-only command, e.g. `devlegate reconcile inspect-historical TICKET --failed EXEC --reviewed EXEC`, with exact-shape IPC read-only dispatch and corresponding owner observation. Return structured JSON evidence and a concise human-readable result. A running service must not be bypassed by a second mutable engine.
2. Prove the two exact immutable execution reports by first-parent control history: reviewed lifecycle, genuine published review/rework attribution, later transport-failed lifecycle, exact `todo -> backlog -> todo` deferral and unchanged ticket blob. Reject ambiguous, missing, modified, reordered, or unrelated reports. Never reclassify the failed attempt as reviewed.
3. Observe exact failed checkpoint and its admitted base, linear ancestry/count and expected execution branch; current product local/remote SHA and cleanliness; managed workspace path, registration/common-dir/branch/HEAD, locked/dirty/submodule state; local and remote execution refs; absence or presence of a live worker and conflicting active intents. Treat unavailable observations as unsafe, not as absence.
4. Distinguish `proven-current-resources-but-historical-ownership-unproven` (operator assumption may be required later) from a disqualifying conflict. Path/branch/SHA matching is never retroactive proof of historical ownership. Output includes a reproducible identity set suitable for exact future authorization, but **does not** imply permission to act.
5. No new stored runtime field, mutation command, pin, rebase, local/remote ref update, replay state, or successor worker in this ticket.

## Acceptance criteria

- The new CLI command reaches the actual IPC/owner observation and correctly reports a valid disposable real-Git reviewed -> failed -> deferral candidate and its exact identities, with unknown historical ownership explicitly labelled.
- Wrong execution ID, changed report/review/ticket, premature deferral, mismatched checkpoint/ref, foreign/locked/dirty worktree and uncertain status fail closed without side effects.
- Ordinary `run-worker` remains blocked for failed-after-reviewed without an authorized repair; active `reconcile update-base` remains unchanged.
- Do not import unaccepted TASK-139 execution-branch changes as an implicit dependency.

## Required regressions

Create disposable real-Git fixtures for positive history/inspection and the listed negative history/worktree/ref cases; compare runtime state, control refs, product refs and managed worktree before/after read-only calls. Exercise both CLI decoding and service IPC validation; reject unknown payload fields. Run focused tests, Ruff and full configured CI.

## Boundaries

No TASK-134-specific production path, invented BOUND evidence, mutable operation, worker command to commit/push/merge, reset/force retry, new Git rewrite, or migration/compatibility scaffold. This ticket is complete when the read-only operator boundary works, not when historical recovery works.
