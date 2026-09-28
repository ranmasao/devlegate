---
"type": "devlegate.ticket"
"title": "Render active execution state as a status table"
"depends_on": ["TASK-022"]
---

## Milestone

Next iteration after Devlegate 0.5.5.

## Goal

Replace the singular free-form `Current:` execution block in human-readable
`devlegate status` output with a compact table/grid representation that remains
natural when Devlegate later supports multiple simultaneous executions.

This is a presentation change, not a parallel-execution implementation.

## Context

The current table-mode status presentation renders repository, eligible, blocked,
review, and accepted state through tables/grids, but renders the currently active
execution as a special singular text block:

```text
Current: TASK-031  •  running
  ...
  Execution: 828f0f5b3b4e
```

That shape is visually asymmetric and would need redesign once more than one worker
can be active.

## Required behavior

### Active executions table

Render non-idle execution state through a named grid/table, preferably
`Active executions`, with one row for the current single-execution model.

Use columns that preserve the useful current information and scale to multiple rows.
At minimum represent:

- ticket ID;
- user-facing execution state;
- execution/stage identity where available;
- shortened execution ID;
- ticket title.

A suitable conceptual shape is:

```text
Active executions
Ticket    State      Stage           Execution     Title
TASK-031  running    worker-running  828f0f5b3b4e  Complete pre-worker ...
```

The exact labels may follow the existing output renderer conventions, but avoid a
layout that assumes permanently that there is exactly one execution.

### Recovery and unverified state

Do not lose the diagnostics currently exposed for `unverified` and
`recovery-required` states.

Represent their state/phase/stage coherently in the row and, only where the tabular
columns are insufficient, retain a concise associated diagnostic below the table.
Do not revert to a separate singular `Current:` presentation.

### Scope

Keep this task presentation-only:

- do not implement parallel scheduling;
- do not change worker ownership semantics;
- do not pluralize or redesign the machine-readable status schema merely to make the
  text output look future-proof;
- preserve existing JSON/YAML field meanings and status exit-code semantics.

A later parallel-execution task may evolve the machine schema deliberately.

## Acceptance criteria

- Human-readable `status` no longer uses the singular `Current:` block.
- One active execution renders as one table row.
- The row preserves ticket, state, execution identity, title, and meaningful
  phase/stage information.
- Recovery-required and unverified cases remain diagnosable.
- Idle status does not render a misleading active execution row.
- Existing repository/workflow/review/accepted tables remain unchanged except for
  necessary spacing/layout integration.
- JSON/YAML compatibility is preserved.
- Tests, Ruff, and coverage remain green.

## Required regressions

- Running execution renders exactly one active-execution row.
- Preparing/finalizing states render coherently.
- Recovery-required state retains phase/stage/recovery evidence.
- Unverified state remains visibly unverified.
- Idle status has no active execution row.
- Long ticket titles remain readable under existing renderer width rules.
- JSON/YAML status snapshots are unchanged by the text-only presentation refactor.
- Existing status/plan agreement tests remain green.
