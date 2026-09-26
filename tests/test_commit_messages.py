# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2

from devlegate.commit_messages import checkpoint_message, integration_message


def test_checkpoint_summary_wraps_on_words_and_keeps_execution_out_of_subject():
    message = checkpoint_message(
        "TASK-001",
        "Use real Debian maintainer metadata",
        "execution-123",
        "Updated package metadata to use the real Debian maintainer identity and "
        "added validation for generated control files without copying raw worker "
        "output into the commit message.",
    )

    subject, body = message.split("\n\n", 1)
    summary = body.split("Worker result:\n", 1)[1].split("\n\n", 1)[0]
    assert subject == "TASK-001: Use real Debian maintainer metadata"
    assert 2 <= len(summary.splitlines()) <= 3
    assert "Debian" in summary
    assert "execution-123" not in subject
    assert "Devlegate-Execution: execution-123" in message


def test_integration_message_carries_report_provenance():
    message = integration_message(
        "TASK-001",
        "Use real Debian maintainer metadata",
        "execution-123",
        "Updated package metadata and validated generated control files.",
        "a" * 40,
    )

    assert message.startswith(
        "TASK-001: Use real Debian maintainer metadata (integrated)"
    )
    assert "Devlegate-Execution: execution-123" in message
    assert f"Devlegate-Checkpoint: {'a' * 40}" in message
