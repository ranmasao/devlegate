# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
from pathlib import Path

import pytest

from devlegate.tickets import TicketError, load_ticket_store

PATHS = {
    state: f"kanban/{state}"
    for state in ("backlog", "todo", "review", "accepted", "done")
}


def write_ticket(
    repo: Path, state: str, ticket_id: str, body: str = "Implement it.", **metadata
):
    directory = repo / PATHS[state]
    directory.mkdir(parents=True, exist_ok=True)
    title = metadata.pop("title", ticket_id)
    lines = ["---", '"type": "devlegate.ticket"', f'"title": "{title}"']
    if "depends_on" in metadata:
        lines.append('"depends_on":')
        lines.extend(f'  - "{dependency}"' for dependency in metadata.pop("depends_on"))
    lines += ["---", body]
    (directory / f"{ticket_id}.md").write_text("\n".join(lines) + "\n")


def test_valid_ticket_and_independent_ticket_parse(tmp_path):
    write_ticket(tmp_path, "todo", "ED-17", depends_on=["ED-10"])
    write_ticket(tmp_path, "done", "ED-10")

    store = load_ticket_store(tmp_path, PATHS)

    ticket = store.by_id["ED-17"]
    assert ticket.title == "ED-17"
    assert ticket.depends_on == ("ED-10",)
    assert ticket.body == "Implement it.\n"
    assert store.selected().id == "ED-17"


def test_flow_dependency_sequence_parses_and_selects(tmp_path):
    done = tmp_path / "kanban/done"
    todo = tmp_path / "kanban/todo"
    done.mkdir(parents=True)
    todo.mkdir(parents=True)
    (done / "ED-10.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "ED-10"\n---\n'
        "Implement it.\n"
    )
    (todo / "ED-17.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "ED-17"\n'
        '"depends_on": ["ED-10"]\n---\nImplement it.\n'
    )

    store = load_ticket_store(tmp_path, PATHS)

    assert store.by_id["ED-17"].depends_on == ("ED-10",)
    assert store.selected().id == "ED-17"


def test_flow_empty_dependency_sequence_is_rejected_by_ticket_schema(tmp_path):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    (directory / "ED-17.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "ED-17"\n'
        '"depends_on": []\n---\nImplement it.\n'
    )

    with pytest.raises(
        TicketError,
        match=r'metadata "depends_on" must be a non-empty sequence',
    ):
        load_ticket_store(tmp_path, PATHS)


def test_missing_or_invalid_frontmatter_fails(tmp_path):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    (directory / "ED-17.md").write_text("body\n")

    with pytest.raises(TicketError, match="opening frontmatter"):
        load_ticket_store(tmp_path, PATHS)


@pytest.mark.parametrize(
    "content, message",
    [
        ('---\n"type": "devlegate.ticket"\n', "closing frontmatter"),
        ('---\n"type": plain\n---\nbody\n', "NanoYAML"),
    ],
)
def test_frontmatter_delimiters_and_nanoyaml_are_strict(tmp_path, content, message):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    (directory / "ED-17.md").write_text(content)

    with pytest.raises(TicketError, match=message):
        load_ticket_store(tmp_path, PATHS)


def test_noncanonical_filename_is_not_ticket(tmp_path):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    (directory / "bad id.md").write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Bad"\n---\nbody\n'
    )

    assert load_ticket_store(tmp_path, PATHS).tickets == ()


def test_editor_artifacts_are_ignored_beside_canonical_ticket(tmp_path):
    write_ticket(tmp_path, "review", "CORPUS-018")
    directory = tmp_path / "kanban/review"
    for name in (
        ".CORPUS-018.md.swp",
        "CORPUS-018.md~",
        ".#CORPUS-018.md",
        "notes.txt",
        "arbitrary.tmp",
    ):
        (directory / name).write_text("temporary\n")

    store = load_ticket_store(tmp_path, PATHS)

    assert [ticket.id for ticket in store.tickets] == ["CORPUS-018"]


def test_canonical_directory_entry_fails_closed(tmp_path):
    directory = tmp_path / "kanban/todo/ED-17.md"
    directory.mkdir(parents=True)

    with pytest.raises(TicketError, match="invalid managed ticket entry"):
        load_ticket_store(tmp_path, PATHS)


@pytest.mark.parametrize(
    "metadata, message",
    [
        ('"title": "A"', "missing required metadata key"),
        ('"type": "devlegate.ticket"', "missing required metadata key"),
        (
            '"type": "devlegate.ticket"\n"title": "line1\nline2"',
            "NanoYAML",
        ),
        (
            '"type": "devlegate.ticket"\n"title": "A"\n"depends_on": "ED-1"',
            'metadata "depends_on"',
        ),
    ],
)
def test_required_and_shaped_metadata_is_strict(tmp_path, metadata, message):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    (directory / "ED-17.md").write_text(f"---\n{metadata}\n---\nbody\n")

    with pytest.raises(TicketError, match=message):
        load_ticket_store(tmp_path, PATHS)


@pytest.mark.parametrize(
    "metadata",
    [
        {"unknown": "x"},
        {"type": "wrong"},
        {"title": ""},
        {"depends_on": ["ED-17", "ED-17"]},
        {"depends_on": ["bad id"]},
    ],
)
def test_strict_metadata_fails(tmp_path, metadata):
    directory = tmp_path / "kanban/todo"
    directory.mkdir(parents=True)
    lines = ["---", '"type": "devlegate.ticket"', '"title": "A"']
    for key, value in metadata.items():
        if key == "depends_on":
            lines.append('"depends_on":')
            lines.extend(f'  - "{item}"' for item in value)
        elif isinstance(value, str):
            lines.append(f'"{key}": "{value}"')
    lines += ["---", "body"]
    (directory / "ED-17.md").write_text("\n".join(lines) + "\n")

    with pytest.raises(TicketError):
        load_ticket_store(tmp_path, PATHS)


def test_filename_duplicate_missing_dependency_and_cycle_fail(tmp_path):
    write_ticket(tmp_path, "todo", "ED-17", depends_on=["ED-99"])
    with pytest.raises(TicketError, match="missing dependency"):
        load_ticket_store(tmp_path, PATHS)

    (tmp_path / "kanban/todo/ED-17.md").unlink()
    write_ticket(tmp_path, "todo", "ED-17", depends_on=["ED-18"])
    write_ticket(tmp_path, "todo", "ED-18", depends_on=["ED-17"])
    with pytest.raises(TicketError, match="dependency cycle"):
        load_ticket_store(tmp_path, PATHS)


def test_review_is_not_done_and_done_dependency_is_satisfied(tmp_path):
    write_ticket(tmp_path, "review", "ED-10")
    write_ticket(tmp_path, "todo", "ED-11", depends_on=["ED-10"])
    assert load_ticket_store(tmp_path, PATHS).selected() is None

    (tmp_path / "kanban/done").mkdir(parents=True)
    (tmp_path / "kanban/review/ED-10.md").rename(tmp_path / "kanban/done/ED-10.md")
    assert load_ticket_store(tmp_path, PATHS).selected().id == "ED-11"


def test_backlog_is_never_runnable_and_selection_is_id_sorted(tmp_path):
    write_ticket(tmp_path, "backlog", "ED-01")
    write_ticket(tmp_path, "todo", "ED-20")
    write_ticket(tmp_path, "todo", "ED-02")

    store = load_ticket_store(tmp_path, PATHS)

    assert [ticket.id for ticket in store.runnable] == ["ED-02", "ED-20"]
    assert store.selected().id == "ED-02"


def test_duplicate_identity_across_states_fails(tmp_path):
    write_ticket(tmp_path, "todo", "ED-17")
    write_ticket(tmp_path, "done", "ED-17")

    with pytest.raises(TicketError, match="duplicate ticket ID ED-17"):
        load_ticket_store(tmp_path, PATHS)
