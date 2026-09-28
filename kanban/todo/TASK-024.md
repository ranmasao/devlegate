---
"type": "devlegate.ticket"
"title": "Render semantic package progress as a compact TTY progress bar"
---

## Milestone

Devlegate 0.5.5 packaging UX polish.

## Goal

Keep the semantic progress model introduced by TASK-007, but present it as a
compact interactive progress bar instead of a debug-like stream when the build is
running in a terminal.

Interactive users should normally see one continuously updated line containing
truthful overall progress and the current semantic operation. Non-interactive
consumers such as CI, redirected logs, and pipes must retain deterministic
line-oriented progress output.

## Context

TASK-007 deliberately introduced a deterministic semantic build plan and
component-owned progress events such as `start`, `complete`, `skip`, and
`fail`. That solved the important observability problem: long standalone and
Debian builds no longer appear stalled inside a handful of coarse outer stages.

The current presentation, however, prints every semantic transition as a new line,
for example:

```text
[0/31] START standalone: build wheel
[1/31] DONE standalone: build wheel
[1/31] START standalone: assemble scie
...
```

This is useful as structured/debug-style output, but too noisy for the normal
interactive build experience.

The semantic model itself is valuable and must remain unchanged. This ticket is a
presentation-layer refinement, not a redesign of build planning.

## Required behavior

### Interactive terminal presentation

When progress output is attached to a TTY:

- render one progress line and rewrite it in place as semantic events arrive;
- show completed semantic steps versus the frozen total;
- show a simple visual bar derived only from exact semantic completion, not elapsed
  time or guessed duration;
- show the current target/operation, for example:

```text
[██████████░░░░░░░░░░] 14/27  standalone: validate archive
```

- update the current-operation label on `start`;
- advance completed progress only on `complete` or `skip`;
- never simulate fractional progress inside a semantic step;
- keep the line bounded to the current terminal width where practical, truncating
  the human label rather than corrupting the progress counter/bar;
- finish successful execution with a clean final line/newline so the shell prompt
  never shares the progress line;
- on failure, terminate/clear the transient progress line cleanly before emitting
  the durable failure/error text.

A spinner, animation loop, ETA, elapsed-time interpolation, or dependency on an
external terminal-progress library is not required. Prefer a small stdlib-only
renderer.

### Non-TTY / machine-readable presentation

When stdout is not a TTY, including CI and redirected output:

- retain deterministic line-oriented progress;
- do not emit carriage-return-only transient updates or ANSI cursor control that
  would make logs unreadable;
- preserve enough semantic information to understand start/completion/skip/failure
  ordering;
- keep the output stable enough for repository tests and operational logs.

The current line-oriented representation is an acceptable baseline for this mode.

#

## Detailed build output versus interactive progress

Some package stages currently print their full detailed reports or subprocess output
directly into the visible terminal. That defeats the compact TTY presentation even
when semantic progress itself is rendered correctly.

Treat verbose build evidence as a separate output channel from interactive progress:

- in an interactive TTY, normal successful execution should show the compact semantic
  progress UI and concise final artifact/result information, not multi-line detailed
  stage reports;
- capture the complete detailed report/log for each relevant build or validation
  stage in a durable file instead of discarding it;
- place that detailed evidence alongside the corresponding build report/artifact
  output, or in another deterministic reported path owned by the packaging run;
- when a stage fails, emit a concise human-readable failure summary and the path to
  the detailed report/log; do not dump an unbounded successful-stage transcript into
  the terminal merely because later work failed;
- CI/non-TTY mode may emit the detailed report content directly when useful for
  diagnostics, while still keeping semantic progress deterministic and line-oriented;
- do not make correctness or provenance depend on terminal verbosity: suppressing
  detail in TTY mode must not reduce the evidence retained on disk;
- subprocess stdout/stderr and structured build reports are diagnostic evidence, not
  progress signals. They must not advance or synthesize semantic progress.

The goal is that a local successful package build remains visually compact even when
individual builders produce rich validation reports, while the complete evidence is
still available for inspection and CI.

## Separation of model and presentation

- `ComponentPlan`, frozen semantic leaf ordering, and progress-event semantics
  remain the source of truth.
- The reporter must not infer progress from time, subprocess output volume, file
  size, or target-specific heuristics.
- TTY selection belongs in the presentation/reporting boundary rather than inside
  individual package builders.
- Component builders continue to emit semantic events without knowing whether a
  progress bar or line-oriented logger is consuming them.
- `skip` consumes its exact semantic slot and advances the total.
- `fail` does not falsely mark the failed step complete.

## Output robustness

- Prefer terminal capability detection based on the actual output stream
  (`isatty()`) rather than environment-name heuristics.
- Handle unavailable terminal-size information with a deterministic fallback.
- Do not require color; the progress bar must remain legible with plain terminal
  characters.
- Avoid corrupting output if other final diagnostics are printed after the progress
  reporter finishes.
- Repeated rendering of the same state must be harmless.

## Acceptance criteria

- A normal interactive `tools/build_distribution.py` invocation shows one
  rewriting progress line rather than one permanent line for every semantic event.
- The line contains an exact completed/total counter and the current semantic
  target/operation.
- The visual bar corresponds exactly to completed semantic leaves.
- Successful completion leaves one clean final newline and a 100% state.
- A skipped semantic step advances progress truthfully.
- A failed step leaves the completed counter unchanged for that step and produces
  readable final failure output.
- Redirected output and CI continue to receive deterministic line-oriented progress
  with no terminal-control garbage.
- Successful interactive builds do not dump verbose per-stage reports/subprocess
  transcripts into the terminal; complete detailed evidence is retained in a
  deterministic report/log file and its location is available to the user.
- CI/non-TTY execution may expose the detailed report content directly for
  diagnostics without changing semantic progress accounting.
- No elapsed-time fake progress, ETA estimation, background animation thread, or
  external runtime dependency is introduced.
- Full tests, lint, packaging/distribution tests, and existing semantic progress
  ordering tests remain green.

## Required regressions

- TTY reporter: `start -> complete` rewrites one line and advances the exact
  counter/bar.
- TTY reporter: multiple steps do not accumulate one permanent line per transition.
- TTY reporter: `skip` advances progress and exposes the skipped/current state
  coherently.
- TTY reporter: `fail` cleans up the transient line and does not count the failed
  step as completed.
- TTY reporter: final successful state is 100% and newline-terminated.
- Narrow terminal width does not break counters or emit malformed control
  sequences; the operation label may be truncated.
- Non-TTY reporter preserves line-oriented START/DONE/SKIP/FAILED semantics.
- TTY mode suppresses verbose successful-stage report/transcript output while
  retaining the complete content in a deterministic on-disk report/log.
- Failure output remains concise and points to retained detailed evidence rather
  than flooding the interactive terminal with an unbounded transcript.
- CI/non-TTY mode can surface retained detailed diagnostics without ANSI/transient
  terminal behavior.
- Reporter selection is based on the supplied output stream's TTY capability and is
  testable without requiring a real terminal.
- Existing frozen-plan validation still rejects unknown/out-of-order progress
  events exactly as before.
