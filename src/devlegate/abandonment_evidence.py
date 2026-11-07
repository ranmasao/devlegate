# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-only, content-addressed evidence with bounded, Git-free byte paging.

This is a library boundary, not a worker API. Only its manifest and read results
may cross the evidence-only execution boundary. It grants no disposal authority.
Large objects are streamed from immutable Git identities, never mirrored into
control history or a persistent bundle. No agent-supplied path or ref is used.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from types import MappingProxyType

from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport, ExecutionReportError
from devlegate.git import GitAncestryError, GitObservationError
from devlegate.review_provenance import ReviewProvenanceError, resolve_rework_review
from devlegate.tickets import Ticket, TicketError, parse_ticket_text

MAX_EXCERPT_BYTES = 256 * 1024
MAX_INLINE_BYTES = 4 * 1024 * 1024
MAX_READ_BYTES = 256 * 1024
_OID = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})\Z")


class EvidenceError(ValueError):
    """Evidence cannot be read with the admitted identity and bounds."""


def _canonical(value: object) -> bytes:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()


@dataclass(frozen=True)
class GitArtifact:
    """Closed owner-created source identity; paths are labels, never read targets."""

    kind: str
    objects: tuple[str, ...]
    repository: str = "product"

    def __post_init__(self) -> None:
        count = {"blob": 1, "diff": 2}.get(self.kind)
        if self.repository not in {"product", "control"}:
            raise EvidenceError("invalid artifact repository")
        if count is None or len(self.objects) != count or not all(
            _OID.fullmatch(item) for item in self.objects
        ):
            raise EvidenceError("invalid immutable artifact identity")

    def as_dict(self) -> dict[str, object]:
        return {
            "kind": self.kind, "objects": list(self.objects),
            "repository": self.repository,
        }


class OwnerGitEvidence:
    """Streaming source used solely by the serialized repository owner.

    The caller must supply its actual owner assertion, including on subsequent
    reads. This class must never be passed into the worker process.
    """

    def __init__(self, repo: Path, assert_owner: Callable[[], None]) -> None:
        self._repo = repo.resolve(strict=True)
        self._assert_owner = assert_owner

    def _command(self, *args: str) -> list[str]:
        # Disable ambient replace refs, external diffs, textconv, pagers and
        # user configuration. Full object IDs are the only source selectors.
        return [
            "git", "--no-replace-objects", "--no-pager", "--literal-pathspecs",
            "-c", "core.attributesFile=/dev/null", "-c", "core.hooksPath=/dev/null",
            "-C", str(self._repo), *args,
        ]

    def _environment(self) -> dict[str, str]:
        return {
            "PATH": os.defpath, "LC_ALL": "C", "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": "/dev/null", "GIT_TERMINAL_PROMPT": "0",
            "GIT_NO_LAZY_FETCH": "1", "GIT_OPTIONAL_LOCKS": "0",
        }

    def scan(
        self, artifact: GitArtifact, *, offset: int = 0, length: int = 0
    ) -> tuple[int, str, bytes]:
        """Hash the entire source while retaining only the selected byte range."""
        self._assert_owner()
        if (
            type(offset) is not int or type(length) is not int
            or offset < 0 or length < 0 or length > MAX_READ_BYTES
        ):
            raise EvidenceError("invalid scan byte range")
        if artifact.kind == "blob":
            args = ("cat-file", "blob", artifact.objects[0])
        else:
            args = (
                "diff", "--no-ext-diff", "--no-textconv", "--no-color",
                "--src-prefix=a/", "--dst-prefix=b/", "--full-index", "--no-renames",
                "--diff-algorithm=myers", "--unified=3", *artifact.objects, "--",
            )
        digest = hashlib.sha256()
        selected = bytearray()
        size = 0
        with subprocess.Popen(
            self._command(*args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=self._environment(),
        ) as process:
            assert process.stdout is not None
            while chunk := process.stdout.read(64 * 1024):
                digest.update(chunk)
                start = max(0, offset - size)
                stop = min(len(chunk), offset + length - size)
                if stop > start:
                    selected.extend(chunk[start:stop])
                size += len(chunk)
            if process.wait() != 0:
                raise EvidenceError("immutable Git artifact is absent or unreadable")
        return size, digest.hexdigest(), bytes(selected)

    def small(self, *args: str, limit: int = MAX_INLINE_BYTES) -> bytes:
        """Bound control/index observations independently of excerpt budgets."""
        result = self._small_result(*args, limit=limit)
        if result.returncode != 0:
            raise EvidenceError("cannot verify Git evidence observation")
        return result.stdout

    def _small_result(
        self, *args: str, limit: int = MAX_INLINE_BYTES,
    ) -> subprocess.CompletedProcess[bytes]:
        self._assert_owner()
        with subprocess.Popen(
            self._command(*args), stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
            env=self._environment(),
        ) as process:
            assert process.stdout is not None
            value = process.stdout.read(limit + 1)
            if len(value) > limit:
                process.kill()
                process.wait()
                raise EvidenceError("control/index observation exceeds its byte limit")
            return subprocess.CompletedProcess(args, process.wait(), value, b"")

    def _runner(
        self, repo: Path, *args: str, check: bool = True,
    ) -> subprocess.CompletedProcess[str]:
        """Adapt bounded observations to existing read-only provenance helpers."""
        if repo.resolve() != self._repo:
            raise EvidenceError("provenance reader cannot access another repository")
        result = self._small_result(*args)
        text = subprocess.CompletedProcess(
            result.args, result.returncode, result.stdout.decode("utf-8"), ""
        )
        if check:
            text.check_returncode()
        return text

    def ancestor(self, older: str, newer: str) -> bool:
        if not all(_OID.fullmatch(value) for value in (older, newer)):
            raise EvidenceError("ancestry requires full object identities")
        self._assert_owner()
        result = subprocess.run(
            self._command("merge-base", "--is-ancestor", older, newer),
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            env=self._environment(), check=False,
        )
        if result.returncode not in {0, 1}:
            raise EvidenceError("ancestry objects are unavailable")
        return result.returncode == 0


@dataclass(frozen=True)
class ArtifactRecord:
    id: str
    source: GitArtifact
    size: int
    sha256: str
    excerpt: bytes
    omission: str | None

    def as_dict(self) -> dict[str, object]:
        try:
            text = self.excerpt.decode("utf-8")
        except UnicodeDecodeError:
            text = None
        return {
            "id": self.id, "source": self.source.as_dict(), "size": self.size,
            "sha256": self.sha256, "text": text, "inline_bytes": len(self.excerpt),
            "omission": self.omission,
        }


class EvidenceBundle:
    """Sealed manifest with an exact artifact allowlist and stateless paging."""

    def __init__(
        self, sources: dict[str, OwnerGitEvidence], metadata: dict[str, object],
        records: tuple[ArtifactRecord, ...],
    ) -> None:
        self._sources = MappingProxyType(dict(sources))
        self._records = MappingProxyType({record.id: record for record in records})
        manifest = {
            "schema": "devlegate.abandonment-evidence.v1", **metadata,
            "limits": {"excerpt": MAX_EXCERPT_BYTES, "inline": MAX_INLINE_BYTES,
                       "read": MAX_READ_BYTES},
            "artifacts": [record.as_dict() for record in records],
        }
        self._manifest = _canonical(manifest)
        self.id = hashlib.sha256(self._manifest).hexdigest()

    def manifest(self) -> dict[str, object]:
        # A caller cannot mutate the sealed manifest through a returned value.
        return {"manifest_id": self.id, **json.loads(self._manifest)}

    def read(
        self, manifest_id: str, artifact_id: str, offset: int, length: int
    ) -> dict[str, object]:
        if manifest_id != self.id or artifact_id not in self._records:
            raise EvidenceError("artifact is not authorized by this manifest")
        record = self._records[artifact_id]
        if (
            type(offset) is not int or type(length) is not int
            or offset < 0 or length < 1 or length > MAX_READ_BYTES
            or offset > record.size or length > record.size - offset
        ):
            raise EvidenceError("invalid or excessive artifact byte range")
        size, digest, value = self._sources[record.source.repository].scan(
            record.source, offset=offset, length=length
        )
        if size != record.size or digest != record.sha256:
            raise EvidenceError("artifact changed or is no longer available")
        try:
            content = value.decode("utf-8")
            encoding = "utf-8"
            if "\0" in content:
                content = base64.b64encode(value).decode("ascii")
                encoding = "base64"
        except UnicodeDecodeError:
            content = base64.b64encode(value).decode("ascii")
            encoding = "base64"
        return {
            "manifest_id": self.id, "artifact_id": artifact_id,
            "source": record.source.as_dict(), "sha256": digest,
            "offset": offset, "length": len(value), "encoding": encoding,
            "content": content, "chunk_sha256": hashlib.sha256(value).hexdigest(),
        }


class EvidenceBuilder:
    """One analysis bundle's conservative global inline budget."""

    def __init__(
        self, source: OwnerGitEvidence, *, control: OwnerGitEvidence | None = None,
    ) -> None:
        self.source = source
        self._sources = {"product": source}
        if control is not None:
            self._sources["control"] = control
        self._records: dict[GitArtifact, ArtifactRecord] = {}
        self.inline_bytes = 0

    def add(self, artifact: GitArtifact) -> str:
        previous = self._records.get(artifact)
        if previous is not None:
            return previous.id
        budget = min(MAX_EXCERPT_BYTES, MAX_INLINE_BYTES - self.inline_bytes)
        source = self._sources.get(artifact.repository)
        if source is None:
            raise EvidenceError("artifact repository is not authorized")
        # Inspect a small prefix even after exhausting the inline budget, so
        # omitted binaries/non-UTF-8 data remain explicitly identified.
        size, digest, sample = source.scan(artifact, length=max(budget, 8192))
        excerpt = sample[:budget]
        omission = None
        try:
            sample.decode("utf-8")
            binary = b"\0" in sample
        except UnicodeDecodeError as error:
            # An otherwise valid UTF-8 prefix may end inside one code point.
            binary = (
                error.end != len(sample) or error.reason != "unexpected end of data"
            )
            if not binary:
                excerpt = excerpt[:min(len(excerpt), error.start)]
        if not binary:
            try:
                excerpt.decode("utf-8")
            except UnicodeDecodeError as error:
                excerpt = excerpt[:error.start]
        if binary:
            excerpt = b""
            omission = "binary or non-UTF-8; use bounded byte reads"
        elif len(excerpt) < size:
            omission = "per-artifact or aggregate inline limit; use bounded byte reads"
        self.inline_bytes += len(excerpt)
        identity = hashlib.sha256(_canonical(artifact.as_dict())).hexdigest()
        self._records[artifact] = ArtifactRecord(
            identity, artifact, size, digest, excerpt, omission,
        )
        return identity

    def seal(self, metadata: dict[str, object]) -> EvidenceBundle:
        return EvidenceBundle(self._sources, metadata, tuple(self._records.values()))


def _tree(
    source: OwnerGitEvidence, commit: str, *paths: str,
) -> dict[str, tuple[str, str]]:
    entries: dict[str, tuple[str, str]] = {}
    for item in source.small("ls-tree", "-r", "-z", commit, "--", *paths).split(b"\0"):
        if not item:
            continue
        info, name = item.split(b"\t", 1)
        mode, _kind, oid = info.decode("ascii").split()
        entries[name.decode("utf-8", "surrogateescape")] = (mode, oid)
    return entries


def _safe_path(path: str) -> None:
    if (
        not path or path.startswith("/") or "\0" in path
        or any(part in {"", ".", ".."} for part in path.split("/"))
    ):
        raise EvidenceError("evidence path must be canonical and repository-relative")


def _delta(
    source: OwnerGitEvidence, builder: EvidenceBuilder, base: str, checkpoint: str,
) -> dict[str, object]:
    if not source.ancestor(base, checkpoint):
        raise EvidenceError("base-to-checkpoint ancestry is not proven")
    records = source.small(
        "diff-tree", "-r", "--raw", "-z", "--no-abbrev", "--no-commit-id",
        "--no-ext-diff", "--no-textconv", "-M", base, checkpoint, "--",
    ).split(b"\0")
    files: list[dict[str, object]] = []
    index = 0
    while index < len(records) and records[index]:
        header = records[index].decode("ascii").lstrip(":").split()
        old_mode, new_mode, old_oid, new_oid, status = header
        old_path = records[index + 1].decode("utf-8", "surrogateescape")
        index += 2
        new_path = old_path
        if status.startswith(("R", "C")):
            new_path = records[index].decode("utf-8", "surrogateescape")
            index += 1
        item: dict[str, object] = {
            "status": status, "old_path": old_path, "path": new_path,
            "old_mode": old_mode, "mode": new_mode,
        }
        for label, mode, oid in (
            ("before", old_mode, old_oid), ("after", new_mode, new_oid),
        ):
            if mode == "000000":
                item[label] = None
            elif mode == "160000":
                item[label] = {"submodule_commit": oid}
            elif mode == "120000":
                # Index the link blob without following its contents as a path.
                item[label] = {
                    "symlink_blob": oid,
                    "artifact": builder.add(GitArtifact("blob", (oid,))),
                }
            else:
                try:
                    item[label] = builder.add(GitArtifact("blob", (oid,)))
                except EvidenceError as error:
                    item[label] = {"gap": str(error), "object": oid}
        files.append(item)
    return {
        "base": base, "checkpoint": checkpoint,
        "diff": builder.add(GitArtifact("diff", (base, checkpoint))), "files": files,
    }


def build_abandonment_evidence(
    *, control: OwnerGitEvidence, product: OwnerGitEvidence,
    control_head: str, request: Ticket, target: Ticket,
    request_path: str, target_path: str,
    todo_path: str = "kanban/todo", review_path: str = "kanban/review",
    backlog_path: str = "kanban/backlog",
) -> EvidenceBundle:
    """Extract chronological evidence, preserving uncertainty as explicit gaps.

    The owner must admit the exact request/target blobs before calling this
    function. This extractor does not reserve a target, approve a request, prove
    current resource ownership, or authorize retirement. Those are distinct
    service-owner transitions. Resource and generation proof is deliberately
    reported as a gap here, never inferred from a ref name or matching SHA.
    """
    if (
        request.type != "devlegate.abandonment" or request.target != target.id
        or target.type != "devlegate.ticket" or request.start_head is None
        or request.abandonment_reason is None
        or not _OID.fullmatch(control_head)
    ):
        raise EvidenceError("request is not a validated abandonment specification")
    for path in (request_path, target_path, todo_path, review_path, backlog_path):
        _safe_path(path)
    builder = EvidenceBuilder(product, control=control)
    gaps: list[str] = [
        "current owned-resource observation and generation binding not supplied; "
        "this bundle cannot authorize cleanup",
    ]
    try:
        if product.small("cat-file", "-t", request.start_head).strip() != b"commit":
            raise EvidenceError("initial product HEAD is not a commit")
        start_head_status = "verified commit"
    except EvidenceError as error:
        start_head_status = f"gap: {error}"
        gaps.append(f"initial product HEAD unavailable or unproven: {error}")
    commits = control.small(
        "rev-list", "--reverse", "--first-parent", control_head
    ).decode("ascii").splitlines()
    previous = None
    publications: dict[str, tuple[str, str]] = {}
    versions: dict[str, set[str]] = {}
    interrupted: set[str] = set()
    report_prefix = f"executions/{target.id}/"
    review_prefix = f"reviews/{target.id}/"
    for commit in commits:
        parents = control.small(
            "rev-list", "--parents", "-n", "1", commit
        ).decode().split()
        if parents[1:] != ([previous] if previous else []):
            raise EvidenceError("control chronology is not a proven linear history")
        previous = commit
        tree = _tree(control, commit, report_prefix, review_prefix)
        interrupted.update(
            path for path in publications
            if path not in tree or tree[path][0] != "100644"
        )
        for path, (mode, oid) in sorted(tree.items()):
            if mode != "100644":
                gaps.append(f"non-regular control artifact: {path} at {commit}")
                continue
            publications.setdefault(path, (commit, oid))
            versions.setdefault(path, set()).add(oid)
    final_tree = _tree(control, control_head, report_prefix, review_prefix)

    def text_artifact(path: str, publication: str, oid: str) -> dict[str, object]:
        return {
            "path": path, "publication": publication, "object": oid,
            "artifact": builder.add(GitArtifact("blob", (oid,), "control")),
        }

    specs = _tree(control, control_head, request_path, target_path)
    if set(specs) != {request_path, target_path} or any(
        mode != "100644" for mode, _oid in specs.values()
    ):
        raise EvidenceError("admitted request and target must be regular Git blobs")
    for ticket, path in ((request, request_path), (target, target_path)):
        try:
            parsed = parse_ticket_text(
                ticket.path, ticket.state,
                control.small("cat-file", "blob", specs[path][1]).decode("utf-8"),
            )
        except (TicketError, UnicodeError) as error:
            raise EvidenceError("admitted specification is invalid") from error
        if parsed != ticket:
            raise EvidenceError("ticket does not match the exact admitted control blob")
    specification = {
        path: text_artifact(path, control_head, specs[path][1])
        for path in (request_path, target_path)
    }
    executions: list[dict[str, object]] = []
    latest: str | None = None
    # Dict insertion order is publication order along the proven control chain,
    # with path order only as a tie-breaker for artifacts in the same commit.
    for path, (publication, oid) in publications.items():
        if not path.startswith(report_prefix) or not path.endswith(".json"):
            continue
        entry: dict[str, object] = {"report": text_artifact(path, publication, oid)}
        executions.append(entry)
        if (
            path in interrupted or len(versions[path]) != 1
            or final_tree.get(path) != ("100644", oid)
        ):
            entry["gap"] = "execution report changed or disappeared after publication"
            latest = None
            continue
        try:
            raw = control.small("cat-file", "blob", oid)
            report = ExecutionReport.from_dict(json.loads(raw))
            if (
                path != f"{report_prefix}{report.execution_id}.json"
                or report.ticket_id != target.id
                or report.control_head not in commits[:commits.index(publication)]
            ):
                raise EvidenceError("execution admission identity/lineage is unproven")
            if not lifecycle_commit_is_exact(
                control._repo, publication, report,
                todo_path=todo_path, review_path=review_path, runner=control._runner,
            ):
                raise EvidenceError("execution publication is not an exact lifecycle")
            entry.update({
                "execution_id": report.execution_id,
                "code_base_head": report.code_base_head,
                "checkpoint": report.workspace_head,
                "resource_generation_claim": report.resource_generation,
                "conclusion": report.result.conclusion,
            })
            admitted_spec_path = f"{todo_path}/{target.id}.md"
            admitted_spec = _tree(control, report.control_head, admitted_spec_path)
            if admitted_spec.get(admitted_spec_path, (None,))[0] == "100644":
                entry["admitted_specification"] = text_artifact(
                    admitted_spec_path, report.control_head,
                    admitted_spec[admitted_spec_path][1],
                )
            else:
                entry["specification_gap"] = "admitted target specification is absent"
            if report.report_artifact is not None:
                semantic = publications.get(report.report_artifact)
                if semantic is None:
                    entry["semantic_report_gap"] = "semantic report is absent"
                else:
                    semantic_revision, semantic_oid = semantic
                    entry["semantic_report"] = text_artifact(
                        report.report_artifact, semantic_revision, semantic_oid
                    )
                    if (
                        report.report_artifact in interrupted
                        or len(versions[report.report_artifact]) != 1
                        or final_tree.get(report.report_artifact)
                        != ("100644", semantic_oid)
                    ):
                        entry["semantic_report_gap"] = "semantic report changed/absent"
            review_artifact = f"{review_prefix}{report.execution_id}.md"
            review_publication = publications.get(review_artifact)
            if review_publication is not None:
                revision, review_oid = review_publication
                entry["review"] = text_artifact(review_artifact, revision, review_oid)
                if (
                    review_artifact in interrupted
                    or len(versions[review_artifact]) != 1
                    or final_tree.get(review_artifact) != ("100644", review_oid)
                    or commits.index(revision) <= commits.index(publication)
                ):
                    entry["review_gap"] = "review changed, absent or predates report"
                else:
                    try:
                        # Stop at the next execution's admission control head,
                        # before later rework cycles can confound this review.
                        next_admission = control_head
                        for later_path, later_publication in publications.items():
                            _later_commit, later_oid = later_publication
                            if (
                                later_path.startswith(report_prefix)
                                and later_path.endswith(".json")
                                and commits.index(_later_commit)
                                > commits.index(publication)
                            ):
                                later_report = ExecutionReport.from_dict(json.loads(
                                    control.small("cat-file", "blob", later_oid)
                                ))
                                next_admission = later_report.control_head
                                break
                        binding = resolve_rework_review(
                            control._repo, next_admission, publication, report,
                            todo_path=todo_path, review_path=review_path,
                            backlog_path=backlog_path, runner=control._runner,
                        )
                        entry["review_decision"] = "rework"
                        entry["review_binding"] = {
                            "publication": binding.publication,
                            "sha256": binding.sha256,
                            "transition": list(binding.transition),
                        }
                    except (ReviewProvenanceError, EvidenceError) as error:
                        entry["review_gap"] = str(error)
            else:
                entry["review_gap"] = "no bound curator review available"
            if report.workspace_head is None:
                raise EvidenceError("execution has no checkpoint; no older fallback")
            entry["delta"] = _delta(
                product, builder, report.code_base_head, report.workspace_head
            )
            latest = report.workspace_head
        except (
            EvidenceError, ExecutionReportError, UnicodeError, json.JSONDecodeError,
            GitObservationError, GitAncestryError, subprocess.SubprocessError,
        ) as error:
            entry["gap"] = str(error)
            latest = None
    comparison: dict[str, object]
    if latest is None:
        comparison = {"gap": "latest execution checkpoint is not proven or unavailable"}
    else:
        try:
            comparison = _delta(product, builder, request.start_head, latest)
            comparison["label"] = (
                "initial-to-latest state comparison, NOT cumulative ticket contribution"
            )
        except EvidenceError as error:
            comparison = {"gap": str(error)}
    return builder.seal({
        "target": target.id, "request": request.id,
        # The reason lives in the indexed specification. Do not duplicate an
        # arbitrarily large reason outside the aggregate inline byte budget.
        "reason": {
            "specification": request_path, "section": "Abandonment reason",
            "sha256": hashlib.sha256(
                request.abandonment_reason.encode("utf-8")
            ).hexdigest(),
        },
        "start_head": request.start_head,
        "start_head_status": start_head_status,
        "control_head": control_head, "specifications": specification,
        "executions": executions, "latest_state_comparison": comparison, "gaps": gaps,
    })
