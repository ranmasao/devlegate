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
- while the build is running, do not print ordinary build logs, detailed reports,
  subprocess transcripts, validation dumps, or per-stage diagnostics below the
  progress line;
- finish successful execution by terminating the transient progress line and printing
  one concise final result that identifies the produced package/artifact path and the
  retained detailed log/report path;
- on failure, terminate/clear the transient progress line and print one concise final
  result that identifies the failed semantic stage and the retained detailed
  log/report path;
- the normal interactive terminal is a progress/result surface, not a log stream.

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

## Detailed build evidence versus interactive terminal output

Interactive terminal output and detailed build evidence are separate products of the
same packaging run.

For a normal TTY invocation, the visible contract is intentionally minimal:

```text
<one continuously rewritten semantic progress line>

success:
package ready: <artifact-path>
details: <log/report-path>

or failure:
build failed at <semantic-stage>
details: <log/report-path>
```

There should be no ordinary multi-line build log in the interactive terminal, even
on failure. The user can open the retained detailed file when investigation is
needed.

Required behavior:

- capture complete useful build/validation diagnostics outside the interactive
  terminal, including subprocess stdout/stderr and structured stage reports where
  applicable;
- retain a deterministic run-level detailed log/report file for every packaging
  invocation, successful or failed; it may reference additional stage-specific
  evidence files rather than duplicating them;
- keep the detailed evidence at a stable reported location associated with the
  packaging output/work area;
- on success, print only the concise artifact result and detailed-log/report path
  after the progress UI finishes;
- on failure, print only the failed semantic stage plus the detailed-log/report path
  after the progress UI finishes; do not replay or dump the detailed transcript to
  the TTY;
- CI/non-TTY mode may print detailed diagnostics directly because its output is
  itself a durable log surface, but the same retained report/log file should still
  be produced when practical;
- suppressing detail in TTY mode must never reduce retained evidence or weaken
  packaging validation/provenance;
- subprocess output and detailed reports are diagnostic evidence, never progress
  signals, and must not affect semantic progress accounting.

This separation should apply consistently across wheel, sdist, standalone, Debian,
full-source, and aggregate packaging targets rather than being implemented as
target-specific terminal exceptions.

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
- Successful completion leaves a clean 100% progress state followed by one concise
  final result containing the artifact/package path and detailed log/report path.
- A skipped semantic step advances progress truthfully.
- A failed step leaves the completed counter unchanged for that step and produces
  one concise final result containing the failed semantic stage and detailed
  log/report path.
- Redirected output and CI continue to receive deterministic line-oriented progress
  with no terminal-control garbage.
- Interactive builds, both successful and failed, emit no ordinary detailed build
  log/transcript outside the progress UI and final result.
- Every packaging invocation retains a deterministic run-level detailed log/report
  containing or referencing the complete useful diagnostics.
- CI/non-TTY execution may expose detailed diagnostics directly without changing
  semantic progress accounting, while retained evidence remains available on disk.
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
- TTY success path emits only progress, then a concise `package ready: <path>` /
  `details: <path>` result; no detailed transcript is printed.
- TTY failure path emits only progress, then a concise
  `build failed at <stage>` / `details: <path>` result; no detailed transcript
  is printed.
- A deterministic retained run-level report/log exists for both success and failure
  and contains or references the complete useful diagnostics.
- CI/non-TTY mode can surface detailed diagnostics without ANSI/transient terminal
  behavior and without changing semantic progress accounting.
- Reporter selection is based on the supplied output stream's TTY capability and is
  testable without requiring a real terminal.
- Existing frozen-plan validation still rejects unknown/out-of-order progress
  events exactly as before.
