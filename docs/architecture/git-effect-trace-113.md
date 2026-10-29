# Git effect trace: partial measurement

Source snapshot: `baeee877466a84522d0b2331ba16d0bb7ec0d2fc`, plus the
audit-only test changes in this workspace. The measurements below exclude
fixture setup and test-side verification commands. They are not architecture
approval. The recovery-boundary audit and final human story gate are separate.

## Measured paths

`tests/test_daemon_cross_layer.py::test_control_sync_trace_covers_runtime_observations`
captures the complete ordered runner argv and Git Trace2 process events around
`ServiceEngine._sync_control`. Trace2 top-level argv, including Git's `-C`
working directory, is checked against the runner trace, and a local transport
`upload-pack` child is observed. The current case has 13 local-read calls and
one fetch; the fast-forward case has 15 local-read calls, one fetch and one
merge. Fetch is both a remote read and local write, so effect categories
overlap. Merge is a local write. These counts do not count child processes as
additional top-level commands. Trace2 child events show launched children, not
a complete account of their filesystem or network effects, hooks or non-Git
descendants. The local fixture does not measure SSH or remote-server effects.

`tests/test_reconciliation.py::test_product_evidence_pin_complete_git_trace`
captures the runner calls of `ServiceEngine._persist_product_reconciliation`
at `src/devlegate/runtime.py:8709-8723`. An absent ref gives one local read,
one create-only `update-ref` with an empty expected-old argument, then three
local reads that re-observe the exact ref. An already matching ref gives six
local reads and no write. The fixture's state-save assertion independently
checks the exact checkpoint ref before persistence. Existing race and restart
tests in `tests/test_reconciliation.py` exercise competing evidence and
interruption; these do not measure complete nested process effects.

Run the focused regression proof with:

```
python -m pytest -q tests/test_daemon_cross_layer.py::test_control_sync_trace_covers_runtime_observations tests/test_reconciliation.py::test_product_evidence_pin_complete_git_trace tests/test_reconciliation.py::test_product_evidence_create_race_reobserves tests/test_reconciliation.py::test_product_evidence_interrupted_after_pin_restarts
```

## Unmeasured scope

Complete dynamic argv and nested effects still need measurement for publication,
recovery, and cleanup, including zero, one, and 32 candidates and the 10000
inventory bound. Cleanup apply must retain its fresh pre-mutation proof; the
source-derived estimates in `integrated-runtime-git-diagnostic.md` are not
measurements. No conclusion about those counts follows from these two traces.
