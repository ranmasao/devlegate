---
"type": "devlegate.ticket"
"title": "Normalize coverage paths for packaged copies of Devlegate"
---

## Milestone

Devlegate 0.5.8 — trustworthy CI coverage reporting.

## Goal

Measure each Devlegate-owned source file once, including execution of identical packaged copies, without counting temporary copies as independent untested code.

## Context

TASK-154 (49d2ce6) introduced an isolated subprocess import directly from a wheel in tests/test_licensing.py; release CI 45344529890 counted 13 additional wheel modules and reported 72%. TASK-184 (74a4c56) extracted that wheel into tmp_path/installed and imported from there; release CI 45396505372 counted the entire second package and reported 41%. TASK-197 CI 45842014863 counted 67 source files and 67 temporary installed files, each cohort with 21059 statements and 8854 branches. Source-only statement coverage was 84.20%, combined statement/branch coverage approximately 81.3% from rounded per-file data. Master CI 44042585971 reported 80% before those packaging changes. The badge correctly consumes coverage.json totals. TASK-009's vendor exclusion is correct; the configuration lacks logical path normalization for package copies.

## Required behavior

- Correct coverage collection/combination/reporting centrally using coverage.py's existing mechanisms, preferably minimal path mapping where verified to work. Account for both directly imported wheel modules and extracted identical packages. Count actual executed statements and branches from those copies against the canonical src/devlegate modules, not separate denominators. Do not merge unrelated or different-version source merely because basenames match.
- Preserve subprocess measurement, branch coverage, complete canonical source inventory including unexecuted modules, vendor exclusion, packaging/license smoke tests and existing badge publication logic. Do not artificially raise coverage by excluding real untested Devlegate source, disabling subprocess coverage, altering tests solely to execute lines, or postprocessing only the badge.
- Extend the existing tests/test_coverage.py behavioral regression rather than create another test framework. Verify the actual combine/report/JSON path with a small disposable equivalent package or real wheel/extraction fixture, including a uniquely executed branch in a packaged subprocess that is reflected in canonical coverage. Keep test cost proportionate. No product runtime change, migration or broad coverage-hardening work.

## Acceptance criteria

Full hosted exact-checkpoint coverage report and coverage.json contain a single canonical set of Devlegate-owned files, preserve meaningful subprocess branch data and exclude vendor code. Badge payload uses the corrected total without publication changes. Report before/after file inventory, statement/branch denominator and percentages; no arbitrary percentage target. Focused tests, lint, diff check and full CI pass.

## Required regressions

1. Import from canonical source, an identical extracted package and a directly imported wheel in disposable subprocesses; combine/report using actual project configuration. Assert one logical canonical file per module, no duplicate denominator, unique covered statements/branches from each equivalent copy preserved, and unexecuted canonical module still counted.
2. Preserve vendor exclusion and existing packaging regression, validate actual full-suite coverage file inventory and badge payload. Demonstrate the old duplicate-path failure is detected by the focused regression.
