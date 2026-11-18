# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Universal inspection uses exact immutable evidence and existing owner IO."""

import json
import sys
import threading
import unicodedata

import pytest
from _control_support import git
from test_clean_preview import owner as preview_owner  # noqa: F401
from test_clean_preview import snapshot

from devlegate.abandonment_evidence import EvidenceError
from devlegate.inspection import (
    ExecutionObservation,
    ServiceObservation,
    TicketObservation,
    inspect_object,
)
from devlegate.ipc_protocol import IPCProtocolError, IPCRequest
from devlegate.ipc_server import dispatch_request
from devlegate.runtime import OperatorCommand


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
    owner._operator_command = command
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
])
def test_ipc_strict_validation(payload):
    with pytest.raises(IPCProtocolError, match="inspection"):
        dispatch_request(object(), IPCRequest(1, "invalid", "inspect", payload))


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


def test_latest_report_is_not_current_independent_generation(owner):
    from dataclasses import replace

    from devlegate.execution_result import ExecutionReportStore

    control = owner.control_worktree
    store = ExecutionReportStore(control)
    first = store.list("T-1")[0]
    review = control / owner.review_path / "T-1.md"
    todo = control / owner.todo_path / "T-1.md"
    review.rename(todo)
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
    text = _render_execution_inspection(view)
    assert "Review state: proved" in text and "Decision: rework" in text
    assert "First finding\nSecond finding" in text


@pytest.mark.parametrize("kind", ["ticket", "execution", "workspace", "service"])
def test_real_socket_owner_serialization(owner, short_state_dir, kind):
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
        command, _ = owner._claim_owner_work(True)
        assert command is not None and not command.mutable
        owner._admit_operator_command(command)
        owner._release_operator_command()
        thread.join(5)
        assert not thread.is_alive()
        assert not errors
        assert results[0]["informational"]
        assert snapshot(owner.repo.parent) == before
    finally:
        server.stop()
        owner.end_hosted_owner()


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
