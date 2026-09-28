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
