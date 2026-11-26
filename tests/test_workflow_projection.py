# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from dataclasses import FrozenInstanceError
from pathlib import Path
from subprocess import run
from time import monotonic, perf_counter

import pytest
from git_support import git

from devlegate import clean_preview, tickets
from devlegate import workflow_projection as projection
from devlegate.change_explorer import ObservationExpired, observation_budget
from devlegate.git import GitObservationError
from devlegate.tickets import WORKFLOW_STATES, TicketError, load_ticket_store
from devlegate.workflow_projection import WorkflowProjectionCache

PATHS = {state: f"kanban/{state}" for state in WORKFLOW_STATES}


def text(title, *deps):
    edges = ""
    if deps:
        edges = '"depends_on":\n' + "".join(f'  - "{dep}"\n' for dep in deps)
    return f'---\n"type": "devlegate.ticket"\n"title": "{title}"\n{edges}---\nWork.\n'


def abandonment(target):
    return (
        '---\n"type": "devlegate.abandonment"\n"title": "Disposition"\n'
        f'"target": "{target}"\n"start_head": "{"a" * 40}"\n---\n'
        "## Abandonment reason\nReason.\n## Report requirements\nRequirements.\n"
    )


def put(repo, state, ticket_id, content):
    path = repo / PATHS[state] / f"{ticket_id}.md"
    path.write_bytes(content.encode() if isinstance(content, str) else content)
    return path


def commit(repo):
    git(repo, "add", "--all")
    git(repo, "commit", "--quiet", "-m", "workflow fixture")
    return git(repo, "rev-parse", "HEAD").stdout.strip()


def object_at(repo, head, path):
    return git(repo, "rev-parse", f"{head}:{path}").stdout.strip()


@pytest.fixture
def repo(tmp_path):
    git(tmp_path, "init", "--quiet", "--initial-branch=control")
    git(tmp_path, "config", "user.name", "Workflow fixture")
    git(tmp_path, "config", "user.email", "workflow@example.com")
    for path in PATHS.values():
        (tmp_path / path).mkdir(parents=True)
        (tmp_path / path / ".gitkeep").touch()
    return tmp_path


@pytest.fixture
def counts(monkeypatch):
    calls, parses, batches = [], [], []
    observe = clean_preview.observe_git
    parse_content = tickets.parse_ticket_content

    def run(repo, *args, **kwargs):
        calls.append(args[1] if args[0] == "--literal-pathspecs" else args[0])
        if args == ("cat-file", "--batch"):
            batches.append(kwargs["input_text"].splitlines())
        return observe(repo, *args, **kwargs)

    def parse(text):
        parses.append(text)
        return parse_content(text)

    monkeypatch.setattr(projection, "observe_git", run)
    monkeypatch.setattr(clean_preview, "observe_git", run)
    monkeypatch.setattr(projection, "parse_ticket_content", parse)
    monkeypatch.setattr(tickets, "parse_ticket_content", parse)
    return calls, parses, batches


def test_cancelled_blob_batch_can_be_retried(repo, monkeypatch):
    put(repo, "todo", "T-1", text("First"))
    put(repo, "todo", "T-2", text("Second"))
    head = commit(repo)
    cache = WorkflowProjectionCache(repo, PATHS)
    parse = projection.parse_ticket_content
    parsed = []

    def counted_parse(content):
        parsed.append(content)
        return parse(content)

    monkeypatch.setattr(projection, "parse_ticket_content", counted_parse)
    token = observation_budget.set((monotonic() + 30, lambda: bool(parsed)))
    try:
        with pytest.raises(ObservationExpired):
            cache.observe(head)
    finally:
        observation_budget.reset(token)
    assert len(parsed) == 1
    assert set(cache.observe(head).graph.by_id) == {"T-1", "T-2"}
    assert len(parsed) == 3


def test_same_tree_moves_edits_additions_deletions_and_attribution(repo, counts):
    common = text("Shared", "DEP").replace("\n", "\r\n")
    source = put(repo, "todo", "A", common)
    put(repo, "backlog", "B", common)
    put(repo, "done", "DEP", text("Completed"))
    deleted = put(repo, "todo", "REMOVE", text("Remove"))
    first = commit(repo)
    cache = WorkflowProjectionCache(repo, PATHS)
    cold = cache.observe(first)
    calls, parses, _ = counts
    assert calls.count("cat-file") == 1
    assert len(parses) == 3
    a, = cold.graph.by_id["A"]
    b, = cold.graph.by_id["B"]
    assert (a.id, a.state, a.path, a.text) == ("A", "todo", "kanban/todo/A.md", common)
    assert (b.id, b.state, b.path, b.text) == (
        "B", "backlog", "kanban/backlog/B.md", common,
    )
    assert a.blob == b.blob == object_at(repo, first, a.path)
    assert a.ticket.depends_on == b.ticket.depends_on == ("DEP",)
    assert a.ticket.body == "Work.\n"
    assert [ticket.id for ticket in cold.graph.runnable] == ["A", "REMOVE"]
    with pytest.raises(TypeError):
        cold.graph.by_id["A"] = ()
    with pytest.raises(FrozenInstanceError):
        a.state = "done"

    calls.clear()
    parses.clear()
    assert cache.observe(first).graph is cold.graph
    # Even an unrelated entry under the common kanban root changes no configured tree.
    (repo / "kanban/notes").write_text("unrelated")
    unrelated = commit(repo)
    warm = cache.observe(unrelated)
    assert warm.control_head == unrelated != cold.control_head
    assert warm.graph is cold.graph
    assert calls == ["rev-parse", "ls-tree"] * 2
    assert not parses

    source.rename(repo / "kanban/review/A.md")
    deleted.unlink()
    put(repo, "backlog", "B", text("Edited"))
    put(repo, "todo", "NEW", text("New"))
    changed = cache.observe(commit(repo)).graph
    assert changed is not cold.graph
    assert set(changed.by_id) == {"A", "B", "DEP", "NEW"}
    moved, = changed.by_id["A"]
    assert (moved.id, moved.state, moved.path, moved.text, moved.blob) == (
        "A", "review", "kanban/review/A.md", common, a.blob,
    )
    assert moved.ticket.depends_on == ("DEP",)
    assert changed.by_id["B"][0].blob != b.blob
    assert changed.by_id["B"][0].ticket.title == "Edited"
    assert [ticket.id for ticket in changed.runnable] == ["NEW"]
    assert parses == [text("Edited"), text("New")]
    assert cold.graph.by_id["A"][0].state == "todo"


def test_configured_paths_missing_and_empty_trees(repo):
    put(repo, "todo", "A", text("Canonical"))
    alternate = "other/[ready]"
    (repo / alternate).mkdir(parents=True)
    (repo / alternate / "B.md").write_text(text("Elsewhere"))
    head = commit(repo)
    canonical = WorkflowProjectionCache(repo, PATHS)
    assert canonical.observe(head).graph.selected().id == "A"
    config = {**PATHS, "todo": alternate}
    custom = WorkflowProjectionCache(repo, config)
    config["todo"] = PATHS["todo"]
    graph = custom.observe(head).graph
    assert graph.selected().id == "B"
    assert graph.by_id["B"][0].path == f"{alternate}/B.md"
    (repo / alternate / "B.md").unlink()
    absent = custom.observe(commit(repo)).graph
    assert absent.entries == ()
    assert absent.directories[1].object_id is None
    assert absent.store is None
    assert absent.diagnostics == (
        f"managed workflow directory is unavailable: {alternate}",
    )
    (repo / alternate / ".gitkeep").touch()
    empty = custom.observe(commit(repo)).graph
    assert empty.directories[1].object_id is not None
    assert empty.entries == empty.diagnostics == empty.runnable == ()
    assert empty.store is not None


def test_nested_mapping_and_literal_empty_tree(repo):
    put(repo, "todo", "A", text("Ready"))
    (repo / "kanban/todo/nested").mkdir()
    (repo / "kanban/todo/nested/N.md").write_text(text("Review"))
    head = commit(repo)
    paths = {**PATHS, "review": "kanban/todo/nested"}
    graph = WorkflowProjectionCache(repo, paths).observe(head).graph
    assert graph.store is not None
    assert {entry.id: entry.state for entry in graph.entries} == {
        "A": "todo", "N": "review",
    }

    def mktree(input_text):
        return run(
            ["git", "mktree"], cwd=repo, input=input_text, text=True,
            capture_output=True, check=True,
        ).stdout.strip()

    empty_tree = mktree("")
    kanban = mktree("".join(f"040000 tree {empty_tree}\t{state}\n" for state in PATHS))
    root = mktree(f"040000 tree {kanban}\tkanban\n")
    empty_head = git(repo, "commit-tree", root, "-m", "empty boards").stdout.strip()
    empty = WorkflowProjectionCache(repo, PATHS).observe(empty_head).graph
    assert all(directory.object_id == empty_tree for directory in empty.directories)
    assert empty.store is not None
    assert empty.entries == empty.diagnostics == ()


@pytest.mark.parametrize(
    ("entries", "diagnostic"),
    [
        ([("todo", "A", text("A")), ("review", "A", text("A"))],
         "duplicate ticket ID A"),
        ([("todo", "A", text("A", "ABSENT"))],
         "missing dependency: A depends on ABSENT"),
        ([("todo", "A", text("A", "B")), ("todo", "B", text("B", "A"))],
         "dependency cycle: A -> B -> A"),
        ([("todo", "A", text("A").replace('"devlegate.ticket"', '[]'))],
         'metadata "type" must be'),
        ([("todo", "A", b"\xff")], "ticket text is not UTF-8"),
        ([("todo", "A", text("A")), ("todo", "R", abandonment("A")),
          ("review", "R2", abandonment("A"))], "conflicting abandonment request R"),
    ],
)
def test_invalid_graph_retains_claims_and_never_schedules(repo, entries, diagnostic):
    for state, ticket_id, body in entries:
        put(repo, state, ticket_id, body)
    put(repo, "todo", "OTHER", text("Otherwise runnable"))
    head = commit(repo)
    cache = WorkflowProjectionCache(repo, PATHS)
    graph = cache.observe(head).graph
    assert any(diagnostic in message for message in graph.diagnostics)
    assert graph.store is graph.selected() is None
    assert graph.runnable == ()
    assert len(graph.entries) == len(entries) + 1
    for state, ticket_id, body in entries:
        entry = next(node for node in graph.by_id[ticket_id] if node.state == state)
        assert entry.text == (body if isinstance(body, str) else None)
        assert entry.blob == object_at(repo, head, entry.path)
    assert graph.by_id["OTHER"][0].ticket.title == "Otherwise runnable"
    assert cache.observe(head).graph is graph


def test_same_content_bound_again_after_id_change_and_malformed_move(repo, counts):
    dependent = put(repo, "todo", "A", text("Dependent", "DEP"))
    put(repo, "done", "DEP", text("Completed"))
    put(repo, "todo", "R", abandonment("T"))
    put(repo, "todo", "T", abandonment("T"))
    malformed = put(repo, "todo", "BAD", "no frontmatter")
    head = commit(repo)
    cache = WorkflowProjectionCache(repo, PATHS)
    before = cache.observe(head).graph
    assert before.by_id["R"][0].ticket.target == "T"
    assert "cannot target itself" in before.by_id["T"][0].diagnostic
    assert len(counts[1]) == 4
    dependent.rename(repo / "kanban/review/DEP.md")
    malformed.rename(repo / "kanban/review/BAD2.md")
    after = cache.observe(commit(repo)).graph
    self_edge = next(entry for entry in after.by_id["DEP"] if entry.state == "review")
    assert self_edge.blob == before.by_id["A"][0].blob
    assert "cannot depend on itself" in self_edge.diagnostic
    assert "review/BAD2.md" in after.by_id["BAD2"][0].diagnostic
    assert any("duplicate ticket ID DEP" in message for message in after.diagnostics)
    assert len(counts[1]) == 4


def test_selection_matches_store_with_reservations_and_terminal_dependencies(repo):
    put(repo, "todo", "A", text("Reserved"))
    put(repo, "todo", "B", text("Unblocked", "DONE"))
    put(repo, "todo", "C", text("Blocked", "STOPPED"))
    put(repo, "done", "DONE", text("Completed"))
    put(repo, "abandoned", "STOPPED", text("Abandoned"))
    request = put(repo, "todo", "R", abandonment("A"))
    cache = WorkflowProjectionCache(repo, PATHS)
    graph = cache.observe(commit(repo)).graph
    store = load_ticket_store(repo, PATHS)
    assert graph.store == store
    assert graph.runnable == store.runnable
    assert graph.selected() == store.selected()
    assert [ticket.id for ticket in graph.runnable] == ["R", "B"]
    request.rename(repo / "kanban/accepted/R.md")
    assert [ticket.id for ticket in cache.observe(commit(repo)).graph.runnable] == ["B"]
    request = repo / "kanban/accepted/R.md"
    request.rename(repo / "kanban/done/R.md")
    assert [ticket.id for ticket in cache.observe(commit(repo)).graph.runnable] == [
        "A", "B",
    ]


def test_explicit_revision_ignores_ref_and_worktree_changes_and_evicts(repo, counts):
    changed = put(repo, "todo", "A", text("First"))
    first = commit(repo)
    git(repo, "branch", "historical", first)
    put(repo, "todo", "A", text("Second"))
    second = commit(repo)
    git(repo, "replace", first, second)
    changed.write_text(text("Uncommitted"))
    cache = WorkflowProjectionCache(repo, PATHS, graph_capacity=1, blob_capacity=1)
    one = cache.observe(first)
    assert one.control_head == first
    assert one.graph.selected().title == "First"
    assert cache.observe(second).graph.selected().title == "Second"
    rebuilt = cache.observe(first)
    assert rebuilt.graph is not one.graph
    assert rebuilt.graph == one.graph
    assert WorkflowProjectionCache(repo, PATHS).observe(first) == rebuilt
    assert len(counts[1]) == 4
    with pytest.raises(GitObservationError, match="full control commit"):
        cache.observe("historical")
    git(repo, "tag", "-am", "not a commit", "annotated", first)
    tag = git(repo, "rev-parse", "annotated").stdout.strip()
    with pytest.raises(GitObservationError, match="not an exact control commit"):
        cache.observe(tag)
    assert changed.read_text() == text("Uncommitted")
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == second


@pytest.mark.parametrize("object_path", ["kanban/todo/C.md", "kanban/todo"])
def test_missing_object_during_build_is_not_published(repo, counts, object_path):
    put(repo, "todo", "A", text("First"))
    first = commit(repo)
    cache = WorkflowProjectionCache(repo, PATHS)
    old = cache.observe(first)
    put(repo, "todo", "C", text("Also new"))
    put(repo, "todo", "B", text("New"))
    second = commit(repo)
    oid = git(repo, "rev-parse", f"{second}:{object_path}").stdout.strip()
    path = repo / ".git/objects" / oid[:2] / oid[2:]
    contents = path.read_bytes()
    path.unlink()
    counts[1].clear()
    with pytest.raises(GitObservationError, match="unavailable|failed"):
        cache.observe(second)
    assert cache.observe(first).graph is old.graph
    prior_attempt = [text("New")] if object_path.endswith(".md") else []
    assert counts[1] == prior_attempt
    path.write_bytes(contents)
    assert set(cache.observe(second).graph.by_id) == {"A", "B", "C"}
    assert counts[1] == [*prior_attempt, text("New"), text("Also new")]
    assert git(repo, "status", "--porcelain").stdout == ""


def test_nonregular_canonical_claim_is_diagnosed(repo):
    put(repo, "todo", "OK", text("Valid"))
    (repo / "kanban/todo/LINK.md").symlink_to("OK.md")
    directory = repo / "kanban/todo/DIR.md"
    directory.mkdir()
    (directory / "ignored").touch()
    graph = WorkflowProjectionCache(repo, PATHS).observe(commit(repo)).graph
    assert graph.by_id["LINK"][0].mode == "120000"
    assert graph.by_id["DIR"][0].mode == "040000"
    assert len(graph.diagnostics) == 2
    assert graph.runnable == ()
    with pytest.raises(TicketError, match="invalid managed ticket entry"):
        load_ticket_store(repo, PATHS)


def test_representative_packed_history(repo, counts):
    for number in range(210):
        state = WORKFLOW_STATES[(number // 35) % len(WORKFLOW_STATES)]
        deps = () if number == 0 else (f"T-{number - 1:03}",)
        put(repo, state, f"T-{number:03}", text(f"Ticket {number}", *deps))
        if number % 35 == 34:
            head = commit(repo)
    git(repo, "gc", "--prune=now")
    assert list((repo / ".git/objects/pack").glob("*.pack"))
    cache = WorkflowProjectionCache(repo, PATHS)
    calls, parses, batches = counts

    def measure(label, operation):
        calls.clear()
        parses.clear()
        batches.clear()
        start = perf_counter()
        result = operation()
        elapsed_ms = (perf_counter() - start) * 1000
        print(f"{label}: {elapsed_ms:.2f} ms, {len(calls)} Git, {len(parses)} parses")
        return result

    def baseline():
        return {
            (Path(path).stem, state): tickets.parse_ticket_text(
                repo / path, state, clean_preview._blob(repo, oid),
            )
            for path, oid in clean_preview._tree(repo, head, *PATHS.values()).items()
            for state, directory in PATHS.items()
            if Path(path).parent.as_posix() == directory
            and tickets.is_canonical_ticket_name(Path(path).name)
        }

    old = measure("per-ticket inspection reads", baseline)
    assert len(calls) == 211 and len(parses) == 210
    cold = measure("cold projection", lambda: cache.observe(head)).graph
    assert len(calls) == 9 and calls.count("cat-file") == 1 and len(parses) == 210
    assert {(entry.id, entry.state): entry.ticket for entry in cold.entries} == old
    assert measure("warm projection", lambda: cache.observe(head)).graph is cold
    assert calls == ["rev-parse", "ls-tree"] and not parses

    (repo / "notes").write_text("unrelated commit")
    unrelated = commit(repo)
    observed = measure("unrelated commit", lambda: cache.observe(unrelated))
    assert observed.control_head == unrelated and observed.graph is cold
    assert calls == ["rev-parse", "ls-tree"] and not parses

    put(repo, "todo", "T-035", text("Edited 35", "T-034"))
    put(repo, "todo", "T-036", text("Edited 36", "T-035"))
    (repo / "kanban/todo/T-037.md").rename(repo / "kanban/review/T-037.md")
    updated = commit(repo)
    new = measure("two edited blobs and one move", lambda: cache.observe(updated)).graph
    assert new is not cold and len(new.entries) == 210
    assert len(calls) == 9 and calls.count("cat-file") == 1 and len(parses) == 2
    edited = [new.by_id[ticket_id][0].blob for ticket_id in ("T-035", "T-036")]
    assert batches == [edited]
    assert new.by_id["T-037"][0].state == "review"
    assert new.by_id["T-037"][0].blob == cold.by_id["T-037"][0].blob
