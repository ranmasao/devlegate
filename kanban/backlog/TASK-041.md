---
"type": "devlegate.ticket"
"title": "Rebase proven multi-checkpoint execution lineage with lease-guarded publication rewrite"
"depends_on": ["TASK-040"]
---

## Milestone

Devlegate 0.5.6 reconciliation recovery.

## Goal

Extend `reconcile update-base` so a proven linear execution lineage can be
transplanted onto an already eligible product target even when an exact prefix of
that execution lineage has already been published under
`devlegate/work/<ticket>`.

Published history may be rewritten only with explicit operator authorization and an
exact compare-and-swap / force-with-lease proof.

Do not add generic force-push, arbitrary-history rebase, merge-based reconciliation,
or heuristic lineage inference.

## Context

TASK-040 supports the narrow rewritten-product-tip case when the preserved worker
checkpoint is exactly one unpublished commit over the original base:

```text
      O -> W
     /
B ---
     \
      T
```

A live LAB-158 recovery exposed the next supported shape:

```text
      O -> W1 -> W2
     /     ^
B ---      |
     \      remote devlegate/work/<ticket>
      T
```

where:

- `O` is the execution's original product base;
- `T` is an update-base target already accepted by the existing target-topology
  rules;
- `W1..Wn` are a linear sequence of execution checkpoint commits rooted at `O`;
- the preserved current checkpoint is `Wn`;
- the remote execution branch may point to an exact published prefix checkpoint
  `Wk`, while later checkpoints remain local/preserved.

The desired reconciled history is:

```text
T -> W1' -> W2' -> ... -> Wn'
```

with the existing execution branch moved from the exact proven old remote checkpoint
to the rewritten current checkpoint under lease/CAS protection.

## Required behavior

### Preserve target eligibility from TASK-040

This ticket does not broaden which product targets are eligible.

The target must still satisfy the existing update-base rules, including:

- the ordinary descendant case; or
- the exact same-parent rewritten-tip case added by TASK-040;
- fresh clean canonical product local/remote agreement;
- exact ticket, reconciliation, workspace, checkpoint, and control bindings.

Do not add patch-id, message, timestamp, author, reflog, tree-similarity, generic
merge-base, or semantic-content inference.

### Prove a linear multi-checkpoint execution lineage

Replace the current exact one-commit worker-shape restriction with a bounded proof of
the persisted execution lineage from `original_base` to the preserved
`worker_checkpoint`.

For the supported case:

- `original_base` is an ancestor of the preserved worker checkpoint;
- every commit in `original_base..worker_checkpoint` is a single-parent commit;
- following first parents from the checkpoint reaches `original_base` exactly;
- no merge commit, side branch, or unrelated commit is accepted;
- the execution workspace is clean and exactly at the preserved checkpoint before
  mutation;
- the existing reconciliation evidence ref still names the exact preserved old
  checkpoint.

Conceptually:

```text
O -> W1 -> W2 -> ... -> Wn
```

is supported; arbitrary DAGs are not.

The transformed lineage must remain one-for-one and ordered. Do not silently squash,
drop, combine, or invent execution commits during reconciliation.

### Published execution prefix

Keep the existing unpublished path unchanged.

If the remote execution branch does not exist, no publication rewrite authorization
is needed.

If the remote execution branch exists, accept it only when all of the following are
proven:

- the exact remote ref is the expected execution branch for this ticket;
- its current remote value equals the persisted/expected execution remote identity;
- that commit is exactly one checkpoint in the proven
  `original_base..worker_checkpoint` lineage;
- it is therefore an exact prefix of the preserved execution lineage;
- no external remote movement has occurred since observation;
- the operator explicitly authorized published-lineage rewrite for this request.

A remote execution commit outside the proven lineage remains a hard rejection.

### Explicit authorization

Add an explicit authorization for the stronger remote side effect, e.g.:

```text
devlegate reconcile update-base <ticket> [--onto <commit>] --rewrite-published
```

The exact option spelling may follow existing CLI conventions, but it must clearly
mean permission to rewrite the already published Devlegate execution ref.

The option does not weaken any proof. It only authorizes the side-effect class after
all normal reconciliation invariants have passed.

Without the authorization, a reconciliation that would require non-fast-forward
publication must remain blocked with a clear diagnostic naming the execution ref and
the required explicit action.

### Preserve displaced lineage before remote rewrite

Before any non-fast-forward remote branch update:

- durably pin the displaced preserved checkpoint under Devlegate provenance
  evidence so the old `O -> W1 -> ... -> Wn` lineage remains recoverable after the
  execution branch moves;
- the evidence identity must be exact and immutable for this reconciliation
  generation;
- publication of the rewritten branch must not be the only surviving reference to
  the old execution history.

The preservation mechanism must be strong enough for crash recovery and must not
depend only on an ephemeral in-memory value or reflog.

### Transplant

For an accepted target `T`, transplant the whole proven execution delta
`original_base..worker_checkpoint` onto `T`.

Conceptually:

```text
git rebase --onto T O
```

over the existing execution branch/worktree is acceptable, provided the result is
proved rather than assumed.

Require:

- a clean successful transform;
- exactly the expected number of rewritten execution commits;
- a single-parent ordered chain rooted directly at `T`;
- the rewritten workspace HEAD is the new current checkpoint;
- no silent conflict resolution, squash, commit drop, or merge;
- a conflict abort restores the exact original preserved checkpoint and clean
  workspace.

### Lease-guarded publication

When the old execution branch was already published, move it only with an exact
lease / compare-and-swap equivalent to:

```text
expected old remote = Wk
new remote          = Wn'
```

A `git push --force-with-lease=<ref>:<expected>`-equivalent update is acceptable.

Never use unconditional `--force`.

If the remote ref no longer equals the expected old identity, fail closed. Do not
retry with a weaker lease, refresh-and-force automatically, or infer that the new
remote value is compatible.

After publication, freshly re-observe the remote and require it to equal the exact
rewritten checkpoint.

### Durable intent and crash recovery

Treat published-lineage rewrite as a recoverable mutable operation.

Before the first irreversible remote effect, persist enough write-ahead identity to
distinguish at minimum:

- ticket and reconciliation generation;
- original base `O`;
- exact target `T`;
- original preserved checkpoint `Wn`;
- expected old remote checkpoint `Wk`;
- displaced-lineage evidence identity;
- the rewritten checkpoint identity once it is known.

Recovery must distinguish:

1. no rewrite occurred -> original pending reconciliation remains recoverable;
2. local rebase occurred but remote did not move -> prove/abort/retry safely;
3. remote lease update succeeded but process died before final state commit ->
   re-observe and prove the exact already-published rewritten checkpoint, then
   finalize idempotently;
4. remote changed to any other value -> block as ambiguous/external movement.

Do not rely on process-local memory to determine whether the remote side effect
occurred.

### Resolved state

After exact publication proof, preserve the existing update-base semantics:

- reconciliation becomes `resolved`;
- `resolution == "update-base"`;
- `effective_base == T`;
- execution base/start state is rebound to `T` as required by existing resume
  handling;
- the current worker checkpoint identity becomes the rewritten `Wn'`;
- the current execution remote identity becomes the rewritten remote checkpoint;
- `resume_required` remains required for the same ticket;
- old displaced lineage remains reachable through its provenance evidence.

## Safety / non-goals

- No generic `git push --force`.
- No generic arbitrary rebase command.
- No merge-based reconciliation.
- No squash/reconstruction of the execution delta into one commit.
- No new execution-generation branch model in this ticket.
- No heuristic equivalence between old and new commits.
- No automatic conflict resolution.
- No acceptance of merge commits or non-linear execution DAGs.
- No deletion of displaced evidence after success.
- Do not weaken TASK-040 target-topology proofs.
- Do not make published-history rewrite implicit merely because it appears safe.

## Interaction with TASK-039

TASK-039 controls how the exact update-base target is selected when `--onto` is
omitted.

This ticket controls execution-lineage shape and authorization/publication after the
target is already resolved and proven.

The implementation must work with both:

- an explicitly asserted target via `--onto`; and
- the future implicit current-product target from TASK-039.

## Acceptance criteria

- Existing one-commit unpublished update-base behavior remains unchanged.
- A clean linear multi-checkpoint execution lineage can be transplanted as a whole.
- A remote execution ref that points to an exact proven prefix checkpoint is
  recognized as published execution lineage rather than rejected categorically.
- Published rewrite requires explicit operator authorization.
- The old execution lineage is durably preserved before the execution branch moves.
- Remote rewrite uses an exact lease/CAS against the previously proven remote SHA.
- External remote movement causes a clean fail-closed rejection with no weaker retry.
- The rewritten result is a one-for-one, ordered single-parent execution chain rooted
  exactly at the target.
- Rebase conflict restores the original checkpoint and clean workspace.
- Crash after successful remote publication but before final state commit is
  recoverable idempotently by exact observation/proof.
- Reconciliation resolves with the rewritten checkpoint and target identities and
  requires resume as before.
- Tests, coverage, Ruff, and existing reconciliation behavior remain green.

## Required regressions

- `O -> W1 -> W2`, unpublished, descendant target -> successful whole-lineage
  transplant.
- `B -> O -> W1 -> W2`, `B -> T`, unpublished -> successful same-parent target
  transplant.
- Published prefix `remote=W1`, current checkpoint `W2`, explicit rewrite
  authorization -> successful lease-guarded rewrite to rewritten `W2'`.
- Published current checkpoint `remote=W2`, explicit rewrite authorization ->
  successful lease-guarded rewrite.
- Same published cases without explicit rewrite authorization -> blocked before
  remote mutation.
- Remote branch points to a commit outside `O..Wn` -> rejected.
- Remote branch moves after proof / lease expectation -> lease failure, no weaker
  force retry, reconciliation remains recoverable.
- Multi-commit lineage containing a merge -> rejected.
- Non-linear or side-branch execution history -> rejected.
- Rebase conflict on any execution commit -> abort to exact old `Wn`, clean
  workspace, old remote unchanged.
- Transform that would drop/squash an execution commit -> rejected rather than
  silently accepted.
- Crash/restart before remote rewrite -> original pending state recoverable.
- Crash/restart after exact remote rewrite but before resolved-state commit -> exact
  published result recognized and finalized idempotently.
- Displaced old checkpoint remains reachable through durable evidence after success.
