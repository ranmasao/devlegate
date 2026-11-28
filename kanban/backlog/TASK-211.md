---
"type": "devlegate.ticket"
"title": "Eliminate unbound active-drop authority and verify exact ref retirement"
---

## Milestone

Devlegate 0.5.8 architecture hardening after the three-phase audit. Backlog until explicitly scheduled. Independent vertical substitution; do not introduce dependencies merely because tickets share an audit theme.

## Goal

Restrict active drop to current independent generation and retirement proof, without weakening exact replay of an already-admitted partial drop.

## Context

Phase II L4: generation-less active drop can still reach pin/removal through historical branch. Audit also identified a potential check-then-delete race around git branch -D after SHA observation; the specific interleaving is unproved.

## Required behavior

- Require current bound generation, exact worker retirement and necessary present-time resource authority before granting new live drop; eliminate unbound live pin/removal fallback. For already-admitted partial effects, settle exact recorded disposition/pin and remaining custody without re-creating resources or synthetic proof.
- Preserve historical completed disposition/receipts, worker output/checkpoint/evidence protection, existing drop semantics and distinct reset/abandonment/cleanup authorization.
- Inspect local and remote ref delete commands. Reproduce the suspected local SHA check/delete interleaving in disposable real Git; if confirmed, use exact expected-old CAS and existing guarded lease primitives so a changed ref survives. Distinguish absence allowed by admitted stage from missing/unproven authority.
- Do not generalize retirement, change reset disposition, add new durable stages or touch unrelated zero-delta cleanup. Report separate residual races with evidence.

## Acceptance criteria

All current bound drop stages replay safely; unsupported generation-less active drop has no effect. A ref that changes after observation is never silently removed by this operation. All other retirement paths remain functional.

## Required regressions

1. Missing/unproved generation or retirement and unsafe workspace: refusal before pin/delete/receipt mutation.
2. Current bound drop with fault cuts after disposition and each remote/worktree/local effect, after state clear, lost acknowledgement and restart; preserve exact pins and no duplicate effects.
3. Deterministic local ref change between proof and delete; assert exact refusal/no loss; remote lease and permitted already-absent effect cases.
4. Existing real worker/drop and distinct reset/cleanup/abandonment cases.

## Operational end state and upgrade

The integrated checkpoint must be usable for subsequent self-hosted development without any later ticket. Preserve all unaffected live workflows. Specify the affected active-state/IPC/evidence contracts, the safe quiescent installation boundary, and exact readiness checks. Preserve immutable reports, reviews, pins and necessary independent authority/effect proofs. No production migration, synthetic evidence or mixed-version protocol. If an actual stopped local installation requires conversion of proven state, describe a narrowly scoped one-shot untracked procedure with exact preconditions, backup/dry-run and validation; never invent authority or erase uncertain effects. Focused regressions, Ruff, diff check and exact-checkpoint hosted CI are mandatory.

No unsettled old drop at upgrade; preserve immutable historical disposition.