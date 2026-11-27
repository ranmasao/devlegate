# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Candidate custody and installation under the serialized repository owner."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from subprocess import CalledProcessError

from devlegate.abandonment_evidence import EvidenceError
from devlegate.abandonment_refs import create_pin, publish_pin, require_pin
from devlegate.execution_result import new_execution_id
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.git import (
    GitObservationError,
    commit_parents,
    commit_range,
    is_ancestor,
    push_refs,
    resolve_ref_commit,
)
from devlegate.implementation_transfer import (
    ImplementationTransfer,
    ResolutionAuthorization,
    ResolutionEditError,
    TransferConflict,
    TransferIdentity,
    TransferReady,
    source_resolution_digest,
)
from devlegate.resource_binding import ResourceBinding, _read
from devlegate.runtime_state_validation import DevlegateError


def origin_digest(provenance):
    origin = dict(provenance)
    if "review" in origin:
        origin["review"] = {key: value for key, value in origin["review"].items()
                            if key not in {"legacy", "legacy_boundary"}}
    return hashlib.sha256(json.dumps(origin, sort_keys=True).encode()).hexdigest()


def conflict_record(conflict):
    record = asdict(conflict)
    record.pop("unmerged")  # Internal replay evidence, not operator identity.
    if any(entry.startswith("160000 ") for entry in conflict.unmerged):
        record["gitlink"] = True
    return {**record, "path": str(conflict.path)}


def waiting_message(conflict, origin="REWORK", error=None):
    identity = conflict["identity"]
    message = (
        f"{origin} waiting for source resolution: {conflict['path']} "
        f"(attempt {identity['attempt']}, A={identity['original_base']}, "
        f"B={identity['target']}, H={identity['checkpoint']}, "
        f"step {conflict['step'] + 1}, commit {conflict['source_commit']}); "
    )
    if conflict.get("gitlink"):
        return message + (
            "source-only resolution cannot select a gitlink commit; "
            f"use devlegate reset {identity['ticket']} only if the original "
            "generation is safe to retire; otherwise preserve the candidate"
        )
    return (f"{error}; " if error else "") + message + (
        f"edit source files, then run devlegate reconcile resolve {identity['ticket']} "
        "with the candidate path"
    )


class ProductLineageError(ExecutionWorkspaceError):
    pass


def automatic_target(owner, base, product):
    """Select only a clean, forward product observation; never authorize a rewrite."""
    if not product["target_eligible"]:
        raise ExecutionWorkspaceError(str(product["reason"]))
    local, target = product["local_head"], product["remote_head"]

    def git(repo, *args, **kwargs):
        return owner._git_runtime(repo, "--no-replace-objects", *args, **kwargs)

    previous = base
    commits = commit_range(owner.repo, f"{base}..{target}", reverse=True, runner=git)
    for commit in commits:
        if commit_parents(owner.repo, commit, runner=git) != (previous,):
            break
        previous = commit
    if previous != target:
        raise ProductLineageError(
            "product remote is not a linear descendant"
        )
    if (not is_ancestor(owner.repo, base, local, runner=git)
            or not is_ancestor(owner.repo, local, target, runner=git)):
        raise ExecutionWorkspaceError(
            "local product is not on the observed forward path; synchronize explicitly"
        )
    return target


def synchronize_target(owner, product, target):
    local = product["local_head"]
    if local == target:
        return
    if owner._stop_before_admission():
        raise ExecutionWorkspaceError("product fast-forward paused for shutdown")
    if owner._execution_remote_head(owner.remote_branch) != target:
        raise ExecutionWorkspaceError("product target changed before fast-forward")
    owner._assert_product_checkout_unchanged(owner.current_branch, local)
    merge = owner._merge_fast_forward_runtime(
        owner.repo, f"{owner.remote_name}/{owner.remote_branch}", local, target,
    )
    if merge.returncode:
        raise ExecutionWorkspaceError(
            f"product fast-forward failed: {merge.stderr.strip()}"
        )


class TransferTransaction:
    def __init__(self, owner, binding, source, base, checkpoint, evidence):
        owner._assert_repository_owner()
        self.owner, self.binding, self.source = owner, binding, source
        self.manager = binding.manager
        self.progress = owner._state.get("transfer_progress") or {}
        self.identity = TransferIdentity(
            owner._state_key, self.manager.ticket_id,
            source.intent["generation"], binding.intent["generation"],
            base, checkpoint, binding.intent["base"],
        )
        if (
            source.intent["execution_id"] == binding.intent["execution_id"]
            or source.intent["base"] != base
            or any(source.intent[key] != binding.intent[key] for key in (
                "path", "ref", "common", "remote", "remote_ref", "remote_urls",
                "push_urls",
            ))
        ):
            raise ExecutionWorkspaceError("transfer source or admission differs")
        self.evidence = evidence
        self.pin = {"location": "local", "ref": evidence, "head": checkpoint}
        self.transfer = ImplementationTransfer(
            owner.repo, owner.execution_worktree_root, self.identity,
            require_owner=owner._assert_repository_owner,
            require_source=self.require_source, runner=owner._git_runtime,
        )

    def save(self, **fields):
        progress = {**self.progress, **fields}
        self.owner._save_state(self.owner._state["phase"], transfer_progress=progress)
        self.progress = progress

    def require_assignment(self):
        self.owner._assert_repository_owner()
        if self.owner._stop_before_admission():
            raise ExecutionWorkspaceError("transfer paused for service shutdown")
        self.binding._validate_intent()
        self.source._validate_intent()
        self.require_source_retirement()

    def require_source_retirement(self):
        self.source.require_worker_retirement()

    def require_source(self, identity):
        self.require_source_retirement()
        if self.installation_started:
            self.require_pin()
        else:
            self.source.transfer_source_absent(
                identity.original_base, identity.checkpoint,
            )
            if self.owner._execution_remote_head(self.manager.branch) != (
                self.binding.intent["prior_remote"]
            ):
                raise ExecutionWorkspaceError("execution upstream predecessor changed")

    def require_pin(self):
        require_pin(self.source, self.pin)
        if resolve_ref_commit(
            self.owner.repo, self.evidence, runner=self.owner._git_runtime,
        ) != self.identity.checkpoint:
            raise ExecutionWorkspaceError("preserved checkpoint evidence changed")

    def require_published_pin(self):
        if self.owner._execution_remote_head(self.evidence) != self.identity.checkpoint:
            raise ExecutionWorkspaceError("remote transfer evidence changed")

    def require_supersedable(self):
        """Retain any exposed conflict or materially ambiguous candidate."""
        self.require_source(self.identity)
        if (self.progress.get("conflict") or self.progress.get("confirmation")
                or self.transfer.intent.exists() and not self._untouched_ready()):
            raise ExecutionWorkspaceError(
                "stale candidate retained; preserve any edits before choosing "
                f"devlegate reset {self.identity.ticket} if the source is safe to "
                "retire"
            )

    def _untouched_ready(self):
        result = self.transfer.observe()
        return isinstance(result, TransferReady) and not result.externally_resolved

    def prepare(self):
        try:
            return self._prepare()
        except (EvidenceError, GitObservationError, OSError, CalledProcessError,
                DevlegateError) as error:
            raise ExecutionWorkspaceError(str(error)) from error

    @property
    def installed(self):
        return self.progress.get("candidate") is not None

    @property
    def installation_started(self):
        return (self.binding.directory / "transfer-started.json").exists()

    def _prepare(self):
        owner, binding, identity = self.owner, self.binding, self.identity
        self.require_assignment()
        if self.installed:
            self.require_pin()
            self.require_published_pin()
            workspace = binding.require_local(clean=False)
            remote = binding.latest_remote(binding.intent["prior_remote"])
            if owner._execution_remote_head(self.manager.branch) != remote:
                raise ExecutionWorkspaceError("installed transfer upstream changed")
            final = self.progress["candidate"]
            self.release(TransferReady(
                identity, Path(final["path"]) if final["path"] else None,
                final["head"], final["tree"], final["effect"], final["resolved"],
            ), workspace)
            return workspace
        result = (self.transfer.observe() if self.installation_started
                  else self.transfer.prepare())
        confirmation = self.progress.get("confirmation")
        resolution_error = self.progress.get("resolution_error")
        if confirmation is not None and isinstance(result, TransferConflict) and (
            conflict_record(result) == self.progress.get("conflict")
        ):
            try:
                result = self._resolve(result, confirmation["source_digest"])
            except ResolutionEditError as error:
                resolution_error = str(error)
        if isinstance(result, TransferConflict):
            record = conflict_record(result)
            if self.progress.get("conflict") != record or confirmation is not None:
                if self.progress.get("conflict") != record:
                    resolution_error = None
                self.save(
                    conflict=record, confirmation=None,
                    resolution_error=resolution_error,
                )
            raise ExecutionWorkspaceError(
                waiting_message(record, self.origin, resolution_error)
            )
        if not isinstance(result, TransferReady):
            raise ExecutionWorkspaceError(
                f"{self.origin} candidate refused: {result.reason}"
            )
        self.require_eligible_result(result)
        receipt = binding.directory / "receipt.json"
        if not receipt.exists():
            self.require_before_install()
            create_pin(self.source, self.evidence, identity.checkpoint, replay=True)
            publish_pin(self.source, self.evidence, identity.checkpoint, replay=True)
            if result.path is not None and not self.installation_started:
                owner._git_runtime(
                    owner.repo, "-c", "protocol.file.allow=always", "fetch",
                    "--no-tags", "--no-write-fetch-head", "--refmap=",
                    str(result.path), result.head, check=True,
                )
            tree = owner._git_runtime(
                owner.repo, "--no-replace-objects", "rev-parse",
                f"{result.head}^{{tree}}", check=True,
            )
            if tree.stdout.strip() != result.tree:
                raise ExecutionWorkspaceError("imported transfer tree differs")

        def require_current(*, publishing=False):
            if publishing or not receipt.exists():
                self.require_before_install()
            else:
                self.require_assignment()
            if self.transfer.observe() != result:
                raise ExecutionWorkspaceError("candidate changed before handoff")

        workspace = binding.install_transfer(
            self.source, identity.original_base, identity.checkpoint, result.head,
            require_current=require_current,
            require_source_retirement=self.require_source_retirement,
        )
        require_current()
        self.require_published_pin()
        self.publish_predecessor(workspace, require_current)
        self.require_assignment()
        self.save_installed({
            "path": str(result.path) if result.path else None,
            "head": result.head, "tree": result.tree, "effect": result.effect,
            "resolved": result.externally_resolved,
        })
        self.release(result, workspace)
        return workspace

    def save_installed(self, candidate):
        self.save(conflict=None, confirmation=None, resolution_error=None,
                  candidate=candidate)

    def require_eligible_result(self, result):
        """Origin-specific policy beyond the common proof of exact C."""

    def release(self, result, workspace):
        def require_consumed(ready):
            if self.binding.require_local(clean=False).head != ready.head:
                raise ExecutionWorkspaceError("transfer candidate custody differs")

        if result.head != workspace.head:
            raise ExecutionWorkspaceError("installed transfer candidate differs")
        self.transfer.release(result, require_consumed=require_consumed)

    def require_before_install(self):
        self.require_assignment()
        if self.owner._execution_remote_head(self.manager.branch) != (
            self.binding.intent["prior_remote"]
        ):
            raise ExecutionWorkspaceError("remote changed before installation")
        if self.installation_started:
            self.require_published_pin()

    def publish_predecessor(self, workspace, require_current):
        owner, binding = self.owner, self.binding
        before = binding.intent["prior_remote"]
        after = workspace.head
        remote = owner._execution_remote_head(self.manager.branch)
        if before is None or before == after:
            if remote != before:
                raise ExecutionWorkspaceError("execution remote predecessor changed")
            return
        op = "predecessor-publication"
        details = {"before": before, "after": after}
        intent = binding.directory / f"{op}-intent.json"
        if (binding.directory / f"{op}-receipt.json").exists():
            receipt = binding.operation_receipt(op)
            if remote != after or any(receipt.get(k) != v for k, v in details.items()):
                raise ExecutionWorkspaceError("transfer publication receipt differs")
            return
        if intent.exists():
            journal = _read(intent)
            if journal != {**details, "operation": op, "sequence": 1,
                           "generation": binding.intent["generation"]}:
                raise ExecutionWorkspaceError("transfer publication intent differs")
        else:
            if remote != before:
                raise ExecutionWorkspaceError("remote replaced without transfer intent")
            binding.require_local(clean=True)
            binding.begin_operation(op, details)
        if remote == before:
            require_current(publishing=True)
            if binding._observe_local(clean=True).head != after:
                raise ExecutionWorkspaceError("transfer checkout changed before push")
            ref = binding.intent["remote_ref"]
            push_refs(
                owner.repo, owner.remote_name, f"{after}:{ref}",
                options=(f"--force-with-lease={ref}:{before}",),
                runner=owner._git_runtime,
            )
        if owner._execution_remote_head(self.manager.branch) != after:
            raise ExecutionWorkspaceError("predecessor publication is unproved")
        binding.finish_operation(op, details)

    def _resolve(self, conflict, digest):
        if source_resolution_digest(conflict.path) != digest:
            raise ResolutionEditError("candidate changed since confirmation")
        tree = self.transfer.stage_source_resolution(conflict)
        self.require_before_install()
        return self.transfer.checkpoint_resolution(ResolutionAuthorization(
            conflict, tree, "Explicit source resolution including added files.", digest,
        ))


class ReworkTransfer(TransferTransaction):
    origin = "REWORK"

    def __init__(self, owner, binding, provenance):
        previous = provenance["previous_execution_id"]
        installed = provenance.get("installed_source")
        execution = installed["execution"] if installed else previous
        source = ResourceBinding.for_execution(binding.manager, execution)
        super().__init__(
            owner, binding, source,
            installed["base"] if installed else provenance["previous_base_head"],
            installed["checkpoint"] if installed
            else provenance["previous_report_checkpoint"],
            owner._workspace_recovery_ref(binding.manager.ticket_id, execution),
        )
        self.installed_source = installed
        self.replaying_stale_install = False
        if (
            binding.intent.get("transfer_origin") != origin_digest(provenance)
            or source.intent["generation"] != (
                installed["generation"] if installed
                else provenance.get("previous_generation")
            )
            or source.intent["control"] != (
                provenance["admitted_control_head"] if installed
                else provenance["previous_control_head"]
            )
            or binding.intent["base"] != provenance["admitted_base_head"]
            or binding.intent["control"] != provenance["admitted_control_head"]
        ):
            raise ExecutionWorkspaceError("reviewed source or admission differs")

    def require_assignment(self):
        super().require_assignment()
        self.require_control_assignment()
        product = self.owner._observe_product_generation(self.identity.target)
        if not product["stable"]:
            if not self.replaying_stale_install:
                raise ExecutionWorkspaceError(
                    f"stale REWORK target: {product['reason']}"
                )
            automatic_target(self.owner, self.identity.target, product)

    def require_control_assignment(self):
        owner, identity = self.owner, self.identity
        control = self.binding.intent["control"]
        current = owner._resolve_commit_runtime(owner.control_worktree, "HEAD")
        if current is None or (
            owner._execution_remote_head(owner.control_branch) != current
        ):
            raise ExecutionWorkspaceError("stale REWORK control; candidate retained")
        if current != control:
            if not self.installed:
                raise ExecutionWorkspaceError("stale REWORK admission; candidate kept")
            owner._prove_bound_ticket_descendant(
                current, control, identity.ticket, owner._state["selected_ticket_body"],
            )
        store = owner._ticket_store(head=current, require_clean=True)
        reason = owner._control_descendant_adoption_error(
            current, identity.ticket, store,
        )
        if reason is not None:
            raise ExecutionWorkspaceError(reason)

    def require_source_retirement(self):
        if self.installed_source:
            self.source.require_unlaunched_install(self.identity.checkpoint)
        else:
            super().require_source_retirement()

    def require_source(self, identity):
        before = self.binding.intent["prior_remote"]
        latest = self.source.latest_remote(self.source.intent["prior_remote"])
        noop = identity.checkpoint == identity.original_base
        retired_noop = noop and self.binding.intent["prior_local"] is None
        if (
            before != latest and not (retired_noop and before is None)
            or (not self.installed_source and before != identity.checkpoint
                and not (noop and before is None))
        ):
            raise ExecutionWorkspaceError("reviewed upstream predecessor is unproven")
        if self.installed_source:
            self.require_pin()
        super().require_source(identity)

    def save_installed(self, candidate):
        self.progress = {**self.progress, "conflict": None, "confirmation": None,
                         "resolution_error": None, "candidate": candidate}
        self.owner._save_state(
            "agent_pending", transfer_progress=self.progress,
            execution_remote_head=self.binding.latest_remote(self.binding.intent["prior_remote"]),
        )

    def advance_stale(self, provenance, product):
        """Settle admitted effects, or attribute a fresh attempt to the same review."""
        owner, binding, identity = self.owner, self.binding, self.identity
        if owner._stop_before_admission():
            return
        target = automatic_target(owner, identity.target, product)
        self.require_control_assignment()
        if owner._resolve_commit_runtime(owner.control_worktree, "HEAD") != (
            binding.intent["control"]
        ):
            raise ExecutionWorkspaceError("stale REWORK control; candidate retained")
        installed = provenance.get("installed_source")
        if self.installation_started:
            self.replaying_stale_install = True
            if not self.installed:
                self.prepare()
                return
            workspace = self.prepare()
            if self.progress["candidate"]["resolved"]:
                raise ExecutionWorkspaceError(
                    "installed human REWORK resolution retained; preserve its edits "
                    f"before choosing devlegate reset {identity.ticket}"
                )
            binding.require_unlaunched_install(workspace.head)
            remote = binding.latest_remote(binding.intent["prior_remote"])
            evidence = owner._workspace_recovery_ref(
                identity.ticket, binding.intent["execution_id"],
            )
            create_pin(binding, evidence, workspace.head, replay=True)
            installed = {
                "execution": binding.intent["execution_id"], "base": identity.target,
                "checkpoint": workspace.head,
                "generation": binding.intent["generation"],
                "prior": installed,
            }
        else:
            self.require_supersedable()
            remote = binding.intent["prior_remote"]
        synchronize_target(owner, product, target)
        provenance = {**provenance, "admitted_base_head": target}
        if installed:
            provenance["installed_source"] = installed
        intent = ResourceBinding.plan(
            self.manager, execution_id=new_execution_id(), base=target,
            control=binding.intent["control"],
            ticket_blob=binding.intent["ticket_blob"],
            remote=owner.remote_name, remote_head=remote,
        ).intent
        intent["transfer_origin"] = origin_digest(provenance)
        owner._save_state(
            "agent_pending", clear_pre_worker_generation=True, transfer_progress=None,
            local_head=target, remote_head=target,
            rework_provenance=provenance, execution_id=intent["execution_id"],
            execution_base_head=target, execution_admitted_base_head=target,
            execution_validated_product_head=target,
            execution_resource_binding=intent, execution_remote_head=remote,
            worker_identity=None,
        )
