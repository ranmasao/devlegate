# Deferred continuation: content proof and generation authority

TASK-149 corrects the assumption that reviewed feedback continuity always
requires reviewed commit ancestry. Both accepted modes consume independently
authenticated lifecycle reports and the genuine atomic curator disposition.

## Source decision

`continuation_history.prove_continuation_history` is read-only:

- Direct ancestry retains the existing failed base-to-checkpoint full linear
  delta check. It does not add a same-base requirement to legacy ancestry.
- Replay-equivalent prefix requires the identical reported base commit, full
  single-parent chains from that base, and exact root-tree equality at every
  reviewed ordinal. The reviewed chain must be nonempty and the failed chain
  must have a positive suffix. Reconvergence after a differing intermediate
  tree, equal-length replay, unrelated bases and merges are refusals.
- The returned failed delta is always the entire source delta. The reviewed
  prefix is feedback continuity evidence, never a replay truncation point.

`continuation_evidence.select_sources` orders lifecycle publications using Git
history rather than execution names or timestamps. It rejects missing,
ambiguous and rewritten publications. `prove_dispositions` owns the repeated
first-parent ticket moves and canonical blob invariant.

`ServiceEngine._prove_deferred_failed_rework` composes those immutable proofs
with existing exact lifecycle authentication, review provenance and current
eligibility checks. Its frozen `ContinuationEvidence` explicitly separates
the reviewed curator source from the failed code source, and carries the
lineage mode, canonical blob and review binding. `_deferred_failed_rework`
adds the existing resource eligibility decision: proven later unrelated done
owner means isolation; otherwise independent legacy current ownership is
required. Status and ordinary scheduling both use this decision. The owner
re-observes the source under serialization before journaling a new intent.

Previously admission and intent inspection repeated publication history walks
and curator binding resolution after the source proof already established
them. Both now consume the exact typed source result instead. Source proof
does not consult or claim the old worktree.

## Resource and transaction boundary

`isolated_generation.bound_generation` validates the single persisted target
against `ExecutionWorkspaceIdentity`: ticket, execution, branch, path, ref and
expected absent predecessor. Preparation, post-worker checkpointing, published
workspace inspection and recovery share this binding. Matching content or an
old report is not a mutation capability. Accepted integration continues to
match the exact generation report and persisted execution identity.

`PREPARATION_STAGES` records the existing journal order:

    admitted -> source_pinning -> candidate_pinning -> candidate_pinned
      -> target_creating -> target_created -> worktree_attaching
      -> worktree_attached -> remote_publishing -> remote_published

`_save_isolated_preparation` accepts only a repeated stage or its immediate
successor. No restart infers a new stage from topology. Effects still use
before-effect persistence, source/candidate pins, local create-if-absent and
remote expected-old leases; uncertain effects require re-observation.
`_inspect_isolated_pins` centralizes the previously triplicated source pin,
candidate binding, symbolic-ref refusal and exact transplant validation used
by target publication, published inspection and post-worker checkpointing.

After preparation, the existing execution journal remains authoritative:
worker-running -> post-worker -> checkpointing -> post-checkpoint -> publishing
-> post-publication -> lifecycle, followed by review/accepted/done. Worker
absence, exact checkpoint/report publication and accepted-integration restart
semantics remain in their existing recovery modules. ServiceEngine retains
lock ownership, current-state observations, durable state writes and Git
effects; the proof modules do not become another workflow engine.

Same-base isolated candidates intentionally reuse the entire exact source
chain. Exact ordered commit identity now verifies that case; `git cherry`
alone suppresses shared commits and cannot verify a tip against itself.
Advanced-base candidates retain the existing patch-equivalence verification.

Cleanup remains a separate optional capability. Generation refs and worktrees
are retained with the existing precise diagnostic when retirement authority
is unavailable. A successful done integration does not expand retirement
authority or authorize mutation of historical ticket-scoped resources.

## Regression inventory and test boundaries

Before: one 1,199-line parameterized regression had 45 cases combining source
setup, admission, isolated preparation, worker/report, integration, recovery and
legacy swap assertions. Its sole positive topology asserted direct ancestry.

After: all 45 parameter combinations remain, partitioned into named legacy
swap/ownership, ordinary scheduler/IPC, isolated lifecycle/integration and
isolated preparation/recovery test groups. Shared source setup,
`_exercise_isolated_lifecycle` and `_exercise_isolated_recovery` are separate
phase helpers. The remaining shared case driver owns admission/preparation
and legacy swap orchestration, rather than containing the worker and recovery
implementations inline. No established real-Git boundary assertion was
removed or replaced by a mock.

The new focused suites add 44 cases:

- `test_continuation_history.py`: 2 accepted-mode cases and 15 refusal cases
  for intermediate trees, ordering, lengths, unrelated bases, merges, missing
  commits/trees, executable modes and gitlinks.
- `test_deferred_continuation.py`: 4 positive cases (read-only status, same-base
  and advanced-base ordinary completion, every preparation-stage restart) and
  9 negative admission cases (reconvergent prefix, forged review, rewritten
  base/head/result reports, ambiguous publication, missing report, conflicting
  cache, nonterminal owner), 11 post-worker/refusal/report/integration restart
  boundaries, and 3 interrupted-worker recovery variants. The latter phase
  cases reuse the extracted direct-mode lifecycle and recovery invariant
  helpers against the genuine divergent-prefix fixture, including live and
  indeterminate workers, dirty/locked/foreign targets and remote/ref races.

`_continuation_support.seed_deferred_source` constructs real 17/18 divergent
chains and authentic historical publications directly, before production
admission. It asserts distinct OIDs, common merge base, failed ancestry,
complete ordered ranges, exact parents and corresponding root trees. It
creates no source SQLite owner or failure cache. A real unrelated owner runs
through accepted/done; three subsequent legal defer/requeue cycles admit
independent ticket additions. The historical worktree stays dirty and its
branch/remote and immutable report/review bytes are checked throughout.
The normal target worker, report and accepted integration use production
paths. New negative status and actual IPC plan assertions check the same
specific refusal text, including the blocked ticket's rendered reason.

Original TASK-145/TASK-147 ref races, external-effect uncertainty, cache match,
worker statuses, checkpoint/report/integration interruptions, zero-delta,
submodule and legacy cleanup checks remain in the retained suites.
