# Git effect trace: bounded measurement in progress

Source snapshot: `baeee877466a84522d0b2331ba16d0bb7ec0d2fc`, plus the
audit-only test changes in this workspace. The measurements below exclude
fixture setup and test-side verification commands. They are not architecture
approval. The recovery-boundary audit and final human story gate are separate.

## Measured paths

Counts in this table are top-level runner invocations; effect columns overlap.
Local read counts classify read-only commands, not implicit reads performed by
write commands. Setup and test verification are excluded. A push is counted
as a remote write, and fetch as both remote read and local write.

| Scenario | Commands | Local read | Remote read | Local write | Remote write | Separately calculated bounds |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| Sync current | 14 | 13 | 1 | 1 | 0 | None |
| Sync fast-forward | 17 | 15 | 1 | 2 | 0 | None |
| Evidence absent | 5 | 4 | 0 | 1 | 0 | None |
| Evidence already matching | 6 | 6 | 0 | 0 | 0 | None |
| Publication, absent remote | 3 | 1 | 1 | 0 | 1 | None |
| Publishing recovery iteration after simulated crash | 97 | 83 | 10 | 6 | 2 | None |
| Cleanup eligible candidate, screening | 26 | 22 | 4 | 1 | 0 | None |
| Cleanup eligible candidate, apply | 104 | 82 | 19 | 7 | 1 | None |
| Cleanup screening, no candidates | 4 | 3 | 1 | 0 | 0 | None |
| Cleanup screening, 32 ref-only skipped candidates | 100 | 99 | 1 | 0 | 0 | None |

Publication is traced by `tests/test_control_workspace.py::test_execution_publication_real_git_trace`:
the ordered argv are exact HEAD resolution, `ls-remote` of the execution ref,
and an empty-expected-old `--force-with-lease` push of the exact checkpoint.
Trace2 observes a `receive-pack` child. The recovery row is the entire resumed
iteration of `tests/test_control_recovery.py::test_publishing_recovery_pushes_checkpoint_without_worker`,
after a simulated crash before publication, including lifecycle publication;
Trace2 top-level argv match the runner and a receive-pack child is observed.
The worker is not rerun and the resulting remote SHA is independently checked.

`tests/test_control_workspace.py::test_cleanup_eligible_real_git_trace` traces
screening and apply separately on one durably done, published candidate. Apply
re-observes the candidate, fetches and observes the remote before worktree
removal, then deletes the local ref with expected-old and the remote with a
lease. The existing ownership and restart regressions cover changes and
interruptions at the destructive boundaries; the trace alone does not prove all
possible concurrent changes. Trace2 observes an upload-pack child.

`test_cleanup_ref_only_screening_real_git_trace` creates zero or 32 local
execution refs without done tickets or worktrees. All 32 are skipped, and no
destructive application is measured for that case. The 100-command result
must not be extrapolated to eligible candidates, which need additional proof.

The inventory ceiling is a cardinality limit, not a limit on discovery work:
`runtime.py:2385-2528` gathers registrations, direct work-root entries, local
and remote execution refs into a ticket-keyed dictionary, sorts the keys, and
returns at most `limit`. `runtime.py:5360-5390` requests 10000 without rotation,
screens at most those 10000 and marks `scan_complete` false when the returned
count reaches 10000 or remote observation fails. Thus a full inventory can
discover and sort more than 10000 identities before limiting the returned set;
the ceiling does not bound input lines, enumeration cost, or Git subprocess
effects per candidate. This is a source-derived model, not a 10000-candidate
measurement.

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
python -m pytest -q tests/test_control_workspace.py::test_execution_publication_real_git_trace tests/test_control_recovery.py::test_publishing_recovery_pushes_checkpoint_without_worker tests/test_control_workspace.py::test_cleanup_eligible_real_git_trace tests/test_control_workspace.py::test_cleanup_ref_only_screening_real_git_trace tests/test_control_workspace.py::test_workspace_cleanup_restart_converges_after_each_retirement_boundary tests/test_control_workspace.py::test_workspace_cleanup_revalidates_ownership_before_worktree_removal tests/test_control_workspace.py::test_execution_publication_lease_rejects_intervening_remote_generation
```

## Unmeasured scope

Cleanup application with 32 eligible candidates and the resulting inventory
pass remain unmeasured. The source-derived estimates in
`integrated-runtime-git-diagnostic.md` are not measurements. Trace2
child events do not establish complete process ancestry or hook effects, nor
filesystem, network or server effects; the fixture uses a local remote.
