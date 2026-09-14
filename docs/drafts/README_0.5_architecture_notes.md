# Temporary 0.5 documentation notes

This file is a non-canonical working draft retained after J3 for J5.
It preserves CI/coverage publication details that have not yet received
their final release-facing documentation home.

Do not treat this file as current public documentation.
J5 should decide whether this material belongs in release or CI documentation.

## Deferred CI And Coverage Details

`./dev coverage` measures product code with statement and branch coverage,
captures normally terminating Python subprocesses, combines parallel data, and
prints missing lines and branches. It has no percentage threshold and is not a
release gate.

GitHub Actions runs the test suite under coverage and runs Ruff separately.
Successful trusted branch runs generate the disposable Shields coverage payload;
failed runs do not replace the last published measurement.
