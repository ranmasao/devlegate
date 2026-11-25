Use Python 3.12+ with the standard library and existing dependencies. Follow
`docs/DEVELOPMENT.md`; do not add speculative migration or compatibility paths.

During editing, run targeted pytest node IDs or narrowly scoped test files by
behavior and proof boundary (including real IPC, process identity, Git/SQLite
effects or recovery when applicable). Avoid `./dev check`, unrestricted pytest
and repeated full local suites during iteration. Before claiming `completed`,
the final focused tests for the whole assignment, `./dev lint` across
`src tests tools`, and `git diff --check` must pass. Fix and rerun failing checks;
report unavailable checks honestly.

Authoritative hosted CI at the exact checkpoint runs the complete configured
suite, coverage and lint before independent review and integration. Local checks
do not replace it; do not rerun an unchanged full suite just to reproduce CI.
