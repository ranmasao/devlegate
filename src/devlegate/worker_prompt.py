# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Pure construction of the implementation worker prompt."""

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .project_context import WorkerProjectContext


class WorkDirective(Enum):
    FRESH = "fresh"
    RESUME = "resume"
    REWORK = "rework"


@dataclass(frozen=True)
class WorkerPromptInput:
    assignment: str
    directive: WorkDirective
    review_feedback: str | None = None
    review_source: str | None = None
    historical_review: bool = False
    blackboard_available: bool = False
    resumed_after_stall: bool = False
    change_explorer_available: bool = False
    project: WorkerProjectContext | None = None


@dataclass(frozen=True)
class RenderedWorkerPrompt:
    text: str
    components: tuple[dict, ...]


_PROMPT_ROOT = Path(__file__).parent / "default_templates/prompts"


def _prompt_text(name: str) -> str:
    path = _PROMPT_ROOT / name
    if not path.is_file():
        raise RuntimeError(f"packaged worker prompt is missing: {path}")
    return path.read_text(encoding="utf-8").rstrip("\r\n")


_CORE_CONTRACT = _prompt_text("core_contract.txt")
_ENGINEERING = _prompt_text("engineering.txt")
_CHANGE_EXPLORER = _prompt_text("change_explorer.txt")
_BLACKBOARD_GUIDANCE = _prompt_text("blackboard.txt")
_DIRECTIVES = {
    WorkDirective.FRESH: _prompt_text("fresh.txt"),
    WorkDirective.RESUME: _prompt_text("resume.txt"),
    WorkDirective.REWORK: _prompt_text("rework.txt"),
}


def _content(value: str) -> str:
    if not isinstance(value, str):
        raise TypeError("prompt content must be text")
    return value.strip("\r\n")


def _fenced_content(value: str) -> str:
    """Keep opaque content from being interpreted as prompt structure."""
    longest = max((len(run) for run in re.findall(r"~+", value)), default=0)
    fence = "~" * max(3, longest + 1)
    return f"{fence}text\n{value}\n{fence}"


def build_worker_prompt(prompt_input: WorkerPromptInput) -> str:
    return render_worker_prompt(prompt_input).text


def render_worker_prompt(prompt_input: WorkerPromptInput) -> RenderedWorkerPrompt:
    """Build one deterministic prompt without observing workflow or Git state."""
    if not isinstance(prompt_input, WorkerPromptInput):
        raise TypeError("prompt input must be WorkerPromptInput")
    if not isinstance(prompt_input.directive, WorkDirective):
        raise ValueError("unknown work directive")
    assignment = _content(prompt_input.assignment)
    if not assignment:
        raise ValueError("assigned work must not be empty")

    directive_text = _DIRECTIVES[prompt_input.directive]
    if (
        prompt_input.directive is WorkDirective.RESUME
        and prompt_input.resumed_after_stall
    ):
        directive_text += (
            "\n\nThe previous attempt was interrupted after extended output silence. "
            "Inspect current changes and do not assume the previous attempt completed."
        )
    sections: list[str] = []
    components: list[dict] = []
    offset = 0

    def add(kind: str, title: str, body: str | None, source: object) -> None:
        nonlocal offset
        component = {"kind": kind, "included": body is not None, "source": source}
        if body is not None:
            if sections:
                offset += 2  # section separator
            section = f"## {title}\n{body}"
            size = len(section.encode("utf-8"))
            component.update(
                offset=offset, bytes=size, sha256=worker_prompt_sha256(section),
            )
            offset += size
            sections.append(section)
        components.append(component)

    add("protocol", "Core Worker Contract", _CORE_CONTRACT, "core_contract.txt")
    add("engineering", "Engineering Principles", _ENGINEERING, "engineering.txt")
    project = prompt_input.project
    documents = project.documents if project else ()
    add("project", "Project Worker Instructions", "\n\n".join(
        _fenced_content(doc.text) for doc in documents
    ) if documents else None, {
        "commit": project.commit,
        "manifest_path": ".devlegate/project.md",
        "manifest_blob": project.manifest_blob,
        "documents": [
            {
                "path": doc.path, "blob": doc.blob,
                "sha256": worker_prompt_sha256(doc.text),
            }
            for doc in documents
        ],
    } if project else None)
    add("change_explorer", "Change Explorer", _CHANGE_EXPLORER
        if prompt_input.change_explorer_available else None, "change_explorer.txt")
    add("blackboard", "Blackboard", _BLACKBOARD_GUIDANCE
        if prompt_input.blackboard_available else None, "blackboard.txt")
    add("directive", "Work Directive", directive_text, {
        "template": f"{prompt_input.directive.value}.txt",
        "resumed_after_stall": directive_text != _DIRECTIVES[prompt_input.directive],
    })
    add("assignment", "Assigned Work", _fenced_content(assignment), "admitted ticket")
    if prompt_input.review_feedback is not None:
        feedback = prompt_input.review_feedback
        if prompt_input.directive is WorkDirective.FRESH:
            raise ValueError("fresh work cannot include review feedback")
        if not _content(feedback):
            raise ValueError("review feedback must not be empty")
        source = _content(prompt_input.review_source or "verified curator review")
        title = "Historical Review Feedback" if prompt_input.historical_review else (
            "Current Review Feedback"
        )
        context = (
            "This feedback reviews the prior specification and checkpoint only; "
            "it is not a review of the current assigned work.\n\n"
            if prompt_input.historical_review else ""
        )
        add(
            "review", title,
            _fenced_content(f"Source: {source}\n\n{context}{feedback}"),
            "verified historical review" if prompt_input.historical_review
            else "verified review",
        )
    elif prompt_input.directive is WorkDirective.REWORK:
        raise ValueError("rework requires verified review feedback")
    else:
        add("review", "", None, None)
    return RenderedWorkerPrompt("\n\n".join(sections) + "\n", tuple(components))


def worker_prompt_sha256(prompt: str) -> str:
    """Return the digest of the exact UTF-8 bytes sent to the worker."""
    if not isinstance(prompt, str):
        raise TypeError("worker prompt must be text")
    return hashlib.sha256(prompt.encode("utf-8")).hexdigest()


def write_worker_prompt_artifact(
    state_dir: Path,
    execution_id: str,
    prompt: RenderedWorkerPrompt,
    *,
    ticket_id: str,
    directive: WorkDirective,
    review_path: str | None,
    review_sha256: str | None,
    review_publication: str | None = None,
    reviewed_execution_id: str | None = None,
    reviewed_checkpoint: str | None = None,
    admitted_control_head: str | None = None,
) -> Path:
    """Atomically retain a private, exact prompt diagnostic for operators."""
    root = state_dir / "prompts"
    if root.is_symlink():
        raise ValueError(f"worker prompt diagnostic directory is symlinked: {root}")
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not root.is_dir():
        raise ValueError(f"worker prompt diagnostic path is not a directory: {root}")
    os.chmod(root, 0o700)
    path = root / f"{execution_id}.json"
    payload = {
        "admitted_control_head": admitted_control_head,
        "directive": directive.value,
        "execution_id": execution_id,
        "prompt": prompt.text,
        "prompt_sha256": worker_prompt_sha256(prompt.text),
        "components": prompt.components,
        "delivery": "constructed",
        "review_path": review_path,
        "review_publication": review_publication,
        "review_sha256": review_sha256,
        "reviewed_checkpoint": reviewed_checkpoint,
        "reviewed_execution_id": reviewed_execution_id,
        "ticket_id": ticket_id,
    }
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=root,
            prefix=f".{execution_id}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            json.dump(payload, temporary, sort_keys=True, ensure_ascii=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
        directory = os.open(root, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
    return path


def mark_worker_prompt_sent(path: Path) -> None:
    """Durably mark successful stdin delivery without logging prompt content."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("delivery") not in {"constructed", "sent"}:
        raise ValueError("worker prompt diagnostic has invalid delivery state")
    payload["delivery"] = "sent"
    temporary_name = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary:
            temporary_name = temporary.name
            os.chmod(temporary_name, 0o600)
            json.dump(payload, temporary, sort_keys=True, ensure_ascii=True)
            temporary.write("\n")
            temporary.flush()
            os.fsync(temporary.fileno())
        os.replace(temporary_name, path)
        os.chmod(path, 0o600)
        directory = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    finally:
        if temporary_name is not None:
            try:
                os.unlink(temporary_name)
            except FileNotFoundError:
                pass
