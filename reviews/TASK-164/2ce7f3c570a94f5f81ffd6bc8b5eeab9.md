<!-- devlegate-review-v1
ticket: TASK-164
execution: 2ce7f3c570a94f5f81ffd6bc8b5eeab9
checkpoint: 048b84d13206852ddebe479258c188f8d04e74e8
control: 307c7667f86628e0960c246f9efead7c8b3a557c
disposition: accept
-->

# TASK-164 — ACCEPT: generation-aware rework retry handoff

## Exact evidence and CI

**ACCEPT** the exact execution `2ce7f3c570a94f5f81ffd6bc8b5eeab9` at immutable checkpoint
`048b84d13206852ddebe479258c188f8d04e74e8`, based on integrated product
`99fec169753c79c270491bf914083ceecdbe7ac5` and admitted control
`307c7667f86628e0960c246f9efead7c8b3a557c`.

The execution report records process exit 0, successful transport and egress,
and a semantic `blocked` claim referring **only** to the downstream
checkpoint/publish and hosted-CI gate. These owner operations are now complete.
GitHub Actions
[run 43821600332](https://github.com/ranmasao/devlegate/actions/runs/43821600332)
finished **SUCCESS** for the **exact checkpoint** on 16 November 2026:
the `check` job completed tests with coverage and Ruff successfully.
The separate `publish-coverage` job was skipped without failing the check.
Worker-side validation independently reported **2264 passed, 2 skipped**,
80% combined coverage, focused **66 passed**, Ruff and whitespace checks.

The reviewer protocol permits acceptance of a valid `blocked` worker
claim when independent review proves the deliverable sufficient. No worker
retry or fabricated `completed` report is warranted.

## Root-cause correction and independently reviewed behavior

1. **Generation-aware preparation — PASS.** The coordinator now selects
   the exact admitted `predecessor_execution`/generation before consulting
   inherited reviewed-rework `preparation_stage`. Review-origin feedback
   remains attached to the reviewed checkpoint, while the resource handoff
   follows the immediately preceding execution generation. A first rework
   without an admitted predecessor retains the original strict reviewed
   preparation route.
2. **Preserved dirty WIP and review context — PASS.** Real-Git
   `tests/test_rework_handoff.py` reconstructs a reviewed execution,
   interrupted rework worker and a later automatic retry. It verifies
   preservation of dirty tracked/untracked edits, same pending
   execution/generation through restarts, unchanged review provenance,
   no duplicate reviewed predecessor publication, and a successful
   subsequent worker checkpoint/push.
3. **Independent predecessor proof — PASS.** `runtime.py` rechecks
   independently receipted worker retirement or explicitly observes the
   prior worker identity as absent. It does not treat the new execution's
   empty `worker_identity` as proof. `ResourceBinding` checks explicit
   predecessor identity, matching resource namespace, independent
   receipt-based remote state and creation preconditions before effects.
4. **Crash/replay boundaries — PASS.** Tests cover SQLite admission before
   independent intent, written intent before handoff, settled handoff
   receipt before launch and repeat process loss; they verify that the
   already admitted execution/generation is not multiplied. Unreceipted
   handoff and partial creation effects fail closed with diagnostics
   requiring preservation of worktree and evidence. Tests also exercise
   mismatched nonce, foreign/replaced inode, local/remote HEAD changes,
   unsettled predecessor and forged/partial handoff evidence.
5. **Documentation and scope — PASS.** `docs/resource-identity.md`
   documents the distinct reviewed origin and retry predecessor, recovery
   behavior at crash boundaries, exact proof requirements and operator
   diagnostics. No runtime migrations, new commands, worker-facing
   resource authority, synthetic receipts or operator-state alterations
   are introduced.

## Deliberate boundaries

An interrupted handoff with written `handoff-intent.json` but missing
creation receipt remains **uncertain and blocked** rather than silently
repairing or re-admitting a different generation. This is required
fail-closed behavior, not an unmet feature. Out-of-band adversarial
mutation remains subject to the existing resource/worker ownership
assumptions, not an asserted global filesystem lock.

The temporary operator bootstrap binary built from
`81ea52ec7cecf603cd6b200ed6cefda4e81a5f5d` is separate deployment
evidence, not a product integration artifact. Do not alter it, local
SQLite, current worktrees or resource witnesses as part of review.
After Devlegate integrates this accepted checkpoint, the operator
can rebuild and install a standard native package under explicit
deployment control.

Promote the **unchanged** canonical ticket from `review` to
`accepted`, publish this review atomically, and let Devlegate
perform the normal accepted-integration lifecycle. Do not manually
merge product code or mark the ticket done from review.
