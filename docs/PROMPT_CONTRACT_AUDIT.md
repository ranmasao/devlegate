# Prompt Contract Audit

## Scope And Sources

This audit covers the worker, Architect, and Reviewer contracts as shipped in
0.5.7. It does not infer model intent from TASK-108. The observed executions
are useful decision scenarios, but their starting states, feedback, environment,
and model differ.

| Concern | Evidence | Finding |
| --- | --- | --- |
| Worker prompt | `src/devlegate/worker_prompt.py` | The running service constructs a deterministic prompt from the packaged core contract, one directive, the canonical assignment, and only injected current review feedback. |
| Prompt provenance | `worker_prompt.py:97-173`, `runtime.py:4896-4953` | The exact sent prompt is SHA-256 bound and retained in a private artifact. Existing executions cannot be changed retroactively. |
| Role contracts | `default_templates/artifacts.toml`, `agent_protocol.py:449-527` | Architect and Reviewer skills are generated from managed project template sources. Upgrade is compare-and-swap and rejects custom sources rather than overwriting them. |
| Review feedback | `worker_prompt.py:76-87`, Reviewer template | Rework requires the separately published current review artifact; feedback remains fenced task data and is not merged into the canonical ticket. |
| Lifecycle | Architect and Reviewer templates, `execution_result.py` | A worker claim is evidence for review, not acceptance. Reviewer moves `review -> todo` or `accepted`; Devlegate alone integrates and moves to `done`. |

The materialized `skills/*/SKILL.md` files are generated project artifacts. Their
sources are `.devlegate/templates/skills/*/SKILL.md.tmpl`; package defaults are
under `src/devlegate/default_templates/skills`. Update sources with
`devlegate upgrade-templates`, not direct edits. Packaged worker prompts are not
project templates and are reloaded only when the host restarts its Python service.

## Gap Analysis And Change

The previous core contract named the three worker outcomes but did not distinguish
an intermediate work unit from the whole ticket. It could be read to permit an
`incomplete` handoff after a safe patch, focused test, or local task list item.
The prior Architect and Reviewer templates stated ownership well, but did not
explicitly connect bounded ticket scope and complete canonical-assignment review
to those worker claims.

The implemented prompt-only change states that work continues while safe and
productive work remains; a focused test, checkpoint, or local task completion is
not by itself a termination condition. `completed` requires an honest assessment
of the entire ticket. `incomplete` is reserved for an invocation that must stop
with concrete work left, which goes in `remaining`, with the truthful stop reason
in the existing optional `report`. `blocked` requires an external decision or
action and records it in `questions`. No outcome is added and no outcome keyword
is machine-policed.

The Architect template now directs decomposition into independently reviewable
dependent tickets when a ticket is broad or separable, without adding a Definition
of Done schema. The Reviewer template now requires independent assessment of the
complete canonical assignment and cumulative evidence for the exact checkpoint,
regardless of the worker claim.

## Authority Matrix

| Responsibility | Deterministic control plane | Semantic role judgment |
| --- | --- | --- |
| Ticket identity, prompt bytes, report shape, checkpoint and CI provenance | Yes | No |
| Worker implementation and truthful claim | Records and routes claim | Worker |
| Requirement adequacy, coverage, coherent tests, and documentation | No | Reviewer |
| Ticket decomposition and clear existing acceptance criteria | No | Architect |
| `review -> todo` or `accepted` | Applies guarded workflow operation | Reviewer |
| Integration and `accepted -> done` | Devlegate | No |

Structured machine reporting beyond the current claim, remaining work, questions,
optional report, and exact evidence is not necessary for these semantics. It
would not mechanically establish that an inventory, test suite, or audit is
complete. The reviewer must make that assessment from the ticket and exact
checkpoint.

## Decision Matrix

| Visible scenario | Expected worker claim | Expected review disposition |
| --- | --- | --- |
| A valid short patch passes focused tests and actionable ticket work remains | Continue, not report yet | Not applicable until termination |
| Invocation must stop with identified implementation or validation work pending | `incomplete`, actions in `remaining`, truthful stop reason in `report` | `todo` with focused feedback if more work is needed |
| A missing product decision, credential, or operator action prevents safe progress | `blocked`, required action in `questions` | `todo` or backlog with authoritative context |
| Imminent shutdown interrupts otherwise productive work | `incomplete` if reporting remains possible | Review retained checkpoint and claim |
| Zero-delta retry proves the full ticket already satisfies criteria | `completed` | May accept after independent proof |
| `completed` contradicts stale audit material or ticket requirements | `completed` is evidence only | `todo` with current review artifact |
| CI is red or pending, or report artifact is absent | Claim cannot establish completion | Do not accept until required evidence is available |
| Consecutive rework | Rework against injected current feedback | New immutable review artifact per rejected execution |

These outcomes are expectations for evidence visible to Devlegate and OpenCode.
They do not attribute an internal reason to any model.

## Validation And Rollout

Focused tests assert exact prompt section composition, stable directive bytes,
fenced feedback, worker termination scenarios, and managed Architect and Reviewer
template upgrades. The policy remains prose evaluated by workers and reviewers;
there is no automatic reviewer or worker-outcome enforcement.

After integration, install the 0.5.7 build on the host for Devlegate and, separately,
on the host for rslab2. Stop or checkpoint each service through its normal operator
procedure, run `devlegate upgrade-templates --check`, then
`devlegate upgrade-templates` when no customization conflict is reported, and run
`devlegate render --check`. Restart the service. A conflict requires a manual
project-owned template merge and must not be overwritten. Compare the
`prompt_sha256` fields in `STATE_DIR/prompts/<execution-id>.json` for newly
admitted executions; existing artifacts retain their original prompt.

## Follow-Ups

1. Sample otherwise-similar real tickets after rollout and compare worker claims,
   reviewer findings, tool calls, token/cache use, and work re-read overhead.
   Treat this as observational evaluation, not an A/B claim about TASK-108.
2. Have Architects split recurrently broad tickets before admission; do not add
   criterion IDs or persistent per-criterion state unless evidence shows they are
   needed.
3. Periodically run `upgrade-templates --check` in maintained projects and resolve
   explicit customization conflicts through normal project review.
