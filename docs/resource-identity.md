# Execution resource identity

## Observable trust boundary

Admission intent, resource identity, and permission to mutate are separate
claims. SQLite records the serialized service owner's intent. It is not proof
that a checkout or ref belongs to that execution. A read-only proof is not an
authorization, and must be repeated at each use boundary.

Local evidence assumes a trusted service account and filesystem: other actors
must not deliberately transplant owner markers or rewrite the evidence store.
It detects ordinary deletion, replacement, recreation and wrong-generation
reuse, including recreation at the same path, branch and SHA. It is not a
cryptographic attestation against another process with the service account's
filesystem permissions. The existing exclusive service-owner lock is required
for every write; evidence does not grant a second owner that lock.
Raw, adversarial filesystem edits that preserve/transplant witnesses are outside
this boundary. This is not a claim of uninterrupted identity against such an
actor. Local ref observations rely on Git's reflog lifecycle: deleting and
recreating a branch through Git replaces its log and loses the pinned witness.

Each fresh admission gets a random, never-reissued generation token. An
exclusive, durable intent file outside the mutable SQLite runtime payload binds
the execution, ticket, original control commit and ticket blob, product base,
Git common directory, lexical checkout path, local/upstream ref and exact
preconditions. Independent receipt files and hard-link witnesses pin the
checkout Git file and Git administrative files. Missing receipts after an
external effect are ambiguous, even when the resulting SHA is expected.
They must block rather than adopt or recreate resources.
Evidence and Git administrative files must share a hard-link-capable filesystem.
Admission rejects unsupported cross-filesystem layouts before resource creation.
One identical configured fetch/push endpoint is required; endpoint substitution
or multiple/different push destinations is not silently treated as the same
observed upstream resource.

Plain Git remote observation cannot distinguish deleting and recreating a ref
with the same name and SHA. This implementation does not impose a remote
non-reuse policy. Upstream continuity is therefore always **unproven**. Exact
`ls-remote` observations can establish a current value or absence only. Remote
drift is a conflict, unavailable observation is unproven. Neither a matching
value nor an owner publication receipt upgrades remote continuity to proved.
Future reset previews must retain that limitation.

## Ordering and recovery

SQLite uses WAL and synchronous FULL. Commit admission intent before creating
the independent intent file, and fsync that file and its parent before creating
any execution resource. Create resources only from the recorded preconditions.
Reserve an empty checkout directory exclusively and keep its directory file
descriptor open through creation and receipt. This prevents directory inode
reuse in the effect/receipt gap. Record its identity before Git worktree creation.
Create the local ref with a create-only CAS, a generation-specific reflog
message, and a pinned log witness. Git worktree add must succeed; an add error
is never converted into adoption of a matching registration. Compare the live
directory with the held identity before recording the creation receipt.
After creation, fsync witnesses and the exclusive receipt before recording
prepared state or launching a worker. A retry with no effects may proceed;
existing effects without exact independent receipts block. A lost marker or
receipt is never regenerated from SQLite or from matching Git history.

Worker, checkpoint and publication attempts have independent, exclusive intent
and receipt files. Missing/partial receipts, an attempted operation with unknown
outcome, or a changed local ref block replay. The existing authorized
reconciliation rewrite/rollback and reviewed predecessor publication boundaries
also retain separate receipts. No generic historical continuation is inferred.
An explicit retry can receive a new token only by proving its exact independently
recorded predecessor and recording an explicit handoff. New unrelated admissions
cannot inherit a prior generation, even at the same ticket path and SHA.

Checkpoint and publication retain the same generation. They require fresh
identity observations in addition to the existing exact-HEAD, cleanliness,
submodule, lease and provenance checks. Accepted integration and automatic
cleanup retain their existing authority and policy. Evidence files are retained
after cleanup; they do not authorize rebuilding a retired generation.
New immutable execution reports carry the generation token, so accepted
integration and cleanup can revalidate the original local resource generation.
Existing report bytes and their acceptance/review provenance are not rewritten.

`ServiceEngine.execution_resource_proof()` returns a detached `ResourceProof`
with ticket/execution/token, lexical checkout path, Git common directory, exact
ref names, local proof state, upstream observation and actionable reason. It
does not acquire mutation authority, write SQLite, fetch, create refs or refresh
the Git index. Successful local evidence still yields overall `unproven` because
upstream continuity is unknown. Use `local_state` and `upstream_exact` separately
for the existing non-destructive admission policy; neither grants reset or
deletion authority. A future destructive apply must obtain its own authority
and repeat every applicable observation.
Read-only status observations disable optional index writes, filesystem-monitor
hooks and the untracked cache. Configured clean/process filters can execute
arbitrary conversion programs during status; the read-only API reports that
cleanliness proof as unsupported instead of running them. Ordinary owner
operations retain their existing trusted Git capability and configuration.

No historical binding is synthesized. An operator must investigate uncertain
resources without deleting preserved evidence. There is no reset command,
non-done retirement, generic replay, or new deletion authority here.
