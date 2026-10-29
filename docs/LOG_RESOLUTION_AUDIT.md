# Log resolution audit

## Shared decisions

The former ticket snapshot and follow history algorithms now both use
`_ticket_selection`, `_ordered_ticket_reports`, `_active_state`, and
`_LogCursor`. Selection applies one global tail budget before follow continues
from the active cursor. Follow alone retains the loop that discovers later
executions and waits for ticket integration. Execution snapshot and CLI follow
both resolve through `resolve_execution`; follow retains the resolved ticket
binding and checks the exact report path. `_report_candidates` is the shared
unscoped enumeration. `_execution_report` handles targeted validation.
`follow_file` has no remaining entry point. Journal command construction is
shared, but journal subprocess streaming remains separate from file cursors.

## Observation costs

`test_ticket_follow_resolution_costs` measures the complete ticket follow path
with two historical reports, two idle iterations, then a third report. The
first sample includes startup and the first idle iteration. Counts below are
per phase; stat counts are calls to the observation's evidence stat helper,
not every filesystem operation in report validation or ticket lookup.

| Phase | SQLite loads | report list/parse passes | ticket directory enumerations | evidence stats | Git history walks | Git HEAD probes | log opens |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| Startup | 1 | 1 (2 reports) | 3 | 16 | 1 | 2 | 2 |
| Each idle iteration | 1 | 0 | 0 | 3 | 0 | 1 | 0 |
| New report transition | 1 | 1 (3 reports) | 3 | 23 | 1 | 3 | 1 |

The previous cache performed the same three directory enumerations on a full
refresh but also checked every cached JSON and referenced Markdown artifact on
every idle iteration: three directory stats plus N JSON and up to N Markdown
stats. It did not probe HEAD, so could reuse stale chronology. The new idle
cost is independent of N, with one bounded Git HEAD probe. A filesystem event
or changed HEAD triggers full validation; changing or removing previously
validated evidence fails closed. The 12-report idle regression measures three
stats per iteration. Watch overflow and watch loss also fail closed.

The observation assumes a local filesystem with reliable inotify and immutable
Git-managed evidence between notified operations. mmap writes, remote clients,
or other changes bypassing inotify are outside that assumption. The watch is
installed before validation and closed on every follow exit.

Finite ticket selection takes one SQLite load, one ticket report enumeration
and validation pass, one Git history walk when at least two reports exist,
and one open per log needed by the global tail (plus existence checks for
older logs after the budget is exhausted). It does not install the follow
watch or perform HEAD polling. These snapshot counts follow directly from the
shared selection path; the table above is the instrumented follow trace.
Execution snapshot and follow both resolve once; prefix resolution enumerates
the execution tree once, while an exact active identity with a known owner
needs no tree enumeration. Neither subsequently rescans a bound owner.

For a bound active execution, `test_resolved_follow_observation_is_bounded`
measures one log open, zero whole-tree scans, four SQLite loads and five exact
report checks through resolution and three idle waits up to report publication.
The previous unbound path could enumerate all ticket directories each poll;
the bound path does not. Exact report checks parse only when a report exists.
Snapshot and follow use the same initial cursor selection, while ticket and
execution completion loops remain distinct because their terminal conditions
are integration and durable execution report respectively. The focused parity
tests exercise global tails, full history, zero tail, appends, and markers.
