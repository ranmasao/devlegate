# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Real Git histories for the ticket amendment observer."""

import subprocess

import pytest

from devlegate.amendment_history import observe_ticket_history
from devlegate.tickets import WORKFLOW_STATES


def git(repo, *args):
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, text=True, capture_output=True,
    ).stdout.strip()


@pytest.fixture
def history(tmp_path):
    git(tmp_path, "init", "-q")
    git(tmp_path, "config", "user.email", "test@example.com")
    git(tmp_path, "config", "user.name", "Test")
    paths = {state: f"kanban/{state}" for state in WORKFLOW_STATES}
    for path in paths.values():
        (tmp_path / path).mkdir(parents=True)
    ticket = tmp_path / paths["backlog"] / "T-1.md"
    ticket.write_text(
        '---\n"type": "devlegate.ticket"\n"title": "Test"\n---\nOriginal\n'
    )
    git(tmp_path, "add", ".")
    git(tmp_path, "commit", "-qm", "initial")
    return tmp_path, paths, ticket, git(tmp_path, "rev-parse", "HEAD")


def commit(repo, message):
    git(repo, "add", "-A")
    git(repo, "commit", "-qm", message)
    return git(repo, "rev-parse", "HEAD")


def test_observes_backlog_and_todo_changes_and_exact_move(history):
    repo, paths, ticket, start = history
    ticket.write_text(ticket.read_text() + "Backlog change\n")
    first = commit(repo, "amend backlog")
    todo = repo / paths["todo"] / ticket.name
    ticket.rename(todo)
    commit(repo, "schedule")
    todo.write_text(todo.read_text() + "Todo change\n")
    last = commit(repo, "amend todo")
    changes = observe_ticket_history(repo, start, last, "T-1", paths)
    assert [(item.commit, item.state) for item in changes] == [
        (first, "backlog"), (last, "todo"),
    ]
    assert all(item.old_blob != item.new_blob for item in changes)


@pytest.mark.parametrize("operation", ["delete-recreate", "move-edit", "protected-edit"])
def test_rejects_ambiguous_or_protected_history(history, operation):
    repo, paths, ticket, start = history
    original = ticket.read_text()
    if operation == "delete-recreate":
        ticket.unlink()
        commit(repo, "delete")
        ticket.write_text(original)
    elif operation == "move-edit":
        destination = repo / paths["todo"] / ticket.name
        ticket.rename(destination)
        destination.write_text(original + "Changed\n")
    else:
        todo = repo / paths["todo"] / ticket.name
        ticket.rename(todo)
        commit(repo, "schedule")
        review = repo / paths["review"] / ticket.name
        todo.rename(review)
        commit(repo, "review")
        review.write_text(original + "Changed\n")
    end = commit(repo, operation)
    with pytest.raises(ValueError):
        observe_ticket_history(repo, start, end, "T-1", paths)
