---
"type": "devlegate.ticket"
"title": "Recover interrupted rework through exact resource-generation handoff"
---

## Incident and exact reproduction — 2026-11-15

Self-hosted Devlegate on WSL lost its service/worker during TASK-132 rework.
The original reviewed execution was
`e7bd25b2c145464aa2a44b486dd085b2` (review checkpoint
`3154861fce42d2e75872b6ab76af8584672a363f`).
The interrupted rework execution was
`8880b0d3e3a84b429ef35508600816bc`, resource generation
`be8c7ee073b366b6545b432270986da0cf605eee54b286e9353d69a99e396cf4`,
with worktree HEAD
`dbc1967b248552bbe81b7c99c101e650adbb6e2d`, ordinary uncommitted
source edits and independently written resource, worker and predecessor
publication receipts. The prior worker's boot ID differs from the restarted
Linux kernel boot ID: it cannot still be running in that kernel.

The automatic retry correctly admitted a new execution
`2df2efc5d03c4cdfa402e94dd8aee51a`, generation
`c94bb907acf616b1a367e6a7d13e996a5e577d9f7fdf52e17ae0e667d79edf9a`,
with `predecessor_execution=8880b0d3...` and matching
`predecessor_generation`. That new generation had only `intent.json`.
However, the coordinator also carried the previous execution's
`rework_provenance.preparation_stage=remote_predecessor_installed`.
`_prepare_execution_workspace()` prioritized this inherited rework stage,
called `_inspect_bound_execution_workspace()` on the just-created intent,
and blocked: `published rework workspace cannot be resumed:
generation intent is untouched; creation is pending`.

The normal `ResourceBinding.prepare_handoff()` branch existed but was
unreachable whenever inherited rework provenance was present. This is an
execution-routing/lineage bug, not missing Git teardown machinery or a
reason to fabricate new receipts.

## User-visible goal

An interrupted or safely retired rework worker must be resumed by an
**authorized new execution and resource generation** that takes over the
exact proven predecessor worktree, including uncommitted WIP, preserving
review provenance and all resource/worker ownership invariants. Automatic
retry must not rewind to the earlier reviewed checkpoint, erase dirty work
or re-run already completed remote predecessor publication. Normal restart
must replay the *same* pending generation, never admit additional generations
merely because preparation was interrupted.

## Required correction

1. Distinguish immutable review-origin provenance (reviewed execution,
   checkpoint, feedback) from **per-resource-generation** rework preparation
   progress. Never treat `remote_predecessor_installed` from generation A as
   proof of completed preparation for newly admitted generation B. Resolve
   the correct preparation operation using the admitted binding and exact
   predecessor relationship, not a stale inherited stage alone.
2. Reuse the existing `ResourceBinding.prepare_handoff()` and resource
   observation/receipt functions for an authorized retry of an already
   prepared rework generation. Preserve the strict standalone reviewed
   rework preparation path when there is no admitted resource predecessor;
   do not conflate reviewed predecessor with the most recent worker
   generation. Avoid duplicate predecessor remote publication or resetting
   committed history; retain dirty tracked/untracked edits.
3. Preserve owner serialization, durable SQLite WAL/revision CAS, worker
   retirement/absence proof, generation nonces, filesystem/path/inode
   witnesses, exact Git refs, remote observation, and fail-closed ownership.
   Do not assume `worker_identity=None` in a *new* execution is proof that
   the preceding worker retired; use the existing documented recovery proof
   boundary. A boot-ID mismatch is a useful observed process-absence fact,
   not permission to invent normal worker-retirement receipts.
4. Make interrupted handoff replay explicit: crash before intent, after
   intent but before resource effect, after `handoff-intent` but before
   creation receipt, after receipt before worker launch, and after worker
   launch. Reuse an independently proved settled generation; on genuinely
   uncertain effects, fail closed with an actionable recovery diagnostic.
   Never synthesize evidence or blindly delete a dirty worktree.
5. If an already persisted state created by the affected code needs a
   special manual correction, provide a **one-shot untracked, narrowly
   scoped operator procedure/script**, with dry-run and independent proof.
   Do not add long-lived runtime migrations, historic-compatibility shims,
   a second recovery engine, or a new CLI command.

## Verification and acceptance

- Regression using disposable real-Git, real worktree and process-lifetime
  fixtures matching the exact `REWORK -> process_loss -> automatic_resume
  -> pending generation intent -> prepare_handoff` incident.
- Validate same execution ID/generation through restart, correct predecessor
  generation, unchanged review context and retained old worktree edits.
- Validate a successfully resumed worker can checkpoint and publish, while
  repeated interruption and restart cannot duplicate a worker, remote
  publication or accept a foreign generation.
- Adversarial cases: mismatched nonce, replaced worktree/inode, changed
  local/remote head, unproved prior worker, stale or incomplete handoff
  evidence, dirty but owned WIP, forged receipt, altered control lineage
  and no predecessor on first reviewed rework.
- Full configured tests/coverage, Ruff, whitespace checks and exact
  checkpoint hosted GitHub CI. Document the state-transition distinction
  and a concise operator diagnostic.

## Self-hosting and scope boundaries

TASK-132 is **done/integrated** at product checkpoint
`99fec169753c79c270491bf914083ceecdbe7ac5`.
The operator recovered that execution using an independently built,
temporary Debian standalone bootstrap based on commit
`81ea52ec7cecf603cd6b200ed6cefda4e81a5f5d`.
Its minimal out-of-band workaround routes a pending inherited rework with
an admitted predecessor to `prepare_handoff()`. Treat it as evidence
for reproducing the bug, **not** a validated general fix or a separate
maintained distribution. It is installed locally as `0.5.7.dev0`; package
version alone cannot identify runtime source provenance.

Do not overwrite the local installed runtime, operator SQLite, current
worktrees, Git refs, immutable reports, reviews or archived TASK-132
resources as part of implementation/testing. Product code and tests must
pass through the normal Devlegate checkpoint/review/integration lifecycle;
native package replacement and retirement of the bootstrap executable are
a separate explicit operator deployment step after integration.
No unrelated TASK-133 amendments, TASK-163 optimization, large refactors,
migration code, new orchestration framework, or arbitrary resource cleanup.
