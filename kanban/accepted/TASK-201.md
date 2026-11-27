---
"type": "devlegate.ticket"
"title": "Automatically drive eligible reconciliation and transaction replay from the service owner"
"depends_on": ["TASK-200"]
---

## Milestone

Deterministic reconciliation consolidation — 4/5, automatic policy and liveness.

## Goal

Ordinary safe reconciliation needs no operator command. The owner observes eligible state, chooses the existing safe continuation, advances the common transaction and resumes the appropriate lifecycle. Human involvement is reserved for semantic ambiguity or genuinely missing external authority.

## Context

The original cr_recon roadmap explicitly describes manual update-base CLI as transitional. Current reconcile auto is itself manually submitted; normal REWORK and active drift now share a transaction after TASK-200. Do not create a second automation scheduler or generic repair framework.

## Required behavior

- Ordinary owner iteration/startup advances a proved unfinished transaction before admitting unrelated work. Same-base recoverable checkpoint/publication resumes automatically; eligible linear product advancement selects the exact current agreed local/remote B and starts the shared transfer; reviewed requeue continues automatically. Preserve explicit abort/drop/reset and intentional deferral semantics. Ordinary clean cases need zero CLI recovery commands.
- Authorize only ordinary source-preserving replacement of the exact Devlegate-owned execution ref under already proved generation, retirement, source pin, fresh lineage and lease; record that specific authority before effects. Do not silently generalize it to product/control history rewrites, same-parent/non-descendant target replacement, unproved historical resources, ambiguous ownership or deliberate discard. Those retain explicit external authorization or fail-closed refusal. No arbitrary Git command from an agent.
- Separate transient observation/network availability from stable semantic conflicts and unsafe/missing evidence; retry on meaningful changes or bounded existing cadence, not busy loop or new receipt each idle pass. Report actual current blocker and waiting state through existing diagnostics. Worker shutdown/drain/reset priority and one owner must hold. No fake success when inspection or effects are uncertain.
- Reobserve target changes: before candidate authorization/install, a safely superseded untouched mechanical candidate can be replaced with a newly attributed attempt; keep H immutable and do not silently discard or reuse a human-resolved candidate on another B. If a proved completed candidate becomes a source for another transfer, retain its lineage and original evidence. For ambiguous or manually edited stale candidates, preserve material and request the smallest necessary explicit decision rather than pretend it is mechanically safe. After an admitted effect, reconcile that exact transaction first; do not widen lease or mutate the admitted target in place. Bound repeated advancement under moving product heads.
- Ensure automatically progressed resolution does not publish final review/accepted/done prematurely, does not replay a worker that was intentionally cancelled, and launches only the correct origin-specific continuation. Mechanically clean does not equal semantically approved; keep worker/reviewer validation. Retain existing manual surface provisionally for exceptional authorized actions until TASK-202, not as a prerequisite for routine progress.
- No automatic Reconciler, historical-resource adoption, concurrent worker scheduling, generic repair CLI, migration or new background service.

## Acceptance criteria

Real service scenarios for ordinary REWORK, same-base recovery, active descendant drift and restart of in-flight transfer converge without CLI recovery calls when proof is sufficient. Stable conflict retains candidate without churn. Unauthorized/divergent product/control or uncertain resources remain safely blocked with specific actionable diagnostics. No broad authority expansion.

## Required regressions

1. Real service detects normal product fast-forward during worker, completes checkpoint, automatically transfers and launches revalidation; no operator reconcile command, no duplicate execution or incorrect report. Include published owned ref and no-op case.
2. Restart in each shared transaction stage and same-base publication stage, lost push ack and transient unavailable remote then recovery; assert automatic convergence without duplicated effects.
3. Product advances again before candidate install and after an admitted effect; verify safe target supersession/replay, retain H and any manual candidate; stale approval cannot affect later attempt. Include non-descendant/same-parent replacement, dirty/foreign/changed resources and control changes.
4. Conflict waiting across idle iterations, stop/start, explicit confirmation, reset/deferral policy and competing mutation. Assert no busy loop, no hidden worker start or unauthorized rewrite. Focused regression suite, docs, lint, diff check and full hosted CI.
