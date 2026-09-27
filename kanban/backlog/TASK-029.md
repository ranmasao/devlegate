---
"type": "devlegate.ticket"
"title": "Guide workers with focused validation profiles"
"depends_on": ["TASK-026"]
---

## Milestone

Next-iteration validation pipeline.

## Goal

Make focused testing an explicit project/ticket concept so implementation workers
know which small validation domains are relevant and stop treating the full suite as
a routine pre-handoff obligation.

Full CI after checkpoint publication remains the authoritative repository-wide proof.

## Context

Devlegate's own suite has already been optimized repeatedly and now contains
meaningfully different proof layers: component, engine semantic, IPC/owner, and
production topology. Running the entire suite inside every LLM edit loop is slow,
can exceed generic shell timeouts, and duplicates the full CI that runs after the
checkpoint is published.

The worker contract already says:

```text
Run focused validation as appropriate.
```

but does not tell the worker what "focused" means for the assigned task.

`TEST_BOUNDARIES.md` explains what each test layer proves. The project validation
catalog from TASK-026 should provide stable runnable profile IDs.

## Required behavior

- Extend project/ticket protocol so the architect can associate an assignment with
  zero or more named focused-validation profiles, or an equivalent deterministic
  mechanism with the same explicit semantics.
- The worker prompt lists the relevant profile IDs, descriptions, and intended proof
  scope without embedding project-specific test commands in Devlegate core logic.
- Architect guidance distinguishes:
  - worker-focused validation needed for implementation feedback;
  - QA-owned deterministic validators;
  - authoritative full CI/acceptance proof.
- Normal implementation workers are not instructed to run the full suite merely
  because acceptance criteria eventually require full CI green.
- Normal implementation workers are not instructed to run QA-owned lint/format
  validators as routine pre-handoff work.
- A ticket may explicitly require a broader/full profile when the task itself
  justifies it; this must be an explicit project/ticket decision rather than a
  default.
- Audit/analysis tasks may legitimately have no worker test profile.
- Profile names are validated against the exact project validation-policy generation
  used for the execution.
- Path-based policy may suggest useful profiles, but must not silently reinterpret an
  explicit ticket requirement.
- Update Devlegate's own project policy so common domains such as runtime recovery,
  workspace, worker/process, IPC, systemd/host, and packaging can be selected
  independently where the current suite supports that distinction.
- Keep `TEST_BOUNDARIES.md` as the explanatory proof map and cross-reference it
  rather than generating a second competing architectural taxonomy.

## Acceptance criteria

- A runtime-recovery ticket can tell a worker to run the runtime/workspace focused
  profiles without implying all production-topology tests.
- A packaging ticket can expose packaging-focused validation independently.
- A ticket with no focused profile does not cause an implicit full-suite run.
- The worker prompt clearly states that full CI occurs outside the worker loop and
  remains authoritative for repository-wide acceptance.
- Invalid profile IDs fail before worker launch.
- QA-owned validators are visibly distinct from agent-invocable test profiles.
- Architect/reviewer/worker documentation uses the same terminology.
- Full tests and lint remain green.

## Required regressions

- Ticket with one valid focused profile -> worker prompt contains that profile and
  its description.
- Ticket with several profiles preserves deterministic order and identity.
- Unknown profile -> admission/configuration failure before worker launch.
- No profile -> no implicit `pytest`/full-suite instruction.
- Explicit full profile -> worker receives it exactly when requested.
- Changing the validation-policy generation cannot silently change profile meaning
  for an already-admitted execution.
