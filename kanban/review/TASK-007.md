---
"type": "devlegate.ticket"
"title": "Show detailed semantic progress during package builds"
---

## Milestone

Devlegate 0.5.5 packaging UX hardening.

## Goal

Make package builds expose fine-grained, truthful progress across the whole selected
distribution graph instead of showing only a few coarse outer stages.

## Context

The current distribution orchestrator primarily reports dependency-graph nodes, for
example:

```text
[1/3] Building wheel
[2/3] Building standalone
[3/3] Building deb
```

This is insufficient for long-running targets such as `standalone` and `deb`,
because one outer node contains multiple meaningful build, packaging, validation,
and proof steps and can appear stalled for a long time.

The desired model was agreed during packaging design:

1. Each build component first exposes a deterministic semantic plan: named steps and
   their count/order for the selected invocation.
2. The outer distribution orchestrator combines those component plans and freezes
   one global progress plan before execution.
3. During execution, components emit semantic progress events such as
   `start`, `complete`, `skip`, and `fail`.
4. The outer orchestrator owns presentation of the global progress scale.

Progress must be based on actual semantic work, not elapsed time or guessed duration.
Do not fabricate smooth time-based percentages.

Relevant code includes `tools/build_distribution.py` and the component builders it
orchestrates, including standalone packaging/validation and Debian packaging.

## Required behavior

- Before performing the selected package build, the orchestration layer can obtain
  the complete semantic step plan for all selected targets and automatically added
  dependencies.
- The combined global plan is frozen before execution begins, so its denominator
  does not move as work proceeds.
- Long compound targets expose their meaningful internal stages rather than
  collapsing the whole component into one opaque "Building ..." step.
- Components report semantic execution events to the outer orchestrator; they do
  not independently invent unrelated progress bars or percentages.
- The outer orchestrator renders one coherent fine-grained progress view using the
  frozen global plan.
- A step advances progress only when the corresponding semantic event warrants it.
  There is no timer-based, throughput-guessed, or fake interpolation.
- Explicitly skipped work is distinguishable from completed work while still
  allowing the global plan to reach a terminal state.
- Failures identify the current semantic step and leave the displayed progress
  truthful about what completed before the failure.
- Existing target dependency semantics remain unchanged: requesting targets such as
  `standalone` or `deb` still builds required prerequisites automatically.
- Non-interactive/log-captured output remains understandable and deterministic; the
  implementation must not depend on terminal cursor tricks for correctness.
- Packaging results, validation, reproducibility, and artifact selection semantics
  remain unchanged.

## Acceptance criteria

- A `./dev package deb` invocation presents materially finer progress than the
  current wheel/standalone/deb three-stage view and exposes meaningful nested
  standalone and Debian build/validation work.
- The total progress denominator is known and fixed before the first build step is
  executed.
- Every visible progress advance corresponds to a planned semantic step transition.
- The progress view reaches a clear completed state on success and clearly identifies
  the failing step on error.
- Different targets produce plans appropriate to their actual dependency graph
  without counting work that will not execute.
- No percentage or progress fraction is derived from elapsed wall-clock time.
- Existing package artifacts and validation behavior are unchanged.
- Full tests and lint remain green.

## Required regressions

- Given a target whose dependency graph includes multiple component builders, when
  planning occurs, then the global plan contains the ordered semantic steps of the
  dependencies and selected target before execution starts.
- Given a component with several internal stages, when it emits start/complete
  events, then the outer progress view advances according to those named stages
  rather than treating the component as one opaque step.
- Given a planned step that is explicitly skipped, when the skip event is received,
  then the global progress state records it as skipped without falsifying completion
  of unrelated work.
- Given a failure during an internal package stage, when the failure event is
  received, then output names the failed semantic step and does not advance later
  planned steps.
- Given captured/non-interactive output, when a package build runs, then semantic
  progress remains readable without ANSI cursor control or terminal-only behavior.
- Given identical source/configuration and selected target, when planning is
  repeated, then the semantic plan and step ordering are deterministic.


## Review hardening

The first implementation reached checkpoint
`b42bc0ff6aa0f94c643146c25e132a1f029381c3` with execution
`09b48a76fa564cea8c7cf1b888d93c04`.

Review found that the implementation does not yet satisfy the component/event
boundary required by this ticket:

- `tools/build_distribution.py` centrally hard-codes `COMPONENT_STEPS` and wraps
  whole component calls with `_step(...)`, while the component builders themselves
  do not expose their semantic plans or emit semantic progress events.
- As a result, the longest standalone operation remains the opaque
  `build standalone executable` subprocess even though it performs multiple
  meaningful internal operations. The outer plan can therefore drift from component
  behavior and cannot prove that its denominator describes the work the component
  will actually execute.
- The progress reporter accepts a `fail` event for any planned step without
  proving that the same step is currently started. Failure events must be ordered
  and fail-closed just like completion.
- `skip` is supported syntactically by the reporter, but there is no real
  component-level skip emission/regression proving that planned optional work is
  surfaced truthfully.

Harden the design so semantic plan ownership lives with the component that owns the
work, with an explicit interface consumed by the outer orchestrator. The outer
orchestrator should combine/freeze component plans and own presentation, but should
not duplicate the components' internal stage lists. Component execution must emit
typed semantic events for the frozen plan (directly or through a small shared
callback/protocol) so internal standalone/Debian/package/validation stages are
observable rather than represented only by one outer subprocess wrapper.

The exact-head CI run `36269805896` also failed with 953 passed, 1 skipped, and one
failure in the unrelated live-service drop test
`test_real_service_drop_retire_old_lineage_and_runs_fresh[T-2]` at concurrent
`git add -A`. A rerun was requested to distinguish that apparent race from this
packaging change. Regardless of the rerun result, the component/event architecture
above remains a blocking review finding.

### Additional required regressions

- Given a component implementation, its exported semantic plan is the source of
  truth used by the outer global plan; the outer orchestrator does not maintain a
  duplicated list of that component's internal steps.
- Given a long standalone build, meaningful internal stages are emitted from the
  component execution boundary and appear in the frozen global progress stream.
- Given a started step, a fail event for a different or future step is rejected
  rather than silently resetting reporter state.
- Given a component that intentionally skips planned work, the component emits a
  typed skip event for that exact planned step and the global reporter records it as
  skipped.
- Exact-head full tests with coverage and lint are green before acceptance.


## Component tree contract

The progress component abstraction must be tree-capable from the start rather than
introducing separate unrelated interfaces for atomic wrappers and compound builders.

Use one component contract for both leaves and composites:

- A leaf component represents one semantic operation, typically a thin wrapper over
  one existing packaging/validation/proof tool or function.
- A composite component owns ordered child components through the same interface.
- Component plans form a deterministic tree. Each node has a stable hierarchical
  identity independent of its display label so repeated labels in different
  subtrees cannot collide.
- Structural/group nodes organize the tree but do not themselves advance the global
  progress denominator. Progress accounting is over the frozen executable leaves.
- The outer distribution orchestrator recursively composes and freezes the selected
  component tree before execution, then renders events for those frozen leaves. It
  must not duplicate internal component step lists.
- Atomic tools do not need to become progress-aware themselves. A thin leaf wrapper
  may expose a one-step plan, run the existing tool, validate its result, and emit
  start plus exactly one terminal event for that leaf.
- Compound builders may contain leaf wrappers and/or nested composite components and
  therefore expose meaningful internal stages without a special second interface.
- Events identify the exact frozen hierarchical leaf ID, not only a display name or
  target/name pair.
- Start/complete/fail/skip sequencing remains fail-closed for the exact current leaf.

Keep this minimal for 0.6. Do not add parallel execution, subtree retry, timing-based
scheduling, visualization, or generalized recovery semantics as part of this ticket.

### Additional tree regressions

- Given an atomic tool wrapper, it satisfies the same component interface as a
  composite component and contributes exactly one executable leaf to the plan.
- Given nested composite components, recursively freezing the plan produces stable,
  unique hierarchical leaf IDs in deterministic execution order.
- Given two leaves with the same display label under different parents, their IDs
  remain distinct and events cannot resolve ambiguously.
- Given structural/group nodes, they do not double-count progress in addition to
  their executable descendants.
- Given an event for a leaf ID not present in the frozen plan, or a terminal event
  for a leaf other than the currently started leaf, progress handling fails closed.
