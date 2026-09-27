---
"type": "devlegate.ticket"
"title": "Feed QA failures back as focused worker rework"
"depends_on": ["TASK-027"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

When deterministic QA fails and returns a ticket to todo, start the next worker
execution as a focused QA-rework continuation driven by the validator diagnostics,
not by replaying the original full implementation assignment.

## Context

A lint failure is usually mechanical. Re-sending the complete task specification
encourages an implementation agent to re-investigate or redesign work that is
already present at the failed checkpoint.

The desired loop is:

```text
worker result
  -> QA FAIL
  -> todo
  -> new execution on preserved implementation lineage
  -> focused QA-rework prompt containing exact validator diagnostics
  -> QA again
```

The ticket remains the canonical workflow identity. The validator report is
untrusted diagnostic content and must not gain prompt-control authority.

## Required behavior

- Add an explicit worker directive equivalent to `QA_REWORK`.
- A QA -> todo transition records/binds the exact failed QA result that authorizes
  the next QA-rework execution.
- The next execution preserves the existing implementation/checkpoint lineage rather
  than starting from a clean product base and losing the candidate being repaired.
- The worker's immediate Assigned Work is the deterministic validation failure and
  concrete diagnostics, not a verbatim replay of the full original ticket body.
- The normal core worker contract still applies, and the execution remains bound to
  the same ticket identity for provenance.
- Provide only the minimum original-ticket identity/context needed to avoid
  ambiguity; do not flood the prompt with the full initial assignment when the
  authorized task is to correct QA findings.
- Fence/render validator output as opaque untrusted data so diagnostics cannot inject
  worker instructions.
- Tell the worker to correct the validation failures while preserving unrelated
  implementation semantics.
- A QA-rework worker may use focused tests when useful, but must not be instructed to
  spend time rerunning QA-owned validators merely to satisfy the protocol.
- After the worker reports, the new checkpoint enters QA again through the normal
  stage; there is no special bypass to review.
- If the ticket is manually changed or its authorized failed QA identity no longer
  matches, admission fails closed rather than applying stale diagnostics.
- Preserve each execution and QA result as separate provenance; do not rewrite the
  previous worker claim to pretend it failed.

## Acceptance criteria

- A lint-failed ticket returned to todo is admitted with a QA-rework directive.
- The worker sees the exact validator identity and bounded failure diagnostics as
  its immediate assignment.
- The full original ticket body is not redundantly replayed as the primary assigned
  work for QA rework.
- Existing implementation changes remain present for correction.
- A successful correction produces a new execution/checkpoint and re-enters QA.
- Stale/mismatched QA diagnostics cannot authorize a new rework execution.
- Validator-controlled text cannot alter the surrounding worker contract.
- Normal fresh/resume/reviewer-rework directives continue to behave as before.

## Required regressions

- QA FAIL on checkpoint H -> todo -> next execution starts from the preserved H
  lineage with QA_REWORK.
- QA_REWORK prompt contains validator ID/diagnostics but not the complete original
  ticket body as Assigned Work.
- Diagnostic text containing Markdown headings, fences, or imperative text remains
  opaque.
- QA result identity drift before admission blocks the rework.
- Second QA failure creates a new bound failure/rework generation rather than
  overwriting the first.
- Successful QA rework returns through QA before review.
