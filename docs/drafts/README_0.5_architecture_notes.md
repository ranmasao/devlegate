# Temporary 0.5 documentation notes

This file is a non-canonical working draft retained during J3.
It preserves material that has not yet received a canonical home and is
reserved for J5 release-facing documentation cleanup.

Do not treat this file as current public documentation. J3/J4/J5 should decide
whether this material belongs in release or CI documentation.

## Deferred CI And Coverage Details

`./dev coverage` measures product code with statement and branch coverage,
captures normally terminating Python subprocesses, combines parallel data, and
prints missing lines and branches. It has no percentage threshold and is not a
release gate.

GitHub Actions runs the test suite under coverage and runs Ruff separately.
Successful trusted branch runs generate the disposable Shields coverage payload;
failed runs do not replace the last published measurement.
