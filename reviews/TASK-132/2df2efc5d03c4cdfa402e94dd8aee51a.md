<!-- devlegate-review-v1
ticket: TASK-132
execution: 2df2efc5d03c4cdfa402e94dd8aee51a
checkpoint: 99fec169753c79c270491bf914083ceecdbe7ac5
control: 5b22baf9919589cb0fa67933693e63af030fdc44
disposition: accept
-->

# TASK-132 — ACCEPT: one-argument reset including safe discard of dirty generation

## Exact execution and independently observed CI

**ACCEPT** execution `2df2efc5d03c4cdfa402e94dd8aee51a` at immutable
checkpoint `99fec169753c79c270491bf914083ceecdbe7ac5`, with
product base `05103891dd59bdbaadc0f8b55f31f354dbd05777` and
admitted control `5b22baf9919589cb0fa67933693e63af030fdc44`.
The canonical execution report records `conclusion=completed`,
`process_returncode=0`, successful transport/egress, and no outstanding
questions or work. The worker reports 2246 tests passed, 2 skipped,
Ruff and whitespace checks passing.

Independently observed GitHub Actions
[run 43770258012](https://github.com/ranmasao/devlegate/actions/runs/43770258012)
for the **exact checkpoint** completed **SUCCESS** on 15 November 2026.
The `check` job successfully executed tests with coverage and Ruff.
The `publish-coverage` job was skipped; it is not a failure of the
required `check` job.

## Resolution of previous reviewer blocker

The prior review of `e7bd25b2c145464aa2a44b486dd085b2` correctly
required rework: a normal running agent leaves tracked and/or untracked
changes inside its worktree, while the original reset retirement demanded
a completely clean checkout. This defeated the principal
`devlegate reset TASK-ID` use case even with green CI.

The correction replaces that blanket rejection with
`_require_discardable_generation`: the exact independently bound
generation is observed using `ResourceBinding.require_local(clean=False,
read_only=True, dirty_submodules=True)`; submodule HEAD, checkout root
and administration are checked; the checkout and generation admin tree
are scanned without following content symlinks. Unknown repositories,
special files, mount boundaries, unsafe Git administration and locks
remain blockers. Only a proven and retired generation can reach
the single `git worktree remove --force` effect.

## Contract review

1. **Worker safety and durable resource ownership — PASS.**
   `ticket_reset.advance` retains owner-serialized cancellation and
   group-retirement proof before invoking the retirement engine.
   `retire_ticket_generation` retains the independent generation
   binding, receipt checks, stage journal, code pin, exact local-ref
   deletion and remote lease/CAS deletion. The dirty-content change
   does not authorize deletion of foreign resources or bypass a missing
   worker-retirement receipt.
2. **Dirty worktree and nested submodule policy — PASS.**
   A stopped or interrupted worker's ordinary tracked edits, untracked
   files, ignored content and hidden-index edits can now be discarded.
   Recursive submodules can be dirty without changing their pinned HEAD;
   unproved submodule administration, locks, changed HEAD, symlinked
   paths and unknown nested Git repositories still fail closed. The
   old code, including linked content symlinks, is not transferred into
   the fresh execution.
3. **Fresh single-argument lifecycle — PASS.**
   The public `reset TASK-ID` contract remains owner-derived and
   requires no plan digest, generation, request ID or prior inspect.
   The real-process/real-Git test exercises CLI-through-IPC cancellation
   after writing tracked and untracked files *inside* the worktree,
   then checks a new execution ID/generation, preserved exact ticket
   blob, current product HEAD/model, clean replacement and absence
   of old source/prompt content.
4. **Replay and negative cases — PASS.**
   Tests exercise ordinary dirty replay after a lost acknowledgement,
   already-stopped dirty attempts, recursive dirty submodules, unknown
   nested/bare repositories, FIFOs, Git administration locks,
   foreign administration and changed submodule HEAD. The previously
   reviewed exact-identity and staged-ref retirement protections remain
   present; no separate historical workspace namespace is introduced.
5. **Documentation — PASS.**
   `docs/reset.md` now distinguishes intentionally discarded ordinary
   content from structural/resource uncertainty, explains duplicate
   delivery versus a second intentional reset, and documents replay
   and recovery.

## Nonblocking boundaries and follow-up

The one-argument interface cannot distinguish a duplicate delivery
from an intentional second reset of an unchanged just-started worker;
the existing receipt is returned. This is documented, not concealed.
The generation scan has a linear cost in checkout/admin entries and
does not promise atomic exclusion of arbitrary out-of-band filesystem
writers; it relies on the existing single-owner and proven worker-
retirement boundary. No production reset was performed by this review.

The separate self-hosted incident in which automatic resume copied a
prior `rework_provenance.preparation_stage` into a new resource
generation is **not** part of TASK-132 and is not fixed by this
acceptance. Preserve the temporary bootstrap and address
`REWORK -> process_loss -> automatic_resume -> prepare_handoff`
in its own regression-backed ticket.

Promote the **unchanged** TASK-132 ticket blob from `review` to
`accepted`, append this review to immutable review history, and let
Devlegate perform its usual accepted integration lifecycle. Do not
manually merge product code, reset runtime SQLite or touch active
execution resources.
