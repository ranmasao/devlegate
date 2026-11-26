# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Universal inspection uses exact immutable evidence and existing owner IO."""

import json
import os
import shlex
import shutil
import sys
import threading
import unicodedata
from time import perf_counter

import pytest
from _cli_support import _long_worker_script, _worker_body_started
from _control_support import Devlegate, control_fixture, git, invoke
from runtime_helpers import run_test_iteration
from service_harness import LiveService
from test_clean_preview import owner as preview_owner  # noqa: F401
from test_clean_preview import snapshot

from devlegate.abandonment_evidence import EvidenceError
from devlegate.inspection import (
    ExecutionObservation,
    ServiceObservation,
    TicketObservation,
    inspect_object,
)
from devlegate.ipc_client import request
from devlegate.ipc_protocol import IPCProtocolError, IPCRequest
from devlegate.ipc_server import dispatch_request
from devlegate.operational_log import execution_log_path
from devlegate.project_context import load_worker_project_context
from devlegate.prompt_journal import validate_component_manifest
from devlegate.runtime import OperatorCommand
from devlegate.worker_egress import WorkerClaim, WorkerRunResult
from devlegate.worker_prompt import (
    WorkDirective,
    WorkerPromptInput,
    render_worker_prompt,
    worker_prompt_artifact_path,
    worker_prompt_sha256,
)


@pytest.fixture
def owner(request):
    return request.getfixturevalue("preview_owner")


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
def test_views_read_only(owner, kind):
    owner.status_view()  # Publish diagnostics before recording the read boundary.
    identifier = owner._state["execution_id"] if kind == "execution" else "T-1"
    if kind == "service":
        identifier = None
    state = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    view = inspect_object(owner, kind, identifier)
    assert view == inspect_object(owner, kind, identifier)
    assert view["schema"] == (
        "devlegate.inspect-workspace.v2" if kind == "workspace"
        else f"devlegate.inspect-{kind}.v1"
    )
    assert view["informational"] and not view["mutation_authorized"]
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == state
    if kind == "ticket":
        assert set(view["observation"]) == TicketObservation.__required_keys__
        assert view["observation"]["state"] == "review"
        assert view["observation"]["dependency_graph"]["T-1"][0]["blob"]
    if kind == "execution":
        observation = view["observation"]
        assert set(observation) == ExecutionObservation.__required_keys__
        assert observation["id"] == identifier
        assert observation["provenance_state"] == "proved"
        assert observation["latest_execution"] == "latest"
        assert observation["independent_generation"]["current_local"] == "proved"
        assert observation["checkpoint"]["head"]
    if kind == "service":
        assert set(view["observation"]) == ServiceObservation.__required_keys__


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
def test_owner_queue_no_refresh_or_persistence(owner, monkeypatch, kind):
    owner.status_view()
    command = OperatorCommand(
        request_id=f"inspect-{kind}", method="inspect", onto=kind,
        ticket_id=owner._state["execution_id"] if kind == "execution" else "T-1",
        fingerprint="fixture", mutable=False,
    )
    owner._operator_observation = command
    monkeypatch.setattr(owner, "status_view", lambda: pytest.fail("status refresh"))
    monkeypatch.setattr(owner, "run_iteration", lambda: pytest.fail("scheduler"))
    state = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)
    assert owner._run_polling(once=True, lock_handle=object()) == 0
    assert command.admission_result["informational"]
    assert snapshot(owner.repo.parent) == before
    assert owner._runtime_store.observe() == state


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
def test_cli_read_only_ipc(owner, monkeypatch, capsys, kind):
    import devlegate.cli as cli

    calls = []
    monkeypatch.setattr(cli, "request", lambda *args, **kwargs: (
        calls.append((args[1:], kwargs)) or {"informational": True}
    ))
    arguments = ["devlegate", "inspect", kind]
    if kind != "service":
        arguments.append("exact-id")
    monkeypatch.setattr(sys, "argv", [*arguments, "--json"])
    assert cli.main() == 0
    assert calls == [(("inspect", {
        "object_type": kind, "object_id": None if kind == "service" else "exact-id",
    }), {})]
    assert json.loads(capsys.readouterr().out) == {"informational": True}


@pytest.mark.parametrize("payload", [
    {}, {"object_type": "ticket", "object_id": "../T-1"},
    {"object_type": "service", "object_id": "T-1"},
    {"object_type": "execution", "object_id": None},
    {"object_type": "prune", "object_id": "T-1"},
    {"object_type": "workspace", "object_id": "T-1", "apply": True},
    {"object_type": [], "object_id": "T-1"},
    {"object_type": "ticket", "object_id": "T-1", "include_prompt": True},
    {"object_type": "execution", "object_id": "exact", "include_prompt": 1},
])
def test_ipc_strict_validation(payload):
    with pytest.raises(IPCProtocolError, match="inspection"):
        dispatch_request(object(), IPCRequest(1, "invalid", "inspect", payload))


@pytest.fixture
def journal(owner):
    identifier = owner._state["execution_id"]
    return identifier, worker_prompt_artifact_path(owner.state_dir, identifier)


@pytest.mark.parametrize(("change", "status", "reason"), [
    ("missing", "missing", ""),
    ("legacy", "present", ""),
    ("digest", "unavailable", "SHA-256"),
    ("manifest", "unavailable", "manifest"),
    ("project-source", "unavailable", "component provenance"),
    ("execution", "unavailable", "mismatch"),
    ("ticket", "unavailable", "mismatch"),
    ("control", "unavailable", "admission"),
    ("project", "unavailable", "foreign"),
    ("file-link", "unavailable", "linked"),
    ("directory-link", "unavailable", "safely read"),
    ("fifo", "unavailable", "non-regular"),
    ("oversized", "unavailable", "IPC limit"),
    ("malformed", "unavailable", "Malformed"),
])
def test_prompt_journal_evidence_gaps(owner, journal, change, status, reason):
    from devlegate.ipc_protocol import MAX_PAYLOAD_BYTES

    identifier, path = journal
    payload = json.loads(path.read_text())
    path.unlink()
    if change == "missing":
        pass
    elif change == "file-link":
        alternate = path.with_suffix(".alternate")
        alternate.write_text(json.dumps(payload))
        path.symlink_to(alternate)
    elif change == "directory-link":
        alternate = path.parent.with_name("alternate")
        path.parent.rename(alternate)
        (alternate / path.name).write_text(json.dumps(payload))
        path.parent.symlink_to(alternate)
    elif change == "fifo":
        os.mkfifo(path)
    elif change == "oversized":
        with path.open("wb") as stream:
            stream.truncate(MAX_PAYLOAD_BYTES + 1)
    elif change == "malformed":
        path.write_bytes(b"\xff")
    else:
        if change == "legacy":
            del payload["components"], payload["project_key"]
        elif change == "manifest":
            payload["components"][0]["offset"] = 1
        elif change == "project-source":
            payload["components"][2]["source"]["documents"] = [{"path": "PRIVATE"}]
        else:
            fields = {"digest": "prompt_sha256", "execution": "execution_id",
                      "ticket": "ticket_id", "control": "admitted_control_head",
                      "project": "project_key"}
            payload[fields[change]] = "PRIVATE-FOREIGN-VALUE"
        path.write_text(json.dumps(payload))
        path.chmod(0o600)

    observation = inspect_object(
        owner, "execution", identifier, include_prompt=True,
    )["observation"]
    assert observation["provenance_state"] == "proved"
    assert observation["report"]["execution_id"] == identifier
    prompt = observation["worker_prompt"]
    assert prompt["status"] == status
    assert reason in prompt.get("reason", "")
    assert "PRIVATE" not in json.dumps(prompt)
    if change == "legacy":
        assert prompt["manifest"] == "absent"
        assert prompt["project_binding"] == "local generation"
        assert prompt["text_status"] == "unavailable"
        assert prompt["delivery"] == "constructed"
    assert "text" not in prompt


@pytest.mark.parametrize("kind", ["ticket", "workspace", "service"])
def test_cli_rejects_prompt_for_other_objects(monkeypatch, kind):
    from devlegate.cli import main

    args = ["T-1"] if kind != "service" else []
    monkeypatch.setattr(sys, "argv", ["devlegate", "inspect", kind, *args, "--prompt"])
    with pytest.raises(SystemExit) as error:
        main()
    assert error.value.code == 2


@pytest.mark.parametrize("identifier", ["missing", "T-1", "HEAD", "main"])
def test_execution_never_falls_back(owner, identifier):
    before = snapshot(owner.repo.parent)
    with pytest.raises(EvidenceError, match="unique canonical committed report"):
        inspect_object(owner, "execution", identifier)
    assert snapshot(owner.repo.parent) == before


def test_execution_prefix_is_not_an_id(owner):
    identifier = owner._state["execution_id"]
    with pytest.raises(EvidenceError, match="unique canonical committed report"):
        inspect_object(owner, "execution", identifier[:-1])


@pytest.mark.parametrize(
    "change", ["duplicate", "foreign", "rewritten", "deleted-review"],
)
def test_execution_evidence_gaps(owner, change):
    control = owner.control_worktree
    identifier = owner._state["execution_id"]
    report = control / f"executions/T-1/{identifier}.json"
    if change == "duplicate":
        destination = control / f"executions/OTHER/{identifier}.json"
        destination.parent.mkdir()
        destination.write_bytes(report.read_bytes())
    elif change == "foreign":
        value = json.loads(report.read_text())
        value["ticket_id"] = "OTHER"
        report.write_text(json.dumps(value))
    elif change == "rewritten":
        value = json.loads(report.read_text())
        value["worker_claim"]["summary"] = "rewritten fixture"
        report.write_text(json.dumps(value))
    else:
        review = control / f"reviews/T-1/{identifier}.md"
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text("exact historical feedback\n")
        git(control, "add", ".")
        git(control, "commit", "-m", "feedback fixture")
        review.unlink()
    git(control, "add", ".")
    git(control, "commit", "-m", "evidence gap fixture")
    before = snapshot(owner.repo.parent)
    if change in {"duplicate", "foreign"}:
        with pytest.raises(EvidenceError):
            inspect_object(owner, "execution", identifier)
    else:
        view = inspect_object(owner, "execution", identifier)
        assert view["blockers"]
        if change == "rewritten":
            assert view["observation"]["provenance_state"] == "unproven"
            versions = view["observation"]["history"][
                f"executions/T-1/{identifier}.json"
            ]
            assert len(versions) == 2
        else:
            assert view["observation"]["review"]["state"] == "deleted"
            assert any("exact historical feedback" in (version["text"] or "")
                       for version in view["observation"]["history"]
                       [f"reviews/T-1/{identifier}.md"])
            from devlegate.cli import _render_execution_inspection

            rendered = _render_execution_inspection(view)
            assert "Review state: deleted" in rendered
            assert "historical immutable review disappeared" in rendered
            assert "Blob: deleted" in rendered
    assert snapshot(owner.repo.parent) == before


def test_unbound_execution_still_has_immutable_checkpoint(owner):
    identifier = owner._state["execution_id"]
    from devlegate.resource_binding import ResourceBinding

    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), identifier,
    )
    binding.directory.rename(binding.directory.with_name("unbound"))
    before = snapshot(owner.repo.parent)
    view = inspect_object(owner, "execution", identifier)
    assert view["observation"]["provenance_state"] == "proved"
    assert view["observation"]["independent_generation"]["state"] == "unproven"
    assert view["blockers"]
    from devlegate.cli import _render_execution_inspection

    text = _render_execution_inspection(view)
    assert "Publication provenance: proved" in text
    assert "Generation state: unproven" in text
    assert "Current local: unknown" in text
    assert "independent generation unproven:" in text
    assert snapshot(owner.repo.parent) == before


def test_workspace_private_authority_is_hash_bound(owner):
    before = inspect_object(owner, "workspace", "T-1")
    owner._state["private_fixture"] = "DO-NOT-PUBLISH"
    after = inspect_object(owner, "workspace", "T-1")
    assert "DO-NOT-PUBLISH" not in json.dumps(after)
    assert before["identity"]["authority"] != after["identity"]["authority"]
    assert before["digest"] != after["digest"]
    intent = after["identity"]["executions"][0]["intent"]
    assert "remote_urls" not in intent
    assert len(intent["remote_urls_sha256"]) == 64


@pytest.mark.parametrize("columns", [80, 120])
@pytest.mark.parametrize("tty", [True, False])
def test_workspace_human_inventory_preserves_snapshot(owner, monkeypatch, columns, tty):
    from devlegate.clean_preview import canonical_digest
    from devlegate.cli import _render_workspace_inspection

    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    view = inspect_object(owner, "workspace", "T-1")
    before = json.dumps(view, sort_keys=True)
    text = _render_workspace_inspection(view)
    assert json.dumps(view, sort_keys=True) == before
    assert view["digest"] == canonical_digest(view["identity"])
    assert "Snapshot complete: yes" in text and "Mutation authorized: no" in text
    assert "Ticket ID: T-1" in text and "Local proof: proved" in text
    assert "upstream continuity is unknown" in text
    assert view["identity"]["resources"]["path"] in text.replace("\n    ", "")
    assert all(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                   for c in line) <= columns for line in text.splitlines())


def test_workspace_human_unproven_and_large_evidence(owner, monkeypatch):
    from devlegate.cli import _render_workspace_inspection
    from devlegate.resource_binding import ResourceBinding

    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    binding.directory.rename(binding.directory.with_name("unbound"))
    view = inspect_object(owner, "workspace", "T-1")
    view["identity"]["artifacts"] = {str(i): {"nested": "x" * 10000}
                                     for i in range(500)}
    view["blockers"].append("界" * 200 + "\x1b")
    monkeypatch.setenv("COLUMNS", "80")
    text = _render_workspace_inspection(view)
    assert "Snapshot complete: no" in text and "Canonical digest: unknown" in text
    assert "Unproven evidence:" in text and "Local proof: unknown" in text
    assert "Committed artifacts: 500" in text and "x" * 100 not in text
    assert "\\u001b" in text and "\x1b" not in text
    assert all(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                   for c in line) <= 80 for line in text.splitlines())


@pytest.mark.parametrize("change", ["dirty", "locked", "disconnected"])
def test_workspace_human_real_blockers(owner, change):
    from pathlib import Path

    from devlegate.cli import _render_workspace_inspection
    from devlegate.resource_binding import ResourceBinding

    binding = ResourceBinding.for_execution(
        owner.recovery_workspace_manager("T-1"), owner._state["execution_id"],
    )
    if change == "dirty":
        (binding.manager.path / "implementation.txt").write_text("dirty\n")
    elif change == "locked":
        git(owner.repo, "worktree", "lock", str(binding.manager.path))
    else:
        remote = Path(git(owner.repo, "remote", "get-url", "origin").stdout.strip())
        remote.rename(remote.with_name("disconnected.git"))
    before = snapshot(owner.repo.parent)
    view = inspect_object(owner, "workspace", "T-1")
    text = _render_workspace_inspection(view)
    assert "Snapshot complete: no" in text
    for blocker in view["blockers"]:
        safe = "".join(c if c.isprintable() else f"\\u{ord(c):04x}" for c in blocker)
        assert "".join(safe.split()) in "".join(text.split())
    assert ("Dirty: yes" if change == "dirty" else
            "Locked: yes" if change == "locked" else
            "Upstream state: unavailable") in text
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("owner", ["submodule"], indirect=True)
def test_workspace_human_submodule_observation(owner):
    from devlegate.cli import _render_workspace_inspection

    view = inspect_object(owner, "workspace", "T-1")
    text = _render_workspace_inspection(view)
    assert f"Submodules: {view['identity']['resources']['submodules']}" in text
    assert "Local proof:" in text


def test_workspace_cli_human_and_machine(owner, monkeypatch, capsys):
    import devlegate.cli as cli
    from devlegate._vendor import nanoyaml

    view = inspect_object(owner, "workspace", "T-1")
    monkeypatch.setattr(cli, "request", lambda *_a, **_kw: view)
    for output in (None, "--json", "--yaml"):
        monkeypatch.setattr(sys, "argv", ["devlegate", "inspect", "workspace", "T-1",
                                         *([output] if output else [])])
        assert cli.main() == 0
        text = capsys.readouterr().out
        if output == "--json":
            assert json.loads(text) == view
        elif output == "--yaml":
            assert nanoyaml.loads(text) == view
        else:
            assert "Workspace inspection" in text and "Local proof: proved" in text


def test_active_worker_owner_snapshot_does_not_grant_authority(owner):
    owner._owned_execution_id = owner._state["execution_id"]
    owner._state["worker_identity"] = {"active": True}
    before = snapshot(owner.repo.parent)
    result = inspect_object(owner, "workspace", "T-1")
    assert result["digest"] is None
    assert any("ownership" in blocker for blocker in result["blockers"])
    assert not result["mutation_authorized"]
    assert snapshot(owner.repo.parent) == before


def test_concurrent_owner_refuses_second_request(owner):
    owner.begin_hosted_owner()
    results = []
    thread = threading.Thread(target=lambda: results.append(owner.submit_inspect(
        "ticket", "T-1", request_id="first",
    )))
    thread.start()
    assert owner._service_wake.wait(2)
    from devlegate.runtime import DevlegateError

    with pytest.raises(DevlegateError, match="busy"):
        owner.submit_inspect("ticket", "T-1", request_id="second")
    command, _ = owner._claim_owner_work(True)
    owner._admit_operator_command(command)
    owner._release_operator_command()
    thread.join(2)
    owner.end_hosted_owner()
    assert results[0]["object_type"] == "ticket"


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
def test_read_only_filesystem_and_no_sqlite_access(owner, monkeypatch, kind):
    owner.status_view()
    identifier = owner._state["execution_id"] if kind == "execution" else "T-1"
    if kind == "service":
        identifier = None
    permissions = {}
    for path in owner.repo.parent.rglob("*"):
        if not path.is_symlink():
            permissions[path] = path.stat().st_mode & 0o777
            path.chmod(permissions[path] & ~0o222)
    monkeypatch.setattr(owner._runtime_store, "observe",
                        lambda: pytest.fail("SQLite access"))
    before = snapshot(owner.repo.parent)
    try:
        assert inspect_object(owner, kind, identifier)["informational"]
        assert snapshot(owner.repo.parent) == before
    finally:
        for path, mode in permissions.items():
            path.chmod(mode)


def test_ticket_and_execution_do_not_require_remote(owner):
    from pathlib import Path

    remote = Path(git(owner.repo, "remote", "get-url", "origin").stdout.strip())
    remote.rename(remote.with_name("unavailable.git"))
    before = snapshot(owner.repo.parent)
    assert inspect_object(owner, "ticket", "T-1")["observation"]["id"] == "T-1"
    identifier = owner._state["execution_id"]
    view = inspect_object(owner, "execution", identifier)
    assert view["observation"]["id"] == identifier
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("kind", ["ticket", "workspace"])
def test_ambiguous_ticket_state_refused(owner, kind):
    control = owner.control_worktree
    source = control / owner.review_path / "T-1.md"
    (control / owner.todo_path / "T-1.md").write_bytes(source.read_bytes())
    git(control, "add", ".")
    git(control, "commit", "-m", "duplicate ticket fixture")
    before = snapshot(owner.repo.parent)
    with pytest.raises(EvidenceError, match="unique canonical committed state"):
        inspect_object(owner, kind, "T-1")
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("output", ["--yaml", None])
def test_cli_output_conventions(owner, monkeypatch, capsys, output):
    import devlegate.cli as cli

    monkeypatch.setattr(cli, "request", lambda *_a, **_kw: {
        "schema": "devlegate.inspect-ticket.v1", "informational": True,
    })
    monkeypatch.setattr(sys, "argv", [
        "devlegate", "inspect", "ticket", "T-1", *([output] if output else []),
    ])
    assert cli.main() == 0
    assert "devlegate.inspect-ticket.v1" in capsys.readouterr().out


def test_unavailable_service_never_constructs_runtime(owner, monkeypatch, capsys):
    import devlegate.cli as cli
    from devlegate.ipc_client import IPCClientError

    def unavailable(*_args, **_kwargs):
        raise IPCClientError("service unavailable")

    monkeypatch.setattr(cli, "request", unavailable)
    monkeypatch.setattr(cli, "Devlegate", lambda *_a, **_kw: pytest.fail("new owner"))
    monkeypatch.setattr(sys, "argv", ["devlegate", "inspect", "service", "--json"])
    assert cli.main() == 1
    assert "service unavailable" in capsys.readouterr().err


def test_service_unpublished_diagnostics(owner):
    owner._published_status_snapshot = None
    view = inspect_object(owner, "service", None)
    assert view["observation"]["diagnostic_snapshot"] is None
    from devlegate.cli import _render_service_inspection

    text = _render_service_inspection(view)
    assert "Snapshot: unavailable" in text
    assert "Owner (observed at request)" in text


@pytest.mark.parametrize("columns", [80, 120])
def test_service_render_real_snapshot_and_lifecycle(owner, monkeypatch, columns):
    from devlegate.cli import _render_service_inspection

    monkeypatch.setenv("COLUMNS", str(columns))
    owner.status_view()
    view = inspect_object(owner, "service", None)
    observation = view["observation"]
    observation["owner"]["instance_id"] = "界" * 16 + "x" * 150
    observation["owner"]["lifecycle"].update(
        phase="draining", intent="stop", ready=False, force=True,
        workers={"active": 2, "identity": "SECRET-WORKER"},
    )
    observation["diagnostic_snapshot"]["execution"].update(
        phase="old-phase", stage=None, bound_ticket="SECRET-TICKET",
    )
    text = _render_service_inspection(view)
    assert "Phase: draining" in text
    assert "Intent: stop" in text
    assert "Ready: no" in text
    assert "Active workers: 2" in text
    assert "Runtime phase: old-phase" in text
    assert "Execution stage: none" in text
    assert "may be stale" in text
    assert "SECRET" not in text
    assert all(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                   for c in line) <= columns for line in text.splitlines())


def test_service_render_incomplete_fields():
    from devlegate.cli import _render_service_inspection

    text = _render_service_inspection({"observation": {
        "owner": {"lifecycle": {"intent": "none"}},
        "diagnostic_snapshot": {"execution": {}},
    }})
    assert "Ready: unavailable" in text
    assert "Intent: none" in text
    assert "Active workers: unavailable" in text
    assert "Runtime phase: unavailable" in text
    assert "Execution stage: unavailable" in text


def _publish_newer_unbound_report(owner, *, amend=False):
    from dataclasses import replace

    from devlegate.execution_result import ExecutionReportStore

    control = owner.control_worktree
    store = ExecutionReportStore(control)
    first = store.list("T-1")[0]
    review = control / owner.review_path / "T-1.md"
    todo = control / owner.todo_path / "T-1.md"
    review.rename(todo)
    if amend:
        todo.write_text(todo.read_text().removesuffix("work\n")
                        + "PRIVATE DIFFERENT ASSIGNMENT\n")
    git(control, "add", ".")
    git(control, "commit", "-m", "retry admission fixture")
    second = replace(
        first, execution_id="newer-unbound", resource_generation=None,
        control_head=git(control, "rev-parse", "HEAD").stdout.strip(),
    )
    store.write(second)
    todo.rename(review)
    git(control, "add", ".")
    git(control, "commit", "-m", "publish unbound retry fixture")
    return first, second


def test_latest_report_is_not_current_independent_generation(owner):
    first, second = _publish_newer_unbound_report(owner)
    before = snapshot(owner.repo.parent)
    historical = inspect_object(owner, "execution", first.execution_id)["observation"]
    latest = inspect_object(owner, "execution", second.execution_id)["observation"]
    assert historical["latest_execution"] == "historical"
    assert historical["independent_generation"]["current_local"] == "proved"
    assert latest["latest_execution"] == "latest"
    assert latest["independent_generation"]["state"] == "unproven"
    assert historical["checkpoint"]["head"] == latest["checkpoint"]["head"]
    from devlegate.cli import _render_execution_inspection

    text = _render_execution_inspection({"observation": historical, "blockers": []})
    assert "Execution chronology: historical" in text
    assert "Current local: proved" in text
    assert "not current local proof" in text
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("change", [
    "matching", "assignment", "other-product", "omitted-source", "legacy", "no-intent",
])
def test_historical_prompt_checks_independent_admission(owner, journal, change):
    identifier, path = journal
    first, second = _publish_newer_unbound_report(owner, amend=True)
    assert first.execution_id == identifier
    owner._state["execution_id"] = second.execution_id
    owner._state.pop("worker_prompt_sha256", None)
    saved = json.loads(path.read_text())
    original = saved["prompt"]

    # Advance product independently. A valid new context is not the context of
    # the historical execution, even though both commits belong to this project.
    manifest = owner.repo / ".devlegate/project.md"
    manifest.write_text(manifest.read_text().removesuffix("---\n")
                        + '"worker":\n  - "README.md"\n---\n')
    (owner.repo / "README.md").write_text("PRIVATE NEW PRODUCT CONTEXT\n")
    git(owner.repo, "add", ".")
    git(owner.repo, "commit", "-m", "new product worker context")
    newer_base = git(owner.repo, "rev-parse", "HEAD").stdout.strip()

    if change in {"assignment", "other-product", "omitted-source"}:
        base = newer_base if change == "other-product" else first.code_base_head
        context = (None if change == "omitted-source"
                   else load_worker_project_context(owner.repo, base))
        rendered = render_worker_prompt(WorkerPromptInput(
            "PRIVATE DIFFERENT ASSIGNMENT\n" if change == "assignment" else "work\n",
            WorkDirective.FRESH, project=context,
        ))
        saved.update(
            prompt=rendered.text, prompt_sha256=worker_prompt_sha256(rendered.text),
            components=list(rendered.components),
        )
        assert validate_component_manifest(saved, rendered.text.encode()) is not None
        path.write_text(json.dumps(saved))
    elif change == "legacy":
        del saved["components"]
        path.write_text(json.dumps(saved))
    elif change == "no-intent":
        intent = (owner.execution_worktree_root / "bindings"
                  / first.resource_generation / "intent.json")
        intent.unlink()

    for include_prompt in (False, True):
        observation = inspect_object(
            owner, "execution", identifier, include_prompt=include_prompt,
        )["observation"]
        assert observation["report"] == first.as_dict()
        assert observation["latest_execution"] == "historical"
        assert observation["provenance_state"] == "proved"
        prompt = observation["worker_prompt"]
        assert "PRIVATE" not in json.dumps(prompt)
        if change == "matching":
            assert prompt["status"] == "present" and prompt["manifest"] == "validated"
            assert prompt["project_component"] == "pinned"
            assert prompt["delivery"] == "constructed"
            # No worker route at admission.
            assert "project" not in prompt["components"]
            assert prompt.get("text") == (original if include_prompt else None)
        elif change == "legacy":
            assert prompt["status"] == "present" and prompt["manifest"] == "absent"
            assert "components" not in prompt and "text" not in prompt
            if include_prompt:
                assert prompt["text_status"] == "unavailable"
                assert "no component manifest" in prompt["text_reason"]
        else:
            assert prompt["status"] == "unavailable" and "text" not in prompt
            assert {
                "assignment": "admitted ticket",
                "other-product": "component provenance",
                "omitted-source": "component provenance",
                "no-intent": "independent prompt admission",
            }[change] in prompt["reason"].lower()


@pytest.mark.parametrize("columns", [80, 120])
@pytest.mark.parametrize("tty", [True, False])
def test_execution_render_real_observation(owner, monkeypatch, columns, tty):
    from devlegate.cli import _render_execution_inspection

    monkeypatch.setenv("COLUMNS", str(columns))
    monkeypatch.setattr(sys.stdout, "isatty", lambda: tty)
    view = inspect_object(owner, "execution", owner._state["execution_id"])
    obs = view["observation"]
    obs["report"]["worker_claim"]["summary"] = "first line\nsecond line"
    obs["review"] = {"state": "proved", "decision": "accepted",
                     "text": "review line one\nreview line two"}
    obs["history"]["large-history"] = [
        {"commit": f"{i:040x}", "blob": f"{i + 1:040x}", "text": "old"}
        for i in range(100)
    ]
    obs["history"]["large-history"][50]["blob"] = None
    text = _render_execution_inspection(view)
    assert obs["id"] in text
    assert obs["checkpoint"]["head"] in text
    assert "Conclusion: completed" in text
    assert "first line\nsecond line" in text
    assert "Review state: proved" in text and "Decision: accepted" in text
    assert "review line one\nreview line two" in text
    assert "Generation state: observed" in text and "Current local: proved" in text
    history = text.split("  Artifact: large-history\n", 1)[1]
    assert "Versions: 100" in history
    assert "Deleted versions: 1" in history
    assert "Older versions omitted: 97 (full versions in --json/--yaml)" in history
    assert f"{2:040x}" in history and f"{99:040x}" not in history
    assert len(history.splitlines()) <= 10
    # Long paths and identities stay intact even when they exceed the width.
    assert all(len(line) <= columns or any(len(word) > columns - 25
                                           for word in line.split())
               for line in text.splitlines())


def test_execution_cli_human_and_machine_views(owner, monkeypatch, capsys):
    import devlegate.cli as cli
    from devlegate._vendor import nanoyaml

    identifier = owner._state["execution_id"]
    view = inspect_object(owner, "execution", identifier)
    view["observation"]["history"]["large-history"] = [
        {"commit": f"{i:040x}", "blob": None if i == 50 else f"{i + 1:040x}",
         "text": "historical text"} for i in range(100)
    ]
    monkeypatch.setattr(cli, "request", lambda *_a, **_kw: view)
    monkeypatch.setattr(sys, "argv", ["devlegate", "inspect", "execution", identifier])
    assert cli.main() == 0
    text = capsys.readouterr().out
    assert "Execution inspection" in text and identifier in text
    assert "Publication provenance: proved" in text
    monkeypatch.setattr(sys, "argv", [
        "devlegate", "inspect", "execution", identifier, "--json",
    ])
    assert cli.main() == 0
    assert json.loads(capsys.readouterr().out) == view
    monkeypatch.setattr(sys, "argv", [
        "devlegate", "inspect", "execution", identifier, "--yaml",
    ])
    assert cli.main() == 0
    assert nanoyaml.loads(capsys.readouterr().out) == view


def test_execution_render_proved_review(owner):
    from devlegate.cli import _render_execution_inspection

    identifier = owner._state["execution_id"]
    initial = inspect_object(owner, "execution", identifier)["observation"]
    control = owner.control_worktree
    path = control / f"reviews/T-1/{identifier}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"<!-- devlegate-review-v1\nticket: T-1\nexecution: {identifier}\n"
        f"checkpoint: {initial['checkpoint']['head']}\n"
        f"control: {initial['checkpoint']['control_head']}\n"
        "disposition: rework\n-->\nFirst finding\nSecond finding\n"
    )
    (control / owner.review_path / "T-1.md").rename(
        control / owner.todo_path / "T-1.md"
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "review fixture")
    view = inspect_object(owner, "execution", identifier)
    assert view["observation"]["review"]["state"] == "proved"
    log = execution_log_path(owner.state_dir, owner._state_key, identifier)
    log.unlink(missing_ok=True)
    worker_prompt_artifact_path(owner.state_dir, identifier).unlink()
    absent = inspect_object(owner, "execution", identifier, include_prompt=True)
    assert absent["observation"]["review"] == view["observation"]["review"]
    assert absent["observation"]["local_log"] == {"path": str(log), "status": "missing"}
    assert absent["observation"]["worker_prompt"] == {"status": "missing"}
    log.mkdir(parents=True)
    unavailable = inspect_object(owner, "execution", identifier)["observation"]
    assert unavailable["local_log"]["status"] == "unavailable"
    assert unavailable["local_log"]["path"] == str(log)
    assert unavailable["review"] == view["observation"]["review"]
    text = _render_execution_inspection(view)
    assert "Review state: proved" in text and "Decision: rework" in text
    assert "First finding\nSecond finding" in text


@pytest.mark.parametrize("output", ["--json", "--yaml", None, "oversized-response"])
def test_execution_prompt_cli_over_real_owner_socket(
    tmp_path, short_state_dir, monkeypatch, capsys, output,
):
    import devlegate.cli as cli
    from devlegate._vendor import nanoyaml
    from devlegate.ipc_client import request
    from devlegate.ipc_protocol import MAX_PAYLOAD_BYTES
    from devlegate.ipc_server import UnixIPCServer

    repo, config, _ = control_fixture(tmp_path)
    manifest = repo / ".devlegate/project.md"
    manifest.write_text(manifest.read_text().removesuffix("---\n")
                        + '"worker":\n  - "README.md"\n---\n')
    git(repo, "add", ".")
    git(repo, "commit", "-m", "worker context fixture")
    git(repo, "push", "origin", "main")
    project_commit = git(repo, "rev-parse", "HEAD").stdout.strip()
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    control = owner.control_worktree
    ticket = control / owner.todo_path / "T-1.md"
    assignment = (
        "ADMITTED ASSIGNMENT: café 界 🙂\r\nunsafe:\x1b[2J\x00\r\x85\u202e\n~~~\n"
    )
    if output == "oversized-response":
        assignment += "x" * (MAX_PAYLOAD_BYTES - 8_192)
    ticket.write_bytes(
        ticket.read_bytes().removesuffix(b"work\n") + assignment.encode(),
    )
    git(control, "add", ".")
    git(control, "commit", "-m", "admit exact assignment fixture")
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    delivered = []

    def worker(workspace, prompt, *, prompt_delivered_handler, **_kwargs):
        (workspace.path / "implementation.txt").write_text("implementation\n")
        delivered.append(prompt)
        prompt_delivered_handler()
        return WorkerRunResult(
            0, None, WorkerClaim("completed", "implemented", (), ()), None,
        )

    monkeypatch.setattr(owner._workers, "run", worker)
    assert run_test_iteration(owner) == 0
    exact, = delivered
    setup_output = capsys.readouterr()
    assert "worker prompt sent:" in setup_output.out
    assert all("ADMITTED ASSIGNMENT" not in text
               for text in (setup_output.out, setup_output.err))
    identifier = owner._state["execution_id"]
    owner._state.pop("worker_prompt_sha256", None)
    if output == "oversized-response":
        # The journal remains readable, but the whole response will not fit.
        path = worker_prompt_artifact_path(owner.state_dir, identifier)
        assert path.stat().st_size <= MAX_PAYLOAD_BYTES
        review = control / f"reviews/T-1/{identifier}.md"
        review.parent.mkdir(parents=True, exist_ok=True)
        review.write_text("review fixture\n" + "x" * 8_192)
        git(control, "add", ".")
        git(control, "commit", "-m", "large committed review observation fixture")
        assert (inspect_object(owner, "execution", identifier, include_prompt=True)
                ["observation"]["worker_prompt"]["text"]) == exact
    log = execution_log_path(owner.state_dir, owner._state_key, identifier)
    log.parent.mkdir(parents=True, exist_ok=True)
    log.write_text("PRIVATE LOG BODY\n")
    owner.status_view()
    socket = short_state_dir / "prompt-inspect.sock"
    server = UnixIPCServer(owner, socket)
    monkeypatch.setattr(cli, "request", lambda _, *args: request(socket, *args))
    owner.begin_hosted_owner()
    server.start()
    state = owner._runtime_store.observe()
    before = snapshot(owner.repo.parent)

    def cli_request(flags, include_prompt):
        monkeypatch.setattr(sys, "argv", [
            "devlegate", "inspect", "execution", identifier, *flags,
        ])
        results = []
        thread = threading.Thread(target=lambda: results.append(cli.main()))
        thread.start()
        assert owner._service_wake.wait(2)
        owner._service_wake.clear()
        command, _ = owner._claim_owner_work(True)
        assert command is not None and not command.mutable
        assert command.include_prompt is include_prompt
        owner._admit_operator_command(command)
        owner._release_operator_command()
        thread.join(5)
        assert results == [0]
        captured = capsys.readouterr()
        assert not captured.err
        assert "PRIVATE LOG BODY" not in captured.out
        return captured.out

    try:
        default = cli_request(["--json"], False)
        observation = json.loads(default)["observation"]
        assert observation["local_log"] == {"path": str(log), "status": "present"}
        prompt = observation["worker_prompt"]
        assert observation["id"] == identifier == observation["report"]["execution_id"]
        assert prompt["manifest"] == "validated"
        assert {"assignment", "project"} <= set(prompt["components"])
        assert prompt["directive"] == "fresh" and prompt["delivery"] == "sent"
        assert prompt["sha256"] == worker_prompt_sha256(exact)
        assert "text" not in prompt and "ADMITTED ASSIGNMENT" not in default
        assert project_commit not in json.dumps(prompt)
        fmt = "--json" if output == "oversized-response" else output
        explicit = cli_request(["--prompt", *([fmt] if fmt else [])], True)
        if fmt:
            parse = json.loads if fmt == "--json" else nanoyaml.loads
            parsed = parse(explicit)
            observed = parsed["observation"]["worker_prompt"]
            assert observed["sha256"] == prompt["sha256"]
            if output == "oversized-response":
                assert observed["text_status"] == "unavailable"
                assert "omit --prompt" in observed["text_reason"]
                assert "text" not in observed
                assert parsed["observation"]["report"] == observation["report"]
            else:
                assert observed["text"].encode() == exact.encode()
                assert observed["delivery"] == "sent"
        else:
            assert "ADMITTED ASSIGNMENT: café 界 🙂" in explicit
            assert "\\u001b[2J" in explicit
            assert not set(explicit).intersection("\x1b\x00\r\x85\u202e")
        assert snapshot(owner.repo.parent) == before
        assert owner._runtime_store.observe() == state
    finally:
        server.stop()
        owner.end_hosted_owner()


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
@pytest.mark.parametrize("supervising", [False, True], ids=["idle", "worker-poll"])
def test_real_socket_owner_serialization(owner, short_state_dir, kind, supervising):
    from devlegate.ipc_client import request
    from devlegate.ipc_server import UnixIPCServer

    owner.status_view()
    owner.begin_hosted_owner()
    identifier = owner._state["execution_id"] if kind == "execution" else "T-1"
    if kind == "service":
        identifier = None
    socket = short_state_dir / "inspect.sock"
    server = UnixIPCServer(owner, socket)
    server.start()
    before = snapshot(owner.repo.parent)
    results = []
    errors = []

    def client():
        try:
            results.append(request(socket, "inspect", {
                "object_type": kind, "object_id": identifier,
            }))
        except Exception as error:
            errors.append(error)

    thread = threading.Thread(target=client)
    try:
        thread.start()
        assert owner._service_wake.wait(2)
        if supervising:
            reset = OperatorCommand(
                request_id="reset", method="reset", ticket_id="T-1",
                fingerprint="fixture",
            )
            owner._operator_command = reset
            owner._poll_worker_observation()
            assert owner._operator_command is reset
            assert owner._operator_observation is not None
            owner._operator_command = None
            owner._poll_worker_observation()
        else:
            command, _ = owner._claim_owner_work(True)
            assert command is not None and not command.mutable
            owner._admit_operator_command(command)
        thread.join(5)
        assert not thread.is_alive()
        assert not errors
        assert results[0]["informational"]
        if supervising and kind == "workspace":
            assert results[0]["digest"] is None and not results[0]["snapshot_complete"]
            assert results[0]["identity"]["resources"]["dirty"] == "unknown"
        if supervising and kind == "execution":
            execution = results[0]["observation"]
            assert execution["id"] == identifier
            assert execution["provenance_state"] == "proved"
            assert execution["independent_generation"]["current_local"] == "unknown"
        assert snapshot(owner.repo.parent) == before
    finally:
        server.stop()
        owner.end_hosted_owner()


def test_expired_read_is_discarded_before_io(owner, short_state_dir, monkeypatch):
    from devlegate.ipc_client import IPCClientError, request
    from devlegate.ipc_server import UnixIPCServer

    owner.begin_hosted_owner()
    socket = short_state_dir / "expire.sock"
    server = UnixIPCServer(owner, socket)
    server.start()
    monkeypatch.setattr("devlegate.runtime.OBSERVATION_WAIT_SECONDS", 0.1)
    monkeypatch.setattr("devlegate.inspection.inspect_object",
                        lambda *_a, **_k: pytest.fail("expired read entered IO"))
    before = owner._runtime_store.observe()
    try:
        with pytest.raises(IPCClientError, match="read-only observation timed out"):
            request(socket, "inspect", {"object_type": "ticket", "object_id": "T-1"})
        owner._poll_worker_observation()
        assert not owner._operator_command_pending()
        assert owner._runtime_store.observe() == before
    finally:
        server.stop()
        owner.end_hosted_owner()


@pytest.mark.parametrize("retry", [False, True], ids=["scheduled", "retry"])
def test_live_worker_inspection(tmp_path, short_state_dir, monkeypatch, retry):
    repo, config, _ = control_fixture(tmp_path)
    config.write_text(config.read_text() + f"\nSTATE_DIR={short_state_dir}\n")
    assert invoke(repo, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(repo)
    owner = Devlegate(config)
    control = owner.control_worktree
    original = (control / owner.todo_path / "T-1.md").read_text()

    def finish(workspace, _prompt, *, prompt_delivered_handler, **_kwargs):
        (workspace.path / "implementation.txt").write_text("historical change\n")
        prompt_delivered_handler()
        return WorkerRunResult(0, None, WorkerClaim("completed", "done", (), ()), None)

    monkeypatch.setattr(owner._workers, "run", finish)
    assert run_test_iteration(owner) == 0
    historical = owner._state["execution_id"]
    evidence = inspect_object(owner, "execution", historical, include_prompt=True)[
        "observation"
    ]
    exact_prompt = evidence["worker_prompt"]["text"]
    report = evidence["report"]
    review = control / "reviews" / "T-1" / f"{historical}.md"
    review.parent.mkdir(parents=True)
    review.write_text(
        "<!-- devlegate-review-v1\nticket: T-1\n"
        f"execution: {historical}\ncheckpoint: {report['workspace_head']}\n"
        f"control: {report['control_head']}\ndisposition: rework\n-->\nDeferred.\n"
    )
    git(control, "mv", f"{owner.review_path}/T-1.md", f"{owner.backlog_path}/T-1.md")
    git(control, "add", ".")
    git(control, "commit", "-m", "Defer historical ticket with review")
    (control / owner.todo_path / "T-2.md").write_text(original)
    git(control, "add", ".")
    git(control, "commit", "-m", "Add current worker fixture")
    # Build distinct blobs and real path history without adding runnable work.
    for batch in range(5):
        for index in range(batch * 42, (batch + 1) * 42):
            ticket = f"S-{index:03}"
            dependency = f'"depends_on": ["S-{index - 1:03}"]\n' if index else ""
            (control / owner.backlog_path / f"{ticket}.md").write_text(
                f'---\n"title": "Scale fixture {ticket}"\n"type": "devlegate.ticket"\n'
                f"{dependency}"
                f"---\nHistorical workflow ticket {ticket}.\n",
            )
        git(control, "add", ".")
        git(control, "commit", "-m", f"Populate workflow batch {batch}")
    scale_publication = git(control, "rev-parse", "HEAD").stdout.strip()
    git(control, "push", "origin", "HEAD:refs/heads/devlegate/control")
    git(control, "gc", "--prune=now")
    if retry:
        monkeypatch.setattr(owner._workers, "run", lambda *_a, **_k: WorkerRunResult(
            1, None, None, None,
        ))
        assert run_test_iteration(owner) == 1
    control_head = git(control, "rev-parse", "HEAD").stdout.strip()

    worker, pid, attempts = (tmp_path / name for name in ("worker", "pid", "attempts"))
    _long_worker_script(worker)
    monkeypatch.setenv("DEVLEGATE_TEST_WORKER_PID", str(pid))
    monkeypatch.setenv("DEVLEGATE_TEST_ATTEMPTS", str(attempts))
    config.write_text(config.read_text().replace(
        "OPENCODE_BIN=true", f"OPENCODE_BIN={worker}",
    ))

    # A test-only Git delay makes a timeout and competing lifecycle request
    # deterministic. Only the workspace inspection's read-only command matches.
    real_git = shutil.which("git")
    gate, observed = tmp_path / "delay", tmp_path / "observing"
    release = tmp_path / "release-observation"
    trace = tmp_path / "owner-git.jsonl"
    wrapper = tmp_path / "git"
    wrapper.write_text(
        "#!/bin/sh\n"
        f"if [ -f {shlex.quote(str(gate))} ]; then\n"
        '  case " $* " in\n'
        '    *" --no-optional-locks "*" rev-list --parents --first-parent "*)\n'
        f"      touch {shlex.quote(str(observed))}\n"
        f"      if [ -f {shlex.quote(str(release))} ]; then exec sleep 60; fi\n"
        f"      while [ -f {shlex.quote(str(gate))} ]; do sleep .01; done;;\n"
        "  esac\nfi\n"
        # The observation runners strip ambient GIT_* variables. Restore Trace2
        # only in this fixture and only for the service process tree.
        'if [ -n "$DEVLEGATE_TEST_OWNER_TRACE" ]; then\n'
        '  export GIT_TRACE2_EVENT="$DEVLEGATE_TEST_OWNER_TRACE"\n'
        "fi\n"
        f'exec {shlex.quote(real_git)} "$@"\n'
    )
    wrapper.chmod(0o755)
    monkeypatch.setenv("PATH", f"{tmp_path}:{os.environ['PATH']}")

    with monkeypatch.context() as service_env:
        service_env.setenv("DEVLEGATE_TEST_OWNER_TRACE", str(trace))
        service = LiveService(repo, config).start()
    try:
        service.wait_ready()
        if retry:
            ack = service.cli("retry", "T-2")
            assert ack.returncode == 0, ack.stderr

        def worker_running():
            state, _ = owner._runtime_store.observe()
            return state.get("execution_stage") == "worker-running" and (
                _worker_body_started(pid, state["worker_identity"], attempts, 1)
            )

        service.wait_for(worker_running)
        before_state = owner._runtime_store.observe()
        state = before_state[0]
        active = state["execution_id"]

        def running_status_published():
            execution = request(service.locator.socket_path, "status")["execution"]
            return (execution["stage"], execution["execution_id"]) == (
                "worker-running", active,
            )

        # Persisting worker identity precedes the owner's status refresh, whose
        # Git porcelain calls can still take index.lock. Wait for the refreshed
        # view before installing the hook and taking the exact file snapshots.
        service.wait_for(running_status_published)
        worktree = owner.recovery_workspace_manager("T-2").path
        # The fixture, not inspection, changes the current worker's files.
        (worktree / "live.txt").write_text("worker changes\n")
        git(repo, "config", "filter.inspect-test.clean",
            f"touch {tmp_path / 'filter-ran'}")
        (worktree / ".gitattributes").write_text("* filter=inspect-test\n")
        hooks = repo / ".git" / "hooks"
        hook = hooks / "post-index-change"
        hook.write_text(f"#!/bin/sh\ntouch {tmp_path / 'hook-ran'}\n")
        hook.chmod(0o755)
        trees = [repo / ".git", control, owner.execution_worktree_root]
        before_files = [snapshot(path) for path in trees]

        def inspect(kind, identifier=None, *flags):
            args = [identifier] if identifier else []
            response = service.cli("inspect", kind, *args, *flags, "--json")
            assert response.returncode == 0, response.stderr
            view = json.loads(response.stdout)
            assert view["informational"] and not view["mutation_authorized"]
            version = 2 if kind == "workspace" else 1
            assert view["schema"] == f"devlegate.inspect-{kind}.v{version}"
            return view

        diagnostics = inspect("service")["observation"]["diagnostic_snapshot"]
        assert diagnostics["execution"]["execution_id"] == active
        current = inspect("ticket", "T-2")["observation"]
        assert (current["id"], current["state"]) == ("T-2", "todo")
        other = inspect("ticket", "T-1")["observation"]
        assert (other["id"], other["state"]) == ("T-1", "backlog")
        assert current["provenance"]["control_head"] == control_head
        assert other["provenance"]["control_head"] == control_head
        execution = inspect("execution", historical)["observation"]
        assert execution["report"]["execution_id"] == historical
        assert execution["provenance_state"] == "proved"
        assert execution["independent_generation"]["current_local"] == "unknown"
        assert "text" not in execution["worker_prompt"]
        assert inspect("execution", historical, "--prompt")["observation"][
            "worker_prompt"
        ]["text"] == exact_prompt
        workspace = inspect("workspace", "T-2")
        assert workspace["identity"]["ticket"]["id"] == "T-2"
        assert workspace["identity"]["resources"]["dirty"] == "unknown"
        assert not workspace["snapshot_complete"] and workspace["digest"] is None
        assert any("worker ownership" in blocker for blocker in workspace["blockers"])

        # Repeat over real IPC without CLI startup time. The held worker does
        # not launch Git; no other owner work is requested during each sample.
        def measured(kind, identifier=None, *, prompt=False):
            payload = {"object_type": kind, "object_id": identifier}
            if prompt:
                payload["include_prompt"] = True
            with trace.open("rb") as events:
                events.seek(0, os.SEEK_END)
                start = perf_counter()
                view = request(service.locator.socket_path, "inspect", payload)
                elapsed = (perf_counter() - start) * 1000
                rows = [json.loads(line) for line in events]
            commands = [row["argv"] for row in rows if row["event"] == "start"]
            assert not any(row["event"] == "child_start" for row in rows)
            blobs = sum("cat-file" in argv for argv in commands)
            logs = sum("log" in argv for argv in commands)
            mode = "retry" if retry else "scheduled"
            label = f"{kind} {identifier or ''}{' --prompt' if prompt else ''}".strip()
            print(f"{mode} {label}: {elapsed:.2f} ms IPC, {len(commands)} Git, "
                  f"{blobs} cat-file, {logs} log")
            assert view["informational"] and not view["mutation_authorized"]
            if kind == "service":
                assert not commands
            if kind == "ticket":
                assert not blobs and logs == 1
                assert sum("ls-tree" in argv and "-zt" in argv
                           for argv in commands) == 1
                assert sum("rev-parse" in argv and f"{control_head}^{{commit}}" in argv
                           for argv in commands) == 1
            return view

        for _ in range(3):
            assert measured("service")["observation"]["diagnostic_snapshot"][
                "execution"
            ]["execution_id"] == active
            graph_ticket = measured("ticket", "S-209")["observation"]
            assert graph_ticket["provenance"]["control_head"] == control_head
            assert graph_ticket["provenance"]["history"] == [scale_publication]
            assert len(graph_ticket["dependency_graph"]) == 212
            assert graph_ticket["depends_on"] == ["S-208"]
            assert measured("execution", historical)["observation"] == execution
            assert measured("execution", historical, prompt=True)["observation"][
                "worker_prompt"
            ]["text"] == exact_prompt
            assert measured("workspace", "T-2")["digest"] is None

        for args, message in (
            (("inspect", "execution", active), "no unique canonical committed report"),
            (("inspect", "ticket", "../T-2"), "canonical object ID"),
            (("inspect", "workspace", "missing-ticket"),
             "ticket has no unique canonical committed state"),
            (("workspace", "ls"), "inventory observation unavailable"),
            (("workspace", "prune", "--dry-run"), "inventory observation unavailable"),
            (("workspace", "prune", "--apply"), "mutable request was not admitted"),
            (("retry", "T-2"), "mutable request was not admitted"),
        ):
            failed = service.cli(*args)
            assert failed.returncode != 0 and message in failed.stderr, failed.stderr
        assert worker_running()
        os.kill(state["worker_identity"]["pid"], 0)
        assert owner._runtime_store.observe() == before_state
        assert [snapshot(path) for path in trees] == before_files
        assert not (tmp_path / "filter-ran").exists()
        assert not (tmp_path / "hook-ran").exists()

        journal_path = worker_prompt_artifact_path(owner.state_dir, historical)
        saved = journal_path.with_suffix(".saved")
        journal_path.rename(saved)
        try:
            missing = inspect("execution", historical, "--prompt")["observation"]
            assert missing["report"] == report
            assert missing["worker_prompt"] == {"status": "missing"}
        finally:
            saved.rename(journal_path)

        if not retry:
            gate.touch()
            moving = service.start_cli("inspect", "workspace", "T-2", "--json")
            try:
                service.wait_for(observed.exists)
                (worktree / "live.txt").write_text("changed during inspection\n")
                gate.unlink()
                output, stderr = moving.communicate(timeout=5)
                assert moving.returncode == 0, stderr
                view = json.loads(output)
                assert view["identity"]["resources"]["dirty"] == "unknown"
                assert view["digest"] is None and not view["mutation_authorized"]
            finally:
                gate.unlink(missing_ok=True)
                observed.unlink(missing_ok=True)
                if moving.poll() is None:
                    moving.kill()
                    moving.wait(timeout=5)

        release.touch()
        gate.touch()
        pending = service.start_cli("inspect", "workspace", "T-2", "--json")
        try:
            service.wait_for(observed.exists)
            assert pending.poll() is None
            status = request(service.locator.socket_path, "status")
            assert status["execution"]["execution_id"] == active
            if retry:
                # Stop while the read is in flight; it must not steal retry's
                # owner slot or stage any replacement execution.
                stopped = service.cli("stop", "--force")
                assert stopped.returncode == 0, stopped.stderr
            _, stderr = pending.communicate(timeout=5)
            assert pending.returncode != 0 and "read-only observation" in stderr
            if retry:
                service.wait_exited()
                assert owner._runtime_store.observe()[0]["worker_identity"] is None
                with pytest.raises(ProcessLookupError):
                    os.kill(state["worker_identity"]["pid"], 0)
            else:
                assert "time budget expired" in stderr
                assert worker_running()
                assert owner._runtime_store.observe() == before_state
                assert inspect("ticket", "T-2")["observation"]["id"] == "T-2"
            assert attempts.read_text().splitlines() == ["attempt"]
        finally:
            if pending.poll() is None:
                pending.kill()
                pending.wait(timeout=5)
    finally:
        gate.unlink(missing_ok=True)
        if service.process.poll() is None:
            service.cli("stop", "--force")
        service.stop()


def test_missing_dependency_stays_visible(owner):
    control = owner.control_worktree
    path = control / owner.review_path / "T-1.md"
    path.write_text(path.read_text().replace(
        "---\n", '---\n"depends_on":\n  - "missing"\n', 1,
    ))
    git(control, "add", ".")
    git(control, "commit", "-m", "missing dependency fixture")
    before = snapshot(owner.repo.parent)
    view = inspect_object(owner, "ticket", "T-1")
    assert view["observation"]["depends_on"] == ["missing"]
    assert "missing dependency: T-1 -> missing" in view["blockers"]
    from devlegate.cli import _render_ticket_inspection

    rendered = _render_ticket_inspection(view)
    assert "Direct dependency: missing" in rendered
    assert "missing dependency: T-1 -> missing" in rendered
    assert snapshot(owner.repo.parent) == before


@pytest.mark.parametrize("columns", [80, 120])
def test_ticket_render_evidence_and_long_text(monkeypatch, columns):
    from devlegate.cli import _render_ticket_inspection

    monkeypatch.setenv("COLUMNS", str(columns))
    body = "---\ntitle: café 界\n---\n\nLong ticket body\nsecond line\n"
    entry = {"state": "todo", "path": "tickets/todo/T-2.md", "blob": "a" * 40,
             "depends_on": ["missing"]}
    view = {"observation": {
        "id": "T-1", "title": "café 界", "type": "task", "state": "review",
        "path": "tickets/review/T-1.md", "blob": "b" * 40, "text": body,
        "depends_on": ["T-2"], "dependency_graph": {"T-2": [entry, entry]},
        "provenance": {"control_head": "c" * 40, "history": ["d" * 40]},
    }, "blockers": ["ambiguous dependency graph ticket: T-2"]}
    text = _render_ticket_inspection(view)
    assert text.index("Current committed ticket") < text.index("Dependencies")
    assert text.index("Blockers") < text.index("Control provenance")
    assert text.index("Ticket path history") < text.index("Ticket text")
    assert text.endswith(body)
    assert "café 界" in text and "\\u" not in text
    assert text.count("tickets/todo/T-2.md") == 2
    assert "ambiguous dependency graph ticket: T-2" in text
    assert all(sum(2 if unicodedata.east_asian_width(c) in "WF" else 1
                   for c in line) <= columns for line in text.splitlines())


def test_ticket_render_unavailable_evidence():
    from devlegate.cli import _render_ticket_inspection

    text = _render_ticket_inspection({"observation": {}})
    assert "Graph: unavailable" in text
    assert "Blockers: unavailable" in text
    assert "History: unavailable" in text
    assert "Ticket text (current committed blob)\nunavailable" in text


def test_ticket_render_escapes_terminal_controls():
    from devlegate.cli import _render_ticket_inspection

    text = _render_ticket_inspection({"observation": {
        "title": "café\x1b[2J", "text": "body\n\x1b[31mred\n",
    }})
    assert "café\\u001b[2J" in text
    assert "body\n\\u001b[31mred\n" in text
    assert "\x1b" not in text


@pytest.mark.parametrize(
    "change", ["missing-ticket", "missing-report", "symlink-report"],
)
def test_workspace_missing_working_evidence_remains_inspectable(owner, change):
    control = owner.control_worktree
    if change == "missing-ticket":
        path = control / owner.review_path / "T-1.md"
    else:
        path = control / f"executions/T-1/{owner._state['execution_id']}.json"
    path.unlink()
    if change == "symlink-report":
        path.symlink_to(owner.repo / ".git/config")
    before = snapshot(owner.repo.parent)
    view = inspect_object(owner, "workspace", "T-1")
    assert view["digest"] is None
    assert view["identity"]["ticket"]["id"] == "T-1"
    assert view["identity"]["executions"][0]["report"]["execution_id"]
    assert any("working evidence unavailable" in blocker
               for blocker in view["blockers"])
    assert snapshot(owner.repo.parent) == before
