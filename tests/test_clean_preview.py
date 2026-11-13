# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Disposable real-Git previews through the production owner admission path."""

import json
import sys
import threading

import pytest
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration

from devlegate.clean_preview import canonical_digest, inspect_clean
from devlegate.ipc_protocol import IPCRequest
from devlegate.ipc_server import dispatch_request
from devlegate.resource_binding import ResourceBinding
from devlegate.worker_egress import WorkerClaim, WorkerRunResult


@pytest.fixture
def owner(tmp_path, monkeypatch, request):
    repo, config, _state = control_fixture(tmp_path)
    if getattr(request, "param", None) in {"submodule", "nested"}:
        dependency = tmp_path / "dependency"
        dependency.mkdir()
        git(dependency, "init", "-b", "main")
        git(dependency, "config", "user.name", "Fixture")
        git(dependency, "config", "user.email", "fixture@example.com")
        (dependency / "data").write_text("dependency\n")
        git(dependency, "add", ".")
        git(dependency, "commit", "-m", "dependency fixture")
        monkeypatch.setenv("GIT_ALLOW_PROTOCOL", "file")
        if request.param == "nested":
            leaf = tmp_path / "leaf"
            git(tmp_path, "clone", str(dependency), str(leaf))
            git(dependency, "submodule", "add", str(leaf), "nested")
            git(dependency, "commit", "-am", "nested fixture")
        git(repo, "submodule", "add", str(dependency), "dependency")
        git(repo, "commit", "-am", "submodule fixture")
        git(repo, "push", "origin", "main")
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    engine = Devlegate(config)

    def worker(workspace, _prompt, **_kwargs):
        (workspace.path / "implementation.txt").write_text("implementation\n")
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None,
        )

    monkeypatch.setattr(engine._workers, "run", worker)
    assert run_test_iteration(engine) == 0
    return engine


def snapshot(root):
    return {
        str(p.relative_to(root)): (
            p.read_bytes(), p.stat().st_mtime_ns, p.stat().st_ino,
        )
        for p in root.rglob("*") if p.is_file() and not p.is_symlink()
    }


def test_fresh_preview_stable_read_only(owner):
    before_store = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    first = inspect_clean(owner, "T-1")
    assert first == inspect_clean(owner, "T-1")
    assert first["digest"] is not None, first["blockers"]
    assert first["identity"]["executions"][0]["proof"]["local_state"] == "proved"
    assert first["identity"]["resources"]["dirty"] is False
    assert first["mutation_authorized"] is False
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == before_store


def test_owner_ipc_admission_does_not_persist_or_launch(owner, monkeypatch):
    monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: pytest.fail("launch"))
    owner.begin_hosted_owner()
    result = []
    before = owner._runtime_store.observe()
    before_files = snapshot(owner.repo.parent)

    def client():
        result.append(dispatch_request(owner, IPCRequest(
            1, "preview-one", "clean-inspect", {"ticket_id": "T-1"},
        )))

    thread = threading.Thread(target=client)
    thread.start()
    assert owner._service_wake.wait(2)
    command, _scheduler = owner._claim_owner_work(True)
    assert command is not None and not command.mutable
    owner._admit_operator_command(command)
    owner._release_operator_command()
    thread.join(2)
    owner.end_hosted_owner()
    assert result[0]["informational"]
    assert snapshot(owner.repo.parent) == before_files
    assert owner._runtime_store.observe() == before


@pytest.mark.parametrize("change", ["dirty", "locked", "symlink", "missing-ref",
                                   "changed-ref", "recreated", "foreign", "absent"])
def test_resource_conflicts_are_read_only(owner, change):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    path = binding.manager.path
    if change == "dirty":
        (path / "implementation.txt").write_text("dirty\n")
    elif change == "locked":
        git(owner.repo, "worktree", "lock", str(path))
    elif change == "symlink":
        moved = path.with_name("moved")
        path.rename(moved)
        path.symlink_to(moved, target_is_directory=True)
    elif change == "missing-ref":
        git(owner.repo, "update-ref", "-d", str(binding.intent["ref"]))
    elif change == "changed-ref":
        git(
            owner.repo, "update-ref", str(binding.intent["ref"]),
            binding.intent["base"],
        )
    else:
        head = git(path, "rev-parse", "HEAD").stdout.strip()
        git(owner.repo, "worktree", "remove", str(path))
        if change == "recreated":
            git(owner.repo, "worktree", "add", str(path), binding.manager.branch)
            assert git(path, "rev-parse", "HEAD").stdout.strip() == head
        elif change == "foreign":
            path.mkdir()
            git(path, "init")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert result["blockers"]
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("field", ["worker_identity", "automatic_retry",
                                   "reconciliation", "operator_recovery",
                                   "accepted_integration", "execution_ref_retirement",
                                   "abandonment_retirements",
                                   "workspace_cleanup_pending"])
def test_pending_authority_invalidates_preview(owner, field):
    initial = inspect_clean(owner, "T-1")
    owner._state[field] = {"pending": "fixture"}
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert result["identity"] != initial["identity"]
    assert any("ownership" in b or field in b for b in result["blockers"])


def test_legacy_report_is_never_adopted(owner):
    # Disposable TASK-129/TASK-073-like historical evidence: exact checkpoint,
    # matching path, no independent receipt. Production has no ticket exceptions.
    manager = owner.recovery_workspace_manager("T-1")
    binding = ResourceBinding.for_execution(manager, owner._state["execution_id"])
    binding.directory.rename(binding.directory.with_name("unbound-history"))
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert any("unproven" in b for b in result["blockers"])
    assert snapshot(owner.repo.parent) == before


def test_canonical_digest_binds_every_identity_leaf(owner):
    identity = inspect_clean(owner, "T-1")["identity"]
    digest = canonical_digest(identity)
    reordered = dict(reversed(list(identity.items())))
    assert canonical_digest(reordered) == digest

    def walk(value, path=()):
        if isinstance(value, dict):
            for key, item in value.items():
                yield from walk(item, (*path, key))
        elif isinstance(value, list):
            for key, item in enumerate(value):
                yield from walk(item, (*path, key))
        else:
            yield path

    for path in walk(identity):
        changed = json.loads(json.dumps(identity))
        target = changed
        for key in path[:-1]:
            target = target[key]
        target[path[-1]] = {"changed": True}
        assert canonical_digest(changed) != digest


@pytest.mark.parametrize("change", ["drift", "absent", "unavailable"])
def test_exact_upstream_observation(owner, change):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    reference = str(binding.intent["ref"])
    remote = binding.intent["remote_urls"][0]
    if change == "unavailable":
        from pathlib import Path

        Path(remote).rename(Path(remote).with_name("unavailable.git"))
    elif change == "absent":
        git(owner.repo, "push", "origin", f":{reference}")
    else:
        git(
            owner.repo, "push", "--force", "origin",
            f"{binding.intent['base']}:{reference}",
        )
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    inventory = result["identity"]["resources"]
    assert inventory["upstream_state"] == {
        "drift": "observed", "absent": "absent", "unavailable": "unavailable",
    }[change]
    assert result["digest"] is None
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize(
    "change", ["report", "review", "generation", "admin", "filter"],
)
def test_evidence_and_unsafe_configuration_refuse_without_effect(owner, change):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    if change == "report":
        path = (owner.control_worktree / "executions/T-1"
                / f"{owner._state['execution_id']}.json")
        value = json.loads(path.read_text())
        value["resource_generation"] = "f" * 64
        path.write_text(json.dumps(value))
    elif change == "review":
        path = (owner.control_worktree / "reviews/T-1"
                / f"{owner._state['execution_id']}.md")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("contradictory review\n")
        git(owner.control_worktree, "add", ".")
        git(owner.control_worktree, "commit", "-m", "contradictory review fixture")
    elif change == "generation":
        path = binding.directory / "receipt.json"
        value = json.loads(path.read_text())
        value["generation"] = "f" * 64
        path.write_text(json.dumps(value))
    elif change == "admin":
        (binding._admin() / "CHERRY_PICK_HEAD").write_text(str(binding.intent["base"]))
    else:
        # Would write into the worktree if status invoked it.
        from pathlib import Path

        git(binding.manager.path, "config", "filter.fixture.clean", "touch FILTER-RAN")
        attributes = Path(git(
            binding.manager.path, "rev-parse", "--path-format=absolute",
            "--git-path", "info/attributes",
        ).stdout.strip())
        attributes.parent.mkdir(exist_ok=True)
        attributes.write_text("* filter=fixture\n")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert snapshot(owner.repo.parent) == before
    assert not (binding.manager.path / "FILTER-RAN").exists()


def test_absent_unexecuted_ticket_has_no_invented_binding(owner):
    path = owner.control_worktree / owner.todo_path / "T-2.md"
    path.write_text((owner.control_worktree / owner.review_path / "T-1.md").read_text())
    git(owner.control_worktree, "add", ".")
    git(owner.control_worktree, "commit", "-m", "unexecuted ticket fixture")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-2")
    assert result["identity"]["resources"]["path_state"] == "absent"
    assert result["identity"]["executions"] == []
    assert result["digest"] is None
    assert snapshot(owner.repo.parent) == before


def test_cli_clean_inspect_uses_existing_read_only_ipc(owner, monkeypatch, capsys):
    import devlegate.cli as cli

    expected = inspect_clean(owner, "T-1")
    calls = []

    def request(_socket, method, payload):
        calls.append((method, payload))
        return expected

    monkeypatch.setattr(cli, "request", request)
    monkeypatch.setattr(sys, "argv", [
        "devlegate", "clean", "inspect", "T-1", "--json",
    ])
    assert cli.main() == 0
    assert calls == [("clean-inspect", {"ticket_id": "T-1"})]
    assert json.loads(capsys.readouterr().out) == expected


@pytest.mark.parametrize("owner", ["submodule"], indirect=True)
@pytest.mark.parametrize("change", ["pristine", "dirty", "filter"])
def test_recursive_submodule_observation_is_read_only(owner, change):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    dependency = binding.manager.path / "dependency"
    if change == "dirty":
        (dependency / "data").write_text("changed\n")
    elif change == "filter":
        git(dependency, "config", "filter.fixture.clean", "touch FILTER-RAN")
        from pathlib import Path

        admin = Path(git(dependency, "rev-parse", "--absolute-git-dir").stdout.strip())
        (admin / "info").mkdir(exist_ok=True)
        (admin / "info/attributes").write_text("* filter=fixture\n")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert (result["digest"] is not None) == (change == "pristine")
    assert snapshot(owner.repo.parent) == before
    assert not (dependency / "FILTER-RAN").exists()


@pytest.mark.parametrize("owner", [None, "submodule", "nested"], indirect=True)
@pytest.mark.parametrize("driver", ["clean", "process"])
@pytest.mark.parametrize("active", [False, True])
@pytest.mark.parametrize("scope", ["local", "global", "system", "include"])
def test_effective_filter_gate(owner, tmp_path, monkeypatch, driver, active, scope):
    from pathlib import Path

    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    target = binding.manager.path
    if (target / "dependency").exists():
        target /= "dependency"
        if (target / "nested").exists():
            target /= "nested"
    sentinel = tmp_path / "FILTER-RAN"
    command = f'touch "{sentinel}"; cat'
    if scope == "local":
        git(target, "config", f"filter.fixture.{driver}", command)
    else:
        config = tmp_path / "filter-config"
        git(tmp_path, "config", "--file", str(config),
            f"filter.fixture.{driver}", command)
        if scope == "include":
            git(target, "config", "include.path", str(config))
        else:
            monkeypatch.setenv(f"GIT_CONFIG_{scope.upper()}", str(config))
            if scope == "system":
                monkeypatch.delenv("GIT_CONFIG_NOSYSTEM", raising=False)
    # info/attributes exercises effective attributes without dirtying a tracked
    # file or running a filter during fixture setup. Inactive driver stays set.
    attributes = Path(git(
        target, "rev-parse", "--path-format=absolute", "--git-path", "info/attributes",
    ).stdout.strip())
    attributes.parent.mkdir(exist_ok=True)
    attributes.write_text(
        "* filter=fixture\n" if active else "nonexistent/** filter=fixture\n",
    )
    before_store = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    first = inspect_clean(owner, "T-1")
    assert (first["digest"] is not None) == (not active), first["blockers"]
    if active:
        assert any("active Git clean/process filter" in b for b in first["blockers"])
    else:
        assert inspect_clean(owner, "T-1")["digest"] == first["digest"]
    assert not sentinel.exists()
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == before_store


@pytest.mark.parametrize("source", ["worktree", "global", "index"])
def test_effective_attribute_sources_refuse_without_running_filter(
    owner, tmp_path, source,
):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    target = binding.manager.path
    sentinel = tmp_path / "FILTER-RAN"
    attributes = target / ".gitattributes"
    attributes.write_text("[attr]danger filter=fixture\n*.txt danger\n")
    if source == "global":
        global_attributes = tmp_path / "global-attributes"
        attributes.rename(global_attributes)
        git(target, "config", "core.attributesFile", str(global_attributes))
    elif source == "index":
        git(target, "add", ".gitattributes")
        attributes.unlink()  # Git falls back to the indexed attributes.
    git(target, "config", "filter.fixture.clean", f'touch "{sentinel}"; cat')
    before_store = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert any("active Git clean/process filter" in b for b in result["blockers"])
    assert not sentinel.exists()
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == before_store


@pytest.mark.parametrize("failure", ["config", "attributes", "drift"])
def test_unknown_conversion_observation_is_unproven(owner, monkeypatch, failure):
    import subprocess

    import devlegate.clean_preview as preview

    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    git(binding.manager.path, "config", "filter.fixture.clean", "false")
    original = preview.observe_git
    configs = 0

    def observe(repo, *args, **kwargs):
        nonlocal configs
        if repo == binding.manager.path:
            if "config" in args and "--list" in args:
                configs += 1
                if failure == "config":
                    return subprocess.CompletedProcess(args, 128, "", "unreadable")
                if failure == "drift" and configs % 2 == 0:
                    result = original(repo, *args, **kwargs)
                    result.stdout += "filter.changed.clean\nfalse\0"
                    return result
            if "check-attr" in args and failure == "attributes":
                return subprocess.CompletedProcess(args, 0, "malformed", "")
            if "status" in args:
                pytest.fail("unsafe status observation")
        return original(repo, *args, **kwargs)

    monkeypatch.setattr(preview, "observe_git", observe)
    before_store = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert result["identity"]["executions"][0]["proof"]["state"] == "unproven"
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == before_store


@pytest.mark.parametrize("driver", ["set", "unset", "unspecified"])
def test_filter_names_colliding_with_attribute_states_fail_closed(
    owner, tmp_path, driver,
):
    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    target = binding.manager.path
    sentinel = tmp_path / "FILTER-RAN"
    (target / ".gitattributes").write_text(f"*.txt filter={driver}\n")
    git(target, "config", f"filter.{driver}.clean", f'touch "{sentinel}"; cat')
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert any("active Git clean/process filter" in b for b in result["blockers"])
    assert not sentinel.exists()
    assert snapshot(owner.repo.parent) == before


def test_multiple_historical_reports_never_select_by_name_or_sha(owner):
    directory = owner.control_worktree / "executions/T-1"
    original = directory / f"{owner._state['execution_id']}.json"
    older = json.loads(original.read_text())
    older["execution_id"] = "historical-unbound"
    older.pop("resource_generation", None)
    (directory / "historical-unbound.json").write_text(json.dumps(older))
    git(owner.control_worktree, "add", ".")
    git(owner.control_worktree, "commit", "-m", "ambiguous historical fixture")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert len(result["identity"]["executions"]) == 2
    assert result["digest"] is None
    assert any("not an exact lifecycle" in b for b in result["blockers"])
    assert snapshot(owner.repo.parent) == before


def test_unknown_ticket_refusal_on_owner_ipc_has_no_effect(owner):
    from devlegate.runtime import DevlegateError, OperatorCommand

    command = OperatorCommand(
        request_id="missing", method="clean-inspect", fingerprint="fixture",
        ticket_id="missing-ticket", mutable=False,
    )
    before = snapshot(owner.repo.parent)
    with pytest.raises(DevlegateError, match="canonical committed state"):
        owner._admit_operator_command(command)
    assert command.admission_event.is_set()
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("change", ["orphan-review", "deleted-review", "accepted"])
def test_disposition_gaps_fail_closed(owner, change):
    control = owner.control_worktree
    if change == "accepted":
        (control / owner.accepted_path).mkdir(parents=True, exist_ok=True)
        (control / owner.review_path / "T-1.md").rename(
            control / owner.accepted_path / "T-1.md",
        )
    else:
        artifact = control / "reviews/T-1/unmatched.md"
        artifact.parent.mkdir(parents=True, exist_ok=True)
        artifact.write_text("unattributed historical feedback\n")
    git(control, "add", ".")
    git(control, "commit", "-m", "disposition gap fixture")
    if change == "deleted-review":
        artifact.unlink()
        git(control, "add", ".")
        git(control, "commit", "-m", "deleted historical feedback fixture")
    before = snapshot(owner.repo.parent)
    result = inspect_clean(owner, "T-1")
    assert result["digest"] is None
    assert any({
        "orphan-review": "no corresponding immutable report",
        "deleted-review": "artifact disappeared",
        "accepted": "no proven accepted review",
    }[change] in b for b in result["blockers"])
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("change", ["foreign", "locked", "admin"])
def test_control_registration_is_proven_not_inferred_from_head(owner, change):
    from devlegate.abandonment_evidence import EvidenceError

    control = owner.control_worktree
    if change == "foreign":
        head = git(control, "rev-parse", "HEAD").stdout.strip()
        foreign = owner.repo.parent / "foreign-control"
        git(owner.repo, "clone", "--no-checkout", str(owner.repo), str(foreign))
        git(foreign, "checkout", "-b", owner.control_branch, head)
        owner.control_worktree = foreign
    elif change == "locked":
        git(owner.repo, "worktree", "lock", str(control))
    else:
        from pathlib import Path

        admin = git(control, "rev-parse", "--absolute-git-dir").stdout.strip()
        (Path(admin) / "MERGE_HEAD").write_text("f" * 40)
    before = snapshot(owner.repo.parent)
    with pytest.raises(EvidenceError, match="control .*conflict"):
        inspect_clean(owner, "T-1")
    assert snapshot(owner.repo.parent) == before


def test_owner_run_loop_preview_avoids_status_refresh(owner, monkeypatch):
    from devlegate.runtime import OperatorCommand

    command = OperatorCommand(
        request_id="loop-preview", method="clean-inspect", fingerprint="fixture",
        ticket_id="T-1", mutable=False,
    )
    owner._operator_command = command
    monkeypatch.setattr(owner, "status_view", lambda: pytest.fail("status refresh"))
    monkeypatch.setattr(owner, "run_iteration", lambda: pytest.fail("scheduler"))
    before_store = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    assert owner._run_polling(once=True, lock_handle=object()) == 0
    assert command.admission_result["digest"] is not None
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == before_store


def test_abandoned_disposition_reuses_independent_terminal_proof(
    tmp_path_factory, monkeypatch,
):
    from test_abandonment_publication import review, setup_analysis

    # Evidence sockets must also fit AF_UNIX's limit under pytest-xdist.
    tmp_path = tmp_path_factory.mktemp("cp")
    engine, _config, _calls, _binding, _target = setup_analysis(tmp_path, monkeypatch)
    run_test_iteration(engine)
    review(engine)
    run_test_iteration(engine)
    before = snapshot(engine.repo.parent)
    result = inspect_clean(engine, "A-TARGET")
    assert result["identity"]["abandoned_disposition"]["state"] == "proved"
    assert result["identity"]["abandoned_disposition"]["artifacts"]
    assert result["digest"] is None  # No generation ever existed for this target.
    assert snapshot(engine.repo.parent) == before
