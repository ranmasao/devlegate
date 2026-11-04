---
"type": "devlegate.ticket"
"title": "Prove replayed reviewed lineage and consolidate deferred continuation"
"depends_on": ["TASK-147"]
---

## Milestone and intent

Devlegate 0.5.7 — repair the ACTUAL TASK-134 admission failure after integrated TASK-147, then perform a focused structural consolidation of the TASK-147 deferred-continuation implementation. The functional repair and architecture consolidation are BOTH required deliverables of this ticket. This is not permission to broaden cleanup/reset authority or rebuild the SDLC engine.

Priority order: (P0) correct and prove the historical replay-equivalent lineage case; (P1) consolidate the existing implementation without changing its safety contract; (P2) complete full real-Git regression/CI. Establish a passing P0 baseline before restructuring P1 so failures can be attributed.

## Observed production failure, 2026-11-04 (ground truth)

After TASK-147 was integrated into release/0.5.7 at product HEAD 3a9d338b1348c6ac3acf3df2b66725e78f84d1ce, the operator requeued the UNCHANGED TASK-134 from backlog to todo in control commit fa8cf6cdc457129d81a72a6d5e55b9e8f4ba1402. Its canonical ticket blob remained 3e5ca32c31437baae5304766c8b959043fb854cd; dependency TASK-125 is done.

On the real service, at 2026-11-04T15:02:40Z, ordinary owner admission refused:
    workflow blocked: failed checkpoint does not preserve the reviewed checkpoint lineage

At the same time the read-only status presented TASK-134 merely as "scheduler admission is blocked" rather than the precise lineage reason. Product/control were locally and remotely synchronized and clean. The block was SAFELY fail-closed: do not bypass it by mutating the old worktree/ref or inventing an old SQLite record.

The HISTORICAL, IMMUTABLE source facts are:
- Report executions/TASK-134/0c944f7c8bb741a4a367c95c4cfc10d0.json: genuinely reviewed earlier execution, code base 5b81966afab8eac2d021332c22a04a34361736ef, checkpoint a5cfa0962934123e45ccd0c8a384dd3979a8b5c2, 17 linear commits.
- Genuine curator review: reviews/TASK-134/0c944f7c8bb741a4a367c95c4cfc10d0.md, binding exactly that execution/checkpoint/control, published with the authentic review disposition.
- Later transport-failed report executions/TASK-134/3cb61e2d366042aeab97caf3f4702ece.json: same code base 5b81966afab8eac2d021332c22a04a34361736ef; failed checkpoint 7c8274a837bd5579b3ac607bd570ff8d14e5016b, 18 linear commits; transport_error "stdout event exceeds maximum size". The worker's code checkpoint exists notwithstanding failed transport.
- The two Git commit chains DIVERGE immediately after the shared base. Reviewed checkpoint a5cfa096... is NOT an ancestor of failed checkpoint 7c8274a8....
- Nevertheless, independent Git-object inspection established that all 17 corresponding commits have IDENTICAL Git tree OIDs, ordinal by ordinal, and the failed chain has an additional 18th commit. The corresponding commit OIDs differ, including from committer timestamp rewrites. Reviewed tip tree and failed commit #17 tree both equal 34c3e5c1d3b418afb8ad9861df23f911b927e336.
- The original branch devlegate/work/TASK-134 remains at 7c8274a8...; its associated historical worktree may be dirty/unowned and must NOT be retargeted, reset, retired or made to appear generation-owned. Earlier report, review and Git objects are immutable input, not authority over those resources.
- Three first-parent defer/requeue pairs with unrelated ticket additions, later clean unrelated completed owner in execution_* state, missing failed_executions[TASK-134], and advanced product remain the other essential real-incident conditions documented by TASK-147 and docs/decisions/TASK-147-isolated-generation-2026-11-03.md.

The regression defect is in src/devlegate/runtime.py::_prove_deferred_failed_rework, around the unconditional is_ancestor(reviewed.workspace_head, failed.workspace_head) gate. The prior disposable positive test in tests/test_control_workspace.py::test_failed_rework_deferral_resumes_checkpoint_and_review explicitly ASSERTS that reviewed HEAD is an ancestor of failed HEAD. It therefore models only a simpler topology. The green TASK-147 E2E and CI did not prove this real divergent-but-identical-tree history.

## Mandatory P0: prove exact replayed reviewed-prefix equivalence safely

Implement an internally reusable, read-only source-history proof with two explicit accepted modes.

1. DIRECT ANCESTRY (existing): if reviewed checkpoint is an ancestor of failed checkpoint, preserve established exact report, source linearity and control-review provenance checks. Do not weaken this path or silently change its admission/ownership rules.

2. REPLAY-EQUIVALENT LINEAR PREFIX (new): when direct ancestry is false, allow continuation only after proving ALL of the following from immutable Git objects and independently authenticated reports:
   a. Reviewed and failed reports refer to the SAME exact reported code base B for this alternative path, and B resolves to a real commit. In particular, do not assume equal-looking final trees from unrelated bases prove equivalence.
   b. Walk B..reviewed and B..failed as ORDERED, FULL first-parent/one-parent commit chains. Every step has exactly the previous step as its single parent, starts at B, reaches the exact report checkpoint, has no merge, ambiguity, gap, reordering or synthetic missing history. Resolve commit/tree objects through trusted Git observation, not commit-message text or report sort order.
   c. Let reviewed length be N and failed length M. For the real incident N=17, M=18. Require N > 0, M >= N, and EXACT tree-object equality for each ordinal i in [1,N]: tree(reviewed[i]) == tree(failed[i]). Compare the full root tree OID, including paths, modes, gitlinks and submodules, not file names or tip-only content. Verify the common base tree implicitly via identical base commit B. A mismatch at ANY step fails, even if later trees reconverge.
   d. For the specific missing-cache + later transport-failed replay case, REQUIRE a positively observed continuation suffix M > N (TASK-134 has one extra commit); do not silently assume an equal-length divergent history is fresh worker progress. If an otherwise valid no-extra-delta case is encountered, keep it blocked with a precise diagnostic until separately specified.
   e. Preserve the distinction between identities: the authentic reviewed execution/feedback is the CURATOR source, whereas the later failed checkpoint/base is the CODE source. Equivalent tree sequences are proof of committed *content continuity*, never proof that the reviewed commit SHA is an ancestor, never proof of old resource ownership and never authority to rewrite the old branch.
   f. Preserve exact lifecycle report/control publication, genuine atomic review artifact attribution, canonical ticket blob and legal first-parent repeated dispositions, clean current product, proven later unrelated completed owner, absence of active workers/competing intents, reject nonempty conflicting failure cache, and all existing target CAS/worktree/remote-lease guards.

IMPORTANT: do NOT simply delete is_ancestor, replace it with merge-base or compare only the last Git tree. Do NOT add generic permission for arbitrary cherry-picked patches or different bases. The permitted alternate proof is intentionally narrow and independently checkable.

The candidate generation continues to replay the ENTIRE failed source base-to-checkpoint linear delta exactly once (all 18 commits in real TASK-134), onto the current product base using the existing TASK-145/TASK-147 candidate/pin/patch-equivalence machinery. The reviewed prefix is a proof of feedback continuity, NOT a reason to truncate or apply the failed delta twice. Preserve source refs and original dirty historical worktree/remote.

## Mandatory P0 positive and negative real-Git regressions

Create an entirely disposable Git fixture that actually reproduces DIVERGENT commit IDs with equivalent trees, rather than hand-constructing a successful ancestry relation. For example, start from one base B, create a 17-step reviewed branch, then create another 17-step chain on B with the identical files/modes/trees at each ordinal but distinct commit metadata/OIDs, followed by a genuine 18th failed worker commit. Verify the fixture itself with merge-base, rev-list, parents and per-step tree checks BEFORE invoking production. Avoid network or dependency on the live TASK-134 Git objects in configured CI.

Build authentic control history: a real reviewed execution report and co-published immutable review; later real transport-failed execution report; three first-parent todo->backlog->todo cycles preserving the ticket blob while admitting independent changes; clean later unrelated completed owner with persisted execution_* describing THAT owner; absent failed_executions[source ticket]; dirty historical ticket-scoped source worktree with old branch/ref/remote still reachable; and clean advanced product head. No fabricated old BOUND or synthetic failure-cache entry.

The NORMAL read-only status/plan must admit the safe candidate; owner scheduling must then create one fresh isolated execution ID, pinned source and candidate, distinct new generation branch/worktree/remote, exactly one replay of the full 18-commit delta, correct genuine curator feedback in the worker prompt, actual worker checkpoint/report publication, ordinary review/accepted/done integration, and safely retained optional generation resources where ownership for cleanup is not proven. Assert original report/review bytes and old source ref/worktree/remote remain unchanged.

Add targeted negative and boundary cases:
- Different intermediate tree at step i despite equal reviewed and failed tip tree after a later revert or reconvergence: MUST refuse.
- Equal tip trees but different order of noncommuting changes, missing or added intermediate step, shorter failed chain, same length with no positive suffix, or unrelated base: refuse the alternate proof.
- A merge/multiple-parent/nonlinear commit in either chain, unreadable or absent commit/tree, wrong base/head in report, rewritten/missing lifecycle publication, ambiguous execution ordering, forged/misattributed review: refuse.
- Conflicting present failed_executions entry, non-terminal unrelated owner, live/indeterminate worker, dirty or changed NEW target, stale product/control, changed target/local/remote ref, symlink, locked/foreign registration and remote push uncertainty: preserve existing fail-closed behavior.
- Two or more repeated owner/restart attempts; interruptions before/after persisted intent, pin, replay, new ref/worktree, remote push, checkpoint/report, and accepted integration: no duplicate replay, execution, report, worker or destructive historical mutation.
- Same-base and advanced-product cases; retain the existing genuine DIRECT-ANCESTRY scenario and all established TASK-145 and TASK-147 negative cases. Match/no-match existing failure cache must remain correct.

If tests simulate hash rewrites, make their Git-tree equality and distinct commit-OID assertions explicit. Do not let test helpers silently fake ancestry or patch correctness.

## Mandatory P1: limited but REAL architectural consolidation of TASK-147

The TASK-147 fix grew over 18 commits and leaves multiple layers of special-case isolated handling. This ticket must reduce structural duplication and make proof, authority and transaction boundaries legible. This is a behavior-PRESERVING refactor after P0's regression is green, not a redesign of the full product.

Baseline signals, NOT arbitrary line-count quotas:
- src/devlegate/runtime.py currently has approximately 13,247 lines. TASK-147 added about 1,145 lines to it. The new code includes _prove_deferred_failed_rework (~195 lines), _publish_isolated_failed_rework_target (~143), _inspect_isolated_failed_rework_intent (~114), _inspect_published_isolated_workspace (~107), and _pin_isolated_failed_rework_candidate (~90).
- tests/test_control_workspace.py has about 6,004 lines; test_failed_rework_deferral_resumes_checkpoint_and_review alone is about 1,199 lines with a large parameterized corruption switch.
- Existing structures include ExecutionWorkspaceIdentity/ExecutionWorkspaceManager, source/review provenance modules, runtime_state_validation, recovery_workflow/recovery_transactions and persisted rework_provenance plus execution identity fields. Reuse them. Avoid introducing another parallel authority system.

P1-A. Establish one canonical READ-ONLY continuation evidence decision. Factor the responsibilities currently interleaved in _prove_deferred_failed_rework, _deferred_failed_rework and _completed_unrelated_owner_proven into cohesive helpers or a small internal module:
  * authentic lifecycle/source report selection and ordering;
  * reviewed/failed code relationship (direct ancestry OR strict tree-equivalent replay prefix);
  * authentic curator review binding;
  * repeated legal control-ticket dispositions and immutable canonical blob;
  * current competing-state, later unrelated owner and eligibility gates.
Return a small immutable/typed result carrying explicit reviewed-feedback and failed-code predecessor identities, matched lineage mode, code base and canonical proof facts, or a typed/stage-specific refusal. Avoid duplicating Git-history walks between status and owner. Do not use a data class merely to wrap every old argument without actually centralizing invariants.

P1-B. Separate SOURCE PROOF from RESOURCE AUTHORITY. Git tree equality authorizes consideration of source content only. An exact independently recorded new generation owner intent and ref/path/registration/remote evidence authorizes mutation of that NEW generation only. Use existing ExecutionWorkspaceIdentity and persisted generation-specific target; consolidate duplicate branch/path/ID reconstruction and validation used by scheduler, _post_worker_workspace_manager, _inspect_published_isolated_workspace, recovery_workspace_manager and accepted integration. Do not infer authority from matching SHA/path, old reports or Git tree. Preserve the older ticket-scoped path only where that particular current owner is already independently proven.

P1-C. Make the already existing isolated transaction stages explicit and centrally validated. Audit admitted -> source_pinning -> candidate_pinning -> candidate_pinned -> target_creating -> target_created -> worktree_attaching -> worktree_attached -> remote_publishing -> remote_published -> worker-running -> post-worker/checkpointing -> publishing/lifecycle -> review/accepted/done. Use stable stage constants or a focused internal transition table/validator where beneficial; do NOT create an unnecessary generic workflow/state-machine framework. The persisted WAL/SQLite schema, durable before-effect journaling, local create-if-absent/CAS, remote expected-old lease, ownership serialization, interruption recovery and exact report semantics are authoritative. Never replace a recorded stage with a guessed one on restart. Classify uncertain external effects by re-observation.

P1-D. Normalize read-only STATUS versus owner admission. Both must consume the same source evidence and eligibility/refusal rules. Status and IPC plan may read Git but must not mutate Git, state, SQLite or launch workers. The real log reason "failed checkpoint does not preserve the reviewed checkpoint lineage" must be reflected as an actionable, specific reason, NOT only generic "scheduler admission is blocked". The owner MUST recompute/revalidate under its exclusive lock immediately before persisted intent and Git effects; a stale status result is not a capability to mutate. Test both positive and negative parity.

P1-E. Keep the integration/cleanup distinction explicit. Worker, immutable report, accepted product/control integration and restart reconciliation must use the exact generation identity. Cleanup/retirement is OPTIONAL and must remain safely blocked when its independent authority is not available. Do not broaden _retire_execution_refs to delete arbitrary devlegate/generation/* refs or fabricate a BOUND record. No regression in legacy current-owner workflows, zero-delta paths or TASK-145 transplant. If the generation-specific safe retirement capability is not yet established, retain refs/worktrees with a precise diagnostic while keeping already-proven done integration durable.

P1-F. Split the large monolithic regression into focused tests and shared disposable fixtures. Preserve the positive direct-ancestry, new rewritten-prefix, previous reviewed feedback, transport-failed checkpoint, missing/matching/conflicting cache, later owner, repeated dispositions, same/advanced product, real IPC, crash windows, target races, legacy cleanup and negative ownership assertions. Extract narrowly named setup/helpers and focused tests by proof / admission / isolated preparation / worker report / accepted integration / restart / refusal, so a failing test identifies its broken invariant. No weakening, skipping, xfail, removal or mock-only substitution of a previously proven real-Git boundary merely to make tests convenient. Record a before/after test-case inventory to demonstrate coverage retention.

P1-G. Avoid cosmetic-only refactoring. Merely moving ~1,000 lines from runtime.py to another file or adding proxy layers without reducing scattered checks is NOT completion. A satisfactory result has one inspectable evidence path, one explicit persisted generation resource identity/authority boundary, an understandable stage transition contract, smaller focused tests, fewer independently repeated isolation checks, and an explanation of what remained intentionally in ServiceEngine. Prefer small private modules that own cohesive invariants if needed; keep stdlib-only and respect current public API. Report before/after module/function boundaries and meaningful duplication removed, not just LOC moved. Avoid churn in unrelated scheduler, host installation, packaging, project schema or operator reset components.

## Suggested execution order and checkpoints

1. Characterize the exact code/branch history and list the CURRENT assumptions, side effects, owners and stage boundaries. Record a short in-repo design note or concise inline documentation of the two accepted lineage modes, invariants, and proof-vs-authority split. No design-only iteration counts as done.
2. Add deterministic divergent-chain real-Git fixture with explicit pairwise identical trees and differing commit OIDs. Show it fails the old direct-ancestry gate for the expected reason; verify the current positive fixture still uses direct ancestry.
3. Implement the narrow P0 lineage proof and pass the new isolated ordinary scheduler scenario through report/accepted/done. Preserve all refusal and noninterference proofs. Stabilize focused tests before moving code.
4. Consolidate source evidence, read-only eligibility, generation binding and transition logic as specified in P1; decompose the monolithic test while comparing its assertions and case inventory before/after.
5. Exercise both direct and replay modes end-to-end, all restart/fault/ref/worker statuses, negative cases, and exact IPC/status parity. Perform Ruff, git diff --check and full configured local CI; Devlegate's independently run exact-checkpoint CI is required before curator ACCEPT.
6. Report objectively: what invariants are now centralized, actual files/functions changed, number of positive/negative cases, whether rewritten-prefix actual-shape test passes without fabricated SQLite ownership, remaining limitations (especially optional generation cleanup), and any unresolved regression. If incomplete, report the first failing assertion and the precise next step rather than declaring success.

## Acceptance criteria / reviewer checklist

- The real TASK-134-shaped 17-tree matching / 18-step divergent DAG is proven by a fully disposable real-Git positive test and goes from ordinary status + scheduler through new isolated generation, actual worker/report, genuine review, accepted integration and done. NO synthetic historical failed_executions entry, BOUND record or legacy ownership claim.
- Both approved lineage modes work; the equivalence mode proves every ordered tree, one-parent continuity, exact common base, positive suffix, and authentic report/review/control history. A matching final tree alone cannot authorize continuation. Negative cases refuse without external mutation or worker launch.
- Existing TASK-147 and TASK-145 safety/recovery/lease/timeout checks remain green; historical worktree/ref/remote and reports/reviews remain unmodified, including after errors and repeated restarts.
- Read-only status/IPC and ordinary owner agree on eligibility and provide specific diagnosis for actual lineage mismatch and other known blockers.
- Cohesive refactor demonstrably eliminates duplicate proof/identity/stage decision paths; no orphaned old fallback that could accidentally reauthorize ticket-scoped resources. Tests have been meaningfully decomposed, not merely moved as a single 1,200-line helper.
- No expansion of destructive resource authority, new general subsystem, new CLI/IPC command, SQLite migration, compatibility scaffolding, TASK-134-specific runtime branch, or manual historical repair.
- Full locally configured test suite, Ruff and git diff --check pass; exact worker checkpoint CI must pass independently. The worker report must explicitly cover functional fix AND refactor, not solely one.

## Hard boundaries and operational handoff

Operate only on disposable local Git fixtures and the currently assigned TASK-149 worktree. Do NOT modify/requeue/run real TASK-134, its historical branch/worktree, execution reports, review artifacts, evidence refs or remote resource refs. Do NOT deploy/restart the running host service or directly push/commit/rewrite product/control branches. Do NOT edit the canonical TASK-134 ticket to make it fit the implementation. Retain all original history as read-only proof input. Do not create migration paths or permanent operator utilities: this project has no external customer migrations to support.

Keep TASK-134 in backlog until TASK-149 has received a separate immutable curator ACCEPT and is durably integrated into product. THEN the operator can requeue the unchanged TASK-134 and use the ordinary scheduler; current safety must be freshly re-observed. This task is the targeted correction and architecture consolidation of TASK-147, not a replacement for the still-unfinished generation resource-binding requirements of TASK-134.
