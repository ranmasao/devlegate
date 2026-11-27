# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Exact-object observations, executed only by the existing repository owner."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Literal, TypedDict

from devlegate.abandonment_evidence import (
    EvidenceError,
    OwnerGitEvidence,
    _review_disposition,
)
from devlegate.change_explorer import ObservationError, observation_timeout
from devlegate.change_explorer import _git as bounded_git
from devlegate.clean_preview import (
    _blob,
    _control_identity,
    _history,
    _tree,
    inspect_clean,
    observe_git,
)
from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import resolve_ref_commit
from devlegate.ipc_protocol import MAX_PAYLOAD_BYTES
from devlegate.log_reader import observe_execution_log
from devlegate.project_context import load_worker_project_context
from devlegate.prompt_journal import (
    PromptJournalError,
    read_prompt_journal,
    validate_component_manifest,
)
from devlegate.resource_binding import ResourceBinding
from devlegate.review_provenance import (
    resolve_amended_rework_review,
    resolve_rework_review,
)
from devlegate.tickets import TicketError, parse_ticket_text
from devlegate.worker_prompt import (
    assignment_prompt_component,
    project_prompt_component,
    review_prompt_component,
)

ObjectType = Literal["ticket", "execution", "workspace", "service"]
_FAILURES = (
    ValueError, RuntimeError, OSError, subprocess.SubprocessError,
    ExecutionWorkspaceError,
)


class TicketObservation(TypedDict):
    id: str
    state: str
    type: str
    title: str
    path: str
    blob: str
    text: str
    depends_on: list[str]
    dependency_graph: dict[str, list[dict]]
    provenance: dict


class ExecutionObservation(TypedDict):
    id: str
    ticket_id: str
    report: dict
    path: str
    blob: str
    publication: str | None
    provenance_state: str
    checkpoint: dict
    review: dict
    history: dict
    latest_execution: str
    independent_generation: dict
    control_head: str
    local_log: dict
    worker_prompt: dict


class ServiceObservation(TypedDict):
    owner: dict
    diagnostic_snapshot: dict | None


def validate_identity(
    object_type: str, object_id: str | None, *, include_prompt: bool = False,
) -> None:
    if object_type not in ("ticket", "execution", "workspace", "service"):
        raise EvidenceError("unsupported inspection object type")
    if type(include_prompt) is not bool or (
        include_prompt and object_type != "execution"
    ):
        raise EvidenceError("inspection prompt opt-in is boolean and execution-only")
    if object_type == "service":
        if object_id is not None:
            raise EvidenceError("service inspection does not accept an object ID")
    elif not isinstance(object_id, str) or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_-]*", object_id,
    ):
        raise EvidenceError("inspection requires an exact canonical object ID")


@dataclasses.dataclass(frozen=True)
class InspectionView:
    schema: str
    object_type: ObjectType
    object_id: str | None
    observation: TicketObservation | ExecutionObservation | ServiceObservation
    blockers: tuple[str, ...] = ()
    informational: bool = True
    mutation_authorized: bool = False

    def as_dict(self) -> dict:
        value = dataclasses.asdict(self)
        value["blockers"] = list(self.blockers)
        return value


def _head(owner, *, supervising_worker: bool = False) -> tuple[str, dict]:
    control = owner.control_worktree
    head = observe_git(control, "rev-parse", "HEAD").stdout.strip()
    if resolve_ref_commit(
        owner.repo, f"refs/heads/{owner.control_branch}", runner=observe_git,
    ) != head:
        raise EvidenceError("control HEAD differs from canonical branch")
    return head, _control_identity(
        owner, control, head, committed_only=supervising_worker,
    )


def _ticket(owner, head: str, object_id: str) -> tuple[TicketObservation, list[str]]:
    control = owner.control_worktree
    workflow = owner._workflow_paths()
    graph = owner._committed_workflow(head).graph
    if graph.inspection_invalid_path is not None:
        raise EvidenceError(
            f"non-regular canonical evidence: {graph.inspection_invalid_path}"
        )
    matches = graph.by_id.get(object_id, ())
    if len(matches) != 1:
        raise EvidenceError("ticket has no unique canonical committed state")
    entry = matches[0]
    nodes: dict[str, list[dict]] = {}
    for node in sorted(graph.entries, key=lambda item: item.path):
        observation_timeout()
        if node.ticket is None:
            raise TicketError(node.diagnostic or "invalid committed ticket")
        nodes.setdefault(node.id, []).append({
            "state": node.state, "path": node.path, "blob": node.blob,
            "depends_on": list(node.ticket.depends_on),
        })
    ticket = entry.ticket
    assert ticket is not None and entry.blob is not None and entry.text is not None
    history = observe_git(
        control, "log", "--format=%H", "--no-renames", head, "--",
        *(f"{p}/{object_id}.md" for p in workflow.values()),
    ).stdout.splitlines()
    return {
        "id": object_id, "state": entry.state, "type": ticket.type,
        "title": ticket.title, "path": entry.path, "blob": entry.blob,
        "text": entry.text,
        "depends_on": list(ticket.depends_on), "dependency_graph": nodes,
        "provenance": {"control_head": head, "history": history},
    }, list(graph.inspection_blockers)


def _prompt_review(control, report, workflow, value, sections) -> str:
    previous = value.get("reviewed_execution_id")
    fields = (previous, value.get("reviewed_checkpoint"), value.get("review_path"),
              value.get("review_publication"), value.get("review_sha256"))
    invalid = PromptJournalError("Saved prompt review binding is missing or unproven.")
    directive = value["directive"]
    review_section = sections.get("review") if sections is not None else None
    if all(field is None for field in fields):
        if directive == "rework" or review_section is not None:
            raise invalid
        return "none recorded"
    if (directive == "fresh" or (sections is not None and review_section is None)
            or not isinstance(previous, str)
            or not re.fullmatch(r"[0-9a-f]{32}", previous)
            or previous == report.execution_id):
        raise invalid
    admission = report.control_head
    path = f"executions/{report.ticket_id}/{previous}.json"
    try:
        oid = _tree(control, admission, path).get(path)
        if oid is None:
            raise invalid
        prior = ExecutionReport.from_dict(json.loads(_blob(control, oid)))
        publication = _history(control, admission, path, oid)
        if (prior.ticket_id != report.ticket_id or prior.execution_id != previous
                or prior.workspace_head != fields[1] or not lifecycle_commit_is_exact(
                    control, publication, prior, todo_path=workflow["todo"],
                    review_path=workflow["review"], runner=observe_git,
                )):
            raise invalid
    except _FAILURES as error:
        raise invalid from error
    for historical, resolver in ((False, resolve_rework_review),
                                 (True, resolve_amended_rework_review)):
        try:
            binding = resolver(
                control, admission, publication, prior, workflow_paths=workflow,
                runner=observe_git,
            )
        except _FAILURES:
            continue
        if fields[2:] != (binding.path, binding.publication, binding.sha256):
            continue
        if review_section is not None:
            title, body = review_prompt_component(
                binding.content, f"{binding.path} (sha256:{binding.sha256})",
                historical,
            )
            if review_section != f"## {title}\n{body}".encode():
                continue
        return "historical" if historical else "verified"
    raise invalid


def _prompt_assignment(control, path, oid, section) -> None:
    try:
        raw = bounded_git(control, "cat-file", "blob", oid, limit=MAX_PAYLOAD_BYTES)
        # Match the universal newlines used by the runtime's Path.read_text().
        text = raw.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        ticket = parse_ticket_text(control / path, "todo", text)
        body = assignment_prompt_component(ticket.body)
    except (*_FAILURES, ObservationError) as error:
        raise PromptJournalError(
            "Cannot read the admitted ticket for prompt validation."
        ) from error
    if section != f"## Assigned Work\n{body}".encode():
        raise PromptJournalError("Saved assignment differs from the admitted ticket.")


def _prompt_project(repo, admitted_base, value, sections) -> str:
    # The manifest's source details are validated privately, never returned.
    project = next(component for component in value["components"]
                   if component["kind"] == "project")["source"]
    invalid = PromptJournalError("Saved project component provenance is unproven.")
    if not isinstance(project, dict) or project.get("commit") != admitted_base:
        raise invalid
    try:
        body, expected = project_prompt_component(
            load_worker_project_context(repo, admitted_base),
        )
    except _FAILURES as error:
        raise invalid from error
    expected_section = (
        f"## Project Worker Instructions\n{body}".encode() if body else None
    )
    if project != expected or sections.get("project") != expected_section:
        raise invalid
    return "pinned"


def _observe_prompt(
    owner, report, workflow, *, proved, admission, include_prompt,
) -> dict:
    try:
        value, prompt = read_prompt_journal(owner.state_dir, report.execution_id)
    except FileNotFoundError:
        return {"status": "missing"}
    except OSError:
        return {"status": "unavailable",
                "reason": "Cannot safely read the local prompt journal."}
    except PromptJournalError as error:
        return {"status": "unavailable", "reason": str(error)}
    try:
        if (value.get("execution_id") != report.execution_id
                or value.get("ticket_id") != report.ticket_id):
            raise PromptJournalError("Prompt journal execution or ticket mismatch.")
        if ("project_key" in value and value["project_key"] != owner._state_key
                or "project_key" not in value and admission is None):
            raise PromptJournalError("Saved prompt project is foreign or unproven.")
        if not proved or value.get("admitted_control_head") != report.control_head:
            raise PromptJournalError("Saved prompt admission does not match control.")
        state = owner._state
        if state.get("execution_id") != report.execution_id:
            state = {}
        if any(
            state.get(state_key) not in (None, value[key])
            for state_key, key in (("worker_prompt_sha256", "prompt_sha256"),
                                    ("worker_prompt_directive", "directive"))
        ):
            raise PromptJournalError("Prompt differs from owner's digest or directive.")
        sections = validate_component_manifest(value, prompt)
        project_source = None
        if sections is not None:
            if admission is None:
                raise PromptJournalError("Independent prompt admission is unproven.")
            admitted_base, ticket_blob = admission
            if state.get("execution_admitted_base_head") not in (None, admitted_base):
                raise PromptJournalError("Prompt product admission is unproven.")
            _prompt_assignment(
                owner.control_worktree, f"{workflow['todo']}/{report.ticket_id}.md",
                ticket_blob, sections["assignment"],
            )
            project_source = _prompt_project(owner.repo, admitted_base, value, sections)
        review_binding = _prompt_review(
            owner.control_worktree, report, workflow, value, sections,
        )
    except PromptJournalError as error:
        return {"status": "unavailable", "reason": str(error)}
    result = {
        "status": "present", "sha256": value["prompt_sha256"],
        "directive": value["directive"], "delivery": value["delivery"],
        "project_binding": "journal" if "project_key" in value else "local generation",
        "review_binding": review_binding,
        "manifest": "validated" if sections is not None else "absent",
    }
    if sections is not None:
        result.update(components=list(sections), project_component=project_source)
    if include_prompt:
        digest_matches = state.get("worker_prompt_sha256") == value["prompt_sha256"]
        if sections is None and not digest_matches:
            result.update(text_status="unavailable", text_reason=(
                "Saved prompt has no component manifest or matching owner digest."
            ))
        else:
            result.update(text=value["prompt"], text_status="available")
    return result


def _execution(
    owner, head: str, object_id: str, identity: dict, *, include_prompt: bool,
    supervising_worker: bool,
) -> tuple[ExecutionObservation, list[str]]:
    control = owner.control_worktree
    # Resolve the full ID by canonical artifact path, never by prefix, SHA,
    # runtime cursor, sorting filenames, or the latest ticket attempt.
    tree = _tree(control, head, "executions")
    matches = [(path, oid) for path, oid in tree.items()
               if len(Path(path).parts) == 3 and Path(path).name == f"{object_id}.json"]
    if len(matches) != 1:
        raise EvidenceError("execution has no unique canonical committed report")
    path, oid = matches[0]
    report = ExecutionReport.from_dict(json.loads(_blob(control, oid)))
    if report.execution_id != object_id or path != (
        f"executions/{report.ticket_id}/{object_id}.json"
    ):
        raise EvidenceError("report identity disagrees with canonical path")
    blockers = []
    publication = None
    provenance = "unproven"
    workflow = owner._workflow_paths()
    try:
        publication = _history(control, head, path, oid)
        if not lifecycle_commit_is_exact(
            control, publication, report, todo_path=workflow["todo"],
            review_path=workflow["review"], runner=observe_git,
        ):
            raise EvidenceError("report publication is not an exact lifecycle")
        provenance = "proved"
    except _FAILURES as error:
        blockers.append(str(error))
    review_path = f"reviews/{report.ticket_id}/{object_id}.md"
    reviews = _tree(control, head, review_path)
    review: dict = {"state": "absent"}
    if review_path in reviews:
        review_oid = reviews[review_path]
        review = {"state": "unproven", "path": review_path, "blob": review_oid,
                  "text": _blob(control, review_oid)}
        try:
            revision = _history(control, head, review_path, review_oid)
            if provenance != "proved":
                raise EvidenceError("review report publication is unproven")
            with OwnerGitEvidence(control, owner._assert_repository_owner) as source:
                decision, binding = _review_disposition(
                    source, report=report, publication=publication,
                    revision=revision, review_oid=review_oid,
                    todo_path=workflow["todo"], review_path=workflow["review"],
                    backlog_path=workflow["backlog"],
                    accepted_path=workflow["accepted"],
                )
            review.update(state="proved", publication=revision,
                          decision=decision, binding=binding)
        except _FAILURES as error:
            blockers.append(f"review unproven: {error}")
    # Retain every committed version, including deleted or rewritten feedback.
    checkpoint = {"head": report.workspace_head, "control_head": report.control_head,
                  "base_head": report.code_base_head, "artifact": None}
    if report.report_artifact is not None:
        artifact = report.report_artifact
        artifact_oid = tree.get(artifact)
        if artifact_oid is None:
            blockers.append("immutable checkpoint artifact is missing")
        else:
            text = _blob(control, artifact_oid)
            checkpoint["artifact"] = {"path": artifact, "blob": artifact_oid,
                                      "text": text, "state": "unproven"}
            try:
                _history(control, head, artifact, artifact_oid)
                if hashlib.sha256(text.encode()).hexdigest() != report.report_sha256:
                    raise EvidenceError("checkpoint artifact digest mismatch")
                checkpoint["artifact"]["state"] = "proved"
            except _FAILURES as error:
                blockers.append(str(error))
    history = {}
    artifact_paths = [path, review_path]
    if report.report_artifact:
        artifact_paths.append(report.report_artifact)
    for artifact in artifact_paths:
        versions = []
        for revision in observe_git(
            control, "log", "--format=%H", "--full-history", head, "--", artifact,
        ).stdout.splitlines():
            version_oid = _tree(control, revision, artifact).get(artifact)
            versions.append({
                "commit": revision, "blob": version_oid,
                "text": _blob(control, version_oid) if version_oid else None,
            })
        history[artifact] = versions
    if history[review_path] and review["state"] == "absent":
        review["state"] = "deleted"
        blockers.append("historical immutable review disappeared")
    generation: dict = {"state": "unproven", "generation": report.resource_generation,
                        "current_local": "unknown", "current_head": None,
                         "checkpoint_matches_current": "unknown"}
    prompt_admission = None
    try:
        manager = ExecutionWorkspaceManager(
            owner.repo, owner.execution_worktree_root, report.ticket_id,
            runner=observe_git,
        )
        binding = ResourceBinding.for_execution(manager, object_id)
        intent = binding.intent
        if (intent["generation"] != report.resource_generation
            or intent["control"] != report.control_head
            or intent["path"] != report.execution_path
            or intent["ref"] != f"refs/heads/{report.execution_branch}"
            or intent["remote"] != owner.remote_name
            or manager.branch != report.execution_branch):
            raise EvidenceError("report and independent generation disagree")
        admission_path = f"{workflow['todo']}/{report.ticket_id}.md"
        if _tree(control, report.control_head, admission_path).get(admission_path) != (
            intent["ticket_blob"]
        ):
            raise EvidenceError("generation ticket blob differs from admission")
        # The intent belongs to this attempt (including a new resume after a
        # reconciliation). Use its admission, not the predecessor's base,
        # a later effective workspace base, or the journal's chosen commit.
        if intent["common"] == identity["common"]:
            prompt_admission = intent["base"], intent["ticket_blob"]
        if intent["base"] != report.code_base_head:
            raise EvidenceError("report and independent generation disagree")
        generation["state"] = "observed"
        if not supervising_worker:
            try:
                workspace = binding.require_local(clean=False, read_only=True)
                generation["current_local"] = "proved"
                generation["current_head"] = workspace.head
                generation["checkpoint_matches_current"] = (
                    workspace.head == report.workspace_head
                )
            except _FAILURES as error:
                generation["current_local"] = "unproven"
                blockers.append(f"current generation unproven: {error}")
    except _FAILURES as error:
        blockers.append(f"independent generation unproven: {error}")
    if supervising_worker:
        blockers.append(
            "current workspace continuity not inspected during worker supervision"
        )
    # Publication chronology determines latest; generation identity is separate.
    latest = "unknown"
    publications = []
    try:
        for candidate, candidate_oid in tree.items():
            if (
                Path(candidate).parent.as_posix() == f"executions/{report.ticket_id}"
                and candidate.endswith(".json")
            ):
                publications.append((_history(control, head, candidate, candidate_oid),
                                     Path(candidate).stem))
        chronology = observe_git(
            control, "rev-list", "--first-parent", head,
        ).stdout.splitlines()
        ranks = {revision: index for index, revision in enumerate(chronology)}
        if any(revision not in ranks for revision, _ in publications):
            raise EvidenceError("execution chronology is ambiguous")
        rank = min(ranks[revision] for revision, _ in publications)
        newest = [identifier for revision, identifier in publications
                  if ranks[revision] == rank]
        if len(newest) != 1:
            raise EvidenceError("latest execution publication is ambiguous")
        latest = "latest" if newest[0] == object_id else "historical"
    except _FAILURES as error:
        blockers.append(str(error))
    return {
        "id": object_id, "ticket_id": report.ticket_id,
        "report": report.as_dict(), "path": path, "blob": oid,
        "publication": publication, "provenance_state": provenance,
        "checkpoint": checkpoint,
        "review": review, "history": history, "latest_execution": latest,
        "independent_generation": generation, "control_head": head,
        "local_log": observe_execution_log(
            owner.state_dir, owner._state_key, object_id,
        ),
        "worker_prompt": _observe_prompt(
            owner, report, workflow, proved=provenance == "proved",
            admission=prompt_admission, include_prompt=include_prompt,
        ),
    }, blockers


def inspect_object(
    owner, object_type: ObjectType, object_id: str | None, *,
    include_prompt: bool = False, supervising_worker: bool = False,
) -> dict:
    validate_identity(object_type, object_id, include_prompt=include_prompt)
    owner._assert_repository_owner()
    if object_type == "workspace":
        assert object_id is not None
        # TASK-130/131 consume the same v2 schema and canonical identity digest.
        return inspect_clean(owner, object_id, supervising_worker=supervising_worker)
    if object_type == "service":
        from devlegate.ipc_server import _service_identity
        from devlegate.runtime import DevlegateError

        try:
            diagnostics = owner.published_status_payload()
        except DevlegateError as error:
            if str(error) != "service status view is not ready":
                raise
            diagnostics = None
        observation = {
            "owner": _service_identity(owner),
            "diagnostic_snapshot": diagnostics,
        }
        blockers = []
    else:
        assert object_id is not None
        head, identity = _head(owner, supervising_worker=supervising_worker)
        observation, blockers = (
            _ticket(owner, head, object_id) if object_type == "ticket"
            else _execution(
                owner, head, object_id, identity, include_prompt=include_prompt,
                supervising_worker=supervising_worker,
            )
        )
        if _head(owner, supervising_worker=supervising_worker) != (head, identity):
            raise EvidenceError("canonical control changed during inspection")
    reset = owner._state.get("ticket_reset")
    if isinstance(reset, dict) and (
        object_type == "service"
        or object_type == "ticket" and reset["ticket_id"] == object_id
    ):
        from devlegate.ticket_reset import response

        observation["reset"] = response(reset)
    progress = owner._state.get("transfer_progress") or {}
    if (
        object_type == "ticket"
        and owner._state.get("execution_ticket_id") == object_id
        and isinstance(progress.get("conflict"), dict)
    ):
        from devlegate.transfer_transaction import waiting_message

        active = progress.get("active_binding") is not None
        key = "transfer_conflict" if active else "rework_conflict"
        observation[key] = progress["conflict"]
        blockers.append(waiting_message(
            progress["conflict"], "update-base" if active else "REWORK",
            progress.get("resolution_error"),
        ))
    return InspectionView(
        f"devlegate.inspect-{object_type}.v1", object_type, object_id,
        observation, tuple(sorted(set(blockers))),
    ).as_dict()
