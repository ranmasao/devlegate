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
The one pre-effect exception is an admission interrupted before the independent
intent directory exists. Its already committed SQLite token can finish the WAL
write only when recorded and current worktree, registration, local ref and
upstream ref are all absent. A partial evidence directory, an existing ref even
at the expected SHA, or any creation reservation blocks that replay. The proof
API continues to report missing independent evidence until the owner finishes
this step; the read path never repairs it.

Worker, checkpoint and publication attempts have independent, exclusive intent
and receipt files. Missing/partial receipts, an attempted operation with unknown
outcome, or a changed local ref block replay. The existing authorized
reconciliation rewrite/rollback and reviewed predecessor publication boundaries
also retain separate receipts. No generic historical continuation is inferred.
An explicit retry can receive a new token only by proving its exact independently
recorded predecessor and recording an explicit handoff. New unrelated admissions
cannot inherit a prior generation, even at the same ticket path and SHA.
Operations have immutable generation, operation and monotonic sequence identities.
The first stage uses `<operation>-intent.json`; later stages use
`<operation>.<sequence>-intent.json`, with corresponding exclusive receipts.
Completed stages are read in sequence order, including interleaved rewrites and
rollbacks. A second attempt requires every prior stage to have a valid receipt;
it cannot overwrite, reuse or skip an uncertain stage. Native rebase conflicts
record the observed return code, original branch ref and rebase administrative
headers. Continue/abort requires that exact receipted stage and the pinned local
identity. Failure between the Git effect and that receipt blocks even abort.

Worker handoff records process identity before the runtime worker-running state.
A separate immutable retirement observation names that exact handoff stage and
is fsynced before post-worker state. A launch receipt alone is not proof that the
worker group retired; missing retirement evidence blocks post-worker recovery.
A supervisor's explicit admission-closed result records that no process was
launched and returns the same admission to agent-pending. A later launch uses a
new operation stage. Proven process loss or ordinary retired worker groups retain
dirty progress for an explicitly admitted retry; a new generation gets its own
marker and creation receipt rather than inheriting its predecessor's identity.
Reviewed zero-delta executions whose resources were ordinarily retired have no
resources to hand off. Rework creates new reserved resources with a fresh token,
even if the base, ticket branch and checkout path are identical.

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
hooks and the untracked cache. Clean/process filters can execute arbitrary
conversion programs during status. The read-only API checks effective `filter`
attributes on every indexed path against configured drivers, including inherited
configuration and includes, before status. Unused configured drivers are safe;
an active driver, unavailable/malformed observation, or observed configuration
drift leaves cleanliness unproven. Every initialized nested submodule passes its
own preflight, with parent status ignoring submodules. No filter is invoked to
establish applicability, and no conversion configuration is disabled to obtain
a clean result. The earlier blanket configuration guard falsely rejected
pristine GitHub CI worktrees with inherited unused filter configuration; see
[clean inspection](clean-preview.md) for the corrected semantics. Ordinary owner
operations retain their existing trusted Git capability and configuration.

No historical binding is synthesized. An operator must investigate uncertain
resources without deleting preserved evidence. There is no reset command,
non-done retirement, generic replay, or new deletion authority here.

# Operator preview

See [clean inspection](clean-preview.md) for the owner-serialized CLI snapshot,
canonical digest, refusal semantics, and follow-on confirmation contract.
