---
"type": "devlegate.ticket"
"title": "Establish layered human-readable project configuration"
---

## Milestone

Project configuration foundation.

## Goal

Replace the current project-local `.env` catch-all with a small, human-readable
configuration model that cleanly separates tracked project semantics, machine-local
runtime choices, and environment/secret overrides.

This ticket establishes ownership, layout, loading, validation, and provenance for
configuration. It does not yet implement declarative workflows, testing policy,
versioning, release topology, or rich project-inspection UX.

## Context

The current `.env` mixes several different kinds of state:

- project-owned semantics such as canonical branch/workflow information;
- machine-local choices such as worker adapter/model, remote alias, polling, and
  state directory;
- values that may be secrets or temporary environment overrides.

The project now expects more project-owned documents under `.devlegate/`, including
future workflow definitions and optional capability policies. The configuration
foundation must therefore support composition without turning into one large
catch-all YAML file.

Devlegate has no external client-compatibility obligation yet. Do not build a
migration/compatibility framework for pre-1.0 development formats. If the current
dogfood repository needs a one-shot local fix, perform it as an untracked
development action rather than preserving obsolete configuration semantics in the
runtime.

Schema identifiers should remain on their initial version wherever possible during
pre-1.0 development; changing an internal schema during this phase does not by
itself justify maintaining multiple compatible generations.

## Required behavior

### Tracked project configuration

- Establish `.devlegate/` as the canonical tracked project-configuration root.
- Define a small project-composition document, tentatively
  `.devlegate/project.yaml`, for project identity/composition and references to
  project-owned policy documents.
- Keep policy documents separated by purpose rather than accumulating unrelated
  settings in one file.
- Design references so future documents such as workflow, testing, versioning, and
  release policy can be added independently and omitted when unused.
- Absence of an optional policy document must be a valid state rather than requiring
  empty placeholder files.
- The tracked format must remain understandable and reasonably editable by a human.

### Machine-local configuration

- Define a machine-local configuration/profile location outside tracked project
  policy for installation/clone-specific settings.
- Classify worker adapter/model selection, local Git remote alias, polling/runtime
  limits, state location, local executable/toolchain bindings, and similar
  operational settings explicitly rather than inheriting ownership from the old
  `.env`.
- Two clones of the same repository must be able to share tracked semantics while
  using different local operational settings.

### Environment and secrets

- Environment variables remain an explicit override/secret-injection boundary, not
  the canonical store for project semantics.
- Secret values must not be materialized into tracked project configuration.
- Define deterministic precedence between tracked project policy, machine-local
  settings, and explicit environment overrides.
- Reject ambiguous/conflicting ownership instead of silently choosing whichever
  source happens to load last.

### Loading and provenance

- All roles and runtime paths for one execution must resolve the same tracked
  project-policy generation.
- The resolved configuration must retain enough source/provenance information for a
  later project-inspection UX to report which files/sources contributed each class
  of settings.
- Configuration generation/fingerprint must be deterministic and bindable to
  execution provenance where project semantics affect behavior.
- Unknown or malformed tracked policy references fail closed.
- Core must not interpret arbitrary future capability documents merely because a
  file exists under `.devlegate/`; project composition must declare what is in
  use.

### Pre-1.0 replacement policy

- Remove obsolete `.env` semantics from the product path once the new model is
  adopted; do not retain a permanent dual-source compatibility mode.
- No compatibility matrix, migration engine, deprecation period, or schema-upgrade
  framework is required for the current pre-client/pre-1.0 phase.
- Any one-shot conversion needed for Devlegate's own repository may be done by an
  untracked helper or manual development step and must not become runtime product
  code solely for historical compatibility.

## Scope boundaries

- Do not implement the declarative workflow state machine in this ticket.
- Do not define testing domains/profiles.
- Do not define VersionSchema or release topology.
- Do not implement a plugin/extension marketplace or external source resolver.
- Do not implement the rich `devlegate inspect` UX; preserve the provenance/data
  required for that later task.
- Do not add a generic schema-migration framework.

## Acceptance criteria

- A fresh clone obtains all tracked project semantics from `.devlegate/` without
  copying another machine's worker/model/remote/state settings.
- The project-composition document remains small and references purpose-specific
  policy documents rather than embedding their full schemas.
- Optional policy families can be absent without errors or generated placeholders.
- Two clones can use different local adapters/models/remotes/state paths while
  resolving the same tracked project policy.
- Environment/secrets override only through documented precedence and cannot become
  accidental tracked policy.
- Runtime, architect, reviewer, and future specialized roles resolve the same exact
  tracked policy generation for an execution.
- Resolved configuration retains source/provenance metadata sufficient for a later
  inspection command to explain active documents and setting ownership.
- The old `.env` configuration is no longer an ambiguous competing authority after
  adoption.
- Runtime remains stdlib-only.

## Required regressions

- Tracked project semantics resolve identically in two clones with different local
  runtime profiles.
- Local model/adapter/remote/state settings do not alter the tracked project-policy
  generation.
- Secret/environment overrides do not create or modify tracked policy files.
- Missing optional capability policy is accepted.
- Declared but missing/malformed policy reference fails before execution admission.
- Conflicting ownership for one semantic field fails closed.
- An admitted execution remains bound to its resolved project-policy generation even
  if tracked configuration changes afterward.
