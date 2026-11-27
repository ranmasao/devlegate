# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Active reconciliation admission and RESUME continuation."""

from devlegate.abandonment_refs import create_pin, require_pin
from devlegate.execution_result import new_execution_id
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.git import GitObservationError, commit_parents
from devlegate.implementation_transfer import TransferEffect
from devlegate.reconciliation import (
    reconciliation_evidence_ref,
    rewritten_evidence_ref,
    same_parent_replacement,
)
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime_state_validation import DevlegateError
from devlegate.transfer_transaction import (
    ProductLineageError,
    TransferTransaction,
    automatic_target,
    origin_digest,
    synchronize_target,
    waiting_message,
)


def _origin(reconciliation, rewrite_published, automatic=False):
    authority = {
        "ticket": reconciliation["ticket_id"],
        "report": reconciliation.get("execution_report"),
        "evidence": reconciliation["evidence_ref"],
        "rewrite_published": rewrite_published,
    }
    if automatic:
        authority["policy"] = "owned-linear-transfer"
    if "installed_source" in reconciliation:
        authority["installed_source"] = reconciliation["installed_source"]
    return origin_digest(authority)


class ActiveTransfer(TransferTransaction):
    origin = "update-base"

    @classmethod
    def admitted(cls, owner, source, reconciliation):
        progress = owner._state["transfer_progress"]
        return cls(
            owner, ResourceBinding(source.manager, progress["active_binding"]),
            source, reconciliation, progress["rewrite_published"],
            progress.get("automatic", False),
        )

    def __init__(self, owner, binding, source, reconciliation, rewrite_published,
                 automatic=False):
        self.reconciliation = reconciliation
        self.automatic = automatic
        super().__init__(
            owner, binding, source, reconciliation["original_base"],
            reconciliation["worker_checkpoint"], reconciliation["evidence_ref"],
        )
        report = reconciliation.get("execution_report")
        installed = reconciliation.get("installed_source")
        if installed is None:
            source_identity = (
                isinstance(report, dict)
                and report.get("execution_id") == source.intent["execution_id"]
                and report.get("resource_generation") == source.intent["generation"]
                and report.get("workspace_head") == self.identity.checkpoint
                and report.get("code_base_head") == self.identity.original_base
            )
        else:
            source_identity = (
                automatic and installed.get("status") == "resolved"
                and installed.get("resumed_execution_id")
                == source.intent["execution_id"]
                and installed.get("effective_base") == self.identity.original_base
                and installed.get("worker_checkpoint") == self.identity.checkpoint
                and installed.get("execution_remote_head")
                == binding.intent["prior_remote"]
                and installed.get("externally_resolved") is False
            )
        if (
            not source_identity
            or source.intent["execution_id"] != reconciliation["execution_id"]
            or source.intent["control"] != reconciliation["control_head"]
            or binding.intent["control"] != source.intent["control"]
            or binding.intent["prior_local"] != self.identity.checkpoint
            or binding.intent["prior_remote"] != reconciliation["execution_remote_head"]
            or binding.intent.get("transfer_origin") != _origin(
                reconciliation, rewrite_published, automatic,
            )
        ):
            raise ExecutionWorkspaceError("active transfer source or admission differs")

    def require_assignment(self):
        super().require_assignment()
        self.require_authority()

    def require_authority(self):
        owner, identity = self.owner, self.identity
        product = owner._observe_product_generation(identity.target)
        if not product["stable"]:
            if not (self.automatic and self.installation_started):
                raise ExecutionWorkspaceError(
                    f"stale update-base target: {product['reason']}"
                )
            # Finish the admitted C and its exact lease before considering any
            # new target. This does not authorize a worker against stale B.
            automatic_target(owner, identity.target, product)
        return require_control(owner, self.binding.intent["control"], identity.ticket)

    def require_source_retirement(self):
        if self.reconciliation.get("installed_source") is None:
            super().require_source_retirement()
        else:
            self.source.require_unlaunched_install(self.identity.checkpoint)

    def require_source(self, identity):
        if not self.installation_started:
            self.require_pin()
        super().require_source(identity)

    def require_eligible_result(self, result):
        if result.externally_resolved or (
            self.automatic and result.effect == TransferEffect.SOURCE_EMPTY
            and self.identity.original_base == self.identity.checkpoint
        ):
            return
        if result.effect == TransferEffect.CHANGED and result.path is not None:
            def trees(path, base, head):
                # Include the base tree; unchanged steps are meaningful only
                # relative to their immediate parent, not to the final tree.
                git = self.transfer._command
                root = git(path, "rev-parse", f"{base}^{{tree}}").strip()
                steps = git(
                    path, "log", "--first-parent", "--reverse", "--format=%T",
                    f"{base}..{head}", "--",
                ).splitlines()
                return [left != right for left, right in zip([root, *steps], steps)]

            identity = self.identity
            source = trees(self.owner.repo, identity.original_base, identity.checkpoint)
            target = trees(result.path, identity.target, result.head)
            if len(source) == len(target) and all(
                not changed or replayed for changed, replayed in zip(source, target)
            ):
                return
        raise ExecutionWorkspaceError("reconciled execution lineage is invalid")

    def complete(self):
        workspace = self.prepare()
        if workspace.dirty:
            raise ExecutionWorkspaceError("installed update-base checkout is dirty")
        owner, binding, identity = self.owner, self.binding, self.identity
        ticket = self.require_authority()
        checkpoint, target = workspace.head, identity.target
        remote = binding.latest_remote(binding.intent["prior_remote"])
        source = self.reconciliation
        evidence = rewritten_evidence_ref(identity.ticket, source["execution_id"])
        # Pin installed C separately; the original report and H retain the
        # source execution identity even when an upstream predecessor moved.
        create_pin(self.source, evidence, checkpoint, replay=True)
        if self.automatic and not self.progress["candidate"]["resolved"]:
            create_pin(binding, reconciliation_evidence_ref(
                identity.ticket, binding.intent["execution_id"],
            ), checkpoint, replay=True)
        owner._save_state(
            "agent_pending", clear_execution=True, clear_pre_worker_generation=True,
            transfer_progress=None, rework_provenance=None,
            reconciliation={
                **source, "status": "resolved", "resolution": "update-base",
                "resolution_class": ("update-base" if remote is None else
                                     "update-base with published-lineage rewrite"),
                "effective_base": target, "worker_checkpoint": checkpoint,
                "execution_remote_head": remote, "evidence_ref": evidence,
                "displaced_evidence_ref": self.evidence,
                "displaced_checkpoint": identity.checkpoint,
                "resumed_execution_id": binding.intent["execution_id"],
                "externally_resolved": self.progress["candidate"]["resolved"],
                **({"automatic": True} if self.automatic else {}),
            },
            local_head=target, remote_head=target, changed_paths="",
            control_head=binding.intent["control"],
            selected_ticket_id=ticket.id, selected_ticket_body=ticket.body,
            selected_ticket_title=ticket.title,
            selected_ticket_dependencies=list(ticket.depends_on),
            execution_ticket_id=ticket.id, execution_base_head=target,
            execution_admitted_base_head=target,
            execution_validated_product_head=target,
            execution_control_head=binding.intent["control"],
            execution_validated_control_head=binding.intent["control"],
            execution_branch=workspace.branch, execution_path=str(workspace.path),
            execution_id=binding.intent["execution_id"], execution_remote_head=remote,
            execution_resource_binding=binding.intent, worker_identity=None,
        )
        return 0


def require_control(owner, control, ticket_id):
    owner._validate_control_worktree()
    if (
        owner._resolve_commit_runtime(owner.control_worktree, "HEAD") != control
        or owner._execution_remote_head(owner.control_branch) != control
    ):
        raise ExecutionWorkspaceError("control revision changed during reconciliation")
    store = owner._ticket_store(head=control, require_clean=True)
    ticket = store.by_id.get(ticket_id)
    if ticket is None or ticket not in store.runnable:
        raise ExecutionWorkspaceError("reconciliation ticket is no longer compatible")
    return ticket


def is_same_parent_replacement(repo, original, target, *, runner):
    """Prove that two distinct commits are single-parent replacements."""
    parent_map = {}
    for commit in (original, target):
        try:
            parents = commit_parents(repo, commit, runner=runner)
        except GitObservationError:
            return False
        if parents is None:
            return False
        parent_map[commit] = parents
    return same_parent_replacement(original, target, parent_map)


def require_target_lineage(owner, base, target):
    # A failed observation is not evidence of non-ancestry and must not reach
    # the same-parent exception. This policy applies only to explicit requests.
    if not (base == target or owner._is_ancestor_runtime(owner.repo, base, target)
            or is_same_parent_replacement(
                owner.repo, base, target, runner=owner._git_runtime,
            )):
        raise DevlegateError(
            "requested reconciliation target is not a descendant of the original "
            "base or a proven same-parent replacement"
        )


def retain_stale_install(owner, value):
    """Turn a proved completed, unlaunched mechanical C into the next source."""
    base, checkpoint = value["effective_base"], value["worker_checkpoint"]
    product = owner._observe_product_generation(base)
    if product["stable"]:
        return False
    target = automatic_target(owner, base, product)
    if value.get("externally_resolved"):
        raise ExecutionWorkspaceError(
            "installed human resolution retained; preserve its edits before "
            f"choosing devlegate reset {value['ticket_id']}"
        )
    ticket = value["ticket_id"]
    require_control(owner, value["control_head"], ticket)
    binding = owner._execution_generation_binding()
    binding.require_unlaunched_install(checkpoint)
    if binding.require_local(clean=True).head != checkpoint:
        raise ExecutionWorkspaceError("installed checkpoint changed before reuse")
    execution = binding.intent["execution_id"]
    evidence = reconciliation_evidence_ref(ticket, execution)
    require_pin(binding, {"location": "local", "ref": evidence, "head": checkpoint})
    pending = {
        **value, "status": "pending", "resolution": None, "execution_id": execution,
        "original_base": base, "evidence_ref": evidence, "observed_product": target,
        "installed_source": value,
        "product_remote_head": target,
    }
    pending.pop("resumed_execution_id")
    owner._save_state("idle", reconciliation=pending)
    return True


def advance_pending(owner):
    """One owner pass over pending active reconciliation, including stale attempts."""
    value = owner.reconciliation_pending()
    ticket, base = value["ticket_id"], value["original_base"]
    progress = owner._state.get("transfer_progress") or {}
    intent = progress.get("active_binding")
    if intent:
        source = owner._execution_generation_binding()
        transaction = ActiveTransfer.admitted(owner, source, value)
        if transaction.installation_started or not transaction.automatic:
            if progress.get("conflict") and not progress.get("confirmation"):
                transaction.require_authority()
                raise ExecutionWorkspaceError(_waiting(progress))
            return complete_admitted(transaction)
    if requires_retry(owner, value):
        raise ExecutionWorkspaceError(
            f"interrupted execution retained; run devlegate retry {ticket} "
            "to authorize continuation"
        )
    product = owner._observe_product_generation(base)
    try:
        target = automatic_target(owner, base, product)
    except ProductLineageError as error:
        target = product["remote_head"]
        require_target_lineage(owner, base, target)
        hint = (
            f"; if intentional, synchronize the product checkout to the observed "
            f"remote and run devlegate reconcile authorize-product {ticket} "
            f"--execution {value['execution_id']} --to {target}"
            if not intent else "; admitted transfer retained"
        )
        raise ExecutionWorkspaceError(f"{error}{hint}") from error
    if target == base and value.get("installed_source") is not None:
        raise ExecutionWorkspaceError("product rolled back; installed source retained")
    if (target == base and not intent
            and not owner._state.get("execution_resource_binding")):
        # Retake the exact same-base publication path for legacy state. That
        # path proves workspace/branch, pinned H, and the predecessor itself.
        return owner._reconcile_resume_owned(ticket)
    source = owner._execution_generation_binding()
    if intent and intent["base"] != target:
        if target == base or not owner._is_ancestor_runtime(
            owner.repo, intent["base"], target,
        ):
            raise ExecutionWorkspaceError("admitted target lost forward lineage")
        transaction.require_supersedable()
    elif progress.get("conflict") and not progress.get("confirmation"):
        transaction.require_authority()
        raise ExecutionWorkspaceError(_waiting(progress))
    require_control(owner, value["control_head"], ticket)
    if value.get("installed_source") is None:
        source.require_worker_retirement()
    else:
        source.require_unlaunched_install(value["worker_checkpoint"])
    require_pin(source, {
        "location": "local", "ref": value["evidence_ref"],
        "head": value["worker_checkpoint"],
    })
    workspace = source.require_local(clean=True)
    if workspace.head != value["worker_checkpoint"] or (
        owner._execution_remote_head(value["execution_branch"])
        != value["execution_remote_head"]
    ):
        raise ExecutionWorkspaceError("execution checkpoint or upstream changed")
    synchronize_target(owner, product, target)
    if intent and intent["base"] != target:
        # Keep the old attempt and H on disk. A new generation cannot consume its
        # confirmation, even if a client submits the old candidate path again.
        owner._save_state("idle", transfer_progress=None)
        intent = None
    if target == base:
        return owner._reconcile_resume_owned(ticket)
    if not intent:
        transaction = plan_transfer(owner, value, target, automatic=True)
        transaction.save(**planned_progress(transaction))
    return complete_admitted(transaction)


def _waiting(progress):
    return waiting_message(progress["conflict"], "update-base",
                           progress.get("resolution_error"))


def requires_retry(owner, value):
    return bool(owner._state.get("manual_retry_required")) or (
        value.get("interruption_kind") == "operator_abort"
        and value.get("retry_authorized") is not True
    )


def plan_transfer(owner, value, target, *, automatic):
    owner._assert_repository_owner()
    if owner._stop_before_admission():
        raise ExecutionWorkspaceError("transfer paused for service shutdown")
    ticket_id = value["ticket_id"]
    base, checkpoint = value["original_base"], value["worker_checkpoint"]
    source = owner._execution_generation_binding()
    manager = source.manager
    if (manager.branch != value["execution_branch"]
            or str(manager.path) != value["execution_path"]):
        raise ExecutionWorkspaceError("reconciliation workspace binding is invalid")
    if "execution_remote_head" not in value:
        raise ExecutionWorkspaceError("reconciliation lacks execution predecessor")
    before = value["execution_remote_head"]
    product = owner._observe_product_generation(base)
    if not product["target_eligible"]:
        raise ExecutionWorkspaceError(str(product["reason"]))
    if product["local_head"] != target or product["remote_head"] != target:
        raise ExecutionWorkspaceError(
            "target must match current local and remote product HEAD"
        )
    if automatic:
        automatic_target(owner, base, product)
    else:
        require_target_lineage(owner, base, target)
        try:
            automatic_target(owner, base, product)
        except ProductLineageError:
            pass
        else:
            raise ExecutionWorkspaceError(
                "product lineage is eligible for automatic reconciliation"
            )
    control = value["control_head"]
    require_control(owner, control, ticket_id)
    if source.latest_remote(source.intent["prior_remote"]) != before:
        raise ExecutionWorkspaceError("active upstream predecessor is unproven")
    if before is not None and source.abandonment_remote_authority() != before:
        raise ExecutionWorkspaceError("published execution ref ownership is unproved")
    if before is not None and (
        (before == base and not (automatic and before == checkpoint))
        or not owner._is_ancestor_runtime(owner.repo, base, before)
        or not owner._is_ancestor_runtime(owner.repo, before, checkpoint)
    ):
        raise ExecutionWorkspaceError(
            "published execution ref is outside the proven execution lineage"
        )
    rewrite_published = before is not None
    binding = ResourceBinding.plan(
        manager, execution_id=new_execution_id(), base=target, control=control,
        ticket_blob=source.intent["ticket_blob"], remote=owner.remote_name,
        remote_head=before,
    )
    binding.intent["transfer_origin"] = _origin(value, rewrite_published, automatic)
    transaction = ActiveTransfer(
        owner, binding, source, value, rewrite_published, automatic,
    )
    transaction.require_source(transaction.identity)
    transaction.require_authority()
    return transaction


def planned_progress(transaction):
    return {
        "active_binding": transaction.binding.intent,
        "rewrite_published": transaction.binding.intent["prior_remote"] is not None,
        **({"automatic": True} if transaction.automatic else {}),
    }


def complete_admitted(transaction):
    binding = transaction.binding
    if not (binding.directory / "intent.json").exists():
        transaction.require_source(transaction.identity)
        binding.record_intent()
    return transaction.complete()
