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
