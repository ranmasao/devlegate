# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Bounded, read-only validation of the existing private prompt journal."""

import hashlib
import json
import os
import stat
from contextlib import ExitStack
from pathlib import Path

from devlegate.ipc_protocol import MAX_PAYLOAD_BYTES
from devlegate.worker_prompt import WorkDirective, worker_prompt_artifact_path


class PromptJournalError(ValueError):
    """A safe diagnostic, without artifact-supplied text."""


def _unique_fields(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PromptJournalError("Prompt journal contains duplicate fields.")
        result[key] = value
    return result


def read_prompt_journal(state_dir: Path, execution_id: str) -> tuple[dict, bytes]:
    path = worker_prompt_artifact_path(state_dir, execution_id)
    # Open every directory relative to a pinned descriptor: checking symlinks
    # before an ordinary open would leave a substitution window.
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    with ExitStack() as stack:
        directory = os.open(path.anchor, directory_flags)
        stack.callback(os.close, directory)
        for part in path.parts[1:-1]:
            directory = os.open(part, directory_flags, dir_fd=directory)
            stack.callback(os.close, directory)

        def check(info):
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                raise PromptJournalError("Prompt journal is non-regular or linked.")
            if info.st_size > MAX_PAYLOAD_BYTES:
                raise PromptJournalError(
                    "Journal exceeds the local IPC limit; exact inspection refused."
                )

        check(os.stat(path.name, dir_fd=directory, follow_symlinks=False))
        fd = os.open(path.name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK,
                     dir_fd=directory)
        with os.fdopen(fd, "rb") as stream:
            before = os.fstat(stream.fileno())
            check(before)
            raw = stream.read(MAX_PAYLOAD_BYTES + 1)
            after = os.fstat(stream.fileno())
            check(after)
        if len(raw) != after.st_size or before.st_ctime_ns != after.st_ctime_ns:
            raise PromptJournalError("Prompt journal changed; retry inspection.")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_unique_fields)
        if not isinstance(value, dict) or not isinstance(value.get("prompt"), str):
            raise PromptJournalError("Prompt journal has no valid prompt text.")
        prompt = value["prompt"].encode("utf-8")
    except PromptJournalError:
        raise
    except (ValueError, RecursionError) as error:
        raise PromptJournalError("Malformed or non-UTF-8 prompt journal.") from error
    if not prompt or hashlib.sha256(prompt).hexdigest() != value.get("prompt_sha256"):
        raise PromptJournalError("Saved prompt SHA-256 does not match retained text.")
    if value.get("delivery") not in ("constructed", "sent") or value.get(
        "directive",
    ) not in tuple(directive.value for directive in WorkDirective):
        raise PromptJournalError("Invalid prompt directive or delivery state.")
    return value, prompt


_COMPONENTS = (
    ("protocol", "Core Worker Contract", "core_contract.txt", True),
    ("engineering", "Engineering Principles", "engineering.txt", True),
    ("project", "Project Worker Instructions", None, False),
    ("change_explorer", "Change Explorer", "change_explorer.txt", False),
    ("blackboard", "Blackboard", "blackboard.txt", False),
    ("directive", "Work Directive", None, True),
    ("assignment", "Assigned Work", "admitted ticket", True),
    ("review", "Current Review Feedback", None, False),
)


def validate_component_manifest(value: dict, prompt: bytes) -> dict[str, bytes] | None:
    """Validate the ordered UTF-8 spans, including the producer's separators."""
    if "components" not in value:
        return None
    components = value["components"]
    invalid = PromptJournalError("Malformed or mismatched prompt component manifest.")
    if not isinstance(components, list) or len(components) != len(_COMPONENTS):
        raise invalid
    sections = {}
    end = 0
    for component, (kind, title, source, required) in zip(
        components, _COMPONENTS, strict=True,
    ):
        if not isinstance(component, dict) or component.get("kind") != kind:
            raise invalid
        included = component.get("included")
        saved_source = component.get("source")
        keys = {"kind", "included", "source"}
        if type(included) is not bool or (required and not included):
            raise invalid
        if included:
            keys |= {"offset", "bytes", "sha256"}
        if set(component) != keys:
            raise invalid
        if source is not None and saved_source != source:
            raise invalid
        if kind == "directive" and (
            not isinstance(saved_source, dict)
            or set(saved_source) != {"template", "resumed_after_stall"}
            or saved_source["template"] != f"{value['directive']}.txt"
            or type(saved_source["resumed_after_stall"]) is not bool
            or (saved_source["resumed_after_stall"] and value["directive"] != "resume")
        ):
            raise invalid
        if kind == "review":
            if included and saved_source == "verified historical review":
                title = "Historical Review Feedback"
            elif saved_source != ("verified review" if included else None):
                raise invalid
        if not included:
            continue
        start = end + 2 if sections else 0
        offset, size = component["offset"], component["bytes"]
        if (type(offset) is not int or offset != start
                or type(size) is not int or not 0 < size <= len(prompt) - start
                or (sections and prompt[end:start] != b"\n\n")):
            raise invalid
        end = start + size
        section = prompt[start:end]
        if (not section.startswith(f"## {title}\n".encode())
                or hashlib.sha256(section).hexdigest() != component["sha256"]):
            raise invalid
        # Reject boundaries cutting a UTF-8 character.
        try:
            section.decode("utf-8")
        except UnicodeError as error:
            raise invalid from error
        sections[kind] = section
    if prompt[end:] != b"\n":
        raise invalid
    return sections
