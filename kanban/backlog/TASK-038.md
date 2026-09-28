---
"type": "devlegate.ticket"
"title": "Define project-owned workflow gate policy"
"depends_on": ["TASK-036"]
---

## Milestone

Devlegate 0.5.6 configurable workflow model.

## Goal

Separate Devlegate's workflow capabilities from the gate sequence chosen by an
individual project.

A project must be able to choose whether worker results pass through deterministic
QA, human review, both, or an explicitly authorized machine-only path without
hard-coding Devlegate's own dogfooding workflow into the runtime.

## Context

The current canonical workflow is effectively fixed in runtime semantics:

```text
backlog -> todo -> worker -> review -> accepted -> done
```

The planned QA work originally assumed a new universal shape:

```text
todo -> worker -> qa -> review -> accepted -> done
```

That is appropriate for Devlegate itself, but it is not a universal project
requirement.

Different projects may legitimately want, for example:

```text
worker -> review -> accepted
worker -> qa -> review -> accepted
worker -> qa -> accepted
```

The runtime therefore needs a project-owned gate policy before QA becomes a
first-class capability.

This is a focused tracked-policy slice and must compose with TASK-014's later,
broader tracked-policy/local-configuration split without waiting for that post-0.6
migration.

## Required behavior

### Tracked workflow policy

- Define a versioned, project-owned declarative workflow policy under the tracked
  `.devlegate/` project-policy boundary or an equivalent tracked location.
- The policy describes workflow/gate semantics shared by all clones and roles.
- Do not put machine-local paths, models, secrets, executable locations, polling
  intervals, or other host choices into this policy.
- Runtime remains stdlib-only.
- Invalid, conflicting, unsupported, or ambiguous policy fails closed before worker
  admission.

### Supported gate model

For the first version, model an ordered active-work route over explicit supported
gate capabilities rather than implementing an arbitrary BPMN/workflow language.

At minimum support the canonical semantic states/capabilities needed for:

- worker execution / handoff;
- deterministic validation gate (`qa`);
- human semantic review gate (`review`);
- accepted integration authorization;
- done/completed work;
- rework back to `todo`.

The policy must be able to express at least these project profiles:

1. human review only:
   `worker -> review -> accepted -> done`;
2. deterministic QA then human review:
   `worker -> qa -> review -> accepted -> done`;
3. explicitly authorized machine gate:
   `worker -> qa -> accepted -> done`.

The third form is an explicit policy decision. Devlegate must never silently infer
that absence of human review means machine acceptance is authorized.

Do not require a fully general arbitrary graph in this ticket. Restrict transitions
to supported lifecycle events and reject cycles, unreachable active states,
undefined gates, or transitions that bypass an authorization rule not explicitly
permitted by policy.

Dependency satisfaction remains tied only to `done`.

### Transition semantics

- Worker handoff target comes from the exact workflow-policy generation, not a
  global hard-coded destination.
- QA PASS / NOT_APPLICABLE follows the policy's configured success target.
- QA deterministic FAIL returns to `todo`.
- QA ERROR / INDETERMINATE remains at the QA gate.
- Human review acceptance follows the configured accepted/integration route.
- Human review rework returns to `todo`.
- Accepted integration remains deterministic and results in `done`.
- A policy generation/fingerprint is bound to admitted execution/handoff evidence so
  editing workflow policy cannot silently reroute an already admitted worker result.
- Policy changes affect later admissions only after the new generation is observed
  and validated.

### Storage and presentation

- Only gate paths actually enabled by the selected project policy are required.
  A project that does not use QA must not need an empty `kanban/qa` directory
  merely because Devlegate supports QA.
- Ticket uniqueness remains global across every enabled canonical state.
- Status and plan identify which gate a ticket is waiting at and why.
- Architect/reviewer protocol rendering reflects the configured route rather than
  assuming QA or review exists universally.
- Project bootstrap/rendering creates or updates the tracked workflow policy
  deterministically.

### Backward compatibility and Devlegate dogfooding

- A project with no new workflow policy must retain the current pre-QA behavior
  (`worker -> review -> accepted -> done`) during migration; adding Devlegate
  support for QA must not silently insert a new gate into existing projects.
- Devlegate's own repository policy should explicitly select:
  `worker -> qa -> review -> accepted -> done`.
- TASK-025 introduces/implements the QA state/capability under this policy rather
  than making QA globally mandatory.
- TASK-026 defines validators/profiles consumed by validation gates; workflow policy
  chooses where such a gate sits, while validation policy defines what it runs.

## Acceptance criteria

- Two projects can use different gate routes with the same Devlegate binary.
- Existing projects without explicit workflow policy preserve direct worker ->
  review behavior.
- Devlegate's dogfood policy explicitly routes worker results through QA then human
  review.
- A QA-only project can explicitly authorize QA success to reach accepted
  integration without a human review gate.
- A malformed or unsupported route blocks admission before a worker can create a
  handoff under ambiguous semantics.
- Exact workflow-policy generation is preserved in execution/handoff provenance.
- Mid-execution policy changes cannot redirect an already admitted handoff.
- Disabled gate directories are not required or synthesized as fake workflow state.
- Runtime remains stdlib-only and does not execute arbitrary commands from workflow
  topology policy.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Legacy/no-policy project -> worker handoff goes directly to review.
- Explicit QA+review policy -> worker handoff goes to QA; QA pass goes to review.
- Explicit QA-only machine policy -> QA pass goes to accepted.
- QA fail -> todo regardless of configured success target.
- QA indeterminate -> remains QA.
- Review rework -> todo.
- Policy edited after execution admission -> original bound generation governs that
  handoff or the operation fails closed; it is never silently rerouted.
- Undefined gate, cycle, unsupported target, or implicit machine acceptance ->
  configuration failure before execution.
