<!-- devlegate-review-v1
ticket: TASK-154
execution: edcbf73f6cdc45f6bd89c76dce8ca7d9
checkpoint: a27f8f9aaf85900fb827f784b86b1f5a211a7103
control: 754fe5244c172b3d2ae0712ebbf4b111e4cc0115
disposition: rework
-->

# TASK-154 — REWORK: complete the actual Change Explorer and fix CI lint

## Primary acceptance-blocking outcome

The exact valid execution report `edcbf73f6cdc45f6bd89c76dce8ca7d9` claims `incomplete` with successful process/transport/egress and no questions, and explicitly lists the missing owner checkpoint-lineage integration, privileged read-only backend, OpenCode tool adapters, metering, pagination, submodules and tests. Source comparison from admitted product `479ff05917081848c5ca2af3c19ef9d7d62bdc5f` to checkpoint `a27f8f9aaf85900fb827f784b86b1f5a211a7103` confirms only two new files, `src/devlegate/change_revisions.py` (90 lines) and `tests/test_change_revisions.py` (43 lines). They introduce an isolated `RevisionNamespace`, `Checkpoint`, cross-base rejection and small pure tests. No actual user-facing `revisions()`, `changes()` or `diff()` tool, owner-scoped authorized transport, current-worktree observation, or historical checkpoint resolution is implemented. This is useful preparatory code, but it is not the canonical ticket outcome.

**Next action:** implement the smallest coherent **end-to-end owner-authorized read-only Change Explorer path** that resolves durable actual task checkpoints to virtual labels and exposes the three required worker-facing operations through the supported OpenCode adapter. Bind requests to the active execution/managed workspace, not worker-submitted paths/refs, and preserve read-only effect boundaries. Do not stop after further namespace-only tests or documentation. Carry forward the current code if it remains useful.

## Mandatory dependent checks

1. Implement factual, bounded `changes(from,to,cursor?)` inventories and exactly-one-path `diff(from,to,path,cursor?)`, with service-owned output/computation budgets, stable pagination, explicit incomplete/unavailable states, stale WORKTREE detection, binary/symlink/submodule rules and no arbitrary Git/ref/control identity exposure. Respect cross-base/retained history and zero-delta checkpoint semantics from the unchanged ticket. The current namespace is not yet connected to provenance; the constructor's owner-supplied list is only a precondition, not proof of correct checkpoint order.
2. Add disposable-repository and real adapter/owner tests for first and multi-checkpoint, failed/rebased/squashed, staged/unstaged/untracked, submodule and .gitmodules cases; safe refusal of wildcards/traversal, stale cursors, oversized/expensive content, aggregate execution budget across reconnects, and read-only authorization. Do not equate two pure `RevisionNamespace` tests with these behavior contracts.
3. **Hosted CI failure is specifically Ruff, not tests or transient runner failure.** [GitHub Actions run 44340645150](https://github.com/ranmasao/devlegate/actions/runs/44340645150) on the exact `a27f8f9aaf85900fb827f784b86b1f5a211a7103` checkpoint finished `failure`. The full `./dev coverage -n 4 --dist=worksteal` step passed; `./dev lint` failed with five findings:
   - `src/devlegate/change_revisions.py:11` — `I001`, `import re` must precede `from dataclasses import dataclass`.
   - `tests/test_change_revisions.py:4` — `I001`, multiline import formatting.
   - `tests/test_change_revisions.py:8`, `:23`, `:41` — `E501`, line lengths 89/92/91 against limit 88.
   These are straightforward mandatory fixes, but fixing lint alone does **not** warrant ACCEPT.
4. Rerun focused tests, `./dev lint`, `git diff --check` and obtain hosted full tests plus Ruff on the **new exact checkpoint**. Worker reported local pytest unavailable; hosted CI proves the current tests can execute but does not substitute for the missing behavioral coverage.

## Optional observations and boundary

The pure namespace's explicit rejection of bogus virtual labels, duplicate checkpoints, and cross-base comparisons is a reasonable foundation. There is no need to discard it solely because lint failed. TASK-184 (durable cross-execution textual research notes) remains separate, not an expansion of TASK-154. The canonical ticket specification is unchanged, including constraints on submodules, absence-versus-unavailable semantics, transport neutrality, no generic VCS framework, no runtime migration, and no unbounded output.

## Disposition

**REWORK** the same canonical TASK-154. Preserve the exact ticket blob and current code checkpoint, publish this immutable review and move `kanban/review/TASK-154.md` back to `kanban/todo/TASK-154.md` in a single control compare-and-swap commit. Do not modify the product branch, execution evidence, or self-hosted runtime artifacts.
