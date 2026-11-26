---
"type": "devlegate.ticket"
"title": "Factor a deterministic source-bound reconciliation candidate transaction"
"depends_on": ["TASK-197","TASK-081","TASK-150"]
---

## Milestone

Deterministic reconciliation consolidation — 1/5, common transfer mechanism.

## Goal

Provide one owner-held, source-bound mechanism for preparing and proving a transferable implementation candidate C on target product B from the exact original base A and retained checkpoint H, without changing the canonical execution workspace or replacing its generation before a result is available.

## Context

At f439371, ExecutionWorkspaceManager.prepare_rework ports a linear delta using cherry-pick after retiring the old checkout and moving its branch; reconciliation_transactions.start_fresh_rewrite uses in-place rebase and separate local/remote pending replay. TASK-056 and TASK-194 demonstrated a real failure: REWORK conflict after old retirement leaves rework-started/reservation but no creation receipt, and neither replay nor ordinary reconcile can continue. The parallel reconciliation roadmap's A/B/H model applies to both origins. This ticket is the bounded common mechanism, not yet the two lifecycle integrations.

## Required behavior

- Establish a minimal typed, immutable transfer identity: exact source A/H, target B, owner/ticket/source generation, and admitted attempt identity; expose mechanically ready candidate C, conflict candidate, unsafe/unavailable and interrupted progress distinctly. Reuse existing Git proof and owner capability boundaries; source admission and downstream lifecycle remain callers' responsibilities. No free-standing owner, scheduler, generic operation interpreter or agent authority.
- Prepare in an owner-controlled isolated candidate representation with explicit resource ownership and durable intent/effect attribution using existing receipt/WAL principles. Do not retire, detach, reset or rewrite the canonical source worktree, execution refs or product/control branches to prepare a candidate. Preserve exact H through independently proved evidence appropriate to the source. Candidate objects/refs must not become ordinary worker checkpoints or historical execution reports.
- Support the actual linear worker contribution accepted by both current paths, including multiple commits, same-base, zero delta and H already in B. Preserve meaningful cumulative code including binary, file mode, rename/delete and supported submodule semantics, and do not silently drop changes or flatten history where existing provenance needs it. Choose one documented Git transformation strategy based on evidence; no new VCS abstraction. Mechanical equivalence is not semantic approval. Explicitly classify a legitimate empty/already-incorporated result rather than assume either loss or success from a commit count.
- Retain conflict material and exact input identity without treating arbitrary dirty or foreign workspace as a candidate. Provide an internal owner operation to validate and checkpoint an explicitly authorized resolved candidate, including justified changes outside original paths; do not require the human/agent to manipulate commits, refs or receipts. Clearly distinguish externally resolved candidates from mechanically produced ones. No implicit approval from a clean index or marker absence. The caller supplies actual authorization; do not add a CLI or semantic agent here.
- Define replay/cleanup for the candidate from real durable boundaries, including interrupted materialization, Git transformation, checkpoint creation, and uncertain outcomes. Recover a uniquely proved result or safely repeat only an owned deterministic attempt; preserve user-resolved/uncertain edits, never infer ownership from path+SHA. Keep candidate protection explicit so normal cleanup cannot erase it. Do not add a competing lifecycle transaction manager: the minimal transfer progress representation must be consumed by subsequent REWORK and active-reconciliation integrations, replacing their duplicate transfer machinery.
- Document which existing functions/proofs will be reused/replaced in TASK-199/200 and the exact result contract. Existing public workflows must continue working in this intermediate checkpoint; do not change existing CLI or start ordinary workers from candidates. No migration paths, actual self-hosting mutation, general historical-resource adoption, Reconciler or architecture-wide refactor.

## Acceptance criteria

One owner-bound transfer contract has real-Git proof of preserving A/B/H and yielding an attributed candidate, conflict or precise blocker, independent of lifecycle origin. Preparation/replay never mutates canonical source resources. Resolution checkpointing requires exact owner authorization and safe candidate identity, with no arbitrary branch adoption. Existing paths and full CI pass.

## Required regressions

1. Disposable repos: same base, advancing descendant, zero delta, already incorporated, clean multi-commit transfer, binary/mode/delete/rename and applicable submodule cases; check resulting tree/delta and unchanged source checkout, source ref, remote, and control/product identities.
2. Real textual conflict and explicit resolved candidate with a justified additional path; preserve H, candidate files and exact attempt identity; refuse wrong/stale authorization, unresolved index, foreign replacement and ambiguous dirty state. No semantic correctness claim.
3. Inject interruption before/after candidate reservation, transformation and checkpoint/evidence effect; reopen owner and replay deterministically or retain precise conflict/uncertainty. Test foreign path/ref and concurrent external changes, unavailable evidence and cleanup protection.
4. Run focused Git/workspace/resource/reconciliation regressions, lint, diff check and exact-checkpoint hosted CI.
