# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bind independent curator feedback to one reviewed execution generation."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path

from devlegate.execution_result import ExecutionReport
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    commit_changes,
    commit_parents,
    commit_range,
    file_history_commit,
    is_ancestor,
    path_object,
    read_path,
)


class ReviewProvenanceError(ValueError):
    """Current control history cannot safely identify rework feedback."""


Runner = Callable[..., subprocess.CompletedProcess[str]]
MAX_REVIEW_BYTES = 256 * 1024
_HEADER = re.compile(
    r"\A<!-- devlegate-review-v1\n"
    r"ticket: (?P<ticket>[A-Za-z0-9][A-Za-z0-9._-]*)\n"
    r"execution: (?P<execution>[0-9a-f]{32})\n"
    r"checkpoint: (?P<checkpoint>[0-9a-f]{40})\n"
    r"control: (?P<control>[0-9a-f]{40})\n"
    r"disposition: (?P<disposition>rework|accept)\n-->\n",
)


@dataclass(frozen=True)
class ReviewBinding:
    content: str
    path: str
    publication: str
    transition: tuple[str, ...]
    sha256: str

    def as_dict(self) -> dict[str, object]:
        return {
            "content": self.content,
            "path": self.path,
            "publication": self.publication,
            "transition": list(self.transition),
            "sha256": self.sha256,
        }


def restore_review_binding(
    value: object,
    *,
    ticket_id: str,
    execution_id: str,
    checkpoint: str,
    control_head: str,
    disposition: str = "rework",
) -> ReviewBinding:
    """Validate a persisted immutable binding before reusing its prompt data."""
    if disposition not in {"rework", "accept"}:
        raise ReviewProvenanceError("unsupported curator review disposition")
    required = {
        "content",
        "path",
        "publication",
        "transition",
        "sha256",
    }
    # Early v1 writers included these two explicit compatibility markers.  They
    # do not weaken the immutable review binding, but rejecting their known
    # inert values makes an otherwise valid interrupted execution unrecoverable.
    legacy = {"legacy", "legacy_boundary"}
    if not isinstance(value, dict) or not required.issubset(value) or set(value) - (
        required | legacy
    ):
        raise ReviewProvenanceError("persisted curator review binding is malformed")
    if (
        "legacy" in value
        and value["legacy"] is not False
        or "legacy_boundary" in value
        and value["legacy_boundary"] is not None
    ):
        raise ReviewProvenanceError(
            "persisted curator review compatibility fields are invalid"
        )
    content = value["content"]
    path = value["path"]
    publication = value["publication"]
    digest = value["sha256"]
    transition = value["transition"]
    if (
        not isinstance(transition, list) or not transition
        or (disposition == "accept" and len(transition) > 3)
    ):
        raise ReviewProvenanceError("persisted curator review provenance is invalid")
    if not all(
        isinstance(item, str) and re.fullmatch(r"[0-9a-f]{40}", item)
        for item in transition
    ):
        raise ReviewProvenanceError("persisted curator review transition is invalid")
    if not all(isinstance(item, str) for item in (content, path, publication, digest)):
        raise ReviewProvenanceError("persisted curator review content is invalid")
    encoded = content.encode("utf-8")
    expected_path = f"reviews/{ticket_id}/{execution_id}.md"
    if len(encoded) > MAX_REVIEW_BYTES:
        raise ReviewProvenanceError("persisted curator review exceeds the prompt limit")
    if path != expected_path or hashlib.sha256(encoded).hexdigest() != digest:
        raise ReviewProvenanceError("persisted curator review integrity check failed")
    if not re.fullmatch(r"[0-9a-f]{40}", publication):
        raise ReviewProvenanceError("persisted curator review publication is invalid")
    if disposition == "rework" and (
        transition[0] != publication or len(set(transition)) != len(transition)
    ):
        raise ReviewProvenanceError("persisted curator review transition is invalid")
    match = _HEADER.match(content)
    if match is None:
        if not content.startswith(f"# {disposition.upper()}\n"):
            raise ReviewProvenanceError(
                "persisted curator review attribution is invalid"
            )
    elif match.groupdict() != {
        "ticket": ticket_id,
        "execution": execution_id,
        "checkpoint": checkpoint,
        "control": control_head,
        "disposition": disposition,
    }:
        raise ReviewProvenanceError("persisted curator review attribution is invalid")
    return ReviewBinding(content, path, publication, tuple(transition), digest)


def _same_blob(
    repo: Path, older: str, older_path: str, newer: str, newer_path: str, runner: Runner
) -> bool:
    original = path_object(repo, older, older_path, runner=runner)
    current = path_object(repo, newer, newer_path, runner=runner)
    return original is not None and original == current


def _transition(
    repo: Path,
    head: str,
    lifecycle: str,
    ticket_id: str,
    *,
    workflow_paths: Mapping[str, str],
    artifact_path: str,
    runner: Runner,
) -> tuple[tuple[str, ...], str | None]:
    paths = {state: f"{path}/{ticket_id}.md" for state, path in workflow_paths.items()}
    review, todo, backlog = paths["review"], paths["todo"], paths["backlog"]
    canonical_paths = set(paths.values())
    try:
        if any(
            path_object(repo, lifecycle, path, runner=runner) is not None
            for path in canonical_paths - {review}
        ):
            raise ReviewProvenanceError("review disposition history is conflicting")
        commits = commit_range(
            repo, f"{lifecycle}..{head}", reverse=True, runner=runner
        )
        transitions: list[str] = []
        deferred = False
        artifact_commit: str | None = None
        parent = lifecycle
        for commit in commits:
            if commit_parents(repo, commit, runner=runner) != (parent,):
                raise ReviewProvenanceError(
                    "review disposition history is not a linear first-parent chain"
                )
            previous = parent
            parent = commit
            changes = set(commit_changes(repo, commit, runner=runner))
            if f"A\t{artifact_path}" in changes:
                if artifact_commit is not None:
                    raise ReviewProvenanceError(
                        "review artifact publication is ambiguous"
                    )
                artifact_commit = commit
            workflow = {
                item for item in changes if item.partition("\t")[2] in canonical_paths
            }
            # Only the initial review disposition must occupy its own commit.
            # Later scheduling moves are checked against the target's paths.
            allowed_extra = (
                {f"A\t{artifact_path}"} if artifact_commit == commit else set()
            )
            if (
                workflow == {f"D\t{review}", f"A\t{todo}"}
                and changes == workflow | allowed_extra
            ):
                if transitions:
                    raise ReviewProvenanceError(
                        "review disposition history is conflicting"
                    )
                if not _same_blob(repo, lifecycle, review, commit, todo, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed during rework disposition"
                    )
                transitions.append(commit)
            elif (
                workflow == {f"D\t{review}", f"A\t{backlog}"}
                and changes == workflow | allowed_extra
            ):
                if transitions:
                    raise ReviewProvenanceError(
                        "review disposition history is conflicting"
                    )
                if not _same_blob(repo, lifecycle, review, commit, backlog, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed during rework deferral"
                    )
                transitions.append(commit)
                deferred = True
            elif workflow == {f"D\t{todo}", f"A\t{backlog}"}:
                if not transitions or deferred:
                    raise ReviewProvenanceError("review deferral history is malformed")
                if not _same_blob(repo, previous, todo, commit, backlog, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed during rework deferral"
                    )
                transitions.append(commit)
                deferred = True
            elif workflow == {f"D\t{backlog}", f"A\t{todo}"}:
                if not deferred:
                    raise ReviewProvenanceError(
                        "review backlog disposition is malformed"
                    )
                if not _same_blob(repo, previous, backlog, commit, todo, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed after rework deferral"
                    )
                transitions.append(commit)
                deferred = False
            elif workflow == {f"M\t{backlog}"} and deferred:
                # A deferred ticket may be amended in backlog, independently
                # of the unchanged-blob review and scheduling moves.
                continue
            elif workflow:
                raise ReviewProvenanceError("review disposition history is conflicting")
        if not transitions or deferred:
            raise ReviewProvenanceError(
                "reviewed execution has no proven rework transition"
            )
        return tuple(transitions), artifact_commit
    except (GitObservationError, GitAncestryError) as error:
        raise ReviewProvenanceError(
            "cannot inspect curator review disposition history"
        ) from error


def resolve_rework_review(
    repo: Path,
    head: str,
    lifecycle: str,
    report: ExecutionReport,
    *,
    workflow_paths: Mapping[str, str],
    runner: Runner,
) -> ReviewBinding:
    """Resolve the immutable review bound to one rejected execution."""
    return _resolve_rework_review(
        repo, head, lifecycle, report, workflow_paths=workflow_paths,
        runner=runner, amended=False,
    )


def resolve_amended_rework_review(
    repo: Path, head: str, lifecycle: str, report: ExecutionReport, *,
    workflow_paths: Mapping[str, str], runner: Runner,
) -> ReviewBinding:
    """Resolve historical feedback for a separately deferred amendment."""
    return _resolve_rework_review(
        repo, head, lifecycle, report, workflow_paths=workflow_paths,
        runner=runner, amended=True,
    )


def _resolve_rework_review(
    repo: Path, head: str, lifecycle: str, report: ExecutionReport, *,
    workflow_paths: Mapping[str, str], runner: Runner,
    amended: bool,
) -> ReviewBinding:
    if report.result.transport_error is not None or not report.result.egress_ok:
        raise ReviewProvenanceError(
            "failed worker execution cannot be attributed curator feedback"
        )
    artifact = f"reviews/{report.ticket_id}/{report.execution_id}.md"
    transitions, transition_publication = _transition(
        repo,
        head,
        lifecycle,
        report.ticket_id,
        workflow_paths=workflow_paths,
        artifact_path=artifact,
        runner=runner,
    )
    # A review published with the disposition describes the specification in
    # review at that boundary.  A later backlog amendment is a valid Git
    # transition, but it does not retroactively make that review a decision on
    # the amended specification.  Until current-spec review is bound by the
    # owner, do not admit it as ordinary rework.
    try:
        same_specification = _same_blob(
            repo, lifecycle, f"{workflow_paths['review']}/{report.ticket_id}.md",
            head, f"{workflow_paths['todo']}/{report.ticket_id}.md", runner,
        )
    except GitObservationError as error:
        raise ReviewProvenanceError(
            "cannot verify the specification covered by curator review"
        ) from error
    if not same_specification and not amended:
        raise ReviewProvenanceError(
            "ticket specification changed after curator review; current-spec "
            "review binding is required before rework admission"
        )
    if amended and (same_specification or len(transitions) < 2):
        raise ReviewProvenanceError(
            "amended continuation requires a separate backlog deferral and amendment"
        )
    try:
        content = read_path(repo, head, artifact, runner=runner)
        publication = file_history_commit(repo, head, artifact, runner=runner)
    except GitObservationError as error:
        raise ReviewProvenanceError(
            "cannot read current curator review artifact"
        ) from error
    if content is None:
        if publication is not None or transition_publication is not None:
            raise ReviewProvenanceError("curator review artifact is missing or deleted")
        raise ReviewProvenanceError(
            "curator review feedback is missing; explicitly dispose of this "
            "pre-protocol rework outside the runtime, or publish a valid review "
            "artifact with a new review disposition"
        )
    encoded = content.encode("utf-8")
    if len(encoded) > MAX_REVIEW_BYTES:
        raise ReviewProvenanceError(
            f"curator review exceeds the {MAX_REVIEW_BYTES}-byte prompt limit"
        )
    if publication is None:
        raise ReviewProvenanceError(
            "curator review publication is ambiguous or mutable"
        )
    try:
        publication_changes = set(commit_changes(repo, publication, runner=runner))
    except GitObservationError as error:
        raise ReviewProvenanceError(
            "cannot inspect curator review publication"
        ) from error
    match = _HEADER.match(content)
    if match is None and not content.startswith("# REWORK\n"):
        raise ReviewProvenanceError(
            "curator review artifact has a malformed attribution header"
        )
    if publication != transition_publication or publication != transitions[0]:
        raise ReviewProvenanceError(
            "curator review was not published with its review disposition"
        )
    if publication_changes - {
        f"A\t{artifact}",
        f"D\t{workflow_paths['review']}/{report.ticket_id}.md",
        f"A\t{workflow_paths['todo']}/{report.ticket_id}.md",
        f"A\t{workflow_paths['backlog']}/{report.ticket_id}.md",
    }:
        raise ReviewProvenanceError(
            "curator review publication contains unrelated changes"
        )
    expected = {
        "ticket": report.ticket_id,
        "execution": report.execution_id,
        "checkpoint": report.workspace_head,
        "control": report.control_head,
        "disposition": "rework",
    }
    if match is not None and match.groupdict() != expected:
        raise ReviewProvenanceError(
            "curator review attribution does not match the reviewed execution"
        )
    try:
        if not is_ancestor(repo, lifecycle, publication, runner=runner):
            raise ReviewProvenanceError(
                "curator review predates its reviewed execution"
            )
    except (GitAncestryError, GitObservationError) as error:
        raise ReviewProvenanceError("cannot verify curator review ancestry") from error
    return ReviewBinding(
        content,
        artifact,
        publication,
        transitions,
        hashlib.sha256(encoded).hexdigest(),
    )


__all__ = [
    "MAX_REVIEW_BYTES",
    "ReviewBinding",
    "ReviewProvenanceError",
    "restore_review_binding",
    "resolve_rework_review",
    "resolve_amended_rework_review",
]
