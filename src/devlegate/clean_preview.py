# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Owner-serialized informational snapshots, never resource disposal authority."""

from __future__ import annotations

import dataclasses
import hashlib
import json
import os
import re
import stat
import subprocess
from pathlib import Path

from devlegate.abandonment_evidence import (
    EvidenceError,
    OwnerGitEvidence,
    _review_disposition,
)
from devlegate.abandonment_publication import proposal_files, verify_terminal
from devlegate.execution_provenance import lifecycle_commit_is_exact
from devlegate.execution_result import ExecutionReport
from devlegate.execution_workspace import (
    ExecutionWorkspaceError,
    ExecutionWorkspaceManager,
)
from devlegate.git import remote_ref, resolve_ref_commit
from devlegate.resource_binding import ResourceBinding, _safe_path
from devlegate.tickets import load_ticket_store, parse_ticket_text

SCHEMA = "devlegate.inspect-workspace.v2"


def canonical_digest(identity: dict) -> str:
    """TASK-130 must bind this exact canonical JSON, including schema."""
    return hashlib.sha256(json.dumps(
        identity, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")).hexdigest()


def observe_git(
    repo: Path, *args: str, check: bool = True,
    input_text: str | None = None, **kwargs,
):
    """No index refresh, lazy fetch, hooks, fsmonitor, or ambient Git injection."""
    _safe_path(repo)
    environment = {
        key: value for key, value in os.environ.items()
        if not key.startswith("GIT_") or key in {
            "GIT_CONFIG_GLOBAL", "GIT_CONFIG_SYSTEM", "GIT_CONFIG_NOSYSTEM",
        }
    }
    environment.update({
        "LC_ALL": "C", "GIT_OPTIONAL_LOCKS": "0", "GIT_NO_LAZY_FETCH": "1",
        "GIT_TERMINAL_PROMPT": "0",
    })
    return subprocess.run(
        ["git", "--no-optional-locks", "--no-replace-objects", "--no-pager",
         "-c", "core.fsmonitor=false", "-c", "core.untrackedCache=false",
         "-c", "core.hooksPath=/dev/null", "-C", str(repo), *args],
        input=input_text, text=True, capture_output=True, check=check,
        env=environment, timeout=30,
        **kwargs,
    )


def _tree(repo: Path, revision: str, *paths: str) -> dict[str, str]:
    result = observe_git(repo, "ls-tree", "-r", "-z", revision, "--", *paths)
    tree = {}
    for entry in result.stdout.split("\0"):
        if not entry:
            continue
        metadata, path = entry.split("\t", 1)
        mode, kind, oid = metadata.split()
        if mode != "100644" or kind != "blob":
            raise EvidenceError(f"non-regular canonical evidence: {path}")
        tree[path] = oid
    return tree


def _blob(repo: Path, oid: str) -> str:
    return observe_git(repo, "cat-file", "blob", oid).stdout


def _history(repo: Path, head: str, path: str, oid: str) -> str:
    changes = observe_git(
        repo, "log", "--format=%H", "--full-history", head, "--", path,
    ).stdout.splitlines()
    if len(changes) != 1 or _tree(repo, changes[0], path).get(path) != oid:
        raise EvidenceError(
            f"immutable artifact changed or has ambiguous history: {path}"
        )
    return changes[0]


def _authority(owner) -> dict:
    # Bind all persisted authority, rather than guessing that an unfamiliar new
    # intent is harmless. Exclude only unrelated presentation/cursor fields.
    state = json.loads(json.dumps(owner._state))
    for key in ("workspace_cleanup", "workspace_cleanup_cursor"):
        state.pop(key, None)
    return {
        "runtime": state,
        "owned_execution_id": owner._owned_execution_id,
        "active_workers": owner._workers.active_count,
        "lifecycle": owner._lifecycle_intent,
    }


def authority_identity(authority: dict) -> dict:
    """Bind private owner state without publishing runtime internals or secrets."""
    return {
        "sha256": canonical_digest(authority),
        "mutable_receipts_present": "mutable_receipts" in authority["runtime"],
    }


def binding_identity(intent: dict) -> dict:
    """Keep exact transport identity bound without disclosing URL credentials."""
    identity = dict(intent)
    urls = identity.pop("remote_urls", None)
    identity["remote_urls_sha256"] = canonical_digest({"remote_urls": urls})
    return identity


def _working_evidence_blockers(control: Path, tree: dict[str, str]) -> list[str]:
    """Missing/unsafe working copies cannot hide immutable committed evidence."""
    blockers = []
    for relative, oid in tree.items():
        candidate = control / relative
        try:
            _safe_path(candidate)
            if not stat.S_ISREG(candidate.stat().st_mode):
                raise EvidenceError("not a regular working evidence file")
            if candidate.read_bytes() != _blob(control, oid).encode("utf-8"):
                blockers.append(f"working evidence differs: {relative}")
        except (ValueError, RuntimeError, OSError, ExecutionWorkspaceError) as error:
            blockers.append(f"working evidence unavailable: {relative}: {error}")
    return blockers


def _control_identity(owner, control: Path, head: str) -> dict:
    """A matching HEAD alone cannot identify the configured control checkout."""
    manager = ExecutionWorkspaceManager(
        owner.repo, owner.execution_worktree_root, "inspection", runner=observe_git,
    )
    registration = manager._registrations().get(control.resolve())
    common = observe_git(
        owner.repo, "rev-parse", "--path-format=absolute", "--git-common-dir",
    ).stdout.strip()
    observed_common = observe_git(
        control, "rev-parse", "--path-format=absolute", "--git-common-dir",
    ).stdout.strip()
    top = observe_git(control, "rev-parse", "--show-toplevel").stdout.strip()
    branch = observe_git(control, "symbolic-ref", "--quiet", "HEAD").stdout.strip()
    if (
        registration is None or registration.get("path") != str(control)
        or registration.get("head") != head
        or registration.get("branch") != owner.control_branch
        or "locked" in registration or "prunable" in registration
        or common != observed_common or top != str(control)
        or branch != f"refs/heads/{owner.control_branch}"
    ):
        raise EvidenceError("control checkout has foreign or conflicting registration")
    admin = Path(observe_git(control, "rev-parse", "--absolute-git-dir").stdout.strip())
    for root in (admin, Path(common)):
        _safe_path(root)
        if any(root.glob("*.lock")) or any((root / name).exists() for name in (
            "MERGE_HEAD", "CHERRY_PICK_HEAD", "REVERT_HEAD", "rebase-merge",
            "rebase-apply", "sequencer", "BISECT_LOG",
        )):
            raise EvidenceError("control Git administrative state conflicts")
    return {"path": str(control), "registration": registration, "common": common,
            "admin": str(admin)}


def _abandoned_disposition(owner, head: str, ticket_id: str) -> dict:
    """Reuse canonical terminal proof, without running cleanup or reconstruction."""
    artifacts = _tree(owner.control_worktree, head, f"abandonments/{ticket_id}")
    result = {"artifacts": artifacts, "state": "unsupported"}
    record = (owner._state.get("abandonment_retirements") or {}).get(ticket_id)
    if not isinstance(record, dict):
        result["reason"] = "abandoned disposition lacks retained independent journal"
        return result
    try:
        report = ExecutionReport.from_dict(record["analysis"]["report"])
        workspace = record["analysis"]["workspace"]
        path = (f"abandonment-proposals/{report.ticket_id}/"
                f"{report.execution_id}.manifest.json")
        manifest = _tree(owner.control_worktree, head, path)
        files = proposal_files(
            record["admission"], report,
            _blob(owner.control_worktree, manifest[path]),
            workspace["manifest_sha256"], workspace["manifest_id"],
        )
        workflow = owner._workflow_paths()
        verify_terminal(
            owner.control_worktree, head, record["admission"], report,
            record["publication"], files, workspace["manifest_id"],
            review_path=workflow["review"], todo_path=workflow["todo"],
            accepted_path=workflow["accepted"], done_path=workflow["done"],
            abandoned_path=workflow["abandoned"], runner=observe_git,
        )
        for relative, oid in artifacts.items():
            candidate = owner.control_worktree / relative
            _safe_path(candidate)
            if candidate.read_bytes() != _blob(owner.control_worktree, oid).encode():
                raise EvidenceError("working abandoned disposition differs")
        result.update(state="proved", publication=record["publication"],
                      admission=record["admission"], manifest=manifest)
    except (KeyError, ValueError, RuntimeError, OSError,
            subprocess.SubprocessError) as error:
        result["reason"] = str(error)
    return result


def inspect_clean(owner, ticket_id: str, *, _evidence_only: bool = False) -> dict:
    """Observe one exact canonical ticket under the existing service owner.

    Internal evidence-only snapshots support the reset journal after resource
    retirement. They omit resource proofs and are never admission authority;
    clean_reset must independently prove each stage's exact resource state.
    """
    owner._assert_repository_owner()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]*", ticket_id):
        raise EvidenceError("invalid canonical ticket ID")
    control = owner.control_worktree
    _safe_path(control)
    workflow = owner._workflow_paths()
    head = observe_git(control, "rev-parse", "HEAD").stdout.strip()
    branch_head = resolve_ref_commit(
        owner.repo, f"refs/heads/{owner.control_branch}", runner=observe_git,
    )
    if branch_head != head:
        raise EvidenceError("control registration HEAD differs from canonical branch")
    control_identity = _control_identity(owner, control, head)
    chain = observe_git(
        control, "rev-list", "--parents", "--first-parent", head,
    ).stdout.splitlines()
    for index, line in enumerate(chain):
        fields = line.split()
        expected = [chain[index + 1].split()[0]] if index + 1 < len(chain) else []
        if fields[1:] != expected:
            raise EvidenceError("control chronology is not a proven linear history")
    # Control cleanliness without invoking status conversion/filter machinery.
    # The committed snapshot is authoritative; dirty control is a blocker below.
    tree = _tree(control, head, *workflow.values())
    matches = [(state, f"{path}/{ticket_id}.md") for state, path in workflow.items()
               if f"{path}/{ticket_id}.md" in tree]
    if len(matches) != 1:
        raise EvidenceError("ticket has no unique canonical committed state")
    state, path = matches[0]
    ticket = parse_ticket_text(control / path, state, _blob(control, tree[path]))
    blockers = []
    identity: dict = {
        "schema": SCHEMA,
        "ticket": {"id": ticket_id, "state": state, "path": path,
                   "blob": tree[path], "control": head,
                   "depends_on": list(ticket.depends_on), "type": ticket.type},
        "workflow": dict(sorted(workflow.items())),
        "control_resource": control_identity,
        "dependency_graph": tree,
        "authority": authority_identity(_authority(owner)),
        "executions": [],
    }
    index = observe_git(
        control, "diff-index", "--cached", "--quiet", "--no-ext-diff",
        "--no-textconv", head, "--", check=False,
    )
    if index.returncode:
        blockers.append("control index differs or cannot be observed")
    untracked = observe_git(control, "ls-files", "--others", "--exclude-standard", "-z")
    if untracked.stdout:
        blockers.append("control worktree contains untracked evidence")
    identity["ticket_history"] = observe_git(
        control, "log", "--format=%H", "--name-status", "--no-renames", head,
        "--", *(f"{p}/{ticket_id}.md" for p in workflow.values()),
    ).stdout
    # Observe working control bytes with no Git conversion. Never follow links.
    blockers.extend(_working_evidence_blockers(control, tree))
    try:
        load_ticket_store(control, workflow)
    except (ValueError, OSError) as error:
        blockers.append(f"working ticket graph unavailable: {error}")
    runtime = _authority(owner)["runtime"]
    if state == "abandoned":
        disposition = _abandoned_disposition(owner, head, ticket_id)
        identity["abandoned_disposition"] = disposition
        if disposition["state"] != "proved":
            blockers.append(f"abandoned disposition unproven: {disposition['reason']}")
    if runtime.get("phase") != "idle":
        blockers.append("finish the pending execution phase before reset")
    if owner._workers.active_count or owner._owned_execution_id or runtime.get(
        "worker_identity"
    ) is not None or "worker_identity" not in runtime:
        blockers.append("worker ownership is present or not proven absent")
    for key, value in runtime.items():
        if _evidence_only and key == "clean_reset_intent":
            continue
        if value is not None and (
            any(token in key for token in (
                "pending", "reconciliation", "recovery", "retry", "retirement",
                "integration", "resume_required", "abandonment", "publication",
                "drop_intent", "reset_intent",
            ))
        ) and value not in ({}, [], False):
            blockers.append(f"resolve or verify retained runtime authority: {key}")
    if owner._lifecycle_intent is not None:
        blockers.append("service lifecycle intent is pending")

    manager = ExecutionWorkspaceManager(
        owner.repo, owner.execution_worktree_root, ticket_id, runner=observe_git,
    )
    inventory: dict = {
        "path": str(manager.path), "local_ref": f"refs/heads/{manager.branch}",
        "upstream_ref": f"{owner.remote_name}:refs/heads/{manager.branch}",
        "path_state": "unknown", "registration": None, "head": None,
        "dirty": "unknown", "locked": "unknown", "submodules": "unknown",
        "local_observed": None, "upstream_state": "unavailable",
        "local_state": "unavailable",
        "upstream_observed": None,
    }
    identity["resources"] = inventory
    try:
        if manager.path.is_symlink():
            inventory["path_state"] = "symlink"
        _safe_path(manager.path)
        inventory["path_state"] = "present" if manager.path.exists() else "absent"
        registration = manager._registrations().get(manager.path.resolve())
        inventory["registration"] = registration
        inventory["locked"] = registration is not None and "locked" in registration
        inventory["local_observed"] = resolve_ref_commit(
            owner.repo, inventory["local_ref"], runner=observe_git,
        )
        inventory["local_state"] = (
            "absent" if inventory["local_observed"] is None else "observed"
        )
        symbolic = observe_git(
            owner.repo, "symbolic-ref", "--quiet", inventory["local_ref"], check=False,
        )
        inventory["symbolic_target"] = symbolic.stdout.strip() or None
        if symbolic.returncode != 1:
            blockers.append("local execution ref is symbolic or cannot be observed")
        if registration:
            inventory["head"] = registration.get("head")
        common = observe_git(
            owner.repo, "rev-parse", "--path-format=absolute", "--git-common-dir",
        ).stdout.strip()
        administrative = []
        for root in (Path(common),):
            _safe_path(root)
            administrative.extend(
                str(p.relative_to(root)) for p in root.glob("*.lock")
            )
        inventory["administrative_locks"] = sorted(administrative)
        if administrative:
            blockers.append(
                "Git administrative locks conflict with resource inspection"
            )
        if registration and inventory["path_state"] == "present":
            admin = Path(observe_git(
                manager.path, "rev-parse", "--absolute-git-dir",
            ).stdout.strip())
            _safe_path(admin)
            conflicts = [name for name in (
                "index.lock", "HEAD.lock", "MERGE_HEAD", "CHERRY_PICK_HEAD",
                "REVERT_HEAD", "rebase-merge", "rebase-apply", "sequencer",
                "BISECT_LOG",
            ) if (admin / name).exists() or (admin / name).is_symlink()]
            inventory["administrative_state"] = conflicts
            if conflicts:
                blockers.append("finish conflicting Git administrative state")
    except (
        ValueError, RuntimeError, OSError, subprocess.SubprocessError,
        ExecutionWorkspaceError,
    ) as error:
        blockers.append(f"local inventory unavailable: {error}")
    try:
        observed = remote_ref(
            owner.repo, owner.remote_name, f"refs/heads/{manager.branch}",
            runner=observe_git,
        )
        inventory.update(upstream_observed=observed,
                         upstream_state="absent" if observed is None else "observed")
    except (RuntimeError, OSError, subprocess.SubprocessError) as error:
        blockers.append(f"upstream observation unavailable: {error}")

    reports = _tree(control, head, f"executions/{ticket_id}")
    artifacts = _tree(control, head, f"executions/{ticket_id}", f"reviews/{ticket_id}")
    identity["artifacts"] = artifacts
    blockers.extend(_working_evidence_blockers(control, artifacts))
    historical_paths = observe_git(
        control, "log", "--format=", "--name-only", head, "--",
        f"executions/{ticket_id}", f"reviews/{ticket_id}",
    ).stdout.splitlines()
    if any(p and p not in artifacts for p in historical_paths):
        blockers.append("historical immutable execution/review artifact disappeared")
    for artifact in artifacts:
        if artifact.startswith(f"reviews/{ticket_id}/") and (
            not artifact.endswith(".md") or (
                f"executions/{ticket_id}/{Path(artifact).stem}.json" not in reports
            )
        ):
            blockers.append(f"review has no corresponding immutable report: {artifact}")
    with OwnerGitEvidence(control, owner._assert_repository_owner) as source:
        for report_path, oid in sorted(reports.items()):
            if not report_path.endswith(".json"):
                continue
            entry: dict = {"path": report_path, "blob": oid}
            identity["executions"].append(entry)
            try:
                publication = _history(control, head, report_path, oid)
                report = ExecutionReport.from_dict(json.loads(_blob(control, oid)))
                if report.ticket_id != ticket_id or report_path != (
                    f"executions/{ticket_id}/{report.execution_id}.json"
                ):
                    raise EvidenceError("report identity disagrees with canonical path")
                entry.update(publication=publication, report=report.as_dict())
                if not lifecycle_commit_is_exact(
                    control, publication, report, todo_path=workflow["todo"],
                    review_path=workflow["review"], runner=observe_git,
                ):
                    raise EvidenceError("report publication is not an exact lifecycle")
                review_path = f"reviews/{ticket_id}/{report.execution_id}.md"
                review_tree = _tree(control, head, review_path)
                if review_path in review_tree:
                    review_oid = review_tree[review_path]
                    revision = _history(control, head, review_path, review_oid)
                    decision, binding = _review_disposition(
                        source, report=report, publication=publication,
                        revision=revision, review_oid=review_oid,
                        todo_path=workflow["todo"], review_path=workflow["review"],
                        backlog_path=workflow["backlog"],
                        accepted_path=workflow["accepted"],
                    )
                    entry["review"] = {"blob": review_oid, "publication": revision,
                                       "decision": decision, "binding": binding}
                else:
                    entry["review"] = {"state": "absent"}
                binding = ResourceBinding.for_execution(manager, report.execution_id)
                if binding.intent["generation"] != report.resource_generation or (
                    binding.intent["control"] != report.control_head
                    or binding.intent["base"] != report.code_base_head
                    or binding.intent["remote"] != owner.remote_name
                    or report.execution_path != binding.intent["path"]
                    or report.execution_branch != manager.branch
                ):
                    raise EvidenceError("report and independent generation disagree")
                admitted = _tree(
                    control, report.control_head, f"{workflow['todo']}/{ticket_id}.md",
                )
                if admitted.get(f"{workflow['todo']}/{ticket_id}.md") != (
                    binding.intent["ticket_blob"]
                ):
                    raise EvidenceError("generation ticket blob differs from admission")
                entry["intent"] = binding_identity(binding.intent)
                evidence = {}
                for evidence_path in sorted(binding.directory.rglob("*")):
                    _safe_path(evidence_path)
                    metadata = evidence_path.stat()
                    if stat.S_ISREG(metadata.st_mode):
                        value: dict = {
                            "device": metadata.st_dev, "inode": metadata.st_ino,
                        }
                        if evidence_path.suffix == ".json":
                            value["sha256"] = hashlib.sha256(
                                evidence_path.read_bytes()
                            ).hexdigest()
                        relative = str(evidence_path.relative_to(binding.directory))
                        evidence[relative] = value
                    elif not stat.S_ISDIR(metadata.st_mode):
                        raise EvidenceError("non-regular independent resource evidence")
                entry["generation_evidence"] = evidence
                if _evidence_only:
                    continue
                try:
                    workspace = binding.require_local(clean=False, read_only=True)
                    entry["current"] = dataclasses.asdict(workspace)
                    entry["current"]["path"] = str(workspace.path)
                    inventory.update(
                        head=workspace.head, dirty=workspace.dirty, submodules="proved",
                    )
                    if workspace.head != report.workspace_head:
                        blockers.append(
                            "checkpoint and current generation HEAD disagree"
                        )
                except (
                    ValueError, RuntimeError, OSError, subprocess.SubprocessError,
                    ExecutionWorkspaceError,
                ):
                    # prove() supplies the structured fail-closed reason below.
                    pass
                proof = binding.prove(
                    runner=observe_git, expected_remote=report.workspace_head,
                    worker_retired=True,
                )
                entry["proof"] = dataclasses.asdict(proof)
                if proof.local_state == "proved" and proof.upstream_observed != (
                    inventory["upstream_observed"]
                ):
                    blockers.append("upstream changed during preview observation")
                if proof.local_state == "proved":
                    workspace = binding.require_local(clean=True, read_only=True)
                    entry["current"] = dataclasses.asdict(workspace)
                    entry["current"]["path"] = str(workspace.path)
                    inventory.update(
                        head=workspace.head, dirty=False, submodules="proved",
                    )
                if proof.local_state != "proved" or not proof.upstream_exact:
                    blockers.append(f"execution {report.execution_id}: {proof.reason}")
            except (
                ValueError, RuntimeError, OSError, subprocess.SubprocessError,
                ExecutionWorkspaceError,
            ) as error:
                entry["unsupported"] = str(error)
                blockers.append(f"execution evidence unproven: {report_path}: {error}")
    if not identity["executions"]:
        blockers.append(
            "no independently bound immutable execution report is available"
        )
    # No ambiguous latest-by-name fallback. Historical generations remain visible;
    # selecting disposal scope is a separate future owner-authorized operation.
    proved = [entry for entry in identity["executions"]
              if entry.get("proof", {}).get("local_state") == "proved"]
    if not _evidence_only and len(proved) != 1:
        blockers.append("current resource has no unique proved report generation")
    if state in {"accepted", "done"} and not any(
        entry.get("review", {}).get("decision") == "accepted"
        for entry in identity["executions"]
    ):
        blockers.append(
            "accepted/done ticket has no proven accepted review disposition"
        )
    if observe_git(control, "rev-parse", "HEAD").stdout.strip() != head or (
        resolve_ref_commit(
            owner.repo, f"refs/heads/{owner.control_branch}", runner=observe_git,
        ) != head
    ) or authority_identity(_authority(owner)) != identity["authority"]:
        raise EvidenceError("control or owner authority changed during preview")
    if _control_identity(owner, control, head) != control_identity:
        raise EvidenceError("control registration changed during preview")
    complete = not blockers
    blockers.append("upstream continuity is unknown (plain Git remote ABA limitation)")
    return {
        "schema": SCHEMA, "informational": True, "mutation_authorized": False,
        "snapshot_complete": complete,
        "identity": identity,
        "digest": canonical_digest(identity) if complete else None,
        "blockers": sorted(set(blockers)),
    }
