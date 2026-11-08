# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Exact, replayable owner publication and independent abandonment review.

Trees are constructed without modifying the control index or checkout. The
owner journals the complete transaction before publishing its commit by lease.
An analyst claim is only a proposal; it confers no terminal authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path, PurePosixPath

from devlegate.abandonment_admission import AbandonmentAdmission
from devlegate.abandonment_evidence import EvidenceError
from devlegate.execution_result import ExecutionReport
from devlegate.git import (
    commit_changes,
    commit_parents,
    file_history_commit,
    history_commits,
    is_ancestor,
    path_object,
    read_path,
)
from devlegate.review_provenance import MAX_REVIEW_BYTES


def encoded(value: object) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, indent=2) + "\n"


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def change_digests(changes: dict[str, str | None]) -> dict[str, str | None]:
    """Keep bounded identities in the owner journal, not a second evidence copy."""
    return {path: digest(content) if content is not None else None
            for path, content in changes.items()}


def proposal_files(admission: dict, report: ExecutionReport, manifest: str,
                   manifest_sha256: str, manifest_id: str) -> dict[str, str]:
    """Bind the immutable report, source specifications and full bounded index."""
    AbandonmentAdmission.restore(admission)
    ExecutionReport.from_dict(report.as_dict())
    if (
        report.ticket_id != admission["request_id"]
        or report.execution_id != admission["execution_id"]
        or report.control_head != admission["control_head"]
        or report.code_base_head != admission["product_head"]
        or report.workspace_head is not None or report.execution_branch
        or not isinstance(manifest, str) or digest(manifest) != manifest_sha256
    ):
        raise EvidenceError("analysis publication source binding differs")
    index = json.loads(manifest)
    if index.get("manifest_id") != manifest_id or index.get("admission") != admission:
        raise EvidenceError("analysis publication manifest binding differs")
    root = f"executions/{report.ticket_id}/{report.execution_id}"
    proposal = f"abandonment-proposals/{report.ticket_id}/{report.execution_id}"
    files = {f"{root}.json": report.to_json(), f"{proposal}.manifest.json": manifest}
    if report.result.claim is not None and report.result.claim.report is not None:
        files[f"{root}.md"] = report.result.claim.report
    files[f"{proposal}.json"] = encoded({
        "schema": "devlegate.abandonment-proposal.v1",
        "admission": admission,
        "manifest_id": manifest_id, "manifest_sha256": manifest_sha256,
        "execution_sha256": digest(report.to_json()),
        "proposal_sha256": report.report_sha256,
        "conclusion": report.result.conclusion,
    })
    return files


def make_transaction(repo: Path, parent: str, changes: dict[str, str | None],
                     message: str, runner) -> dict:
    """Build an isolated tree, leaving the live index/worktree untouched."""
    def git(*args, input_text=None):
        result = runner(repo, *args, input_text=input_text, check=False)
        if result.returncode:
            raise EvidenceError(result.stderr.strip() or "control tree creation failed")
        return result.stdout.strip()

    edits: dict = {}
    for path, content in changes.items():
        parts = PurePosixPath(path).parts
        if not parts or str(PurePosixPath(path)) != path or any(
            not re.fullmatch(r"[A-Za-z0-9._-]+", part) or part in {".", "..", ".git"}
            for part in parts
        ):
            raise EvidenceError("unsafe abandonment publication path")
        node = edits
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = content

    def tree(original: str | None, node: dict) -> str:
        entries = {}
        if original is not None:
            output = runner(repo, "ls-tree", "-z", original, check=False)
            if output.returncode:
                raise EvidenceError("cannot observe publication tree")
            for record in output.stdout.split("\0"):
                if record:
                    identity, name = record.split("\t", 1)
                    entries[name] = identity
        for name, value in node.items():
            previous = entries.get(name)
            if isinstance(value, dict):
                if previous is not None and not previous.startswith("040000 tree "):
                    raise EvidenceError("publication parent is not a directory")
                oid = tree(previous.split()[2] if previous else None, value)
                entries[name] = f"040000 tree {oid}"
            elif value is None:
                if previous is None:
                    raise EvidenceError("publication source disappeared")
                del entries[name]
            else:
                if previous is not None:
                    raise EvidenceError("immutable publication destination exists")
                oid = git("hash-object", "-w", "--stdin", input_text=value)
                entries[name] = f"100644 blob {oid}"
        material = "".join(f"{identity}\t{name}\0"
                           for name, identity in sorted(entries.items()))
        return git("mktree", "-z", input_text=material)

    root = tree(f"{parent}^{{tree}}", edits)
    commit = git("commit-tree", root, "-p", parent, "-m", message)
    transaction = {"parent": parent, "commit": commit,
                   "changes": change_digests(changes)}
    verify_transaction(repo, transaction, runner)
    return transaction


def verify_transaction(repo: Path, transaction: dict, runner) -> None:
    if not isinstance(transaction, dict) or set(transaction) != {
        "parent", "commit", "changes",
    } or not isinstance(transaction["changes"], dict):
        raise EvidenceError("invalid abandonment publication transaction")
    parent, commit = transaction["parent"], transaction["commit"]
    if commit_parents(repo, commit, runner=runner) != (parent,):
        raise EvidenceError("abandonment publication parent differs")
    expected = set()
    for path, content_digest in transaction["changes"].items():
        old = read_path(repo, parent, path, runner=runner)
        new = read_path(repo, commit, path, runner=runner)
        if content_digest is None:
            if old is None or new is not None:
                raise EvidenceError("abandonment publication deletion differs")
            expected.add(f"D\t{path}")
        else:
            if old is not None or new is None or digest(new) != content_digest:
                raise EvidenceError("abandonment publication content differs")
            expected.add(f"A\t{path}")
    if set(commit_changes(repo, commit, runner=runner)) != expected:
        raise EvidenceError("abandonment publication contains unrelated changes")


def first_parent_contains(repo: Path, ancestor: str, head: str, runner) -> bool:
    result = runner(repo, "rev-list", "--first-parent", "--max-count=10001", head,
                    check=False)
    if not result.returncode and ancestor in result.stdout.splitlines():
        return True
    if result.returncode or len(result.stdout.splitlines()) > 10000:
        raise EvidenceError(
            "control first-parent proof exceeds bounded recovery history"
        )
    return False


def reconcile_transaction(repo: Path, transaction: dict, head: str, runner) -> dict:
    """Explicit owner decision only; retain old journal and never rebase silently."""
    verify_transaction(repo, transaction, runner)
    if first_parent_contains(repo, transaction["commit"], head, runner):
        return transaction
    parent = transaction["parent"]
    if not first_parent_contains(repo, parent, head, runner):
        raise EvidenceError(
            "publication parent is not in first-parent control ancestry"
        )
    changed = runner(repo, "log", "--format=%H", "--max-count=1",
                     f"{parent}..{head}", "--", *transaction["changes"], check=False)
    if changed.returncode or changed.stdout.strip():
        raise EvidenceError(
            "admitted publication paths changed; restore the exact admitted decision "
            "through independent control review before authorizing recovery"
        )
    if head == parent:
        return transaction
    changes = {path: (read_path(repo, transaction["commit"], path, runner=runner)
                      if content is not None else None)
               for path, content in transaction["changes"].items()}
    replacement = make_transaction(
        repo, head, changes, "Explicitly reconcile abandonment publication", runner,
    )
    if replacement["changes"] != transaction["changes"]:
        raise EvidenceError("authorized publication contents changed")
    return replacement


def verify_proposal(repo: Path, head: str, publication: dict,
                    files: dict[str, str], runner) -> None:
    verify_transaction(repo, publication, runner)
    if any(publication["changes"].get(path) != digest(content)
           for path, content in files.items()):
        raise EvidenceError("published analysis differs from retained result")
    if not first_parent_contains(repo, publication["commit"], head, runner):
        raise EvidenceError("analysis publication is not in current control history")
    for path, content in files.items():
        if read_path(repo, head, path, runner=runner) != content or (
            history_commits(repo, head, path, runner=runner)
            != (publication["commit"],)
        ):
            raise EvidenceError("immutable analysis publication changed")


def review_header(admission: dict, publication: str, manifest_id: str,
                  proposal_sha256: str | None, disposition: str) -> str:
    """Public protocol shared by the curator instructions and provenance gate."""
    return (
        "<!-- devlegate-abandonment-review-v1\n"
        f"ticket: {admission['request_id']}\n"
        f"execution: {admission['execution_id']}\n"
        f"control: {admission['control_head']}\n"
        f"publication: {publication}\n"
        f"manifest: {manifest_id}\n"
        f"proposal: {proposal_sha256 or 'none'}\n"
        f"disposition: {disposition}\n-->\n"
    )


def resolve_review(repo: Path, head: str, admission: dict, publication: str,
                   manifest_id: str, report: ExecutionReport, *, review_path: str,
                   todo_path: str, accepted_path: str, runner) -> dict | None:
    """Require an immutable curator artifact and exact same-blob disposition."""
    request = admission["request_id"]
    artifact = f"reviews/{request}/{admission['execution_id']}.md"
    content = read_path(repo, head, artifact, runner=runner)
    history = history_commits(repo, head, artifact, runner=runner)
    if content is None and not history:
        return None
    if content is None or len(content.encode("utf-8")) > MAX_REVIEW_BYTES or (
        len(history) != 1
    ):
        raise EvidenceError("abandonment reviewer artifact is missing or mutable")
    commit = file_history_commit(repo, head, artifact, runner=runner)
    if commit is None:
        raise EvidenceError("abandonment review publication is unavailable")
    parents = commit_parents(repo, commit, runner=runner)
    if parents is None or len(parents) != 1 or not is_ancestor(
        repo, publication, parents[0], runner=runner,
    ):
        raise EvidenceError("abandonment review predates analysis publication")
    disposition = next((kind for kind in ("accept", "rework") if content.startswith(
        review_header(admission, publication, manifest_id, report.report_sha256, kind)
    )), None)
    if disposition is None:
        raise EvidenceError(
            "abandonment review attribution differs from exact proposal"
        )
    if disposition == "accept" and report.result.conclusion != "completed":
        raise EvidenceError(
            "failed or unfinished abandonment analysis cannot be accepted"
        )
    source = f"{review_path}/{request}.md"
    board = accepted_path if disposition == "accept" else todo_path
    destination = f"{board}/{request}.md"
    if set(commit_changes(repo, commit, runner=runner)) != {
        f"A\t{artifact}", f"D\t{source}", f"A\t{destination}",
    }:
        raise EvidenceError("abandonment review disposition is not an exact transition")
    original = admission["request"]["blob"]
    if path_object(repo, parents[0], source, runner=runner) != original or (
        path_object(repo, commit, destination, runner=runner) != original
        or path_object(repo, head, destination, runner=runner) != original
    ):
        raise EvidenceError("abandonment review changed the admitted request")
    return {"path": artifact, "content": content, "sha256": digest(content),
            "commit": commit, "disposition": disposition}


def terminal_changes(repo: Path, head: str, admission: dict, report: ExecutionReport,
                     publication: str, manifest_id: str, review: dict, *,
                     accepted_path: str, done_path: str, abandoned_path: str,
                     runner) -> dict[str, str | None]:
    if review["disposition"] != "accept" or report.result.conclusion != "completed":
        raise EvidenceError("terminal abandonment requires independent acceptance")
    if report.result.claim is None or report.result.claim.report is None:
        raise EvidenceError("terminal abandonment requires a substantive proposal")
    request, target = admission["request_id"], admission["target_id"]
    source = f"{accepted_path}/{request}.md"
    target_source = admission["target"]["path"]
    for path, identity in ((source, admission["request"]["blob"]),
                           (target_source, admission["target"]["blob"])):
        if path_object(repo, head, path, runner=runner) != identity:
            raise EvidenceError("terminal abandonment source identity changed")
    canonical = (
        "<!-- devlegate-abandonment-v1\n"
        f"request: {request}\nexecution: {admission['execution_id']}\n"
        f"target: {target}\ncontrol: {admission['control_head']}\n"
        f"publication: {publication}\nmanifest: {manifest_id}\n"
        f"proposal: {report.report_sha256}\n"
        f"review: {review['commit']}\nreview-sha256: {review['sha256']}\n-->\n\n"
        + report.result.claim.report
    )
    return {
        source: None, f"{done_path}/{request}.md": read_path(repo, head, source,
                                                                  runner=runner),
        target_source: None,
        f"{abandoned_path}/{target}.md": read_path(repo, head, target_source,
                                                  runner=runner),
        f"abandonments/{target}/{admission['execution_id']}.md": canonical,
    }


def verify_terminal(repo: Path, head: str, admission: dict, report: ExecutionReport,
                    publication: dict, files: dict[str, str], manifest_id: str, *,
                    review_path: str, todo_path: str, accepted_path: str,
                    done_path: str, abandoned_path: str, runner) -> None:
    """Reconstruct cleanup authority from reviewed canonical control evidence."""
    terminal = publication["terminal"]
    verify_transaction(repo, terminal, runner)
    parent = terminal["parent"]
    verify_proposal(repo, head, publication["proposal"], files, runner)
    review = resolve_review(
        repo, parent, admission, publication["proposal"]["commit"], manifest_id,
        report, review_path=review_path, todo_path=todo_path,
        accepted_path=accepted_path, runner=runner,
    )
    if review is None:
        raise EvidenceError("terminal abandonment has no independent review")
    expected = terminal_changes(
        repo, parent, admission, report, publication["proposal"]["commit"],
        manifest_id, review, accepted_path=accepted_path, done_path=done_path,
        abandoned_path=abandoned_path, runner=runner,
    )
    if terminal["changes"] != change_digests(expected) or not first_parent_contains(
        repo, terminal["commit"], head, runner,
    ):
        raise EvidenceError("terminal abandonment does not match reviewed disposition")
    for path, content in expected.items():
        if read_path(repo, head, path, runner=runner) != content:
            raise EvidenceError("canonical terminal abandonment state changed")
        if content is not None and history_commits(repo, head, path, runner=runner) != (
            terminal["commit"],
        ):
            raise EvidenceError("canonical abandonment publication is mutable")
    review_history = history_commits(repo, head, review["path"], runner=runner)
    if review_history != (review["commit"],):
        raise EvidenceError("terminal abandonment review was altered")
    if any(history_commits(repo, f"{admission['control_head']}..{head}", prefix,
                           runner=runner) for prefix in (
        f"executions/{admission['target_id']}", f"reviews/{admission['target_id']}",
    )):
        raise EvidenceError("target execution history changed after admission")
