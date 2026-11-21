---
"type": "devlegate.ticket"
"title": "Unify the ASCII insignia and remove typography-sensitive startup assertions"
---

## Goal

Keep Devlegate's existing ASCII insignia visually consistent without making its typography part of the automated test contract.

The `src/devlegate/cli.py::_startup_report()` version is the approved visual reference. In `README.md`, `S.P.Q.R.` currently has nine leading spaces; the service variant has ten. The latter looks better balanced and should be retained.

## Required changes

1. Correct the README insignia to match the current service variant exactly (ten spaces before `S.P.Q.R.`). Preserve the existing `D | L` and `---< D E V L E G A T E >---` lines and README's explanatory `State · Provenance · Quality · Recovery` text. Do not redesign or otherwise normalize the artwork.
2. Remove the typography-coupled check in `tests/test_cli_topology.py::test_once_emits_one_startup_identity_block` that counts the exact `---< D E V L E G A T E >---` line. Preserve appropriate **semantic** coverage that a one-shot service invocation emits its startup identity once and includes the necessary service identity/diagnostic data. Prefer observing the startup-report call boundary or parsing semantic values over assertions about decorative text.
3. Do not introduce replacements that assert exact whitespace, alignment, ASCII glyphs, borders, insignia lines, README-to-code equality or other typography. Tests are for behavior, observable information and protocols, not visual design.
4. Keep `devlegate version` runtime identity diagnostics distinct from the ASCII insignia; do not introduce an insignia abstraction, extra branding module, new CLI options or runtime changes solely for this editorial correction.

## Boundaries

- Narrow change: README typography and the directly affected startup-identity test. Do **not** perform a broad rewrite of unrelated presentation tests under this ticket; the general principle is that incidental typography is not a testing contract.
- Machine-readable formats, structured output, required protocol syntax and semantic error markers **are** functional contracts and must retain their relevant tests.
- No changes to orchestration, lifecycle, state, Git semantics, installation, logging transport or generated assets.

## Acceptance

- README displays the same approved insignia layout as the service startup.
- The startup test continues to catch missing or duplicated startup **identity behavior** without pinning an ASCII artwork line or its spacing.
- No new typography-sensitive tests are added.
- Run the relevant focused tests, `./dev lint`, `git diff --check` and hosted CI on the exact worker checkpoint. No access to the installed host runtime is required.
