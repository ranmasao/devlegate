# Reconciliation boundary assessment

This assessment starts from commit `a083ef2598c1da7cd23fe2ebb762192b88b9517b`
and includes the changes accompanying this document. Historical architecture
audits are leads; this assessment is based on the source at that revision.

`reconciliation_transactions.py` admits immutable target, checkpoint, workspace,
and predecessor identities before dispatching fresh, local-pending, or
remote-pending work. The transaction now obtains detached pending reconciliation
snapshots through `ServiceEngine.reconciliation_pending` instead of reading
`owner._state`. The engine remains the state owner. The transaction continues
to call the existing `_save_state` for WAL and resolution; adding stage-specific
forwarders would duplicate this boundary without removing its authority coupling.

The historical temporary `execution_base_head` assignment is already absent at
the assessed revision. `start_fresh_rewrite` passes the target explicitly to
`_execution_workspace_for_recovery(base_head=...)`; the durable base changes
only on resolution. Local-pending is saved before rebase, remote-pending before
evidence pin and lease-guarded publication. Recovery reobserves workspace,
refs and exact heads before aborting or resolving; evidence pinning uses CAS.

Regression coverage includes detached nested state in `test_reconciliation.py`
and local and published interruption/replay cases in
`test_control_cross_layer.py`, including post-rebase, evidence pin, publication,
and resolution interruption, plus conflicting pinned evidence. These tests
exercise selected crash windows, not every possible Git or storage failure.
The broad transaction capability surface and direct `_save_state` calls remain
owner-coupled; this bounded change does not establish an independent state
owner or final architecture approval. Recovery-boundary and final story audits
remain separate work.
