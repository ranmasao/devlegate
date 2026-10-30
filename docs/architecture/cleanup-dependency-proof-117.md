# Cleanup dependency proof boundary

`ServiceEngine._assert_cleanup_has_no_execution_dependency` in
`src/devlegate/runtime.py` reads the current engine state on every call. It
checks worker identity and active execution, then the nested reconciliation,
automatic retry, and accepted integration records, in that order. Invalid
records block even when they appear unrelated. Reconciliation also binds the
workspace path, branch, evidence identity, and exact checkpoint; resolved
records remain dependencies. Retry checks both stale and fresh execution IDs.

The accepted-integration record's shape and SHA identities are still validated
in the engine. Only the comparison of its validated ticket and checkpoint with
the accepted report is isolated in
`src/devlegate/cleanup_dependency.py:accepted_integration_dependency_reason`.
It takes immutable strings, compares checkpoints exactly, and returns the same
diagnostics; it neither observes state nor authorizes cleanup. The other nested
records remain engine-owned: extracting them would require preserving malformed
input handling, path and evidence validation, identity parsing, and diagnostic
precedence as well as producing a genuinely immutable snapshot.

`_revalidate_cleanup_retirement` re-runs the gate and reloads ticket, report,
ref and integration evidence before deletion. Owner admission, the cleanup WAL,
resource effects and ref compare-and-swap remain outside this seam. Tests in
`tests/test_cleanup_dependency.py` cover unrelated, matching, and contradictory
accepted-integration identities. `tests/test_control_workspace.py` covers live
and malformed dependencies, dependency drift before worktree retirement, and
restart after each retirement boundary and after ref drift.

This is a bounded comparison, not a proof that the whole gate or cleanup
executor is pure. The tests do not exhaust every malformed nested state or
possible interruption interleaving. The recovery-boundary audit and final
human architecture story gate remain separate work.
