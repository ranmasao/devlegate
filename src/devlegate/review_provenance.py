# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bind independent curator feedback to one reviewed execution generation."""

from __future__ import annotations

import hashlib
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from devlegate.execution_result import ExecutionReport
from devlegate.git import (
    GitAncestryError,
    GitObservationError,
    commit_changes,
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
    r"disposition: (?P<disposition>rework)\n-->\n",
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
) -> ReviewBinding:
    """Validate a persisted immutable binding before reusing its prompt data."""
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
    if not isinstance(transition, list) or not (1 <= len(transition) <= 2):
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
    match = _HEADER.match(content)
    if (
        match is None
        or match.groupdict()
        != {
            "ticket": ticket_id,
            "execution": execution_id,
            "checkpoint": checkpoint,
            "control": control_head,
            "disposition": "rework",
        }
    ):
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
    todo_path: str,
    review_path: str,
    backlog_path: str,
    artifact_path: str,
    runner: Runner,
) -> tuple[tuple[str, ...], str | None]:
    review = f"{review_path}/{ticket_id}.md"
    todo = f"{todo_path}/{ticket_id}.md"
    backlog = f"{backlog_path}/{ticket_id}.md"
    try:
        commits = commit_range(
            repo, f"{lifecycle}..{head}", reverse=True, runner=runner
        )
        direct: tuple[str, ...] | None = None
        deferred: str | None = None
        artifact_commit: str | None = None
        for commit in commits:
            changes = set(commit_changes(repo, commit, runner=runner))
            if f"A\t{artifact_path}" in changes:
                if artifact_commit is not None:
                    raise ReviewProvenanceError(
                        "review artifact publication is ambiguous"
                    )
                artifact_commit = commit
            workflow = {item for item in changes if item.endswith(f"/{ticket_id}.md")}
            allowed_extra = (
                {f"A\t{artifact_path}"} if artifact_commit == commit else set()
            )
            if (
                workflow == {f"D\t{review}", f"A\t{todo}"}
                and changes == workflow | allowed_extra
            ):
                if direct is not None or deferred is not None:
                    raise ReviewProvenanceError(
                        "review disposition history is conflicting"
                    )
                if not _same_blob(repo, lifecycle, review, commit, todo, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed during rework disposition"
                    )
                direct = (commit,)
            elif (
                workflow == {f"D\t{review}", f"A\t{backlog}"}
                and changes == workflow | allowed_extra
            ):
                if direct is not None or deferred is not None:
                    raise ReviewProvenanceError(
                        "review disposition history is conflicting"
                    )
                if not _same_blob(repo, lifecycle, review, commit, backlog, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed during rework deferral"
                    )
                deferred = commit
            elif workflow == {f"D\t{backlog}", f"A\t{todo}"} and changes == workflow:
                if deferred is None or direct is not None:
                    raise ReviewProvenanceError(
                        "review backlog disposition is malformed"
                    )
                if not _same_blob(repo, deferred, backlog, commit, todo, runner):
                    raise ReviewProvenanceError(
                        "ticket specification changed after rework deferral"
                    )
                direct = (deferred, commit)
        if direct is None:
            raise ReviewProvenanceError(
                "reviewed execution has no proven rework transition"
            )
        return direct, artifact_commit
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
    todo_path: str,
    review_path: str,
    backlog_path: str,
    runner: Runner,
) -> ReviewBinding:
    """Resolve the immutable review bound to one rejected execution."""
    artifact = f"reviews/{report.ticket_id}/{report.execution_id}.md"
    transitions, transition_publication = _transition(
        repo,
        head,
        lifecycle,
        report.ticket_id,
        todo_path=todo_path,
        review_path=review_path,
        backlog_path=backlog_path,
        artifact_path=artifact,
        runner=runner,
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
    if match is None:
        raise ReviewProvenanceError(
            "curator review artifact has a malformed attribution header"
        )
    if publication != transition_publication or publication != transitions[0]:
        raise ReviewProvenanceError(
            "curator review was not published with its review disposition"
        )
    if publication_changes - {
        f"A\t{artifact}",
        f"D\t{review_path}/{report.ticket_id}.md",
        f"A\t{todo_path}/{report.ticket_id}.md",
        f"A\t{backlog_path}/{report.ticket_id}.md",
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
    if match.groupdict() != expected:
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
]
