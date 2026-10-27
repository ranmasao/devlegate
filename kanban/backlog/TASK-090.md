---
"type": "devlegate.ticket"
"title": "Fix CLI help discoverability and reconcile command parsing"
---

## Dogfooding observation (Arch package 0.5.7.dev0, 2026-10-19)

`devlegate --help` lists Service, Project, Recovery and Other commands but omits `host`. However `devlegate host --help`, `host install --help` and `host uninstall --help` work; host policy can be installed, checked, removed and reinstalled successfully. Users cannot discover this lifecycle via top-level help.

Top-level help explicitly says `start` starts the persistent background service and bare `devlegate` shows help. Some project docs previously described bare invocation as start/automatic supervision. This is a **documentation drift hypothesis** requiring verification against currently deployed version before broad changes.

## Additional reproduced defect: reconcile parser and help

On the installed 0.5.7 CLI, `devlegate reconcile --help` exposes an internal `auto         ==SUPPRESS==` entry. In `src/devlegate/cli.py` of `release/0.5.7`, `auto` is registered as an argparse subparser with `help=argparse.SUPPRESS`; the suppression string leaks into subcommand help.

The same CLI rewrites unknown-looking arguments after `reconcile` into hidden `auto` syntax unless they match `auto`, `resume`, `update-base` or `control`. Hence `devlegate reconcile update` is treated as a ticket selector `update` and reaches the mutable IPC request; the live service replied `service busy; mutable request was not admitted`. The service refusal is appropriate and no mutation was admitted; the user-facing ambiguity and surprising IPC admission attempt require correction. The normal shorthand `reconcile TICKET` must remain supported. Distinguish syntactically invalid commands from valid ticket identifiers without inventing a blacklist of legitimate ticket IDs.

## Required work

1. Add `host` with accurate purpose to `devlegate --help` in a suitable section, keeping CLI parser/help and documented command vocabulary in sync. Avoid inaccurate implication that package install automatically provisions host policy or project units.
2. Audit current README, Operations and Architecture documentation for `start` vs bare-command semantics and host install separation; correct stale descriptions against the actual CLI contract, not by changing working runtime startup behavior gratuitously.
3. Add lightweight CLI snapshot/behavior coverage that `host` is discoverable and all shown commands parse; exercise `host --help`, `project --help`, and initial invocation.
4. Correct the hidden `reconcile auto` implementation so internal argparse details do not leak (`==SUPPRESS==`) while preserving the documented shorthand `devlegate reconcile TICKET`, explicit `update-base`, `resume`, and `control` forms. Prefer an explicit public grammar over a misleading hidden subparser; keep backwards compatibility where demonstrably required.
5. Specify input-disambiguation behavior, including `reconcile update`: determine whether `update` is a syntactically valid ticket ID under the project's actual schema. If it is, don't arbitrarily reject it; instead provide meaningful diagnostics before mutation attempts when safely possible. Never mask a legitimate `service busy` ownership refusal or bypass IPC mutation ownership.
6. Add CLI/parser tests for help output without `==SUPPRESS==`, shorthand, explicit subcommands, unknown/partial commands, ticket IDs resembling subcommands, output format options, and the boundary between parsing errors and mutable IPC requests. Verify expected exit codes; do not emit a request for inputs rejected as syntax.
7. Keep this CLI UX/parser fix separate from TASK-087 role-skill audit and all Git/recovery transaction changes.

Priority: low-to-medium; help/parser UX correctness with an observed attempted mutable IPC call, but no unauthorized mutation. Backlog only.


## Additional 0.5.7 help-layout regression (2026-10-27)

The top-level Project help may concatenate the longest command and its description: `upgrade-templatessafely upgrade managed role templates`. Fix the actual shared help layout/width calculation, guaranteeing visible separation (preferably two spaces) for all commands, including longer future names and narrow output where applicable. Preserve command spelling and parser behavior. Add focused help-formatting tests asserting separation and command discovery rather than freezing incidental full output. Coordinate with the existing host/reconcile help work above.
