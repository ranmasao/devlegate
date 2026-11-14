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
from devlegate.resource_binding import ResourceBinding
from devlegate.tickets import (
    _validate_cycles,
    is_canonical_ticket_name,
    parse_ticket_text,
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


class ServiceObservation(TypedDict):
    owner: dict
    diagnostic_snapshot: dict


def validate_identity(object_type: str, object_id: str | None) -> None:
    if object_type not in ("ticket", "execution", "workspace", "service"):
        raise EvidenceError("unsupported inspection object type")
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


def _head(owner) -> tuple[str, dict]:
    control = owner.control_worktree
    head = observe_git(control, "rev-parse", "HEAD").stdout.strip()
    if resolve_ref_commit(
        owner.repo, f"refs/heads/{owner.control_branch}", runner=observe_git,
    ) != head:
        raise EvidenceError("control HEAD differs from canonical branch")
    return head, _control_identity(owner, control, head)


def _ticket(owner, head: str, object_id: str) -> tuple[TicketObservation, list[str]]:
    control = owner.control_worktree
    workflow = owner._workflow_paths()
    tree = _tree(control, head, *workflow.values())
    matches = [(state, f"{path}/{object_id}.md")
               for state, path in workflow.items()
               if f"{path}/{object_id}.md" in tree]
    if len(matches) != 1:
        raise EvidenceError("ticket has no unique canonical committed state")
    state, path = matches[0]
    text = _blob(control, tree[path])
    ticket = parse_ticket_text(control / path, state, text)
    nodes: dict[str, list[dict]] = {}
    parsed = {}
    for relative, oid in sorted(tree.items()):
        if not is_canonical_ticket_name(Path(relative).name):
            continue
        for node_state, directory in workflow.items():
            if Path(relative).parent.as_posix() != directory:
                continue
            node = parse_ticket_text(
                control / relative, node_state, _blob(control, oid),
            )
            parsed[node.id] = node
            nodes.setdefault(node.id, []).append({
                "state": node_state, "path": relative, "blob": oid,
                "depends_on": list(node.depends_on),
            })
    blockers = []
    for node_id, entries in nodes.items():
        if len(entries) != 1:
            blockers.append(f"ambiguous dependency graph ticket: {node_id}")
        for entry in entries:
            for dependency in entry["depends_on"]:
                if dependency not in nodes:
                    blockers.append(f"missing dependency: {node_id} -> {dependency}")
    if not blockers:
        try:
            _validate_cycles(parsed)
        except ValueError as error:
            blockers.append(str(error))
    history = observe_git(
        control, "log", "--format=%H", "--no-renames", head, "--",
        *(f"{p}/{object_id}.md" for p in workflow.values()),
    ).stdout.splitlines()
    return {
        "id": object_id, "state": state, "type": ticket.type,
        "title": ticket.title, "path": path, "blob": tree[path], "text": text,
        "depends_on": list(ticket.depends_on), "dependency_graph": nodes,
        "provenance": {"control_head": head, "history": history},
    }, blockers


def _execution(
    owner, head: str, object_id: str,
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
    try:
        manager = ExecutionWorkspaceManager(
            owner.repo, owner.execution_worktree_root, report.ticket_id,
            runner=observe_git,
        )
        binding = ResourceBinding.for_execution(manager, object_id)
        intent = binding.intent
        if (intent["generation"] != report.resource_generation
            or intent["control"] != report.control_head
            or intent["base"] != report.code_base_head
            or intent["path"] != report.execution_path
            or intent["remote"] != owner.remote_name
            or manager.branch != report.execution_branch):
            raise EvidenceError("report and independent generation disagree")
        admission_path = f"{workflow['todo']}/{report.ticket_id}.md"
        if _tree(control, report.control_head, admission_path).get(admission_path) != (
            intent["ticket_blob"]
        ):
            raise EvidenceError("generation ticket blob differs from admission")
        generation["state"] = "observed"
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
    }, blockers


def inspect_object(owner, object_type: ObjectType, object_id: str | None) -> dict:
    validate_identity(object_type, object_id)
    owner._assert_repository_owner()
    if object_type == "workspace":
        assert object_id is not None
        # TASK-130/131 consume the same v2 schema and canonical identity digest.
        return inspect_clean(owner, object_id)
    if object_type == "service":
        from devlegate.ipc_server import _service_identity

        observation = {
            "owner": _service_identity(owner),
            "diagnostic_snapshot": owner.published_status_payload(),
        }
        blockers = []
    else:
        assert object_id is not None
        head, identity = _head(owner)
        observation, blockers = (
            _ticket(owner, head, object_id) if object_type == "ticket"
            else _execution(owner, head, object_id)
        )
        if _head(owner) != (head, identity):
            raise EvidenceError("canonical control changed during inspection")
    reset = owner._state.get("ticket_reset")
    if isinstance(reset, dict) and (
        object_type == "service"
        or object_type == "ticket" and reset["ticket_id"] == object_id
    ):
        from devlegate.ticket_reset import response

        observation["reset"] = response(reset)
    return InspectionView(
        f"devlegate.inspect-{object_type}.v1", object_type, object_id,
        observation, tuple(sorted(set(blockers))),
    ).as_dict()
