# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Identity-bound authorization and WAL-ordered exact resource retirement."""

import copy
import hashlib
import json
import re
from pathlib import Path

from devlegate.clean_preview import (
    SCHEMA,
    _authority,
    authority_identity,
    binding_identity,
    canonical_digest,
    inspect_clean,
    observe_git,
)
from devlegate.execution_workspace import ExecutionWorkspaceManager
from devlegate.git import remote_ref, resolve_ref_commit
from devlegate.resource_binding import ResourceBinding, _identity, _read, _safe_path

INTENT_SCHEMA = "devlegate.clean-reset-intent.v1"
REMOTE_POLICY = "exact-observation-only-no-continuity-claim"
STAGES = (
    "authorized-pre-effect", "worktree-pending", "worktree-retired",
    "local-pending", "local-retired", "remote-pending", "completed",
)


def prepare_reset(owner, ticket_id, digest, generation):
    """Reobserve under the service owner; a supplied digest is never evidence."""
    if owner._state_write_uncertain:
        raise ValueError("runtime state write outcome uncertain; restart required")
    if not all(isinstance(v, str) and re.fullmatch(r"[0-9a-f]{64}", v)
               for v in (digest, generation)):
        raise ValueError("clean apply requires exact plan digest and generation")
    preview = inspect_clean(owner, ticket_id)
    if not preview["snapshot_complete"] or preview["digest"] != digest:
        raise ValueError("stale or incomplete clean preview; inspect again")
    identity = preview["identity"]
    if identity["ticket"]["state"] not in {"backlog", "done", "abandoned"}:
        raise ValueError("active ticket state cannot authorize reset")
    current = [e for e in identity["executions"]
               if e.get("proof", {}).get("local_state") == "proved"]
    if len(current) != 1 or current[0]["intent"]["generation"] != generation:
        raise ValueError("confirmed generation differs from proved resource")
    persisted, revision = owner._runtime_store.observe()
    # Preview excludes presentation fields; compare the full live state here
    # to avoid treating those exclusions as write authority.
    if persisted != owner._state:
        raise ValueError(
            "runtime authority differs from durable state; restart required"
        )
    return {
        "schema": INTENT_SCHEMA, "status": "authorized-pre-effect",
        "plan_digest": digest, "generation": generation,
        "identity": identity, "remote_policy": REMOTE_POLICY,
        "runtime_revision": revision, "effects": [],
    }


def validate_reset_intent(state):
    """Restore only a complete authorization/receipt pair, never execute it."""
    intent = state.get("clean_reset_intent")
    if intent is None:
        receipts = state.get("mutable_receipts", {})
        if isinstance(receipts, dict) and any(
            isinstance(r, dict) and r.get("method") == "clean-apply"
            for r in receipts.values()
        ):
            raise ValueError("clean reset receipt has no durable intent")
        return
    try:
        if not isinstance(intent, dict) or set(intent) != {
            "schema", "status", "plan_digest", "generation", "identity",
            "remote_policy", "request_id", "fingerprint",
            "runtime_revision", "effects",
        }:
            raise ValueError("invalid clean reset intent fields")
        identity = intent["identity"]
        ticket = identity["ticket"]
        if (intent["schema"] != INTENT_SCHEMA
                or intent["status"] not in STAGES
                or intent["remote_policy"] != REMOTE_POLICY
                or identity["schema"] != SCHEMA
                or canonical_digest(identity) != intent["plan_digest"]
                or ticket["state"] not in {"backlog", "done", "abandoned"}
                or state.get("phase") != "idle"
                or state.get("worker_identity") is not None):
            raise ValueError("invalid clean reset authorization")
        if not isinstance(intent["runtime_revision"], str) or not re.fullmatch(
            r"sqlite:[1-9][0-9]*", intent["runtime_revision"],
        ):
            raise ValueError("invalid clean reset runtime revision")
        current = [e for e in identity["executions"]
                   if e.get("proof", {}).get("local_state") == "proved"]
        if len(current) != 1 or (
            current[0]["intent"]["generation"] != intent["generation"]
        ):
            raise ValueError("invalid clean reset generation")
        completed = STAGES.index(intent["status"]) // 2
        if intent["effects"] != [
            _effect_record(intent, operation)
            for operation in ("worktree", "local", "remote")[:completed]
        ]:
            raise ValueError("invalid clean reset effect journal")
        receipt = state["mutable_receipts"][intent["request_id"]]
        fingerprint = hashlib.sha256(json.dumps({
            "method": "clean-apply", "payload": {
                "ticket_id": ticket["id"], "plan_digest": intent["plan_digest"],
                "generation": intent["generation"],
            },
        }, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        if (receipt["method"] != "clean-apply" or receipt["accepted"] is not True
                or intent["fingerprint"] != fingerprint
                or receipt["fingerprint"] != intent["fingerprint"]
                or receipt["ticket_id"] != ticket["id"]
                or receipt["result"] != reset_ack(intent)):
            raise ValueError("clean reset receipt differs from authorization")
    except (KeyError, TypeError, ValueError, AttributeError) as error:
        raise ValueError(f"invalid persisted clean reset intent: {error}") from error


def reset_ack(intent):
    return {
        "accepted": True, "ticket_id": intent["identity"]["ticket"]["id"],
        "plan_digest": intent["plan_digest"], "generation": intent["generation"],
        "status": "authorized-pre-effect", "effects_performed": False,
    }


def _stable_identity(identity):
    """Only resource observations vary as the authorized effects are applied."""
    result = copy.deepcopy(identity)
    result.pop("resources")
    for entry in result["executions"]:
        entry.pop("current", None)
        entry.pop("proof", None)
    return result


def _effect_record(intent, operation):
    resources = intent["identity"]["resources"]
    resource, before = {
        "worktree": (resources["path"], resources["registration"]),
        "local": (resources["local_ref"], resources["local_observed"]),
        "remote": (resources["upstream_ref"], resources["upstream_observed"]),
    }[operation]
    return {
        "operation": operation, "generation": intent["generation"],
        "resource": resource, "before": before, "after": "confirmed-absent",
    }


def _require_no_hidden_changes(manager, repo):
    """Status alone omits ignored files and assume-unchanged index entries."""
    _safe_path(repo)
    if observe_git(repo, "ls-files", "--others", "-z").stdout:
        raise ValueError("clean reset worktree contains untracked or ignored files")
    entries = observe_git(repo, "ls-files", "-v", "-z").stdout.split("\0")
    if any(entry and (entry[0].islower() or entry[0] == "S") for entry in entries):
        raise ValueError("clean reset worktree has hidden index flags")
    submodules = manager._gitlink_paths(repo)
    for relative in submodules:
        _require_no_hidden_changes(manager, repo / relative)


def _reobserve(owner, intent):
    owner._assert_repository_owner()
    if owner._state_write_uncertain:
        raise ValueError("runtime state write outcome uncertain; restart required")
    validate_reset_intent(owner._state)
    persisted, revision = owner._runtime_store.observe()
    if persisted != owner._state:
        raise ValueError(
            "runtime authority differs from durable state; restart required"
        )
    expected = intent["identity"]
    runtime = copy.deepcopy(owner._state)
    runtime.pop("clean_reset_intent")
    receipts = runtime["mutable_receipts"]
    receipts.pop(intent["request_id"])
    if not expected["authority"]["mutable_receipts_present"] and not receipts:
        runtime.pop("mutable_receipts")
    for key in ("workspace_cleanup", "workspace_cleanup_cursor"):
        runtime.pop(key, None)
    authority = _authority(owner)
    authority["runtime"] = runtime
    if authority_identity(authority) != expected["authority"]:
        raise ValueError("clean reset owner authority changed")
    preview = inspect_clean(owner, expected["ticket"]["id"], _evidence_only=True)
    observed = preview["identity"]
    observed["authority"] = authority_identity(authority)
    if not preview["snapshot_complete"] or (
        _stable_identity(observed) != _stable_identity(expected)
    ):
        raise ValueError("clean reset ticket, owner or generation evidence changed")
    entry = next(e for e in expected["executions"]
                 if e.get("proof", {}).get("local_state") == "proved")
    manager = ExecutionWorkspaceManager(
        owner.repo, owner.execution_worktree_root, expected["ticket"]["id"],
        runner=observe_git,
    )
    binding = ResourceBinding.for_execution(manager, entry["intent"]["execution_id"])
    if binding_identity(binding.intent) != entry["intent"]:
        raise ValueError("clean reset generation changed")
    binding._validate_intent()
    binding._require_settled()
    binding.require_worker_retirement()
    receipt = _read(binding.directory / "receipt.json")
    if _identity(Path(str(binding.intent["common"]))) != receipt["common_identity"]:
        raise ValueError("clean reset Git common directory changed")
    head = entry["report"]["workspace_head"]
    local = resolve_ref_commit(
        owner.repo, str(binding.intent["ref"]), runner=observe_git,
    )
    symbolic = observe_git(
        owner.repo, "symbolic-ref", "--quiet", str(binding.intent["ref"]), check=False,
    )
    if symbolic.returncode != 1:
        raise ValueError("clean reset local ref is symbolic or unavailable")
    upstream = remote_ref(
        owner.repo, str(binding.intent["remote"]), str(binding.intent["remote_ref"]),
        runner=observe_git,
    )
    registrations = manager._registrations()
    _safe_path(manager.path)
    admin = Path(str(receipt["admin"]))
    _safe_path(admin)
    absent = not manager.path.exists() and not admin.exists() and not any(
        p == manager.path.resolve() or r.get("branch") == manager.branch
        for p, r in registrations.items()
    )
    stage = STAGES.index(intent["status"])
    if stage < 2 and not (stage == 1 and absent):
        workspace = binding.require_local(clean=True, read_only=True)
        _require_no_hidden_changes(manager, manager.path)
        if workspace.head != head or (
            registrations.get(manager.path.resolve())
            != expected["resources"]["registration"]
        ) or any(
            p != manager.path.resolve() and r.get("branch") == manager.branch
            for p, r in registrations.items()
        ):
            raise ValueError("clean reset worktree changed")
    elif not absent:
        raise ValueError("clean reset retired worktree reappeared or outcome unknown")
    if stage < 4 and not (stage == 3 and local is None):
        if local != head:
            raise ValueError("clean reset local ref changed")
        binding.require_ref(head)
    elif local is not None:
        raise ValueError("clean reset retired local ref reappeared")
    remote_expected = expected["resources"]["upstream_observed"]
    if stage < 6 and not (stage == 5 and upstream is None):
        if upstream != remote_expected:
            raise ValueError("clean reset remote ref changed")
    elif upstream is not None:
        raise ValueError("clean reset retired remote ref reappeared")
    owner._assert_repository_owner()
    persisted, final_revision = owner._runtime_store.observe()
    if persisted != owner._state or revision != final_revision:
        raise ValueError("runtime authority changed during clean reset observation")
    return binding, revision, absent, local, upstream


def advance_reset(owner):
    """Replay only this intent, under the service owner, before scheduler work.

    A pending effect can replay its exact precondition or confirm its complete
    postcondition. Mixed/partial observations and IO failures never authorize a
    broader cleanup. Each completed effect is committed before the next begins.
    """
    while True:
        intent = owner._state["clean_reset_intent"]
        if intent["status"] == "completed":
            return
        binding, revision, absent, local, upstream = _reobserve(owner, intent)
        stage = STAGES.index(intent["status"])
        if stage % 2 == 0:
            owner._save_state(
                "idle", clean_reset_intent={**intent, "status": STAGES[stage + 1]},
                expected_store_revision=revision,
            )
            continue
        manager = binding.manager
        if stage == 1 and not absent:
            # Native Git requires one force for even clean initialized submodules.
            # The recursive safety gate has just proved them; never double-force
            # (override a lock), unlock, prune, or remove an unregistered path.
            submodules = manager._gitlink_paths(manager.path)
            observe_git(
                owner.repo, "worktree", "remove", *(("--force",) if submodules else ()),
                "--", str(manager.path),
            )
        elif stage == 3 and local is not None:
            observe_git(
                owner.repo, "update-ref", "--no-deref", "-d",
                str(binding.intent["ref"]), local,
            )
        elif stage == 5 and upstream is not None:
            ref = str(binding.intent["remote_ref"])
            observe_git(
                owner.repo, "-c", "push.followTags=false", "push", "--porcelain",
                "--no-verify", "--recurse-submodules=no",
                f"--force-with-lease={ref}:{upstream}",
                str(binding.intent["remote"]), f":{ref}",
            )
        # Recheck all authority and exact postconditions before recording success.
        _, revision, absent, local, upstream = _reobserve(owner, intent)
        if (stage == 1 and not absent) or (stage == 3 and local is not None) or (
            stage == 5 and upstream is not None
        ):
            raise ValueError("clean reset effect outcome unknown")
        owner._save_state(
            "idle", clean_reset_intent={
                **intent, "status": STAGES[stage + 1],
                "effects": [*intent["effects"], _effect_record(
                    intent, {1: "worktree", 3: "local", 5: "remote"}[stage],
                )],
            },
            expected_store_revision=revision,
        )
