# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Abandonment fixtures never use hosted control or execution resources."""

import base64
import hashlib
import json
import subprocess
import sys
from types import SimpleNamespace

import pytest

from devlegate.abandonment_evidence import (
    MAX_EXCERPT_BYTES,
    MAX_INLINE_BYTES,
    MAX_READ_BYTES,
    EvidenceBuilder,
    EvidenceError,
    GitArtifact,
    OwnerGitEvidence,
    build_abandonment_evidence,
)
from devlegate.execution_result import ExecutionReport, ExecutionResult
from devlegate.runtime import ServiceEngine
from devlegate.tickets import TicketError, load_ticket_store
from devlegate.worker_egress import WorkerClaim

PATHS = {
    state: f"kanban/{state}"
    for state in ("backlog", "todo", "review", "accepted", "done", "abandoned")
}


def workflow_directories(repo):
    for path in PATHS.values():
        directory = repo / path
        directory.mkdir(parents=True, exist_ok=True)
        (directory / ".gitkeep").touch()


def ticket(repo, ticket_id, state="todo", *, dependencies=()):
    workflow_directories(repo)
    path = repo / PATHS[state] / f"{ticket_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    deps = f'"depends_on": {json.dumps(list(dependencies))}\n' if dependencies else ""
    path.write_text(
        f'---\n"type": "devlegate.ticket"\n"title": "{ticket_id}"\n'
        f"{deps}---\nImplement the requested behavior.\n"
    )
    return path


def request(repo, request_id="Z-REQUEST", target="A-TARGET", start_head="a" * 40):
    workflow_directories(repo)
    path = repo / PATHS["todo"] / f"{request_id}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        '---\n"type": "devlegate.abandonment"\n"title": "Retire unfinished work"\n'
        f'"target": "{target}"\n"start_head": "{start_head}"\n---\n'
        "## Abandonment reason\nThe experiment was superseded by a different design.\n"
        "## Report requirements\nDescribe each execution, review and unresolved gap.\n"
    )
    return path


def test_abandoned_dependency_is_known_but_never_satisfied(tmp_path):
    ticket(tmp_path, "A", "abandoned")
    ticket(tmp_path, "B", dependencies=("A",))
    store = load_ticket_store(tmp_path, PATHS)
    assert store.by_id["A"].state == "abandoned"
    assert not store.runnable
    with pytest.raises(TicketError, match="all six states"):
        load_ticket_store(
            tmp_path, {k: v for k, v in PATHS.items() if k != "abandoned"}
        )


def test_absent_or_symlinked_sixth_board_is_refused(tmp_path):
    workflow_directories(tmp_path)
    abandoned = tmp_path / PATHS["abandoned"]
    (abandoned / ".gitkeep").unlink()
    abandoned.rmdir()
    with pytest.raises(TicketError, match="directory is unavailable.*abandoned"):
        load_ticket_store(tmp_path, PATHS)
    abandoned.symlink_to(tmp_path / PATHS["done"], target_is_directory=True)
    with pytest.raises(TicketError, match="directory is unavailable.*abandoned"):
        load_ticket_store(tmp_path, PATHS)


def test_request_reserves_target_and_has_selection_priority(tmp_path):
    ticket(tmp_path, "A-TARGET")
    ticket(tmp_path, "B-OTHER")
    request(tmp_path)
    store = load_ticket_store(tmp_path, PATHS)
    assert [item.id for item in store.runnable] == ["Z-REQUEST", "B-OTHER"]
    assert store.selected().target == "A-TARGET"
    assert store.selected().abandonment_reason.startswith("The experiment")


@pytest.mark.parametrize(
    "old,new,message",
    [
        ('"target": "A-TARGET"', '"target": ["A-TARGET"]', "target"),
        ('"target": "A-TARGET"', '"target": "../bad"', "target"),
        ('"target": "A-TARGET"', '"target": "Z-REQUEST"', "itself"),
        ('"start_head": "' + "a" * 40 + '"', '"start_head": "HEAD"', "start_head"),
        ("## Abandonment reason\nThe experiment was superseded by a different design.",
         "## Abandonment reason\n", "concrete text"),
        ("## Report requirements", "## Other", "Report requirements"),
        ('"title": "Retire unfinished work"',
         '"title": "Retire unfinished work"\n"extra": "value"', "unknown"),
    ],
)
def test_request_schema_is_strict(tmp_path, old, new, message):
    ticket(tmp_path, "A-TARGET")
    path = request(tmp_path)
    path.write_text(path.read_text().replace(old, new))
    with pytest.raises(TicketError, match=message):
        load_ticket_store(tmp_path, PATHS)


def test_request_conflict_missing_and_terminal_target_fail_closed(tmp_path):
    request(tmp_path)
    with pytest.raises(TicketError, match="missing abandonment target"):
        load_ticket_store(tmp_path, PATHS)
    target = ticket(tmp_path, "A-TARGET")
    second = request(tmp_path, "OTHER")
    with pytest.raises(TicketError, match="conflicting abandonment"):
        load_ticket_store(tmp_path, PATHS)
    second.unlink()
    done = tmp_path / PATHS["done"]
    done.mkdir(exist_ok=True)
    target.rename(done / target.name)
    with pytest.raises(TicketError, match="already terminal"):
        load_ticket_store(tmp_path, PATHS)


def test_ordinary_ticket_cannot_use_abandonment_metadata(tmp_path):
    path = ticket(tmp_path, "A")
    path.write_text(path.read_text().replace('"title": "A"',
                                           '"title": "A"\n"target": "B"'))
    with pytest.raises(TicketError, match="unknown metadata"):
        load_ticket_store(tmp_path, PATHS)


@pytest.mark.parametrize("state", ["todo", "review", "accepted"])
def test_request_cannot_enter_checkout_worker_or_product_integration(tmp_path, state):
    ticket(tmp_path, "A-TARGET")
    path = request(tmp_path)
    if state != "todo":
        destination = tmp_path / PATHS[state] / path.name
        destination.parent.mkdir(parents=True, exist_ok=True)
        path.rename(destination)
    engine = ServiceEngine.__new__(ServiceEngine)
    store = load_ticket_store(tmp_path, PATHS)
    barrier = engine._admission_barrier(
        {"phase": "idle"}, store, False, True, SimpleNamespace(detached=False)
    )
    assert barrier.action == "blocked"
    assert barrier.kind == "abandonment"
    assert barrier.ticket_id == "Z-REQUEST"
    assert "A-TARGET" in barrier.reason


def git(repo, *args, data=None):
    return subprocess.run(
        ["git", "-c", "user.name=Disposable Test", "-c", "user.email=test@example.test",
         "-C", str(repo), *args],
        input=data, capture_output=True, check=True,
    ).stdout.decode().strip()


def init_repo(path):
    path.mkdir()
    git(path, "init", "-b", "fixture")
    return path


def commit(repo, message):
    git(repo, "add", ".")
    git(repo, "commit", "-m", message)
    return git(repo, "rev-parse", "HEAD")


def owner_source(repo):
    return OwnerGitEvidence(repo, lambda: None)


def blob(repo, data):
    return git(repo, "hash-object", "-w", "--stdin", data=data)


def test_streamed_artifacts_have_hard_inline_and_read_limits(tmp_path):
    repo = init_repo(tmp_path / "objects")
    source = owner_source(repo)
    builder = EvidenceBuilder(source)
    below = b"a" * (MAX_EXCERPT_BYTES - 1)
    above = b"b" * (MAX_EXCERPT_BYTES + 1)
    first = builder.add(GitArtifact("blob", (blob(repo, below),)))
    second = builder.add(GitArtifact("blob", (blob(repo, above),)))
    # Distinct immutable objects fill the shared inline budget exactly.
    for index in range(16):
        builder.add(GitArtifact("blob", (blob(
            repo, bytes([65 + index]) * MAX_EXCERPT_BYTES
        ),)))
    binary = bytes(range(256)) * (MAX_READ_BYTES // 128)
    binary_oid = blob(repo, binary)
    binary_id = builder.add(GitArtifact("blob", (binary_oid,)))
    bundle = builder.seal({"target": "DISPOSABLE"})
    artifacts = {item["id"]: item for item in bundle.manifest()["artifacts"]}
    assert artifacts[first]["omission"] is None
    assert artifacts[second]["inline_bytes"] == MAX_EXCERPT_BYTES
    assert artifacts[second]["omission"]
    assert sum(item["inline_bytes"] for item in artifacts.values()) == MAX_INLINE_BYTES
    assert any(
        item["inline_bytes"] == 0 and item["omission"] for item in artifacts.values()
    )
    assert artifacts[binary_id]["size"] == len(binary)
    pages = []
    for offset in range(0, len(binary), MAX_READ_BYTES):
        result = bundle.read(bundle.id, binary_id, offset, MAX_READ_BYTES)
        assert result["encoding"] == "base64"
        pages.append(base64.b64decode(result["content"]))
    assert b"".join(pages) == binary
    assert result["sha256"] == hashlib.sha256(binary).hexdigest()
    assert bundle.manifest() == bundle.manifest()


@pytest.mark.parametrize("offset,length", [(-1, 1), (0, 0), (0, MAX_READ_BYTES + 1),
                                           (True, 1), (0, False), (10, 1), (0, 11)])
def test_invalid_byte_ranges_are_denied(tmp_path, offset, length):
    repo = init_repo(tmp_path / "objects")
    builder = EvidenceBuilder(owner_source(repo))
    identity = builder.add(GitArtifact("blob", (blob(repo, b"0123456789"),)))
    bundle = builder.seal({})
    with pytest.raises(EvidenceError, match="byte range"):
        bundle.read(bundle.id, identity, offset, length)


def test_allowlist_stale_object_and_owner_assertion(tmp_path):
    repo = init_repo(tmp_path / "objects")
    owner = True

    def assert_owner():
        if not owner:
            raise EvidenceError("not the owner")

    builder = EvidenceBuilder(OwnerGitEvidence(repo, assert_owner))
    oid = blob(repo, b"only authorized bytes")
    identity = builder.add(GitArtifact("blob", (oid,)))
    bundle = builder.seal({})
    for artifact_id in ("../../etc/passwd", "/etc/passwd", oid, "other"):
        with pytest.raises(EvidenceError, match="authorized"):
            bundle.read(bundle.id, artifact_id, 0, 1)
    with pytest.raises(EvidenceError, match="authorized"):
        bundle.read("other-manifest", identity, 0, 1)
    owner = False
    with pytest.raises(EvidenceError, match="not the owner"):
        bundle.read(bundle.id, identity, 0, 1)
    owner = True
    # Only an unreachable disposable loose object is removed in this fixture.
    (repo / ".git" / "objects" / oid[:2] / oid[2:]).unlink()
    with pytest.raises(EvidenceError, match="absent or unreadable"):
        bundle.read(bundle.id, identity, 0, 1)


def test_changed_content_is_not_released_under_old_digest(tmp_path):
    repo = init_repo(tmp_path / "objects")
    source = owner_source(repo)
    builder = EvidenceBuilder(source)
    identity = builder.add(GitArtifact("blob", (blob(repo, b"original"),)))
    bundle = builder.seal({})
    source._verified.clear()
    with pytest.raises(EvidenceError, match="changed"):
        bundle.read(bundle.id, identity, 0, 1)


def test_sequential_binary_pages_have_linear_source_io(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "objects")
    data = bytes(range(256)) * (MAX_READ_BYTES // 256) * 32
    source = owner_source(repo)
    builder = EvidenceBuilder(source)
    identity = builder.add(GitArtifact("blob", (blob(repo, data),)))
    bundle = builder.seal({})
    remaining = source._bytes_left
    starts = []
    original = source._start

    def start(artifact):
        starts.append(artifact)
        return original(artifact)

    monkeypatch.setattr(source, "_start", start)
    for offset in range(0, len(data), MAX_READ_BYTES):
        result = bundle.read(bundle.id, identity, offset, MAX_READ_BYTES)
        assert base64.b64decode(result["content"]) == data[
            offset:offset + MAX_READ_BYTES
        ]
    assert starts == [GitArtifact("blob", (blob(repo, data),))]
    # Include one tiny immutable-size observation per page, never a full rescan.
    assert remaining - source._bytes_left < len(data) + 1024
    bundle.close()
    assert source._stream is None


def test_replaced_object_page_is_denied_before_release(tmp_path):
    repo = init_repo(tmp_path / "objects")
    data = b"a" * (MAX_READ_BYTES * 2)
    oid = blob(repo, data)
    replacement = blob(repo, b"b" * len(data))
    source = owner_source(repo)
    builder = EvidenceBuilder(source)
    identity = builder.add(GitArtifact("blob", (oid,)))
    bundle = builder.seal({})
    # Corrupt only this disposable loose object, preserving the requested name.
    (repo / ".git/objects" / oid[:2] / oid[2:]).chmod(0o600)
    (repo / ".git/objects" / oid[:2] / oid[2:]).write_bytes(
        (repo / ".git/objects" / replacement[:2] / replacement[2:]).read_bytes()
    )
    with pytest.raises(EvidenceError, match="changed"):
        bundle.read(bundle.id, identity, MAX_READ_BYTES, 1)
    assert source._stream is None


def test_owner_byte_call_and_elapsed_budgets_are_enforced(tmp_path, monkeypatch):
    repo = init_repo(tmp_path / "objects")
    artifact = GitArtifact("blob", (blob(repo, b"a" * (MAX_READ_BYTES + 1)),))
    source = OwnerGitEvidence(repo, lambda: None, byte_budget=MAX_READ_BYTES)
    with pytest.raises(EvidenceError, match="byte budget"):
        source.scan(artifact)
    source = OwnerGitEvidence(repo, lambda: None, call_budget=2)
    source.scan(artifact)
    with pytest.raises(EvidenceError, match="request/time budget"):
        source.scan(artifact)
    source = OwnerGitEvidence(repo, lambda: None, seconds_budget=0.05)
    monkeypatch.setattr(source, "_command", lambda *_args: [
        sys.executable, "-c", "import time; time.sleep(10)",
    ])
    with pytest.raises(EvidenceError, match="time budget"):
        source.scan(artifact)


def test_control_tree_observations_skip_unrelated_history(tmp_path, monkeypatch):
    product, control, _head, _start, _base2, _checkpoint2 = history_fixture(tmp_path)
    for index in range(20):
        (control / "unrelated.txt").write_text(str(index))
        commit(control, "Unrelated control publication")
    head = git(control, "rev-parse", "HEAD")
    store = load_ticket_store(control, PATHS)
    source = owner_source(control)
    observations = []
    small = source.small

    def observe(*args, **kwargs):
        if args[0] == "ls-tree":
            observations.append(args)
        return small(*args, **kwargs)

    monkeypatch.setattr(source, "small", observe)
    bundle = build_abandonment_evidence(
        control=source, product=owner_source(product), control_head=head,
        request=store.by_id["Z-REQUEST"], target=store.by_id["A-TARGET"],
        request_path="kanban/todo/Z-REQUEST.md", target_path="kanban/todo/A-TARGET.md",
    )
    assert len(bundle.manifest()["executions"]) == 2
    # Four target events, final tree, specs, two admissions, and exact review
    # predecessor/destination proofs. Unrelated commits add no tree observations.
    assert len(observations) == 14


def test_non_utf8_and_utf8_limit_boundary_keep_index_entries(tmp_path):
    repo = init_repo(tmp_path / "objects")
    builder = EvidenceBuilder(owner_source(repo))
    bad = builder.add(GitArtifact("blob", (blob(repo, b"text\xffrest"),)))
    # The three-byte code point crosses the exact excerpt limit.
    data = b"a" * (MAX_EXCERPT_BYTES - 1) + bytes([0xE2, 0x82, 0xAC]) + b"rest"
    utf8 = builder.add(GitArtifact("blob", (blob(repo, data),)))
    bundle = builder.seal({})
    entries = {entry["id"]: entry for entry in bundle.manifest()["artifacts"]}
    assert "non-UTF-8" in entries[bad]["omission"]
    assert entries[utf8]["text"] == "a" * (MAX_EXCERPT_BYTES - 1)
    assert entries[utf8]["omission"]
    read = bundle.read(bundle.id, utf8, MAX_EXCERPT_BYTES - 1, 3)
    assert read["encoding"] == "utf-8"
    assert read["content"].encode("utf-8") == bytes([0xE2, 0x82, 0xAC])


def test_large_diff_is_indexed_and_can_be_paged_beyond_inline_limit(tmp_path):
    repo = init_repo(tmp_path / "objects")
    path = repo / "large.txt"
    path.write_text("base\n")
    base = commit(repo, "Before large text")
    path.write_bytes(b"x" * (MAX_INLINE_BYTES + 100) + b"\n")
    checkpoint = commit(repo, "Large text")
    builder = EvidenceBuilder(owner_source(repo))
    identity = builder.add(GitArtifact("diff", (base, checkpoint)))
    bundle = builder.seal({})
    entry = bundle.manifest()["artifacts"][0]
    assert entry["size"] > MAX_INLINE_BYTES
    assert entry["inline_bytes"] == MAX_EXCERPT_BYTES
    assert entry["omission"]
    result = bundle.read(bundle.id, identity, MAX_INLINE_BYTES, 100)
    assert result["content"] == "x" * 100


@pytest.mark.parametrize("objects", [("HEAD",), ("../file",), ("a" * 39,), ()])
def test_source_selectors_cannot_be_refs_or_paths(objects):
    with pytest.raises(EvidenceError, match="identity"):
        GitArtifact("blob", objects)


def publish_report(
    repo, execution_id, base, checkpoint, *, deferred=False, accepted=False,
):
    admission = git(repo, "rev-parse", "HEAD")
    claim = WorkerClaim("incomplete", "Investigated the approach", ("Finish work",), ())
    result = ExecutionResult(0, None, None, claim, "incomplete", (),
                             ("Finish work",), "valid worker claim")
    report = ExecutionReport(
        execution_id, "A-TARGET", base, admission, "devlegate/work/A-TARGET",
        "/disposable/work", checkpoint, result,
    )
    report_path = repo / "executions" / "A-TARGET" / f"{execution_id}.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(report.to_json())
    review = repo / PATHS["review"]
    review.mkdir(parents=True, exist_ok=True)
    (repo / PATHS["todo"] / "A-TARGET.md").rename(review / "A-TARGET.md")
    commit(repo, "Publish execution and enter review")
    review_path = repo / "reviews" / "A-TARGET" / f"{execution_id}.md"
    review_path.parent.mkdir(parents=True, exist_ok=True)
    disposition = "accept" if accepted else "rework"
    review_path.write_text(
        "<!-- devlegate-review-v1\n"
        f"ticket: A-TARGET\nexecution: {execution_id}\ncheckpoint: {checkpoint}\n"
        f"control: {admission}\ndisposition: {disposition}\n-->\n"
        "The approach needs another iteration.\n"
    )
    destination = "accepted" if accepted else ("backlog" if deferred else "todo")
    (review / "A-TARGET.md").rename(repo / PATHS[destination] / "A-TARGET.md")
    commit(repo, "Publish independently attributed rework review")


def history_fixture(tmp_path, *, deferred=False, accepted=False):
    product = init_repo(tmp_path / "product")
    (product / "file.txt").write_text("starting point\n")
    (product / "obsolete.txt").write_text("remove in the second execution\n")
    start = commit(product, "Initial product")
    (product / "file.txt").write_text("first attempt\n")
    checkpoint1 = commit(product, "First experiment")
    # A separate second base has unrelated product evolution.
    (product / "unrelated.txt").write_text("product advancement\n")
    base2 = commit(product, "Advanced product base")
    (product / "file.txt").rename(product / "renamed.txt")
    (product / "obsolete.txt").unlink()
    (product / "huge.bin").write_bytes(bytes(range(256)) * 4096)
    (product / "link").symlink_to("/etc/passwd")
    checkpoint2 = commit(product, "Second experiment")
    control = init_repo(tmp_path / "control")
    ticket(control, "A-TARGET")
    commit(control, "Admit target")
    # IDs deliberately run opposite lexical order.
    publish_report(control, "f" * 32, start, checkpoint1)
    publish_report(control, "0" * 32, base2, checkpoint2,
                   deferred=deferred, accepted=accepted)
    request(control, start_head=start)
    head = commit(control, "Admit abandonment request")
    return product, control, head, start, base2, checkpoint2


def build_fixture(product, control, head):
    store = load_ticket_store(control, PATHS)
    return build_abandonment_evidence(
        control=owner_source(control), product=owner_source(product), control_head=head,
        request=store.by_id["Z-REQUEST"], target=store.by_id["A-TARGET"],
        request_path="kanban/todo/Z-REQUEST.md",
        target_path=store.by_id["A-TARGET"].path.relative_to(control).as_posix(),
    )


def test_real_git_chronology_distinct_bases_reviews_and_bounded_binary(tmp_path):
    product, control, head, start, base2, checkpoint2 = history_fixture(tmp_path)
    bundle = build_fixture(product, control, head)
    manifest = bundle.manifest()
    assert build_fixture(product, control, head).manifest() == manifest
    executions = manifest["executions"]
    assert all("gap" not in item for item in executions), executions
    assert [item["execution_id"] for item in executions] == ["f" * 32, "0" * 32]
    assert [item["delta"]["base"] for item in executions] == [start, base2]
    assert all(item["review_decision"] == "rework" for item in executions)
    second = executions[1]["delta"]
    assert second["checkpoint"] == checkpoint2
    assert all(item["path"] != "unrelated.txt" for item in second["files"])
    assert any(item["status"].startswith("R") for item in second["files"])
    deleted = next(item for item in second["files"] if item["path"] == "obsolete.txt")
    assert deleted["status"] == "D"
    assert deleted["after"] is None
    link = next(item for item in second["files"] if item["path"] == "link")
    assert "symlink_blob" in link["after"]
    binary = next(item for item in second["files"] if item["path"] == "huge.bin")
    result = bundle.read(bundle.id, binary["after"], MAX_READ_BYTES, MAX_READ_BYTES)
    assert base64.b64decode(result["content"]) == (bytes(range(256)) * 1024)
    assert "NOT cumulative" in manifest["latest_state_comparison"]["label"]
    assert manifest["gaps"]  # Resource-generation proof is never invented.


def test_still_deferred_review_has_exact_historical_attribution(tmp_path):
    product, control, head, _start, _base2, _checkpoint = history_fixture(
        tmp_path, deferred=True,
    )
    bundle = build_fixture(product, control, head)
    manifest = bundle.manifest()
    assert manifest["executions"][0]["review_decision"] == "rework"
    assert manifest["executions"][1]["review_decision"] == "rework-deferred"
    assert all("review_gap" not in entry for entry in manifest["executions"])
    assert "kanban/backlog/A-TARGET.md" in manifest["specifications"]


def test_exact_accepted_review_is_attributed_without_inventing_integration(tmp_path):
    product, control, head, _start, _base2, _checkpoint = history_fixture(
        tmp_path, accepted=True,
    )
    manifest = build_fixture(product, control, head).manifest()
    last = manifest["executions"][1]
    assert last["review_decision"] == "accepted"
    assert "review_gap" not in last
    assert last["integration"]["state"] == "unknown"


def test_product_checkpoint_attribution_uses_exact_owner_observation(tmp_path):
    product, control, head, start, _base2, checkpoint = history_fixture(tmp_path)
    store = load_ticket_store(control, PATHS)

    def observed(product_head):
        return build_abandonment_evidence(
            control=owner_source(control), product=owner_source(product),
            control_head=head, request=store.by_id["Z-REQUEST"],
            target=store.by_id["A-TARGET"],
            request_path="kanban/todo/Z-REQUEST.md",
            target_path="kanban/todo/A-TARGET.md", product_head=product_head,
        ).manifest()

    integrated = observed(checkpoint)["executions"]
    assert all(
        entry["integration"]["state"] == "checkpoint-in-product" for entry in integrated
    )
    assert all(
        entry["integration"]["product_head"] == checkpoint for entry in integrated
    )
    not_integrated = observed(start)["executions"]
    assert all(
        entry["integration"]["state"] == "checkpoint-not-in-product"
        for entry in not_integrated
    )
    assert "cherry-picks remain unknown" in not_integrated[0]["integration"]["reason"]
    with pytest.raises(EvidenceError, match="exact available commit"):
        observed("HEAD")


def test_mismatched_accepted_review_header_is_not_a_proven_disposition(tmp_path):
    product, control, head, _start, _base2, _checkpoint = history_fixture(
        tmp_path, accepted=True,
    )
    review = control / "reviews/A-TARGET" / ("0" * 32 + ".md")
    # A changed accepted review must remain a gap, not a rejection or acceptance
    # manufactured from its latest contents.
    review.write_text(review.read_text().replace("disposition: accept",
                                                "disposition: rework"))
    head = commit(control, "Change disposable accepted review")
    manifest = build_fixture(product, control, head).manifest()
    assert "review_gap" in manifest["executions"][1]
    assert "review_decision" not in manifest["executions"][1]


def test_unavailable_diff_preserves_attributed_file_index(tmp_path, monkeypatch):
    product, control, head, _start, _base2, _checkpoint = history_fixture(tmp_path)
    source = owner_source(product)
    start = source._start

    def bounded_start(artifact):
        if artifact.kind == "diff":
            raise EvidenceError("diff producer cannot complete within owner budgets")
        return start(artifact)

    monkeypatch.setattr(source, "_start", bounded_start)
    store = load_ticket_store(control, PATHS)
    bundle = build_abandonment_evidence(
        control=owner_source(control), product=source, control_head=head,
        request=store.by_id["Z-REQUEST"], target=store.by_id["A-TARGET"],
        request_path="kanban/todo/Z-REQUEST.md", target_path="kanban/todo/A-TARGET.md",
    )
    manifest = bundle.manifest()
    for entry in manifest["executions"]:
        assert entry["delta"]["files"]
        assert "owner budgets" in entry["delta"]["diff"]["gap"]
        assert entry["delta"]["diff"]["source"]["objects"] == [
            entry["code_base_head"], entry["checkpoint"],
        ]


def test_changed_review_and_missing_latest_checkpoint_are_explicit_gaps(tmp_path):
    product, control, _head, _start, _base2, _checkpoint2 = history_fixture(tmp_path)
    review = control / "reviews" / "A-TARGET" / ("0" * 32 + ".md")
    review.write_text(review.read_text() + "Altered after publication.\n")
    head = commit(control, "Mutate review in disposable history")
    manifest = build_fixture(product, control, head).manifest()
    assert "changed" in manifest["executions"][1]["review_gap"]
    report_path = control / "executions" / "A-TARGET" / ("0" * 32 + ".json")
    payload = json.loads(report_path.read_text())
    payload["workspace_head"] = "e" * 40
    report_path.write_text(json.dumps(payload))
    head = commit(control, "Mutate latest report in disposable history")
    manifest = build_fixture(product, control, head).manifest()
    assert "changed" in manifest["executions"][1]["gap"]
    assert "gap" in manifest["latest_state_comparison"]


def test_evidence_rejects_silent_request_retargeting(tmp_path):
    product, control, head, _start, _base2, _checkpoint2 = history_fixture(tmp_path)
    path = control / "kanban/todo/Z-REQUEST.md"
    path.write_text(path.read_text().replace("The experiment", "A different reason"))
    with pytest.raises(EvidenceError, match="exact admitted control blob"):
        build_fixture(product, control, head)


def test_absent_latest_commit_never_falls_back_to_older_checkpoint(tmp_path):
    product, control, head, _start, _base2, checkpoint2 = history_fixture(tmp_path)
    (product / ".git/objects" / checkpoint2[:2] / checkpoint2[2:]).unlink()
    manifest = build_fixture(product, control, head).manifest()
    assert "delta" in manifest["executions"][0]
    assert "gap" in manifest["executions"][1]
    assert "gap" in manifest["latest_state_comparison"]
