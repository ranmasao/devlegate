---
"type": "devlegate.ticket"
"title": "Validate exact remote ref identity in Git observation"
"depends_on": ["TASK-114"]
---

## Scope
Repair the source-grounded identity-validation gap in `src/devlegate/git.py::remote_ref` identified by TASK-114 (`docs/architecture/git-boundary-audit-114.md`) and recorded in curator review `reviews/TASK-114/7aeaaf51ecb743e59cf61c3fa77b56e2.md`. The observer currently validates returned object IDs but does not prove that the returned ref name equals the **exact requested ref**. Keep this a narrowly scoped Git observation correctness fix; first confirm caller inputs and the actual command/response contract.

## Required behavior and proof
- Exact remote ref lookup accepts one well-formed response only when its returned ref identity matches the requested fully qualified ref and the object ID is valid. Reject mismatched names, ambiguous/multiple lines, malformed fields and unexpected symbolic/peeled ref responses as observation errors; never silently take the first matching-looking line.
- Preserve successful empty response as proven absence; preserve the distinction between absence, Git command failure, malformed response and changed identity. Do not reinterpret arbitrary nonzero exit codes as absence.
- Inspect callers that feed remote observation into publication leases, reconciliation/recovery replay and cleanup decisions. Preserve their expected SHA, WAL/evidence/revalidation semantics, failure handling and runner ownership.
- Add focused deterministic negative tests using controlled `ls-remote` output (wrong ref, malformed output, multiple refs, invalid SHA), exact absence and normal exact match; exercise real-Git remote behavior and the relevant lease/replay or cleanup regression paths. No weakened CAS, no unconditional push/delete and no bypass of ownership proof.
- If source investigation reveals a materially different Git output contract, document evidence and amend only the smallest correct validation boundary; do not invent a general-purpose parser or VCS abstraction.

## Boundaries
No migrations, backward-compatibility layer, broad Git refactor, or unrelated cleanup. Keep TASK-085 recovery-boundary audit and TASK-073 final human-only story gate separate. Report exact changed source/tests, proof, residual limitations and successful configured checks.
