<!-- devlegate-review-v1
ticket: TASK-106
execution: 25fbafc8962b4af9a5e08fafce7c3a4a
checkpoint: 6630a2283a347eee5f93cebe369903c05a1c9677
control: 3dccb5fbf040bf2f2b14ada7bcb48d384ac943c5
disposition: accept
-->

## ACCEPT — reconciliation persistence deduplication

Implementation checkpoint 6630a2283a347eee5f93cebe369903c05a1c9677 changes only runtime.py, focused control-recovery tests and the accumulated architecture audit. Normal changed-product handling now invokes existing `_persist_product_reconciliation`; `allow_base_fallback=True` preserves normal-path observed-product fallback while recovery retains its stricter remote-identity precondition. Existing evidence identity consistency, pin-before-state ordering, reconciliation payload, and ServiceEngine-owned state save remain. No new general framework or second transaction introduced. Scope is bounded.

Worker reported `incomplete` with one outstanding verification: full GitHub CI. Independently resolved: exact checkpoint Actions run https://github.com/ranmasao/devlegate/actions/runs/40574144065 completed success, 1424 tests passed, 2 skipped, Ruff all checks passed. This reviewer ACCEPT does not rewrite the historical worker claim.

Remaining prior technical debt: the unified helper still uses direct `runner=_git` for evidence resolve/ref update. It existed in both antecedent paths; follow-up owner/shutdown audit should address it separately and must not be confused with new regression or waived architecture acceptance.

Engine owns integration and movement to done. TASK-107 release cleanup remains deferred.
