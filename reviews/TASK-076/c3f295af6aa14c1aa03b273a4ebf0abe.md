# TASK-076 — seventh execution independent review

**Disposition: REWORK REQUIRED (narrow remaining proof).**

Execution `c3f295af6aa14c1aa03b273a4ebf0abe` checkpoint `08eff0f506409126dcbffa15ee509c75a69e94c6`. Worker reported completed, exit 0, no remaining, but `report_artifact: null`; exact-checkpoint CI and Ruff evidence has not been independently verified. Diff only updates `tests/test_control_scheduler.py`.

## Substantial acceptance progress (retain)

- New `test_bound_pending_generation_blocks_changed_ticket_content` verifies a pending generation fails closed on modified ticket text, retains immutable execution ID and bound control.
- New `test_interleaved_generations_keep_distinct_admission_provenance` uses two Devlegate instances/worktrees and distinct generation bases/control heads; proves distinct worker reports/admitted snapshots despite shared branch advances.
- New `test_descendant_integration_conflict_preserves_checkpoint_evidence` forces a real conflicting edit to the same file, checks integration does not mark done, preserved worker checkpoint and remote execution ref, accepted ticket, and unchanged published product head.
- Prior passes established legacy accepted integration restart, process crash-effect replay, exact merge parents, product content preservation and pre-worker fast-forward replay. Avoid duplicating these.

## Specific remaining conditions before acceptance

1. **Publication contention:** Provide tests/proof for competing remote product advancement between observation and CAS push, lease rejection and ambiguous/nonzero push that nevertheless published the exact intended merge. Verify exact predecessor validation, no forced overwrite of foreign history, preservation of durable integration intent and retry idempotence. Identify and cite existing tests if already present; otherwise add focused cases.
2. **Error and ownership refusals:** Explicit test evidence (existing or new) for remote Git observation/transport failure, dirty/foreign product checkout, unrelated/non-descendant product history, proving fail-closed behavior and checkpoint protection.
3. **Conflict durability:** Newly added conflict test only checks immediate failure. Demonstrate service restart after the conflict does not mistakenly adopt partial merge or silently commit; or identify a preexisting integration-replay test that covers it.
4. **Verification report:** Supply full exact-checkpoint `pytest` and `ruff` results, or concrete failing tests/errors, along with named coverage map for TASK-076 acceptance. Current worker JSON does not include any test results and reviewer cannot infer CI success.
5. **Implementation caveat:** Preserve TASK-083 semantics, current product/control lineage code and prior checkpoint/evidence. No broad TASK-084/085/086 refactoring. Do not require access to the live rslab2 instance for validation.

The seventh pass meaningfully satisfies several outstanding reviewer requests. Future rework should focus on remaining publishing/error behavior and demonstration of successful test suite, not another large redesign.
