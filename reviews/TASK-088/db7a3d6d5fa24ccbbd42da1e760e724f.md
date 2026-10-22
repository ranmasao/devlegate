<!-- devlegate-review-v1
ticket: TASK-088
execution: db7a3d6d5fa24ccbbd42da1e760e724f
checkpoint: efb852661aa29c8aacade1cefc240c471312c38a
control: 922604052f92f321dc540e5c3e2a1d889e4f1a97
disposition: rework
-->

## Rework: deterministic CI regressions caused by new exact-ref probing

The root-cause fix is directionally correct: `resolve_object` and `resolve_ref_commit` now inspect exact `refs/*` via locale-independent `show-ref --verify --quiet`, preventing absent control refs from being misclassified after localized `rev-parse --verify` diagnostics. **Retain this runtime fix.**

However, GitHub Actions run 39767473674 (job 119786389990) for checkpoint `efb852661aa29c8aacade1cefc240c471312c38a` failed **9 tests** (1355 passed, 1 skipped). This is not the previously observed isolated intermittent timing failure:

- Eight failures in `tests/test_control_recovery.py`. The `_control_lineage_runner` double identifies `show-ref --verify`'s reference as `args[2]`; with the new call `show-ref --verify --quiet <ref>`, it treats `--quiet` as a reference, producing `fatal: '--quiet' - not a valid ref`. Some expected command traces also omit the newly required observation.
- One failure in `tests/test_control_workspace.py::test_prior_generation_is_repaired_with_evidence_and_same_execution_id`: expected 54 Git calls, observed 55, consistent with an additional exact-ref probe. Confirm this by inspecting the trace, rather than merely changing a count.

### Required changes

1. Update relevant test doubles, including `_control_lineage_runner`, to correctly model `show-ref --verify --quiet <ref>` status semantics: 0 for present, 1 for absent, and other statuses for observation failures. Retain realistic `show-ref --verify <ref>` output/behavior for non-quiet calls.
2. Revise command-trace expectations and call-count assertions to reflect the intended extra observation, without weakening checks for admission, evidence preservation, recovery race safety, worktree mutation or idempotency.
3. Preserve coverage for absent, present, corrupt, and unreadable refs. Verify that the tests still reject unsafe behavior; do not make mocks unconditionally succeed.
4. Run and pass the affected recovery/workspace tests and full CI on the **committed** checkpoint. Then repeat canonical installed Arch-package fresh `control init` smoke verification before acceptance. Previously recorded real installed-package failure remains the acceptance baseline.
5. Keep scope limited to TASK-088 and test compatibility with the corrected ref contract. Investigate unrelated intermittent service timing failures separately, if they recur.

Do not accept or integrate this checkpoint while its CI remains red.
