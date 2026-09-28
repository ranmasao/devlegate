---
"type": "devlegate.ticket"
"title": "Reconcile managed systemd units across installation identity changes"
---

## Milestone

Devlegate 0.5.5 host supervision hardening.

## Goal

Make Devlegate-owned systemd hosting converge safely to the desired representation
of the currently installed Devlegate product whenever installation identity
changes.

The mechanism must be generic and bidirectional: upgrade, downgrade, reinstall,
and supported packaging-form changes use the same reconciliation logic. Do not
implement one-way version migrations.

## Context

Dogfooding an updated standalone build exposed friction in the current managed
systemd authority model.

The existing unit was recognizably Devlegate-owned: it carried the managed marker,
the expected state key, the persisted project/supervision authority, and the
canonical unit name. However, its full rendered contents no longer matched what the
newly installed Devlegate would render because the product launcher identity had
changed.

The CLI's systemd authority preflight treated this deployment-representation
difference as an unexpected identity and refused to operate:

```text
devlegate: refusing to operate on systemd unit with unexpected identity
/home/daniil/.config/systemd/user/devlegate-2f443efe.service
```

This creates a catch-22: the unit must already match the current render before the
command path that can refresh it is allowed to run.

A second compatibility edge appeared because newer host policy requires a durable
host installation record. An existing registered project/systemd authority created
under an earlier product state can therefore be operationally recognizable while
the newer installation record is absent.

The desired model is not "migrate version A to version B". Persist stable intent
and ownership, observe the current machine/product state, derive the desired
deployment representation, and reconcile owned resources toward it.

Conceptually distinguish:

- **ownership identity**: facts proving that Devlegate is authorized to manage the
  resource (managed marker, state key, registered project/repository/env identity,
  persisted supervision authority, canonical unit identity);
- **deployment representation**: fields expected to change with the currently
  installed product (ExecStart/launcher path and other rendered service details).

A stale deployment representation is normal after an installation-identity change.
A foreign or ambiguous ownership identity is not.

## Required behavior

- Devlegate can distinguish a stale but provably owned managed systemd unit from an
  unmanaged, foreign, or ambiguously owned unit.
- Stable ownership proof does not require the existing unit's full text or
  `ExecStart` to already equal the render produced by the current executable.
- When ownership is proven and only deployment representation is stale, normal
  service install/start/default-command flows reconcile the unit to the current
  desired representation.
- Reconciliation is symmetric with respect to product versions. Installing an
  older supported Devlegate over a newer one follows the same observe/classify/
  converge path as an upgrade.
- Reconciliation also handles supported installation-form/path changes through the
  same desired-state mechanism; do not encode version-to-version migration tables.
- Managed-unit refresh preserves project runtime state, control state, repository
  registration, and persisted execution state.
- Unit replacement is atomic at the file boundary, followed by the required
  daemon-reload/start or restart behavior and readiness proof.
- If an existing service process must be stopped before replacing the unit, do so
  through the established lifecycle/authority semantics; do not silently kill a
  healthy owner merely to rewrite configuration.
- Repeated reconciliation is idempotent.
- Unmanaged units, wrong state keys, wrong project/env/repository bindings, or
  conflicting persisted authority continue to fail closed and are never silently
  overwritten.
- Host-installation metadata must not become an unrecoverable historical stamp.
  When current durable project/systemd evidence is sufficient to reconstruct or
  adopt the intended host policy safely, provide a supported deterministic path;
  ambiguous evidence must fail closed.
- No manual editing, renaming, or deletion under
  `~/.config/systemd/user` should be required for an ordinary supported
  upgrade/downgrade.

## Classification model

The implementation should expose one coherent internal classification or equivalent
semantics:

- **CURRENT** — owned resource already matches the desired representation;
- **RECONCILABLE** — ownership is proven but representation is stale/incomplete;
- **AMBIGUOUS/FOREIGN** — ownership or material binding cannot be proven strongly
  enough for automatic mutation.

Only RECONCILABLE state may be automatically rewritten.

## Acceptance criteria

- Given a Devlegate-owned unit rendered by installation identity A, running
  supported installation identity B can refresh and start the same managed project
  without manual systemd-file manipulation.
- Repeating the scenario in the opposite direction, B -> A, succeeds through the
  same reconciliation mechanism.
- A change only to `ExecStart`/launcher identity is treated as stale deployment
  representation, not as loss of ownership.
- After reconciliation, `systemctl --user cat` reflects the current desired unit,
  the service reaches ready state, and client/service versions and identities are
  coherent.
- Runtime SQLite state, workflow control history, registered project identity, and
  active ticket state survive the host representation refresh.
- A unit with an invalid managed marker/state key/project binding remains untouched
  and produces a concrete fail-closed error.
- A missing newer host-installation record can be safely reconstructed/adopted only
  when existing durable evidence proves one unambiguous host policy; otherwise the
  operation refuses to guess.
- Full tests, lint, systemd supervisor tests, and relevant standalone/distribution
  proofs remain green.

## Required regressions

- Install/render with launcher A, then reconcile using launcher B: same owned unit
  name, refreshed `ExecStart`, successful daemon-reload/start/readiness.
- Repeat B -> A to prove downgrade symmetry and absence of one-way migration logic.
- Owned unit with stale non-ownership render fields -> RECONCILABLE.
- Wrong managed marker, state key, registered project/env binding, or conflicting
  supervision authority -> AMBIGUOUS/FOREIGN and no rewrite.
- Persisted authority plus missing canonical unit -> deterministic recreation using
  the current desired representation.
- Existing owned unit plus missing host-installation policy record -> safe adoption
  only with sufficient unambiguous evidence.
- Repeated reconciliation when already current is a no-op.
- Failure between unit rewrite and start leaves a recoverable, inspectable state and
  a subsequent invocation converges without manual cleanup.


## Review continuation after execution 5296ef5c7c594e3da0f11c38295f173a

The implementation has the right architectural direction and should be continued,
not redesigned.

Retain:

- explicit CURRENT / RECONCILABLE / AMBIGUOUS_FOREIGN classification;
- separation of stable ownership identity from mutable deployment representation;
- bidirectional desired-render reconciliation rather than version migration tables;
- repository/env binding metadata in newly rendered managed units;
- atomic unit rewrite plus daemon-reload;
- missing host-installation adoption only after a successful systemd start.

However checkpoint `51b7bb03818f6766849fc0b68eabf8e37eed668d` is not yet
acceptable.

### 1. Exact-head CI is red

The full suite completed with:

```text
5 failed, 991 passed, 1 skipped
```

The failures are:

- `test_foreign_unit_is_ambiguous_and_untouched`: only the expected error-text
  assertion is stale (`unmanaged` versus `unexpected identity`);
- `test_two_project_units_and_operations_are_independent` and
  `test_same_state_managed_candidate_is_reused`: old tests still assume that an
  already managed unit may silently change its env-file binding. Under TASK-021,
  env/repository binding is ownership identity, so do not weaken the classifier to
  satisfy these old expectations. Update the tests to distinguish legitimate stale
  deployment representation from ownership-binding changes and prove the latter
  fail closed;
- `test_systemd_default_start_never_uses_internal_background`: update the test
  double for the new `_host_installation(required=False)` call;
- `test_real_service_foreign_local_control_descendant_stays_blocked`: investigate
  and preserve its existing workflow invariant. Do not dismiss it until a rerun
  proves it nondeterministic or the deterministic cause is fixed.

Rerun the exact-head full suite and Ruff after these corrections.

### 2. Exercise reconciliation through start/readiness, not only unit rewrite

The new A -> B -> A regressions currently call `install()` and count
`daemon-reload`, but TASK-021 requires convergence through the service lifecycle.

Add focused proof that an owned unit rendered with launcher A is reconciled by B,
then started/restarted and reaches readiness using the same authoritative unit name;
repeat B -> A through the same mechanism. Reuse existing lifecycle seams rather than
inventing a second service model.

### 3. Complete the ownership fail-closed matrix

Add explicit TASK-021-level regressions for:

- wrong state key;
- wrong repository binding;
- wrong env-file binding;
- conflicting persisted systemd supervision authority.

Each must classify as AMBIGUOUS_FOREIGN (or equivalent), remain untouched, and never
be rewritten merely because the unit name/managed marker looks familiar.

Legacy units that predate the new repository/env comments may still be adopted only
when the surrounding durable authority is sufficient to prove the binding; do not
make absence of the new comments equivalent to arbitrary ownership.

### 4. Prove host-record adoption and recovery cases

The new missing-host-installation behavior in `_start_systemd()` has no dedicated
regression. Add proof that:

- an existing provably owned systemd authority plus a missing host-installation
  record is safely adopted after successful start;
- an existing conflicting non-systemd host policy is rejected without rewriting the
  unit;
- persisted authority plus a missing canonical unit recreates the same authoritative
  unit with the current desired representation;
- failure after unit rewrite/daemon-reload but before successful start leaves a
  recoverable state and a subsequent invocation converges without manual cleanup.

Where practical, assert that workflow/runtime/control state is not rewritten as a
side effect of host representation refresh.

### 5. Fix the reporting-path variable clobber

In `_start_systemd()`, `path` first holds the systemd unit path, but when the host
installation record is missing it is reassigned to `installation_path()`. The
final message therefore reports the installation-record path as the systemd unit.
Use distinct variables and keep the reported unit identity truthful.

No version-specific migration logic is needed. Keep the current desired-state model
and finish the missing proof surface around it.


## Review continuation after execution ab20c00c0abe4cf09cce7ef86dc3a327

Checkpoint `6edb49e2eeaffe24d8e419185dcbae8005b0a184` is materially
improved and the desired-state/classification design should be preserved.

Confirmed progress:

- repository/env/state-key mismatches are now explicitly AMBIGUOUS_FOREIGN and
  remain untouched;
- legacy units without repository/env binding comments are no longer silently
  trusted: they require an explicit durable-authority path via `allow_legacy`;
- persisted systemd authority is validated against ticket/project state before
  mutation;
- stale owned deployment representation causes restart rather than blind start;
- failed daemon-reload leaves the desired unit on disk and a subsequent install can
  converge;
- the unit-path reporting clobber is fixed.

The task is still not ready for acceptance for the following narrow reasons.

### 1. Exact-head CI is still red

Authoritative CI completed with:

```text
2 failed, 999 passed, 1 skipped
```

One failure is deterministic and directly caused by the new API surface:

```text
tests/test_launcher.py::
test_unmanaged_systemd_unit_is_normal_cli_error_for_stop_and_restart

UnmanagedSupervisor.inspect() got an unexpected keyword argument 'allow_legacy'
```

Update that test double (or the compatibility seam if there is a better supported
one) so the maintained suite matches the new supervisor contract.

The other failure is the already-known intermittent
`test_real_service_drop_retire_old_lineage_and_runs_fresh[T-2]` Git commit race.
Do not change TASK-021 code for it unless it reproduces deterministically. After the
deterministic failure is fixed, rerun exact-head CI; if the drop test fails again,
investigate it as a real suite reliability defect instead of assuming flakiness.

### 2. Add an explicit lifecycle proof for representation reconciliation

The current A -> B -> A tests still prove only `install()`, rendered content, and
`daemon-reload` counts. TASK-021 requires the reconciled unit to be operated through
the service lifecycle.

Add a focused test through the supported CLI/supervisor path that proves, for the
same authoritative unit name:

- launcher A unit exists and is owned;
- launcher B classifies it RECONCILABLE, rewrites it, then uses restart/start as
  appropriate and reaches the readiness seam;
- B -> A repeats through the same mechanism;
- an already CURRENT representation is idempotent and does not unnecessarily
  rewrite/restart.

This may use a deterministic fake systemctl/readiness seam; it need not require a
real host systemd manager.

### 3. Add explicit host-installation adoption/conflict regressions

The production code now contains the desired behavior, but the maintained tests do
not directly prove it.

Add focused coverage that:

- with missing host-installation metadata **and** an existing exact persisted
  systemd authority for the selected repository/env/state/unit, successful
  systemd start writes `HostInstallation("systemd")` only after start succeeds;
- if start/restart fails, the host-installation record is not created;
- an existing non-systemd host-installation record rejects the systemd path without
  unit rewrite;
- the final success message continues to report the canonical systemd unit path,
  not the installation-record path.

### 4. Strengthen the post-rewrite/start-failure recovery proof

The new daemon-reload retry test is useful, but the ticket specifically calls out a
failure after representation rewrite and before successful service start.

Add a CLI/supervisor-level regression where:

1. a stale owned unit is rewritten to the current desired representation;
2. restart/start fails before readiness;
3. the unit and durable authority remain inspectable and unambiguously owned;
4. the next invocation sees CURRENT/owned representation and successfully converges
   without manual file deletion or authority reset.

Do not add migration tables or weaken ownership checks. The remaining work is proof
and compatibility cleanup around the current implementation, not a redesign.


## Review acceptance after execution f314d8eec5b14bbbae24bc6eb0872359

Accepted checkpoint: `7883c27078d75d95d78156b7eb14ee820f11c779`.

Final review confirms:

- managed systemd ownership is classified separately from deployment representation
  using CURRENT / RECONCILABLE / AMBIGUOUS_FOREIGN semantics;
- launcher/path representation changes reconcile bidirectionally without
  version-specific migration tables;
- repository, env-file, state-key, managed-marker, and persisted-authority
  mismatches remain fail closed and are not silently rewritten;
- legacy units lacking newer binding comments require explicit durable systemd
  authority before reconciliation;
- A -> B -> A reconciliation keeps the same authoritative unit identity, rewrites
  the desired representation, uses restart/start lifecycle semantics, and exercises
  the readiness seam; CURRENT representation remains idempotent;
- persisted authority plus missing unit is deterministically reprovisioned using the
  authoritative unit name;
- missing host-installation metadata is adopted only after successful systemd
  startup, is not written after startup failure, and conflicting non-systemd host
  policy is rejected before mutation;
- failure after representation rewrite but before successful restart/start leaves
  durable authority intact and a subsequent invocation can converge without manual
  cleanup;
- the systemd success message reports the canonical unit path rather than the host
  installation record path.

Exact-head GitHub validation is fully green:
`1006 passed, 1 skipped`, coverage generated at 79%, and Ruff reports
`All checks passed!`.

The exact reviewed result is approved for Devlegate-owned accepted finalization.
