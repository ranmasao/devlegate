# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real Git observations at the scheduler, publication and inspection boundary."""

import subprocess
from time import perf_counter

import pytest
from _control_support import (
    Devlegate,
    control_fixture,
    fresh_control_fixture,
    git,
    invoke,
)
from runtime_helpers import run_test_iteration

from devlegate import tickets, workflow_projection
from devlegate.abandonment_evidence import EvidenceError
from devlegate.clean_preview import _blob, _tree, observe_git
from devlegate.inspection import _head, _ticket, inspect_object
from devlegate.runtime import WorkflowBlockedError


@pytest.fixture
def engine(tmp_path):
    working, config, _ = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    return Devlegate(config)


def write_ticket(engine, state, name, dependencies=(), *, title=None, target=None):
    path = engine.control_worktree / engine._workflow_paths()[state] / f"{name}.md"
    dependency_line = (
        '"depends_on": [' + ", ".join(f'"{dep}"' for dep in dependencies) + "]\n"
        if dependencies else ""
    )
    kind, extra, body = "devlegate.ticket", "", "Work\n"
    if target is not None:
        product = git(engine.repo, "rev-parse", "HEAD").stdout.strip()
        kind = "devlegate.abandonment"
        extra = f'"target": "{target}"\n"start_head": "{product}"\n'
        body = "## Abandonment reason\nRetire obsolete work.\n"
        body += "## Report requirements\nRecord the disposition.\n"
    path.write_text(
        f'---\n"type": "{kind}"\n"title": "{title or name}"\n'
        f'{dependency_line}{extra}---\n{body}'
    )
    return path


def commit(control, message):
    git(control, "add", "-A")
    git(control, "commit", "-m", message)
    return git(control, "rev-parse", "HEAD").stdout.strip()


def runnable(store):
    return tuple(ticket.id for ticket in store.runnable)


def test_scheduler_status_and_inspection_share_committed_transitions(engine):
    control = engine.control_worktree
    workflow = engine._workflow_paths()
    write_ticket(engine, "done", "DONE")
    write_ticket(engine, "abandoned", "ABANDONED")
    moving = write_ticket(engine, "todo", "A", ("DONE",))
    write_ticket(engine, "todo", "AFTER", ("A",))
    write_ticket(engine, "todo", "WAIT", ("ABANDONED",))
    request = write_ticket(engine, "todo", "RESERVE", target="T-1")
    original = commit(control, "dependency and reservation fixture")

    def observe(head, state, expected):
        store = engine._ticket_store(head=head, require_clean=True)
        assert store == tickets.load_ticket_store(control, workflow)
        assert runnable(store) == expected
        view = engine.status_view()
        assert tuple(ticket_id for ticket_id, _ in view.runnable) == expected
        assert view.control.local_head == head
        ticket = inspect_object(engine, "ticket", "A")["observation"]
        assert ticket["state"] == state
        assert ticket["provenance"]["control_head"] == head
        assert ticket["depends_on"] == ["DONE"]
        assert ticket["text"] == moving.read_text()
        blob = git(control, "rev-parse", f"{head}:{ticket['path']}").stdout.strip()
        assert ticket["blob"] == blob
        assert engine._committed_workflow(head).graph.store is store
        return view

    git(control, "push", "origin", "HEAD")
    assert observe(original, "todo", ("RESERVE", "A")).plan.action == "blocked"
    request.rename(control / workflow["done"] / request.name)
    (control / workflow["todo"] / "T-1.md").rename(
        control / workflow["abandoned"] / "T-1.md",
    )
    for state in ("review", "accepted", "done"):
        moving = moving.rename(control / workflow[state] / moving.name)
        head = commit(control, state)
        git(control, "push", "origin", "HEAD")
        expected = ("AFTER",) if state == "done" else ()
        view = observe(head, state, expected)
        if state == "review":
            assert view.plan.action == "none"
        elif state == "done":
            assert view.plan.ticket_id == "AFTER"
    old = engine._committed_workflow(head)
    (control / "audit.txt").write_text("non-workflow evidence\n")
    unrelated = commit(control, "unrelated control change")
    assert engine._committed_workflow(unrelated).graph is old.graph
    assert inspect_object(engine, "ticket", "A")["observation"]["provenance"] == {
        "control_head": unrelated,
        "history": _ticket(engine, head, "A")[0]["provenance"]["history"],
    }
    historical, _ = _ticket(engine, original, "A")
    assert historical["state"] == "todo"
    assert historical["path"] == f"{workflow['todo']}/A.md"


@pytest.mark.parametrize(
    "change", ["dirty", "staged", "untracked", "ignored", "product"],
)
def test_warm_graph_cannot_authorize_dirty_or_conflicting_workflow(engine, change):
    control = engine.control_worktree
    workflow = engine._workflow_paths()
    head = _head(engine)[0]
    assert runnable(engine._ticket_store(head=head, require_clean=True)) == ("T-1",)
    before = engine._runtime_store.observe()
    if change == "dirty":
        (control / workflow["todo"] / "T-1.md").write_text("broken\n")
    elif change == "staged":
        git(control, "mv", f"{workflow['todo']}/T-1.md", f"{workflow['review']}/T-1.md")
        status = engine.status_view()
        assert tuple(ticket_id for ticket_id, _ in status.review) == ("T-1",)
    elif change == "untracked":
        write_ticket(engine, "todo", "DUP", ("missing",))
    elif change == "ignored":
        path = write_ticket(engine, "todo", "IGNORED", ("missing",))
        exclude = git(
            control, "rev-parse", "--path-format=absolute", "--git-path",
            "info/exclude",
        )
        with open(exclude.stdout.strip(), "a") as output:
            output.write(f"\n/{path.relative_to(control)}\n")
        assert not git(control, "status", "--porcelain").stdout
    else:
        (engine.repo / workflow["todo"]).mkdir(parents=True)
    with pytest.raises(WorkflowBlockedError):
        engine._ticket_store(head=head, require_clean=True)
    ticket = inspect_object(engine, "ticket", "T-1")["observation"]
    assert ticket["state"] == "todo" and ticket["provenance"]["control_head"] == head
    assert engine._runtime_store.observe() == before


@pytest.mark.parametrize(("change", "pattern"), [
    ("malformed", "invalid ticket"), ("duplicate", "duplicate ticket ID"),
    ("missing", "missing dependency"), ("cycle", "dependency cycle"),
])
def test_changed_commit_cannot_reuse_valid_graph(engine, change, pattern):
    control = engine.control_worktree
    first = _head(engine)[0]
    previous = engine._ticket_store(head=first, require_clean=True)
    if change == "malformed":
        write_ticket(engine, "todo", "A").write_text("invalid\n")
    elif change == "duplicate":
        write_ticket(engine, "review", "T-1")
    elif change == "missing":
        write_ticket(engine, "todo", "A", ("missing",))
    else:
        write_ticket(engine, "todo", "A", ("B",))
        write_ticket(engine, "todo", "B", ("A",))
    current = commit(control, change)
    with pytest.raises(WorkflowBlockedError, match=pattern):
        engine._ticket_store(head=current, require_clean=True)
    assert engine.status_view().plan.action == "blocked"
    assert engine._committed_workflow(first).graph.store is previous


def test_ref_advance_during_observation_refuses_mutation(engine, monkeypatch):
    control = engine.control_worktree
    first = _head(engine)[0]
    original = engine._committed_workflow
    original(first)

    def advance(head):
        view = original(head)
        git(control, "commit", "--allow-empty", "-m", "independent control advance")
        return view

    monkeypatch.setattr(engine, "_committed_workflow", advance)
    state = engine._runtime_store.observe()
    with pytest.raises(WorkflowBlockedError, match="canonical control changed"):
        engine._ticket_store(head=first, require_clean=True)
    with pytest.raises(EvidenceError, match="canonical control changed"):
        inspect_object(engine, "ticket", "T-1")
    assert engine._runtime_store.observe() == state


def test_scheduler_refuses_advance_after_selection(engine, monkeypatch):
    control = engine.control_worktree
    first = _head(engine)[0]
    engine._committed_workflow(first)
    lookup = engine._execution_remote_head

    def advance(branch):
        result = lookup(branch)
        git(control, "commit", "--allow-empty", "-m", "changed after selection")
        return result

    monkeypatch.setattr(engine, "_execution_remote_head", advance)
    with pytest.raises(WorkflowBlockedError, match="canonical control changed"):
        run_test_iteration(engine)
    assert engine._state["phase"] == "idle"
    assert engine._state.get("execution_id") is None
    assert git(control, "rev-parse", "HEAD^").stdout.strip() == first
    assert not git(engine.repo, "branch", "--list", "devlegate/work/*").stdout


def test_custom_paths_and_exact_committed_text(tmp_path):
    working, config, _ = fresh_control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    engine = Devlegate(config, read_only=True)
    control = engine.control_worktree
    path = write_ticket(engine, "todo", "CUSTOM")
    raw = path.read_bytes().replace(b"\n", b"\r\n")
    path.write_bytes(raw)
    head = commit(control, "custom path ticket")
    store = engine._ticket_store(head=head, require_clean=True)
    assert store.selected().body == "Work\n"
    view = inspect_object(engine, "ticket", "CUSTOM")["observation"]
    assert view["text"].encode() == raw
    assert view["provenance"]["control_head"] == head
    assert view["path"] == "workflow/todo/CUSTOM.md"


def test_real_size_owner_read_paths(engine, monkeypatch, tmp_path):
    control = engine.control_worktree
    workflow = engine._workflow_paths()
    (control / workflow["todo"] / "T-1.md").rename(
        control / workflow["backlog"] / "T-1.md",
    )
    for batch in range(5):
        for index in range(batch * 42, (batch + 1) * 42):
            name = f"S-{index:03}"
            deps = (f"S-{index - 1:03}",) if index else ()
            write_ticket(engine, "backlog", name, deps)
        head = commit(control, f"batch {batch}")
    git(control, "push", "origin", "HEAD")
    git(control, "gc", "--prune=now")

    launches, parses = [], []
    run = subprocess.run
    parse = tickets.parse_ticket_content

    def traced(argv, *args, **kwargs):
        if not isinstance(argv, str) and argv[0] == "git":
            launches.append(argv)
        return run(argv, *args, **kwargs)

    def parsed(text):
        parses.append(None)
        return parse(text)

    monkeypatch.setattr(subprocess, "run", traced)
    monkeypatch.setattr(tickets, "parse_ticket_content", parsed)
    monkeypatch.setattr(workflow_projection, "parse_ticket_content", parsed)

    def measure(label, read):
        launches.clear()
        parses.clear()
        start = perf_counter()
        value = read()
        reads = sum("cat-file" in argv for argv in launches)
        print(f"{label}: {(perf_counter() - start) * 1000:.2f} ms, "
              f"{len(launches)} Git, {reads} cat-file, {len(parses)} parses")
        return value, (len(launches), reads, len(parses))

    def legacy_ticket():
        revision, identity = _head(engine)
        tree = _tree(control, revision, *workflow.values())
        path = f"{workflow['backlog']}/S-209.md"
        text = _blob(control, tree[path])
        tickets.parse_ticket_text(control / path, "backlog", text)
        graph = {}
        for path, oid in tree.items():
            if tickets.is_canonical_ticket_name(path.rsplit("/", 1)[-1]):
                ticket = tickets.parse_ticket_text(
                    control / path, "backlog", _blob(control, oid),
                )
                graph[ticket.id] = ticket
        tickets._validate_cycles(graph)
        observe_git(control, "log", "--format=%H", "--no-renames", revision, "--",
                    *(f"{p}/S-209.md" for p in workflow.values()))
        assert _head(engine) == (revision, identity)

    def legacy_store(**_):
        engine._validate_control_worktree()
        store = tickets.load_ticket_store(control, workflow)
        engine._workflow_validation_succeeded = True
        return store

    def inspect():
        return inspect_object(engine, "ticket", "S-209")["observation"]

    _, baseline = measure("legacy inspection", legacy_ticket)
    assert baseline[1:] == (212, 212)
    with monkeypatch.context() as m:
        m.setattr(engine, "_ticket_store", legacy_store)
        assert run_test_iteration(engine) == 0  # Prime non-graph startup work.
        baseline_status, _ = measure("legacy status", engine.status_view)
        result, counts = measure(
            "legacy idle iteration", lambda: run_test_iteration(engine),
        )
        assert result == 0 and counts[2] >= 2 * 211

    cold, counts = measure("cold inspection", inspect)
    assert counts[1:] == (1, 211)
    assert cold["provenance"]["control_head"] == head
    warm, counts = measure("warm inspection", inspect)
    assert warm == cold and counts[1:] == (0, 0) and counts[0] < 25
    status, counts = measure("warm status", engine.status_view)
    assert status.counts == baseline_status.counts and counts[1:] == (0, 0)
    result, counts = measure("warm idle iteration", lambda: run_test_iteration(engine))
    assert result == 0 and counts[1:] == (0, 0)

    publisher = tmp_path / "publisher"
    git(tmp_path, "clone", "--branch", engine.control_branch,
        tmp_path / "remote.git", publisher)
    (publisher / "audit.txt").write_text("independent publisher\n")
    unrelated = commit(publisher, "independent non-workflow change")
    git(publisher, "push", "origin", "HEAD")
    previous = engine._committed_workflow(head)
    (_, _), counts = measure("independent sync", engine._sync_control)
    assert counts[1:] == (0, 0)
    unrelated_view, counts = measure("unrelated commit inspection", inspect)
    assert unrelated_view["provenance"]["control_head"] == unrelated
    assert counts[1:] == (0, 0)
    assert engine._committed_workflow(unrelated).graph is previous.graph

    (control / workflow["backlog"] / "S-209.md").rename(
        control / workflow["todo"] / "S-209.md",
    )
    write_ticket(engine, "todo", "S-209", ("S-208",), title="Amended")
    updated = commit(control, "transition and amend")
    changed, counts = measure("changed tree inspection", inspect)
    assert counts[1:] == (1, 1)
    assert changed["title"] == "Amended" and changed["state"] == "todo"
    assert changed["provenance"]["control_head"] == updated
    assert engine._committed_workflow(updated).graph is not previous.graph
    status, counts = measure("warm changed status", engine.status_view)
    assert not status.runnable and counts[1:] == (0, 0)
