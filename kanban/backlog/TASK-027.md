---
"type": "devlegate.ticket"
"title": "Run deterministic QA validators with durable evidence"
"depends_on": ["TASK-025", "TASK-026"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

Execute applicable deterministic project validators while a ticket is parked in QA,
record exact evidence, and move the ticket automatically according to the validated
outcome.

The first Devlegate dogfooding validator should be lint. The mechanism must remain
generic and project-defined.

## Context

The QA stage separates deterministic machine verification from human review.
Validation policy supplies the project-specific validator definitions.

The runtime must distinguish three materially different outcomes:

```text
PASS / NOT_APPLICABLE -> QA -> review
FAIL                  -> QA -> todo
ERROR / INDETERMINATE -> remain QA
```

A validator reporting a lint error proves something about the candidate. A missing
tool, crashed runner, lost host, or unreadable result does not prove the candidate
bad and must not be converted into a rework decision.

Audit/analysis tasks with no code/test delta should cross QA without a visible wait
when no configured validator applies.

## Required behavior

- After a valid worker handoff reaches QA, resolve the exact checkpoint delta and
  the exact validation-policy generation.
- Freeze the applicable QA validator plan before executing effects.
- Run validators outside the LLM worker process; the worker is not responsible for
  invoking QA-owned deterministic checks.
- Bind every validator run to exact ticket ID, execution ID, checkpoint/product
  identity, validator ID, and validation-policy generation.
- Record a compact durable validator result plus bounded diagnostic evidence
  sufficient to explain a failure and reproduce the QA disposition. Large raw
  operational logs may remain local, but control history must not depend on an
  ephemeral log to know why QA passed or failed.
- Support explicit states equivalent to PASS, FAIL, ERROR/INDETERMINATE, and
  NOT_APPLICABLE/SKIP.
- All applicable validators passing -> move QA -> review automatically.
- No applicable validators -> move QA -> review automatically, preferably in the
  same lifecycle iteration so no human-visible parking occurs.
- Any deterministic validator FAIL -> move QA -> todo automatically.
- Validator process launch failure, missing local toolchain, timeout without a
  defined validation result, service interruption, or evidence corruption -> keep
  the ticket in QA and expose a retryable diagnostic.
- Retrying QA for the same exact checkpoint is idempotent and does not fabricate or
  overwrite conflicting evidence.
- QA validator execution must not mutate the canonical product branch or workflow
  outside the authorized QA/evidence transition.
- Add Devlegate's own lint validator using the project validation policy; do not
  hard-code Ruff as a generic runtime concept.
- Do not make the full test suite an automatic QA validator in this ticket.
- Status/plan exposes QA progress sufficiently to distinguish pending/running,
  failed, indeterminate, and complete validation.
- Crash/restart and control-branch advancement during QA remain fail closed and
  recover from durable evidence rather than rerunning blindly.

## Acceptance criteria

- A Devlegate code/test checkpoint with lint-clean changes moves QA -> review without
  worker or reviewer intervention.
- A checkpoint with a deterministic lint violation records exact diagnostics and
  moves QA -> todo.
- A missing/broken lint executable does not send the ticket to todo; it remains in
  QA with an infrastructure diagnostic.
- An audit/analysis zero-product-delta handoff has no applicable code/test validator
  and crosses QA -> review without a user-visible stop.
- QA evidence identifies the exact candidate and policy generation.
- Re-running/recovering the same validation cannot attach results from a different
  checkpoint or duplicate a workflow transition.
- Runtime remains stdlib-only.
- Full tests and lint for the implementation remain green.

## Required regressions

- Applicable lint PASS -> durable PASS evidence -> QA -> review.
- Applicable lint FAIL -> bounded durable diagnostics -> QA -> todo.
- Validator executable missing -> ERROR/INDETERMINATE -> remains QA.
- Validator timeout/process crash -> remains QA unless the project validator
  explicitly defines that condition as a deterministic FAIL.
- No matching delta/domain -> NOT_APPLICABLE -> immediate QA -> review.
- Zero product delta audit -> no validator launch.
- Crash after saving validator evidence but before workflow movement replays exactly
  once.
- Crash after workflow movement does not rerun or duplicate the validator result.
- Checkpoint or validation-policy drift between planning and effect refuses stale
  disposition.
