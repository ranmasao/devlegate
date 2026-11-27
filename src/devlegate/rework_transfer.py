# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Ordinary reviewed continuation under the serialized repository owner."""

import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from subprocess import CalledProcessError

from devlegate.abandonment_evidence import EvidenceError
from devlegate.abandonment_refs import create_pin, publish_pin, require_pin
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.git import GitObservationError, push_refs, resolve_ref_commit
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

INSTALLED = "remote_predecessor_installed"


def origin_digest(provenance):
    origin = {key: value for key, value in provenance.items()
               if key not in {"preparation_stage", "conflict", "candidate", "review",
                              "confirmation", "resolution_error"}}
    origin["review"] = {key: value for key, value in provenance["review"].items()
                        if key not in {"legacy", "legacy_boundary"}}
    return hashlib.sha256(json.dumps(origin, sort_keys=True).encode()).hexdigest()


def conflict_record(conflict):
    record = asdict(conflict)
    record.pop("unmerged")  # Internal replay evidence, not operator identity.
    return {**record, "path": str(conflict.path)}


def waiting_message(conflict):
    identity = conflict["identity"]
    return (
        f"REWORK waiting for source resolution: {conflict['path']} "
        f"(attempt {identity['attempt']}, A={identity['original_base']}, "
        f"B={identity['target']}, H={identity['checkpoint']}, "
        f"step {conflict['step'] + 1}, commit {conflict['source_commit']}); "
        f"edit source files, then run reconcile resume {identity['ticket']} "
        "--resolve-candidate with the candidate path"
    )


class ReworkTransfer:
    def __init__(self, owner, binding, provenance):
        owner._assert_repository_owner()
        self.owner = owner
        self.binding = binding
        self.provenance = provenance
        self.manager = binding.manager
        previous = provenance["previous_execution_id"]
        self.source = ResourceBinding.for_execution(self.manager, previous)
        identity = self.identity = TransferIdentity(
            owner._state_key, self.manager.ticket_id,
            self.source.intent["generation"], binding.intent["generation"],
            provenance["previous_base_head"], provenance["previous_report_checkpoint"],
            provenance["admitted_base_head"],
        )
        if (
            binding.intent.get("rework_origin") != origin_digest(provenance)
            or self.source.intent["execution_id"] == binding.intent["execution_id"]
            or self.source.intent["generation"] != provenance.get("previous_generation")
            or self.source.intent["control"] != provenance["previous_control_head"]
            or binding.intent["base"] != identity.target
            or binding.intent["control"] != provenance["admitted_control_head"]
            or any(self.source.intent[key] != binding.intent[key] for key in (
                "path", "ref", "common", "remote", "remote_ref", "remote_urls",
                "push_urls",
            ))
        ):
            raise ExecutionWorkspaceError("reviewed source or admission differs")
        self.evidence = owner._workspace_recovery_ref(identity.ticket, previous)
        self.pin = {
            "location": "local", "ref": self.evidence, "head": identity.checkpoint,
        }
        self.transfer = ImplementationTransfer(
            owner.repo, owner.execution_worktree_root, identity,
            require_owner=owner._assert_repository_owner,
            require_source=self.require_source,
            runner=owner._git_runtime,
        )

    def require_assignment(self):
        owner, identity = self.owner, self.identity
        owner._assert_repository_owner()
        if owner._stop_before_admission():
            # Use the existing runtime drain boundary without requiring a new
            # owner-specific shutdown path in the transfer primitive.
            raise ExecutionWorkspaceError("REWORK paused for service shutdown")
        self.binding._validate_intent()
        self.source._validate_intent()
        self.source.require_worker_retirement()
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
        product = owner._observe_product_generation(identity.target)
        if not product["stable"]:
            raise ExecutionWorkspaceError(f"stale REWORK target: {product['reason']}")

    def require_source(self, identity):
        self.source.require_worker_retirement()
        before = self.binding.intent["prior_remote"]
        latest = self.source.latest_remote(self.source.intent["prior_remote"])
        noop = identity.checkpoint == identity.original_base
        retired_noop = noop and self.binding.intent["prior_local"] is None
        if (
            before != latest and not (retired_noop and before is None)
            or before != identity.checkpoint and not (noop and before is None)
        ):
            raise ExecutionWorkspaceError("reviewed upstream predecessor is unproven")
        if (self.binding.directory / "rework-started.json").exists():
            self.require_pin()
        else:
            self.source.rework_source_absent(
                identity.original_base, identity.checkpoint,
            )
            if self.owner._execution_remote_head(self.manager.branch) != before:
                raise ExecutionWorkspaceError("reviewed upstream predecessor changed")

    def require_pin(self):
        require_pin(self.source, self.pin)
        if resolve_ref_commit(
            self.owner.repo, self.evidence, runner=self.owner._git_runtime,
        ) != self.identity.checkpoint:
            raise ExecutionWorkspaceError("reviewed checkpoint pin changed")

    def prepare(self):
        try:
            return self._prepare()
        except (EvidenceError, GitObservationError, OSError, CalledProcessError,
                DevlegateError) as error:
            raise ExecutionWorkspaceError(str(error)) from error

    @property
    def installed(self):
        return self.provenance.get("preparation_stage") == INSTALLED

    @property
    def installation_started(self):
        return (self.binding.directory / "rework-started.json").exists()

    def _prepare(self):
        owner, binding, identity = self.owner, self.binding, self.identity
        self.require_assignment()
        if self.installed:
            workspace = binding.require_local(clean=False)
            remote = binding.latest_remote(binding.intent["prior_remote"])
            if owner._execution_remote_head(self.manager.branch) != remote:
                raise ExecutionWorkspaceError("installed REWORK upstream changed")
            final = self.provenance.get("candidate")
            if final is not None:
                ready = TransferReady(
                    identity, Path(final["path"]) if final["path"] else None,
                    final["head"], final["tree"], final["effect"], final["resolved"],
                )
                self.release(ready, workspace)
            return workspace
        result = (self.transfer.observe() if self.installation_started
                  else self.transfer.prepare())
        confirmation = self.provenance.get("confirmation")
        resolution_error = self.provenance.get("resolution_error")
        if confirmation is not None and isinstance(result, TransferConflict) and (
            conflict_record(result) == self.provenance.get("conflict")
        ):
            try:
                result = self._resolve(result, confirmation["source_digest"])
            except ResolutionEditError as error:
                resolution_error = str(error)
        if isinstance(result, TransferConflict):
            record = conflict_record(result)
            if self.provenance.get("conflict") != record or confirmation is not None:
                if self.provenance.get("conflict") != record:
                    resolution_error = None
                owner._save_state("agent_pending", rework_provenance={
                    **self.provenance, "conflict": record, "confirmation": None,
                    "resolution_error": resolution_error,
                })
            message = waiting_message(record)
            raise ExecutionWorkspaceError(
                f"{resolution_error}; {message}" if resolution_error else message
            )
        if not isinstance(result, TransferReady):
            raise ExecutionWorkspaceError(f"REWORK candidate refused: {result.reason}")
        if confirmation is not None:
            # A durable approval may already have advanced the transfer on a
            # previous owner pass. Consume only after re-observing its outcome.
            self.provenance = {**self.provenance, "conflict": None,
                               "confirmation": None, "resolution_error": None}
            owner._save_state("agent_pending", rework_provenance=self.provenance)

        receipt = binding.directory / "receipt.json"
        if not receipt.exists():
            self.require_before_install()
            create_pin(self.source, self.evidence, identity.checkpoint, replay=True)
            publish_pin(self.source, self.evidence, identity.checkpoint, replay=True)
            if result.path is not None:
                owner._git_runtime(
                    owner.repo, "-c", "protocol.file.allow=always", "fetch",
                    "--no-tags", "--no-write-fetch-head", "--refmap=",
                    str(result.path), result.head,
                )
            tree = owner._git_runtime(
                owner.repo, "--no-replace-objects", "rev-parse",
                f"{result.head}^{{tree}}",
            )
            if tree.stdout.strip() != result.tree:
                raise ExecutionWorkspaceError("imported REWORK tree differs")

        def require_current(*, publishing=False):
            if publishing or not receipt.exists():
                self.require_before_install()
            else:
                self.require_assignment()
            if self.transfer.observe() != result:
                raise ExecutionWorkspaceError("REWORK candidate changed before handoff")

        # The binding replays only its exact C; it never runs the transfer again.
        workspace = binding.install_rework(
            self.source, identity.original_base, identity.checkpoint, result.head,
            require_current=require_current,
        )
        require_current()
        self.require_pin()
        if owner._execution_remote_head(self.evidence) != identity.checkpoint:
            raise ExecutionWorkspaceError("immutable remote REWORK evidence changed")
        self.publish_predecessor(workspace, require_current)
        self.require_assignment()
        owner._save_state(
            "agent_pending",
            execution_remote_head=binding.latest_remote(binding.intent["prior_remote"]),
            rework_provenance={
                **self.provenance, "conflict": None,
                "preparation_stage": INSTALLED,
                "candidate": {
                    "path": str(result.path) if result.path else None,
                    "head": result.head, "tree": result.tree, "effect": result.effect,
                    "resolved": result.externally_resolved,
                },
            },
        )
        self.release(result, workspace)
        return workspace

    def release(self, result, workspace):
        def require_consumed(ready):
            if self.binding.require_local(clean=False).head != ready.head:
                raise ExecutionWorkspaceError("REWORK candidate custody differs")

        if result.head != workspace.head:
            raise ExecutionWorkspaceError("installed REWORK candidate differs")
        self.transfer.release(result, require_consumed=require_consumed)

    def require_before_install(self):
        self.require_assignment()
        if self.owner._execution_remote_head(self.manager.branch) != (
            self.binding.intent["prior_remote"]
        ):
            raise ExecutionWorkspaceError("REWORK remote changed before installation")
        if self.installation_started:
            self.require_pin()
            if self.owner._execution_remote_head(self.evidence) != self.pin["head"]:
                raise ExecutionWorkspaceError("remote REWORK evidence changed")

    def publish_predecessor(self, workspace, require_current):
        owner, binding = self.owner, self.binding
        before = binding.intent["prior_remote"]
        after = workspace.head
        remote = owner._execution_remote_head(self.manager.branch)
        if before is None or before == after:
            if remote != before:
                raise ExecutionWorkspaceError("REWORK remote predecessor changed")
            return
        op = "predecessor-publication"
        details = {"before": before, "after": after}
        intent = binding.directory / f"{op}-intent.json"
        if (binding.directory / f"{op}-receipt.json").exists():
            receipt = binding.operation_receipt(op)
            if remote != after or any(receipt.get(k) != v for k, v in details.items()):
                raise ExecutionWorkspaceError("REWORK publication receipt differs")
            return
        if intent.exists():
            journal = _read(intent)
            if journal != {**details, "operation": op, "sequence": 1,
                           "generation": binding.intent["generation"]}:
                raise ExecutionWorkspaceError("REWORK publication intent differs")
        else:
            if remote != before:
                raise ExecutionWorkspaceError("REWORK remote replaced without intent")
            binding.require_local(clean=True)
            binding.begin_operation(op, details)
        if remote == before:
            require_current(publishing=True)
            if binding._observe_local(clean=True).head != after:
                raise ExecutionWorkspaceError("REWORK checkout changed before push")
            ref = binding.intent["remote_ref"]
            push_refs(
                owner.repo, owner.remote_name, f"{after}:{ref}",
                options=(f"--force-with-lease={ref}:{before}",),
                runner=owner._git_runtime,
            )
        if owner._execution_remote_head(self.manager.branch) != after:
            raise ExecutionWorkspaceError("REWORK predecessor publication is unproved")
        binding.finish_operation(op, details)

    def _resolve(self, conflict, digest):
        if source_resolution_digest(conflict.path) != digest:
            raise ResolutionEditError("candidate changed since confirmation")
        tree = self.transfer.stage_source_resolution(conflict)
        self.require_before_install()
        # The primitive re-observes the attempt, staged tree and source bytes
        # before publishing approval, and again on approval replay.
        return self.transfer.checkpoint_resolution(ResolutionAuthorization(
            conflict, tree, "Explicit source resolution including added files.",
            digest,
        ))
