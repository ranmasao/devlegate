# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Active reconciliation admission and RESUME continuation."""

from devlegate.abandonment_refs import create_pin
from devlegate.execution_result import new_execution_id
from devlegate.execution_workspace import ExecutionWorkspaceError
from devlegate.implementation_transfer import TransferEffect
from devlegate.git import GitObservationError, commit_parents
from devlegate.reconciliation import rewritten_evidence_ref, same_parent_replacement
from devlegate.resource_binding import ResourceBinding
from devlegate.runtime_state_validation import DevlegateError
from devlegate.transfer_transaction import TransferTransaction, origin_digest


def _origin(reconciliation, rewrite_published):
    return origin_digest({
        "ticket": reconciliation["ticket_id"],
        "report": reconciliation.get("execution_report"),
        "evidence": reconciliation["evidence_ref"],
        "rewrite_published": rewrite_published,
    })


class ActiveTransfer(TransferTransaction):
    origin = "update-base"

    def __init__(self, owner, binding, source, reconciliation, rewrite_published):
        self.reconciliation = reconciliation
        super().__init__(
            owner, binding, source, reconciliation["original_base"],
            reconciliation["worker_checkpoint"], reconciliation["evidence_ref"],
        )
        report = reconciliation.get("execution_report")
        if (
            not isinstance(report, dict)
            or report.get("execution_id") != source.intent["execution_id"]
            or report.get("resource_generation") != source.intent["generation"]
            or report.get("workspace_head") != self.identity.checkpoint
            or report.get("code_base_head") != self.identity.original_base
            or source.intent["execution_id"] != reconciliation["execution_id"]
            or source.intent["control"] != reconciliation["control_head"]
            or binding.intent["control"] != source.intent["control"]
            or binding.intent["prior_local"] != self.identity.checkpoint
            or binding.intent["prior_remote"] != reconciliation["execution_remote_head"]
            or binding.intent.get("transfer_origin") != _origin(
                reconciliation, rewrite_published,
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
            raise ExecutionWorkspaceError(
                f"stale update-base target: {product['reason']}"
            )
        return require_control(owner, self.binding.intent["control"], identity.ticket)

    def require_source(self, identity):
        if not self.installation_started:
            self.require_pin()
        super().require_source(identity)

    def require_eligible_result(self, result):
        if result.externally_resolved:
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


def update_base(owner, ticket_id, onto, *, rewrite_published=False, replay=False):
    owner._assert_repository_owner()
    if owner._stop_before_admission():
        raise ExecutionWorkspaceError("transfer paused for service shutdown")
    value = owner.reconciliation_pending()
    if value["ticket_id"] != ticket_id:
        raise ExecutionWorkspaceError(
            "requested ticket does not match pending reconciliation"
        )
    base, checkpoint = value["original_base"], value["worker_checkpoint"]
    target = owner._resolve_commit_runtime(owner.repo, onto)
    if target is None:
        raise ExecutionWorkspaceError("requested reconciliation target is not a commit")
    source = owner._execution_generation_binding()
    manager = source.manager
    if (manager.branch != value["execution_branch"]
            or str(manager.path) != value["execution_path"]):
        raise ExecutionWorkspaceError("reconciliation workspace binding is invalid")
    progress = owner._state.get("transfer_progress")
    if "execution_remote_head" not in value:
        raise ExecutionWorkspaceError("reconciliation lacks execution predecessor")
    before = value["execution_remote_head"]
    if target != base and before is not None and not rewrite_published and not replay:
        raise ExecutionWorkspaceError(
            f"published execution ref refs/heads/{manager.branch} requires "
            "--rewrite-published authorization"
        )
    if progress is not None:
        intent = progress.get("active_binding")
        if intent is None or intent["base"] != target:
            raise ExecutionWorkspaceError("target differs from admitted transfer")
        binding = ResourceBinding(manager, intent)
        rewrite_published = progress["rewrite_published"]
    else:
        product = owner._observe_product_generation(base)
        if product["branch"] != owner.current_branch:
            raise ExecutionWorkspaceError("product checkout is on the wrong branch")
        if product["dirty"]:
            raise ExecutionWorkspaceError("product checkout is dirty")
        if product["local_head"] != target:
            raise ExecutionWorkspaceError(
                "requested reconciliation target is not the current product HEAD"
            )
        if product["remote_head"] != target:
            raise ExecutionWorkspaceError(
                "requested reconciliation target is not the fresh product remote HEAD"
            )
        require_target_lineage(owner, base, target)
        if target == base:
            return owner._reconcile_resume_owned(ticket_id)
        control = value["control_head"]
        require_control(owner, control, ticket_id)
        if source.latest_remote(source.intent["prior_remote"]) != before:
            raise ExecutionWorkspaceError("active upstream predecessor is unproven")
        if before is not None and (
            before == base
            or not owner._is_ancestor_runtime(owner.repo, base, before)
            or not owner._is_ancestor_runtime(owner.repo, before, checkpoint)
        ):
            raise ExecutionWorkspaceError(
                "published execution ref is outside the proven execution lineage"
            )
        binding = ResourceBinding.plan(
            manager, execution_id=new_execution_id(), base=target, control=control,
            ticket_blob=source.intent["ticket_blob"], remote=owner.remote_name,
            remote_head=before,
        )
        binding.intent["transfer_origin"] = _origin(value, rewrite_published)
    transaction = ActiveTransfer(owner, binding, source, value, rewrite_published)
    if progress is None:
        transaction.require_source(transaction.identity)
        transaction.save(
            active_binding=binding.intent, rewrite_published=rewrite_published,
        )
    if not (binding.directory / "intent.json").exists():
        transaction.require_source(transaction.identity)
        binding.record_intent()
    return transaction.complete()
