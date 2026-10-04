---
"type": "devlegate.ticket"
"title": "Recover lifecycle replay after a failed validation on a published control descendant"
---

## Milestone

Devlegate 0.5.6 lifecycle and recovery hardening.

## Goal

Allow an execution already in the durable `lifecycle` stage to recover correctly
when a previous lifecycle replay fast-forwarded the local control worktree to a
published compatible descendant, but failed before creating the lifecycle commit.

Do not misclassify that already-published descendant as an unpublished local
lifecycle mutation.

## Context

Dogfooding TASK-018 exposed this sequence:

1. Execution `986cc3c6ec0b4440987c0b1272a6e10a` was admitted at control
   `116de834e0a1...`.
2. The worker completed and checkpoint `a8f162a70895...` was created.
3. Remote control had advanced normally to `ecf2a9b64e7a...` because an unrelated
   TODO ticket (TASK-044) was added.
4. `_apply_execution_lifecycle()` proved the descendant compatible and
   fast-forwarded the local control worktree from `116de...` to `ecf2...`.
5. `_apply_lifecycle_once()` then failed during ticket-store validation because
   TASK-044 contained invalid metadata (`depends_on: []`).
6. Runtime correctly remained in the durable TASK-018 lifecycle stage.
7. On the next recovery iteration local control HEAD equaled the already-published
   remote descendant `ecf2...`, while the execution report remained bound to
   `116de...`.
8. The current code enters:

   ```python
   if local_head != report.control_head:
       _prove_unpublished_lifecycle_lineage(local_head, remote_head, report)
   ```

   and therefore treats the published descendant itself as if it were an
   unpublished local lifecycle commit.
9. Recovery blocks with:

   ```text
   unpublished control commit is not the exact lifecycle mutation
   ```

This diagnostic is false for the observed topology: the local commit is a published
control descendant, not an unpublished lifecycle mutation.

## Required behavior

Lifecycle recovery must classify local/remote/control topology before applying the
unpublished-lifecycle proof.

At minimum distinguish:

### A. Local == report.control_head

Existing normal case. If remote advanced compatibly, prove the descendant,
fast-forward, and apply lifecycle once.

### B. Local == remote, both are a published descendant of report.control_head

This is the dogfood recovery case.

- prove `report.control_head` is an ancestor of local/remote;
- prove the active ticket is unchanged between the admitted generation and the
  published descendant;
- treat local/remote as an already-observed published descendant;
- continue/replay lifecycle from that descendant;
- do NOT invoke unpublished-lifecycle classification on the descendant itself.

### C. report.control_head < local < remote, and local is a published ancestor of remote

If local is itself part of the freshly observed remote lineage and contains no
unpublished local-only commits, it is a partially synchronized published descendant,
not an unpublished lifecycle mutation.

Prove the ancestry, fast-forward to remote, then replay lifecycle.

### D. Local contains exactly one local-only lifecycle commit

Retain the existing exact unpublished lifecycle proof. Only this topology should use
`_prove_unpublished_lifecycle_lineage()`.

### E. Any divergent or ambiguous topology

Fail closed without reset, merge, push, or lifecycle mutation.

## Validation ordering

Do not leave recovery dependent on an incidental local fast-forward performed before
a later validation failure.

Where practical, validate the candidate descendant ticket store before permanently
advancing local control, or make the subsequent recovery classification explicitly
idempotent as described above.

An unrelated invalid ticket on the control descendant may block lifecycle
application, but after that ticket is fixed on a later descendant the original
execution must be able to resume lifecycle replay without manual Git surgery.

## Acceptance criteria

- The TASK-018 dogfood sequence above recovers automatically after the unrelated
  invalid ticket is fixed.
- A local HEAD that equals the published remote descendant is not described as
  `unpublished control commit`.
- Compatible active-ticket identity is still proven before lifecycle replay.
- Changed active ticket still fails closed.
- True local-only lifecycle commits still require the exact lifecycle mutation proof.
- Divergent local/remote histories still fail closed.
- No lifecycle report is duplicated.
- No duplicate todo->review transition is created.
- Publication remains lease/identity safe and idempotent.
- Full tests, coverage, and Ruff remain green.

## Required regressions

1. Start lifecycle at control C1.
2. Publish compatible unrelated descendant C2.
3. Lifecycle recovery fast-forwards local C1 -> C2.
4. Force ticket-store/lifecycle validation to fail before lifecycle commit creation.
5. Restart/retry recovery with local=C2 and remote=C2.
6. Publish/fix a later compatible descendant C3 if needed.
7. Prove lifecycle replays exactly once on C3 and completes.

Also cover:

- local=C1, remote=C2 normal replay;
- local=C2, remote=C3 where C2 is a published ancestor;
- one true local-only exact lifecycle commit;
- local divergence;
- active ticket changed on descendant.
