---
"type": "devlegate.ticket"
"title": "Decouple residual live observations and remove obsolete owner observation slots"
"depends_on": ["TASK-206"]
---

## Milestone

Devlegate 0.5.8 architecture hardening after the three-phase audit. Backlog until explicitly scheduled. Independent vertical substitution; do not introduce dependencies merely because tickets share an audit theme.

## Goal

Complete O2 after committed inspection separation in TASK-206; isolate live resource discovery and preview from mutation execution where safe.

## Context

Phase II/III O2: TASK-206 is narrowed to committed/service reads (O1). Existing live workspace inspection, ls/prune preview, safe-point admission and passive flock probes remain; live observations cannot be made atomic merely by owner serialization.

## Required behavior

- Inventory remaining owner-bound read consumers after TASK-206. Serve workspace and live inventory/preview by independent bounded read paths or minimal detached snapshot capture. Honest unavailable/partial attribution for worker changes, removal or ambiguity.
- Remove the remaining observation slot, admission/cancel/poll/safe-point/timeout machinery when its last required consumer is replaced; keep only a demonstrably necessary short capture and explain it. No second mutation owner, observation daemon or generic async framework.
- Preserve exact preview confirmation digest and mutation-time re-observation; never allow stale observation to authorize cleanup. Preserve authentication, privacy, genuine exclusion and sole writer. Passive readiness probes must not obstruct exclusive acquisition solely by observing it; do not infer authority from unchecked absence.
- Bound read subprocesses, output, cancellation, concurrency and shutdown independent of worker reset polling. Measure before/after owner busy time, actual Git calls and mutation latency; no global timeout increase.

## Acceptance criteria

Remaining ordinary live reads do not occupy mutation-owner supervision except justified minimal capture. Existing preview/apply, reset, stop, cleanup and startup still work; superseded observer infrastructure removed or specifically justified.

## Required regressions

1. Real service with held worker, slow workspace/prune preview and concurrent reset/stop; independent responses and exact stale preview rejection.
2. Concurrent live worktree/ref deletion, unsafe/unknown observations, cancellation, service shutdown and concurrent expensive requests: bounded read-only behavior, no worker disruption.
3. Concurrent passive authority observations and owner startup; no read-induced acquisition interference while actual mutation exclusion remains.
4. Before/after actual Git counts and owner timing; all affected endpoints, status and TASK-206 committed reads remain coherent.

## Operational end state and upgrade

The integrated checkpoint must be usable for subsequent self-hosted development without any later ticket. Preserve all unaffected live workflows. Specify the affected active-state/IPC/evidence contracts, the safe quiescent installation boundary, and exact readiness checks. Preserve immutable reports, reviews, pins and necessary independent authority/effect proofs. No production migration, synthetic evidence or mixed-version protocol. If an actual stopped local installation requires conversion of proven state, describe a narrowly scoped one-shot untracked procedure with exact preconditions, backup/dry-run and validation; never invent authority or erase uncertain effects. Focused regressions, Ruff, diff check and exact-checkpoint hosted CI are mandatory.

Match service/CLI versions; no durable read migration.