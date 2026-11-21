---
"type": "devlegate.ticket"
"title": "Publish iteration-scoped coverage badges for release branches"
---

## Goal
Make the coverage badge displayed by each release iteration README reflect that iteration's own CI coverage instead of the mutable repository-wide badge. A release branch `release/X.Y.Z` owns a stable badge URL throughout its lifetime; updates stop naturally when that branch stops receiving CI runs. No explicit freeze or tag-finalization state machine.

## Implementation contract
- At the initial publication of a new `release/X.Y.Z` branch, its README must reference a version-scoped endpoint (for example `badges/coverage-X.Y.Z.json`). Ensure the link is installed as part of the new iteration's branch-publication flow, not manually edited for every ticket. A briefly unavailable badge before the first successful CI publication is acceptable.
- On successful CI runs for `release/X.Y.Z`, compute coverage using the existing configured coverage command and publish only that iteration's badge payload. Each subsequent integration/push of a ticket to that branch should update the same file. Updates must correspond to that branch's commit, and older/out-of-order CI runs must not overwrite results from newer commits.
- Store iteration payloads together in the existing `badges` branch without losing unrelated versions. Replace the current destructive `git push --force` workflow with safe preservation of existing files and concurrency handling (serialization or ref CAS/retry).
- An iteration badge is not frozen by an explicit event: it simply remains unchanged once its release branch stops producing successful CI publications. Do not require a release tag, deletion of the branch, manual freezing, or release workflow synchronization.
- The README committed in release tags continues to reference its own iteration-specific badge. Preserve the existing dynamic badge for `master` separately if desired, without allowing default-branch CI to overwrite release-specific payloads.
- Existing historical Git tags/README files must not be rewritten to repair the previous shared dynamic link. No retroactive release-history migration is required; apply the rule to the active iteration when implemented and subsequent iterations.

## Verification
- Test or otherwise deterministically validate URL selection and README generation at first branch publication, repeat updates within the same release branch, and independent badges for two sequential iterations.
- Verify an update of `master` or a newer release iteration does not change the old iteration payload; verify no update occurs without a relevant successful branch CI run.
- Exercise concurrent/out-of-order publish attempts, preserving both existing payload files and the newest applicable coverage result; ensure a failed CI run cannot publish coverage.
- Preserve ordinary CI results, test/coverage calculation, branch naming and release mechanics except where needed for this badge publication contract.

## Boundaries
Small CI/release-publication change; no new runtime dependencies, no historical tag rewrites, no explicit freeze state, and no broad workflow redesign.
