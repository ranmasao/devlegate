# Decision: retire TASK-134 execution and revert deferred continuation

**Date:** 2026-11-05
**Scope:** TASK-134, TASK-145, TASK-147, TASK-149
**Disposition:** Supersedes the prospective continuation architecture
approved in docs/decisions/TASK-147-isolated-generation-2026-11-03.md.
Historical approvals and execution evidence remain unchanged.

## Reason

TASK-134 attempted to implement durable generation-specific ownership
binding for execution resources.

Its previous worker produced an 18-commit checkpoint ending at
7c8274a837bd5579b3ac607bd570ff8d14e5016b, but the execution
failed after a transport error. Those commits were not accepted into
the product.

TASK-145, TASK-147 and TASK-149 introduced generic deferred failed-
execution continuation in order to recover this historical attempt.

The live TASK-134 continuation subsequently blocked at isolated
generation stage candidate_pinning. Independent Git replay exposed a
content conflict on the second source commit against the advanced
product baseline.

Preserving this continuation mechanism is no longer justified.
The project has no deployed customer compatibility requirements.

## Decision

1. Abandon the unfinished historical TASK-134 attempt.
2. Remove TASK-134 from the active Kanban without marking it done.
3. Retain the original ticket, reports, reviews and Git evidence.
4. Revert the product changes introduced by TASK-145/147/149.
5. Keep independent TASK-137/138 changes.
6. Do not add generic continuation, compatibility migration or
   automatic historical-workspace ownership inference.
7. Reimplement the original ownership-binding goal as a fresh ticket,
   with a new execution lineage, after runtime recovery.

The original ticket is archived at:
archive/tickets/TASK-134.md

The historical done records of TASK-145, TASK-147 and TASK-149 remain
unchanged. Their completed execution history must not be interpreted
as evidence that the corresponding product functionality remains active.

## Product rollback

Original release HEAD:
bd015afa4d11c69db78384314c15c88e8dc72f1a

Baseline retained after TASK-138:
d0572c6f7704b138ed9e7cabb33ba4c025001a89

Prepared forward rollback commit:
945dea8e93aaab327b9e7a76cc3e8d0b3519b69f

The rollback restores the exact baseline tree without rewriting Git
history. The rollback commit passed local tests and GitHub CI before
the control retirement was prepared.

## Runtime recovery

Before product/control publication, the operator stopped the service,
backed up runtime SQLite, verified generation-specific source and
target resources, and explicitly detached the never-launched
TASK-134 execution:

50a546a454d046268663caa269359632

The persisted state returned from agent_pending to idle. Historical
resources and immutable reports were not deleted.

This was an explicit one-shot operator recovery, not a generic
runtime migration or a new production repair interface.
