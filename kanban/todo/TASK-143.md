---
"type": "devlegate.ticket"
"title": "Execute and recover durable historical checkpoint repair"
"depends_on": ["TASK-142"]
---

## Milestone

TASK-139 historical repair decomposition: 4/5 — Git effect and replay, no successor yet.

## Goal

Consume the exact authorized `historical_repair` intent from TASK-141 through the shared source-bound rewrite transaction of TASK-142, preserving displaced history and producing a durable, verified `repaired` checkpoint based on current product HEAD.

## Context

The retained failed worker checkpoint must survive an authorized product-base rebase even when the original active execution is gone. An old failed generation remains failed; the operator's historical assumption is bounded to its exact observed identity. Do not pretend it is an active execution.

## Required behavior

1. Under owner serialization, reobserve the admitted exact reports, review binding, ticket/control/product identities, no worker, clean registered worktree/common-dir/branch/HEAD, submodule topology and expected local/remote ref heads. Refuse if any is missing or drifted.
2. Persist durable stage and **pin original failed checkpoint/displaced evidence before changing any ref or worktree**. Keep original review/report files immutable. Guard local refs by expected-old CAS and published remote branch by exact `--force-with-lease` when separately authorized.
3. Call TASK-142's common source-bound linear `git rebase --onto` transaction; do not clone/refork its Git rewrite. Prove full expected commit count/content lineage, correct target parent chain and clean workspace, then store repaired head/new effective base and rewritten evidence identity.
4. Persist and recover `admitted/pinned/local-pending/remote-pending/repaired` stages across process crash/owner restart. Re-observe effects idempotently; changed remote/local ref, rebase conflict or uncertain effects fail closed with displaced originals pinned, no duplicate rebase and no false `repaired`.
5. Do **not** admit or start any fresh worker in this ticket. A `repaired` historical intent is handed to TASK-144 later. Active-only update-base behavior remains intact.

## Acceptance criteria

- With a disposable real-Git reviewed -> failed -> deferred/requeued candidate, two or more failed-attempt commits, and advanced product, explicit operator repair produces a new clean linear code head rooted in the current product, while old exact checkpoint/ref and review/report remain provably intact.
- Restart in each durable stage recovers or blocks deterministically; publication uses recorded expected remote predecessor; success is distinguishable from merely admitted.
- No stale authorization, unrelated workspace, worker or ref can be mutated.

## Required regressions

Success and restart in stages; injected crash before/after evidence pin, before/after local rebase/ref CAS and remote lease; changed local/remote head, missing/dirty/locked/foreign workspace, submodule mismatch, product/control drift, rebase conflict, forged count/identity, concurrent commands. Verify old refs/reports unmodified. Ruff and exact-checkpoint CI.

## Boundaries

No TASK-134-specific code or real resource mutation, no fabricated active `execution_*`/BOUND state, alternate rewrite path, reset or migration. No successor execution until TASK-144.
