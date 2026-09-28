---
"type": "devlegate.ticket"
"title": "Allow update-base across a proven amended product tip"
"depends_on": ["TASK-036"]
---

## Milestone

Devlegate 0.5.6 reconciliation recovery.

## Goal

Allow an explicit `reconcile update-base` to transplant a preserved worker
checkpoint onto a rewritten current product tip when Devlegate can prove the narrow,
common `git commit --amend` topology.

Keep arbitrary divergent-history reconciliation fail-closed.

## Context

Current update-base requires the requested/current product target to be a descendant
of the execution's original product base.

That is correct for ordinary forward movement:

```text
B -> O -> T
```

but rejects the common case where the operator amended/replaced the product tip while
an execution was running:

```text
      O  <- original execution base
     /
B ---
     \
      T  <- current product HEAD after amend/rewrite

and the preserved worker checkpoint is:

O -> W
```

Here `O` and `T` are siblings with the same exact parent `B`. The existing
transplant operation already rebases the exact one-commit worker delta with:

```text
git rebase --onto T O
```

but the precondition rejects the topology before that safe, conflict-detecting
operation can run.

The operator has already explicitly requested update-base. Devlegate should support
this narrowly proven rewritten-tip case without generalizing to arbitrary divergent
history.

## Required behavior

### Supported target topologies

Keep the existing ordinary descendant case:

```text
original_base is ancestor of target
```

and additionally accept one rewritten-tip case when all of the following are true:

- `original_base` and the current target are distinct commits;
- both have exactly one commit parent for this proof;
- `original_base^` and `target^` resolve to the same exact commit;
- the target is the freshly observed canonical product HEAD accepted by update-base;
- the existing execution/checkpoint/workspace/publication invariants all hold.

Conceptually:

```text
parent(original_base) == parent(target)
```

is accepted as a proven single-tip replacement.

Do not infer equivalence from commit message, tree similarity, patch-id, timestamps,
author identity, reflog, or heuristic content matching.

### Preserve existing authority and safety

All existing update-base checks remain authoritative, including:

- exact pending reconciliation ticket identity;
- clean product checkout on the expected branch;
- fresh local/remote product agreement;
- exact preserved worker checkpoint evidence;
- exact execution workspace and branch binding;
- unpublished execution history where force-push would otherwise be required;
- exact supported one-commit worker checkpoint shape over `original_base`;
- no workspace mutation before reconciliation authority is proven;
- clean abort back to the preserved checkpoint if rebase conflicts.

The new sibling-tip proof changes only eligibility of the target topology. It does
not weaken the rest of reconciliation.

### Rebase result

For a proven rewritten-tip target:

- transplant the worker delta from `original_base` onto the exact current target;
- require a clean successful rebase;
- prove the reconciled checkpoint parent is exactly the target;
- preserve the existing resolved reconciliation provenance and resume-required
  behavior;
- do not silently resolve content conflicts.

If rebase conflicts:

- abort the rebase;
- prove the execution workspace returned to the exact preserved worker checkpoint
  and is clean;
- remain fail-closed with a clear manual/advanced reconciliation diagnostic.

### Unsupported divergence remains blocked

Continue rejecting cases such as:

```text
B -> ... -> O
 \
  C -> ... -> T
```

or any topology where the target is neither:

1. an allowed descendant under the existing rule; nor
2. an exact same-parent replacement of the original base.

Do not add generic merge-base, cherry-pick, patch-equivalence, or arbitrary rebase
authorization in this ticket.

## Interaction with TASK-039

TASK-039 makes `--onto` optional and defaults update-base to the fresh current
product HEAD.

This ticket is orthogonal:

- TASK-039 decides how the exact target is selected;
- TASK-040 decides whether a same-parent rewritten target is an eligible topology.

The implementation must remain correct whether the target was resolved implicitly or
asserted explicitly with `--onto`.

## Acceptance criteria

- Existing descendant update-base behavior remains unchanged.
- A pending reconciliation with topology `B -> O`, `B -> T`, and exact worker
  checkpoint `O -> W` can be reconciled onto `T` when all other existing safety
  checks pass.
- The same-parent relationship is proven from exact Git commit parents, not inferred
  heuristically.
- A clean transplant yields a new worker checkpoint whose parent is exactly `T`.
- A content conflict aborts cleanly and restores the exact preserved checkpoint.
- Arbitrary divergent targets remain rejected.
- No force push, hidden merge, or guessed conflict resolution is introduced.
- Reconciliation provenance records the exact effective target and existing
  resolution identity.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Descendant target -> existing update-base success path unchanged.
- Same-parent amended/replacement target + non-conflicting worker delta -> success.
- Same-parent target + conflicting worker delta -> rebase abort, original worker
  checkpoint restored exactly, reconciliation remains unresolved/fail-closed.
- Same-parent proof fails when either commit parent differs -> rejected.
- Multi-parent/merge original base or target is not accepted through the rewritten-tip
  shortcut unless already valid under the existing descendant rule.
- Unrelated divergence with a common older merge-base -> rejected.
- Local/remote target disagreement -> rejected before transplant.
- Published execution history -> existing rewrite prohibition remains enforced.
