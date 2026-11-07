---
"type": "devlegate.ticket"
"title": "Design and enforce capability isolation for every managed agent role"
---

## Milestone
Defense-in-depth for the Devlegate worker/control boundary. **Deferred by curator decision on 2026-11-07. Keep in backlog until explicitly scheduled.** Do not make TASK-151 (abandonment workflow) contingent on completing this ticket or on Landlock ABI 6.

## Goal
Convert the existing "Devlegate owns Git and control operations; agents only handle assigned work" instruction into an enforceable least-privilege execution boundary for **all managed agent roles**, not only abandonment analysis. Establish separate, testable capabilities for implementation workers, reviewers, and evidence/report workers while retaining the current self-hosted lifecycle, restart, recovery and execution reporting semantics.

## Context
- Today implementation workers run in a Git worktree with the agent instructed not to issue Git commands. OpenCode external-directory permissions and the existing worker prompt are not equivalent to OS-level denial of Git/control access.
- Existing cgroup v2 containment proves lifetime/descendant retirement; it does not isolate filesystem, credentials, sockets, or syscalls.
- TASK-151 introduced experimental Landlock/seccomp evidence-only launcher and bounded artifact pipe. The 2026-11-07 checkpoint's GitHub CI failed in the isolation tests because its ELF runtime allowlist omitted `libpython3.12.so.1.0` in GitHub's `/opt/hostedtoolcache` layout. That implementation is **not** the mandatory foundation for this task. Preserve useful lessons/code only when justified; do not enshrine Landlock ABI 6 or an environment-specific allowlist as a permanent requirement.
- TASK-151 may be completed using the established worker contract and scoped logical/tool-level restrictions now, with explicit documentation that this is not a hardened security boundary. The hardening here is a separate later deliverable.

## Required behavior
1. Specify a threat model and trust boundaries: process/worker identity, read/write/execute access to source files, Git metadata and refs, control worktree, owner IPC, state database, provider/host credentials, inherited descriptors, subprocesses and network. Distinguish policy instruction, OpenCode tool permission and enforced kernel/OS isolation without conflating them.
2. Provide role-specific capabilities. Implementation worker may read and edit assigned source files and invoke permitted build/test tools, but may not read/write managed Git administrative data or wield Git branch/ref authority. Reviewer and abandonment-analysis workers receive only bounded read-only evidence and no control-plane mutation authority. Devlegate owner retains all Git/checkpoint/integration and lifecycle operations.
3. Solve the Git-worktree problem explicitly: access to a directory containing `.git` cannot be granted wholesale when Git metadata must be inaccessible. Explore a projected code tree, namespace/bind-mount policy, or another implementable separation; preserve efficient file editing/checkpointing/recovery semantics. Do not merely remove `git` from PATH or rely on prompts.
4. Prefer Linux-native, stdlib-friendly mechanisms and reuse existing cgroup/process-supervision infrastructure. Evaluate the minimum kernel/LSM ABI and feature probes required by the actual threat model, rather than assuming Landlock ABI 6 across deployments. Assess executable and shared-library resolution for wheel, source, standalone, Debian and Arch distributions. When kernel-enforced guarantees are unavailable, fail closed for operations requiring them; do not label a logical-only fallback as OS isolation.
5. Preserve provider connectivity that is explicitly needed for LLM inference while preventing arbitrary access to privileged local Unix IPC, unexpected process signalling, inherited secrets, and indirect escapes through permitted tools. State remaining same-UID and compromised-tool limits honestly.
6. Make resource/credential/IPC access auditable and bounded. Reject ambiguous runtime roots, symlinks, foreign inherited FDs, unsafe execution workspaces and mismatched resource generations. Do not introduce a generic container orchestration framework or new mandatory third-party runtime dependency without demonstrated need.
7. Introduce the hardened policy incrementally and test it with real subprocesses in disposable Git repositories. Separate actual kernel-feature tests from pure fallback/configuration tests; never skip a required production isolation check silently.

## Acceptance criteria
- Concrete design and enforceable role capability matrix, with explicit unsupported-host diagnostics.
- An implementation worker cannot directly operate managed Git history/refs/control plane even through shell/subprocess tooling, yet can edit files and run representative tests; Devlegate can still checkpoint the worker's result.
- Read-only analyst/reviewer can access allowlisted evidence but not source/control/secret/owner IPC outside that scope.
- Cross-platform/build deployment constraints are documented; real-Git and live-subprocess regressions verify the claimed guarantees and restart/process retirement behavior.
- No silent lowering of an enforced boundary, no broad environment allowlists (such as granting all of `/opt`), and no automatic rewrite of existing hosted ticket resources.

## Required regressions
1. Worker attempts `git status`, `git commit`, ref updates and direct `.git` file access from allowed source path: all managed-Git capabilities denied, while ordinary source file edits and focused tests succeed.
2. Worker attempts credentials/environment/FD leaks, control IPC access, local process signalling, symlink traversal and child-process escape; enforce actual OS denials.
3. Different Python and executable layouts (system, hostedtoolcache-like, standalone bundled runtime); fail with precise diagnostics when required ELF/interpreter libraries cannot be allowlisted.
4. Older/unavailable Landlock or other isolation facilities: capability probe and explicit policy decision, no fabricated isolation.
5. Cgroup cancellation, worker-group retirement, crash/restart and resource generation checks continue to prevent orphaned privileged work.

## Boundaries
Future work only. Do not retroactively add mandatory sandbox requirements to TASK-151's next iteration; do not touch TASK-129–133 or archived TASK-134. No compatibility/migration layers for hypothetical customers. Schedule this ticket separately after the abandonment pipeline is operational and its failure/recovery path has been reviewed.
