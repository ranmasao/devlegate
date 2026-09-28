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


## Review continuation after execution c51a83951e7a48d2b84ef27cb256e167

Checkpoint `9deab8c2e741217230a13363afcbd27516068a5b` has the right
presentation direction and should be continued rather than redesigned.

Confirmed good work:

- semantic completion accounting still advances only on exact complete/skip events;
- TTY selection is based on the actual supplied stream;
- interactive progress uses one rewritten line and preserves deterministic non-TTY
  START/DONE/SKIP/FAILED output;
- interactive component stdout/stderr is redirected away from the terminal;
- concise TTY success/failure result text points to a retained build log;
- no spinner, ETA, time interpolation, background animation, or external progress
  dependency was introduced.

The checkpoint is not ready for acceptance because the retained-evidence contract
and required end-to-end regressions are incomplete.

### 1. The retained log can omit the actual failure reason

The main packaging failure handler currently does:

```python
except Exception:
    ...
    evidence.write(f"FAILED at {stage}")
```

For failures raised directly by in-process Python validation/build functions, there
may be no subprocess stdout/stderr to have been captured by `run()`. In that case
the detailed file named by:

```text
build failed at <stage>
details: <path>
```

can contain the stage name but not the exception type/message that explains why the
stage failed.

Record the actual caught exception in retained evidence, with enough context to make
the file useful for diagnosis. Do not re-dump it into the interactive terminal.

Add a focused TTY package failure regression where an in-process stage raises a
distinct diagnostic string and prove:

- the terminal contains only concise progress/failure/result text;
- the detailed log contains the exact diagnostic reason;
- the failed semantic leaf is not counted complete.

### 2. Structured standalone build evidence is still temporary

`build_standalone()` creates:

```text
<temporary-workspace>/standalone-build/standalone-build.json
```

and returns it as `standalone["report"]`, but `selected_final_files()` publishes
only the standalone archive and checksum. The run-level evidence log does not copy,
embed, or durably reference the structured report.

On normal successful cleanup, the temporary workspace is deleted, so the structured
build report disappears even though TASK-024 explicitly requires useful structured
stage reports to be retained or referenced from durable evidence.

Preserve this report for standalone/deb/all runs, either by:

- publishing/copying it to a deterministic durable path associated with the run; or
- embedding its complete useful content into the retained run-level report/log.

The same rule applies to any other stage-specific diagnostic artifact that would
otherwise be lost with the temporary workspace.

Add a regression proving that after successful workspace cleanup the reported
details path still contains or references the standalone structured report.

### 3. Retained evidence is incomplete in non-TTY mode

The new stdout/stderr redirection into `EvidenceLog` is enabled only when
`reporter.interactive` is true. In non-TTY mode, direct in-process diagnostic
output remains visible in CI/logs but is not necessarily duplicated into the
retained build log; only subprocesses that happen to use the shared `run()` helper
are copied there.

TASK-024 requires a retained run-level report/log for every packaging invocation,
while allowing CI/non-TTY to *also* expose detailed diagnostics directly.

Make retained evidence complete independently of presentation mode. A tee-like
boundary is appropriate if non-TTY should both print and retain diagnostics; do not
make diagnostic retention depend on whether stdout is a TTY.

### 4. The required package-level TTY success/failure regressions are missing

The added tests exercise `ProgressReporter` directly, but the new behavior that
matters most lives in `package()`: stdout/stderr capture, durable log creation,
artifact publication, concise final result, workspace cleanup, and exception
handling.

Add end-to-end/focused package-level regressions covering at least:

- TTY success: only transient progress plus
  `package ready: <path>` / `details: <path>`, with no detailed transcript;
- TTY failure: only transient progress plus
  `build failed at <stage>` / `details: <path>`, with diagnostic detail retained
  in the file;
- retained evidence exists on both success and failure after workspace cleanup;
- non-TTY preserves deterministic line-oriented progress and retained diagnostics.

### 5. Narrow-terminal behavior is not actually covered

TASK-024 explicitly requires a narrow-terminal regression. The new test fixes width
at 40 columns only.

`ProgressReporter._width()` currently forces a minimum of 20 columns:

```python
return max(20, shutil.get_terminal_size(...).columns)
```

so a real terminal narrower than 20 columns causes the renderer to emit more columns
than the reported terminal width. That contradicts the bounded-width requirement.

Add a genuinely narrow-width test and make the renderer degrade deterministically:
preserve an intact counter/minimal bar and truncate/drop only the human label rather
than pretending the terminal is wider than it is.

### 6. Preserve useful non-TTY final artifact reporting

Before this checkpoint, successful packaging printed the produced artifact paths and
digests. The new implementation prints `package ready: ...` only in TTY mode and
returns silently after line-oriented progress in non-TTY mode.

TASK-024 changes interactive presentation; it does not require removing useful
machine/log-facing final artifact information. Preserve a deterministic non-TTY
final result containing produced artifact paths (and existing digest/size information
where appropriate), without CR/ANSI transient behavior.

Add a regression so CI/redirected logs retain a stable final artifact summary.

Do not redesign the semantic plan or component event model. The current renderer and
presentation split are appropriate; finish the durable evidence/result boundary and
the required integration regressions, then rerun full exact-head tests, coverage,
and Ruff.


## Review continuation after execution d94c8e02f25142578dc8300545dcd5a1

Checkpoint `98f0bac08ee59f773f51974acd6e60e6b76b8226` closes the main
implementation findings from the prior review and should be continued without
redesign.

Confirmed implementation improvements:

- in-process package failures now retain exception type/message in the detailed log;
- non-TTY diagnostic output is tee'd into retained evidence rather than being
  presentation-only;
- standalone structured build reports are copied to a durable output path before
  temporary workspace cleanup and referenced from the run-level evidence;
- non-TTY successful runs again print deterministic artifact path/size/SHA-256
  summaries;
- narrow terminal rendering no longer forces a synthetic 20-column width and the new
  eight-column regression preserves a minimal bar plus exact counter;
- semantic plan/event accounting remains unchanged.

Two required end-to-end proof gaps remain.

### 1. Add package-level TTY success/failure contract regressions

The maintained tests still exercise the new TTY behavior primarily through
`ProgressReporter`. Existing `GRAPH.package(args)` tests cover old workspace
cleanup/keep-work behavior, but they do not exercise the new presentation/evidence
contract.

Add focused `package()` regressions with a supplied/faked TTY stdout proving:

- success emits transient progress and then only the concise
  `package ready: <path>` / `details: <path>` result, without ordinary builder
  transcript;
- failure from an in-process semantic stage emits only concise
  `build failed at <stage>` / `details: <path>` terminal text;
- the failure details file contains a distinctive exception diagnostic that is not
  dumped to the TTY;
- the failed semantic leaf is not counted complete;
- success and failure details paths still exist after normal temporary-workspace
  cleanup.

These are explicitly required regressions in TASK-024 and are especially important
because most of the new behavior lives in `package()`, stdout/stderr redirection,
and the cleanup/failure paths rather than in `ProgressReporter` alone.

### 2. Prove durable standalone structured evidence survives cleanup

`retain_stage_reports()` now copies the standalone JSON report into the output
directory and records its path in the run-level evidence, which is the correct
implementation direction.

Add a focused standalone/deb/all package regression proving that after the normal
temporary workspace is removed:

- the retained standalone report still exists at the durable path;
- the reported details log contains/references that exact retained path;
- the temporary original report is not required for later diagnosis.

The current test delta adds only the narrow-terminal regression, so this durable
structured-evidence continuation is not yet covered.

### Validation

The worker could not run pytest/Ruff locally. Exact-head GitHub validation for this
checkpoint was still running when review completed. A green exact-head full suite,
coverage, and Ruff remain required after the missing regressions are added.

No new semantic redesign is requested. The evidence tee, exception retention,
durable report copy, non-TTY artifact summary, and narrow renderer should all be
retained.


### Exact-head CI follow-up for checkpoint 98f0bac08ee59f773f51974acd6e60e6b76b8226

GitHub exact-head validation completed after the review mutation:

```text
1016 passed, 1 skipped, 2 failed
```

One failure is the already-known intermittent real-service drop Git commit race:

```text
tests/test_cli.py::test_real_service_drop_retire_old_lineage_and_runs_fresh[T-2]
```

The TASK-024-specific failure is:

```text
tests/test_distribution_graph.py::test_tty_progress_fits_a_narrow_terminal
```

The new assertion currently measures raw `\r` segments including the final
newline emitted by `finish(True)`:

```python
assert all(len(part) <= 8 for part in rendered.split("\r") if part)
```

At width 8 the rendered visible progress line can be exactly 8 columns and then be
newline-terminated, making the raw final segment length 9 even though the terminal
line itself is correctly bounded.

Do not widen/truncate the renderer merely to satisfy this raw-string artifact.
Make the regression measure visible line width while excluding line terminators
(for example normalize/split the terminal records appropriately), then keep the
actual eight-column bar/counter invariant.

After that, still add the package-level TTY success/failure and durable standalone
report regressions requested above, and require a new fully green exact-head run.
