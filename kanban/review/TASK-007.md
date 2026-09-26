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


## Review hardening — tree execution integration

The completed attempt at checkpoint
`fa9485b8432b1f1492a19f7d2223a4bd1fcb8430` with execution
`9574fc6619814fd3822cbdea6cac0cb3` is not yet acceptable.

The tree primitives exist, but planning and execution are still split across two
inconsistent models.

### Blocking findings

1. Debian plan/execution mismatch

`_component_steps("deb")` freezes the internal `package_deb.component_plan()`
leaves first:

- validate standalone input for Debian
- assemble Debian filesystem
- write Debian control metadata
- build Debian package
- write Debian package checksum

and then appends validation/proof/checksum leaves.

However `build_deb()` does not execute that component plan or bridge its events.
It emits one outer step named `build Debian package` before running
`tools/package_deb.py`.

Therefore the frozen plan expects the first Debian internal leaf while execution
attempts to start a later leaf. The reporter should fail closed with an out-of-order
progress error. Fix the ownership boundary rather than weakening ordering checks.

For every atomic or compound tool choose one coherent model:

- either treat it as one thin leaf wrapper with a one-leaf plan and one terminal
  event, or
- treat it as a composite whose own plan is frozen and whose execution emits the
  corresponding internal leaf events.

Do not expose an internal component plan while executing it as one unrelated outer
leaf.

2. Outer orchestrator still duplicates component-owned stage lists

`_component_steps()` still manually appends standalone package/validate/prove
steps and Debian validate/prove/checksum steps. Wheel, sdist, and full-source steps
are also hard-coded there.

The selected distribution must be composed from component objects through the common
leaf/composite interface. Thin wrappers are valid components and may contribute one
leaf. The outer orchestrator may select and compose components, but must not maintain
a second copy of their internal stage lists.

3. The tree is not yet the execution source of truth

`component_tree()` exists, but `package()` still builds a separate flat
`semantic_plan()` and then executes procedural target branches with `_step()`.
The tree therefore does not prove that the frozen denominator corresponds to the
actual component execution structure.

Freeze executable leaves from the actual selected component tree and bind execution
events to that same frozen tree.

4. Hierarchical identities are not yet fully enforced

The new protocol supports `ComponentPlan`, `ComponentStep.key`, and
`freeze_plan()`, but current production plans mostly rely on display names and the
outer layer synthesizes positional IDs such as `standalone/3`.

Use stable component-owned local keys/path segments and derive hierarchical frozen
leaf IDs from the composed tree. Repeated display labels under different parents
must remain unambiguous without name-based fallback.

The reporter should not fall back from an unknown exact identity to matching
`target + name`; an event that does not resolve to exactly one frozen leaf must
fail closed.

5. Required regressions are still missing

Add direct regressions for:

- leaf and composite components satisfying the same interface;
- nested composite freezing with deterministic hierarchical IDs;
- repeated display labels under different parents;
- structural nodes not contributing to the denominator;
- unknown leaf IDs rejected;
- terminal event for a different/future leaf rejected;
- a real component-level skip path;
- a Debian execution path proving frozen plan and emitted events stay in lock-step;
- captured/non-interactive output for a compound target.

### Exact-head CI

GitHub Actions run `36272291730` failed with 951 passed, 1 skipped, 3 failed.

Two failures are directly part of this attempt:

- `tools/build_progress.py` lacks the required Devlegate EUPL source header.
- `test_semantic_plan_is_frozen_and_in_dependency_order` was not updated for the
  changed semantic plan.

The third failure is the previously observed unrelated live-service drop race, this
time in the T-1 variant during a control-worktree Git commit.

Acceptance still requires exact-head tests with coverage and lint to be green.


## Review hardening — executable tree fidelity

The completed attempt at checkpoint
`4d3d321926ccc912c06160060e8fc229cd407edc` with execution
`45c1aad4f1c344329d0fdf70acaf2010` is still not acceptable.

This attempt moves the architecture materially closer to the required model: the
global denominator is now frozen from `component_tree()`, exact identity fallback by
display name was removed, stable component keys are present, Debian emits internal
events, and the licensing header regression was fixed. However the executable tree
and actual target semantics still diverge in several blocking ways.

### Blocking findings

1. Standalone composite execution is structurally invalid

`_target_plan("standalone")` produces a plan with all standalone executable leaves.
But `_component_for_target("standalone")` constructs:

- one `FunctionComponent` whose plan is the whole standalone plan, wrapped inside
- a `TreeComponent` whose own plan has many direct children.

`TreeComponent.run()` pairs `self._children` and `self._plan.children` using
`zip(..., strict=True)`. The standalone component therefore has one runtime child
for many plan children. The first child's event scope is bound only to the first plan
child, so the second internal standalone event cannot resolve correctly; strict zip
cardinality is also inconsistent.

Do not represent one compound executor as one child of a composite plan containing
many unrelated direct leaves. Either make the compound builder itself a component
whose `plan()` and `run()` correspond exactly, or recursively construct runtime
children that mirror the plan tree one-for-one.

Add an execution regression that runs a multi-leaf composite through the real
component adapter and proves that all emitted leaf IDs match the frozen plan in
order. A plan-only regression is insufficient.

2. Debian post-build validation/proof semantics were lost

Before this refactor, distribution-level Debian handling performed, after package
creation:

- `validate_deb.py` against the produced package and standalone build report;
- extraction of the installed binary path;
- execution of the extracted binary with `devlegate version`;
- failure if that execution proof failed.

The new `build_deb()` only calls `package_deb.package()`, whose component plan
covers input validation, package assembly, control metadata, `dpkg-deb`, and
checksum creation. The previous post-build Debian package validation and extracted
payload execution proof are no longer performed.

Restore those semantics as components/leaves in the same tree. They may be thin
wrappers, but they must remain in both the frozen plan and execution path. Do not
satisfy progress architecture by deleting existing validation work.

3. Dependency-only wheel behavior changed

`_target_plan("wheel")` always includes the Python installation proof. Therefore a
wheel built only as an automatically added dependency of `standalone` or `deb`
now runs work that the previous distribution graph did not run. Previously the
isolated Python wheel installation proof was selected for direct `wheel`,
`python`, and `all` requests, not merely because another target depended on the
wheel.

Make component selection context-sensitive where existing semantics require it:
the frozen tree for a selected invocation must contain exactly the work that invocation
will execute, including optional/direct-target proofs, without adding or dropping
validation stages merely because the representation changed.

4. The executable component tree must mirror the plan tree

The current implementation still allows a `FunctionComponent` to advertise an
arbitrary multi-leaf `ComponentPlan` while its runtime action is unconstrained
except by emitted events. This is useful as an adapter for a genuinely compound
builder, but then that builder's `run()` must be the source of those same events and
the adapter must bind the whole subtree consistently.

Strengthen the common component contract/regressions so a component's frozen plan and
its emitted execution sequence cannot silently disagree in structure or scope.

### Exact-head CI

GitHub Actions run `36273351410` completed the full test suite and coverage
successfully, but the workflow still failed at Ruff.

Ruff reported six errors, including:

- duplicate `import contextlib` in `tools/build_standalone.py`;
- two overlong skip-event lines in `tools/build_standalone.py`;
- import formatting failures in `tools/validate_standalone_package.py`.

Acceptance still requires the exact-head workflow, including lint, to be green.

### Additional required regressions

- Execute a real nested/compound component adapter with multiple leaves and assert
  its emitted exact hierarchical IDs are the same ordered leaves frozen from its
  plan.
- Execute the standalone target through the component abstraction far enough to
  prove more than the first internal standalone leaf can be emitted without scope or
  cardinality failure.
- Assert the Debian target tree contains and executes post-build package validation
  and extracted-binary execution proof after package construction.
- Assert direct `wheel`/Python selections include the isolated-install proof while
  dependency-only wheel construction for standalone/Debian preserves the previous
  selection semantics.
- Exact-head tests, coverage, and Ruff all pass.


## Review hardening — remove stale component contract and prove real adapters

The completed attempt at checkpoint
`91e9b8eb49233337cc9efce33f372db9fd8cdbcf` with execution
`c1253f89c78a41898755ef6360685682` fixes the previous runtime-tree mismatches:
standalone now has a scoped build subtree plus package/validate/prove leaves, Debian
post-build validation and extracted-binary proof are restored, and dependency-only
wheel construction no longer includes the direct Python install proof.

Two blocking review findings remain.

### 1. package_standalone still exposes a contradictory multi-step component plan

`tools/package_standalone.py` still exports `semantic_plan()` and
`component_plan()` describing several internal standalone-packaging stages.

However the selected distribution tree deliberately treats the entire
`package_standalone.package()` call as one thin-wrapper leaf:

`standalone/package` — `package standalone archive`.

That is a valid design choice, but the raw tool must then not simultaneously
advertise a second, unused multi-leaf component contract. This is exactly the
plan/execution ambiguity prohibited by the earlier review hardening.

Choose one model and make it unambiguous. For 0.6 the simplest acceptable result is:

- keep `package_standalone.py` as a raw atomic tool;
- remove its unused progress-plan/component API and related imports;
- let the distribution-level thin wrapper own the single
  `standalone/package` leaf.

Alternatively, if its internal stages are intentionally exposed, then its execution
must emit those stages and the distribution tree must freeze that same plan. Do not
keep both models.

### 2. Required regressions still prove only the generic toy tree, not the real target adapters

The new nested-component regression correctly proves the generic scoping machinery,
but the required production-adapter regressions from the previous review are still
missing.

Add focused tests that exercise the real target component construction with expensive
operations mocked/stubbed:

- Standalone: construct/run the real `_component_for_target("standalone", ...)`
  adapter and prove at least two internal `standalone/build/*` events can flow
  through the scoped build subtree followed by outer package/validate/prove leaves,
  with emitted IDs matching the frozen `_target_plan("standalone")`.
- Debian: construct/run the real `_component_for_target("deb", ...)` adapter and
  prove package internal leaves are followed by `deb/validate-deb` and
  `deb/prove-deb` in the same order as the frozen plan.
- Real skip: exercise the supplied-wheel standalone path and prove the component
  emits typed skips for the skipped reproducibility-wheel leaves without advancing
  unrelated work.
- Captured output: run a compound target through the reporter with subprocess/build
  work stubbed and assert the non-interactive output remains a deterministic
  readable sequence with the fixed denominator.

These tests should validate the integration boundary that repeatedly regressed, not
only the standalone `ComponentPlan`/TreeComponent primitives in isolation.

### CI

Exact-head GitHub Actions run `36274320406` was still running at review time.
Acceptance requires its tests, coverage, and Ruff steps all to complete successfully.


## Review hardening — finish required adapter regressions and lint

The completed attempt at checkpoint
`649eb65ec199cfea5c5efcf4b26805c870bce4ba` with execution
`8a0478bf45cd46b99b759d1bed32ab15` correctly removes the contradictory
multi-stage progress/component API from `tools/package_standalone.py`. The
standalone archive packager is now unambiguously a raw atomic tool behind the
distribution-level `standalone/package` leaf.

The attempt does not complete the rest of the previous review requirements.

### 1. Production-adapter regressions were not added

The checkpoint does not add new tests beyond the previous state. In particular, the
required regressions are still missing for the real target adapters:

- run the real `_component_for_target("standalone", ...)` with expensive work
  stubbed and prove multiple `standalone/build/*` events plus
  `standalone/package`, `standalone/validate`, and `standalone/prove` match
  the frozen target plan;
- run the real `_component_for_target("deb", ...)` with work stubbed and prove
  `deb/package/*` is followed by `deb/validate-deb` and `deb/prove-deb` in
  exact frozen-plan order;
- exercise the supplied-wheel standalone path and assert typed skip events for the
  skipped reproducibility-wheel leaves;
- exercise a compound target through `ProgressReporter` with work stubbed and
  assert deterministic captured/non-interactive output and a fixed denominator.

The generic toy nested-tree regression is useful but is not a substitute for these
production-boundary tests, because the integration between real target construction,
component-owned events, hierarchical scoping, and the global reporter was the source
of repeated regressions in this ticket.

### 2. Exact-head Ruff is still red

GitHub Actions run `36275216163` passed the full tests and coverage, but failed
`./dev lint` with the same four Ruff errors as the preceding attempt:

- `tools/build_standalone.py`: two E501 overlong skip-event lines;
- `tools/validate_standalone_package.py`: two I001 import-block formatting errors.

Fix these mechanical lint failures and require the exact-head workflow to complete
green before review acceptance.

No further packaging-progress architecture change is requested here unless one of
the required real-adapter regressions exposes a concrete defect.


## Review hardening — make the real-adapter regressions actually intercept production imports

The completed attempt at checkpoint
`ebf67e0a7eee214a3b41dc68f122e54238820c05` with execution
`6a54e5734d0248e7a250f152be87e2bb` adds the required standalone, Debian,
supplied-wheel skip, and captured-output regressions and includes the previously
requested Ruff formatting changes.

The new regressions do not yet test the intended production adapters because their
monkeypatches target a different module identity from the one dynamically imported by
`_component_for_target()`.

### Blocking test failures

GitHub Actions run `36276357021` failed with 957 passed, 1 skipped, and 3 failed.

1. `test_real_standalone_adapter_emits_frozen_internal_steps`

The test patches `tools.build_standalone.build`, but
`_component_for_target("standalone", ...)` resolves the top-level
`build_standalone` module in this test environment. The real builder therefore
runs and fails reading the synthetic temporary repository's missing
`pyproject.toml`.

2. `test_real_debian_adapter_emits_package_and_post_build_steps`

The test patches `tools.package_deb.package`, but the adapter resolves the
top-level `package_deb` module. The real Debian packager therefore runs and fails
because the synthetic report lacks `source_commit`.

3. `test_real_component_progress_output_is_deterministic_and_noninteractive`

This has the same standalone module-identity problem as the first regression and
runs the real builder instead of the test double.

Fix the regressions so they replace the exact implementation object imported by the
production adapter. Acceptable approaches include arranging the test module aliases
so top-level and `tools.*` imports refer to the same module object, or refactoring
the adapter import seam in a small deterministic way that gives tests one canonical
object to replace. Do not weaken the tests by bypassing
`_component_for_target()`; they must continue to exercise the real production
adapter boundary.

After the fix, the regressions must prove the originally requested event sequences,
skip behavior, and captured output rather than merely avoiding the real builder.

### Final acceptance gate

No new packaging-progress architecture is requested. Re-run exact-head CI and require
tests, coverage, and Ruff all to be green before acceptance.
