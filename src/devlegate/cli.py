"""Command-line interface and runtime for Devlegate."""

import argparse
import dataclasses
import fcntl
import hashlib
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
import termios
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from devlegate import __version__
from devlegate.tickets import (
    Ticket,
    TicketError,
    TicketStore,
    is_canonical_ticket_name,
    load_ticket_store,
)


class DevlegateError(Exception):
    """A user-facing startup error."""


class WorkflowBlockedError(DevlegateError):
    """The repository cannot currently be interpreted safely for scheduling."""


class SnapshotChanged(Exception):
    """The observed project changed during a status snapshot attempt."""


@dataclasses.dataclass(frozen=True)
class ExecutionPlan:
    """The immutable scheduling decision for one observed project state."""

    action: str
    reason: str
    ticket_id: str | None = None
    ticket_title: str | None = None
    ticket_state: str | None = None
    bound: bool = False

    def as_dict(self) -> dict[str, object]:
        return {
            "action": self.action,
            "reason": self.reason,
            "ticket": (
                {
                    "id": self.ticket_id,
                    "title": self.ticket_title,
                    "state": self.ticket_state,
                }
                if self.ticket_id is not None
                else None
            ),
            "bound": self.bound,
        }


@dataclasses.dataclass(frozen=True)
class StatusSnapshot:
    phase: str
    bound_ticket_id: str | None
    persisted_body_present: bool
    branch: str
    local_head: str
    remote_ref: str
    remote_head: str | None
    working_tree_clean: bool
    counts: tuple[tuple[str, int], ...]
    runnable: tuple[tuple[str, str], ...]
    blocked: tuple[tuple[str, str, tuple[tuple[str, str], ...]], ...]
    review: tuple[tuple[str, str], ...]
    next_ticket: tuple[str, str] | None
    plan: ExecutionPlan

    def as_dict(self) -> dict[str, object]:
        return {
            "execution": {
                "phase": self.phase,
                "bound_ticket": self.bound_ticket_id,
                "persisted_body": self.persisted_body_present,
            },
            "repository": {
                "branch": self.branch,
                "local_head": self.local_head,
                "remote_ref": self.remote_ref,
                "remote_head": self.remote_head,
                "working_tree_clean": self.working_tree_clean,
            },
            "tickets": {
                "counts": dict(self.counts),
                "runnable": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.runnable
                ],
                "blocked": [
                    {
                        "id": ticket_id,
                        "title": title,
                        "blocked_by": [
                            {"id": dependency_id, "state": state}
                            for dependency_id, state in blockers
                        ],
                    }
                    for ticket_id, title, blockers in self.blocked
                ],
                "review": [
                    {"id": ticket_id, "title": title}
                    for ticket_id, title in self.review
                ],
                "next": (
                    {"id": self.next_ticket[0], "title": self.next_ticket[1]}
                    if self.next_ticket is not None
                    else None
                ),
            },
            "plan": self.plan.as_dict(),
        }


def _workflow_fingerprint(repo: Path, workflow_paths: dict[str, str]) -> str:
    entries: list[str] = []
    for state in ("backlog", "todo", "review", "done"):
        directory = repo / workflow_paths[state]
        if not directory.is_dir():
            continue
        for path in sorted(directory.iterdir(), key=lambda item: item.name):
            if not is_canonical_ticket_name(path.name):
                continue
            if path.is_symlink():
                kind = "symlink"
                digest = ""
            elif path.is_file():
                kind = "file"
                digest = hashlib.sha256(path.read_bytes()).hexdigest()
            else:
                kind = "other"
                digest = ""
            entries.append(f"{state}/{path.name}\0{kind}\0{digest}\n")
    return hashlib.sha256("".join(entries).encode()).hexdigest()

def _read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    try:
        lines = path.read_text().splitlines()
    except OSError as error:
        raise DevlegateError(
            f"cannot read configuration file: {path}: {error}"
        ) from error
    for line in lines:
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[7:].lstrip()
        name, separator, value = line.partition("=")
        if separator and name.strip().replace("_", "a").isalnum():
            value = value.strip()
            if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
                value = value[1:-1]
            values[name.strip()] = value
    return values


def _git(
    repo: Path, *args: str, check: bool = True
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        text=True,
        capture_output=True,
        check=check,
    )


def _log(message: str) -> None:
    print(f"[{time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}] {message}")


@contextmanager
def _preserve_terminal():
    terminal_fd = None
    terminal_state = None
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        try:
            fd = stream.fileno()
            if not os.isatty(fd):
                continue
            terminal_state = termios.tcgetattr(fd)
            terminal_fd = fd
            break
        except (OSError, ValueError):
            continue
    try:
        yield
    finally:
        if terminal_fd is not None and terminal_state is not None:
            try:
                termios.tcsetattr(terminal_fd, termios.TCSANOW, terminal_state)
            except OSError as error:
                _log(f"could not restore terminal state: {error}")


def _render_worker_text(text: str) -> str:
    """Make worker-controlled text inert before it reaches an operator TTY."""
    rendered: list[str] = []
    for character in text:
        codepoint = ord(character)
        if character in "\n\t":
            rendered.append(character)
        elif 0x20 <= codepoint <= 0x7E or character.isprintable():
            rendered.append(character)
        elif codepoint <= 0x7F:
            rendered.append(f"\\x{codepoint:02x}")
        elif codepoint <= 0x9F:
            rendered.append(f"\\u{codepoint:04x}")
        else:
            rendered.append(f"\\u{codepoint:04x}")
    return "".join(rendered)


_OPENCODE_JSON_TYPES = {
    "error",
    "reasoning",
    "step_finish",
    "step_start",
    "text",
    "tool_use",
}


def _write_worker_text(text: str, stream) -> None:
    rendered = _render_worker_text(text)
    if not rendered:
        return
    stream.write(rendered)
    stream.flush()


def _extract_error_message(error: object) -> str | None:
    if isinstance(error, str):
        return error
    if not isinstance(error, dict):
        return None
    message = error.get("message")
    if isinstance(message, str):
        return message
    data = error.get("data")
    if isinstance(data, dict):
        message = data.get("message")
        if isinstance(message, str):
            return message
    name = error.get("name")
    return name if isinstance(name, str) else None


def _write_worker_line(text: str, stream) -> None:
    _write_worker_text(text + ("" if text.endswith("\n") else "\n"), stream)


def _run_opencode(command: list[str], prompt: str) -> int:
    """Run OpenCode headlessly and render its worker output as inert text."""
    process = subprocess.Popen(
        command + [prompt],
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    output_lock = threading.Lock()
    protocol_failed = False

    def write(text: str, stream) -> None:
        with output_lock:
            _write_worker_text(text, stream)

    def consume_stdout() -> None:
        nonlocal protocol_failed
        assert process.stdout is not None
        for raw_line in process.stdout:
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                protocol_failed = True
                _log("OpenCode protocol error: invalid JSON event on stdout")
                continue
            if (
                not isinstance(event, dict)
                or event.get("type") not in _OPENCODE_JSON_TYPES
            ):
                protocol_failed = True
                _log("OpenCode protocol error: unsupported event on stdout")
                continue
            event_type = event["type"]
            if event_type == "text" or event_type == "reasoning":
                part = event.get("part")
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    protocol_failed = True
                    _log("OpenCode protocol error: text event has no text part")
                    continue
                write(part["text"] + "\n", sys.stdout)
            elif event_type == "tool_use":
                part = event.get("part")
                if (
                    not isinstance(part, dict)
                    or not isinstance(part.get("tool"), str)
                    or not isinstance(part.get("state"), dict)
                    or part["state"].get("status")
                    not in {"pending", "running", "completed", "error"}
                ):
                    protocol_failed = True
                    _log("OpenCode protocol error: invalid tool event on stdout")
                    continue
                tool = part["tool"]
                write(f"OpenCode tool: {tool}\n", sys.stdout)
                state = part["state"]
                if state.get("status") == "completed":
                    output = state.get("output")
                    if isinstance(output, str):
                        _write_worker_line(output, sys.stdout)
                elif state.get("status") == "error":
                    error = _extract_error_message(state.get("error"))
                    if error:
                        _write_worker_line(f"OpenCode tool failed: {error}", sys.stdout)
            elif event_type == "error":
                error = _extract_error_message(event.get("error"))
                if error:
                    write(f"OpenCode error: {error}\n", sys.stdout)

    def consume_stderr() -> None:
        assert process.stderr is not None
        while raw_chunk := process.stderr.read(4096):
            write(raw_chunk.decode("utf-8", errors="replace"), sys.stderr)

    stdout_thread = threading.Thread(target=consume_stdout)
    stderr_thread = threading.Thread(target=consume_stderr)
    stdout_thread.start()
    stderr_thread.start()
    returncode = process.wait()
    stdout_thread.join()
    stderr_thread.join()
    if protocol_failed:
        return 1
    return returncode


def _has_todo_files(repo: Path, todo_path: str) -> bool:
    todo_dir = repo / todo_path
    return todo_dir.is_dir() and any(
        is_canonical_ticket_name(item.name)
        and not item.is_symlink()
        and item.is_file()
        for item in todo_dir.iterdir()
    )


def _todo_fingerprint(repo: Path, todo_path: str) -> tuple[str, int]:
    todo_dir = repo / todo_path
    entries: list[str] = []
    if todo_dir.is_dir():
        for item in todo_dir.iterdir():
            if (
                is_canonical_ticket_name(item.name)
                and item.is_file()
                and not item.is_symlink()
            ):
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                entries.append(f"{item.name}\0{digest}\n")
    entries.sort()
    return hashlib.sha256("".join(entries).encode()).hexdigest(), len(entries)


_CONFLICT_CODES = {"DD", "AU", "UD", "UA", "DU", "AA", "UU"}


def _status_summary(status: str) -> dict[str, int]:
    summary = {
        "tracked_modified": 0,
        "staged": 0,
        "untracked": 0,
        "deleted": 0,
        "renamed": 0,
        "conflicted": 0,
    }
    for line in status.splitlines():
        code = line[:2]
        if code == "??":
            summary["untracked"] += 1
            continue
        if code in _CONFLICT_CODES:
            summary["conflicted"] += 1
        if code[0] != " ":
            summary["staged"] += 1
        if code[1] != " ":
            summary["tracked_modified"] += 1
        if "D" in code:
            summary["deleted"] += 1
        if "R" in code:
            summary["renamed"] += 1
    return summary


def _status_fingerprint(status: str) -> str:
    return hashlib.sha256(status.encode()).hexdigest()


def _render_prompt(template: str, values: dict[str, str]) -> str:
    """Render configured prompt values while leaving unknown variables intact."""
    rendered = string.Template(template).safe_substitute(values)
    for name, value in values.items():
        rendered = rendered.replace("{{" + name + "}}", value)
    return rendered


class Devlegate:
    """Run the repository polling and agent execution workflow."""

    def __init__(self, env_file: Path, *, read_only: bool = False) -> None:
        if not env_file.is_file():
            raise DevlegateError(
                f"configuration file not found: {env_file} "
                f"(copy devlegate's .env.example to $PWD/.env)"
            )
        self.env_file = env_file
        config = _read_env(env_file)
        self.repo = self._repository_root()
        if Path.cwd().resolve() != self.repo:
            raise DevlegateError(f"run devlegate from repository root: {self.repo}")

        def setting(name: str, default: str) -> str:
            return config.get(name, os.environ.get(name, default))

        self.remote_name = setting("REMOTE_NAME", "origin")
        self.remote_branch = setting("REMOTE_BRANCH", "")
        self.backlog_path = setting("BACKLOG_PATH", "kanban/backlog")
        self.todo_path = setting("TODO_PATH", "kanban/todo")
        self.review_path = setting("REVIEW_PATH", "kanban/review")
        self.done_path = setting("DONE_PATH", "kanban/done")
        self.poll_interval = setting("POLL_INTERVAL", "300") or "300"
        prompt_root = Path(__file__).resolve().parents[2]
        default_prompt = prompt_root / "agent-prompt.txt"
        self.agent_prompt_file = Path(
            setting("AGENT_PROMPT_FILE", str(default_prompt))
        )
        default_recovery_prompt = prompt_root / "recovery-prompt.txt"
        self.recovery_prompt_file = Path(
            setting("RECOVERY_PROMPT_FILE", str(default_recovery_prompt))
        )
        self.opencode_bin = setting("OPENCODE_BIN", "opencode")
        self.opencode_model = setting("OPENCODE_MODEL", "")
        self.opencode_agent = setting("OPENCODE_AGENT", "")
        self.read_only = read_only
        self._recovery_pending = False
        state_default = Path(
            os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))
        ) / "devlegate"
        self.state_dir = Path(
            config.get("STATE_DIR", os.environ.get("STATE_DIR", state_default))
        ).expanduser()
        self._state_key = hashlib.sha256(str(self.repo).encode()).hexdigest()
        self._state_file = self.state_dir / f"{self._state_key}.json"
        self._state: dict[str, object] = {"phase": "idle"}
        self._dirty_fingerprint: str | None = None
        self._workflow_blocker_fingerprint: str | None = None
        self._workflow_validation_succeeded = False
        self._recovery_pending = False
        self._validate()
        self._state = self._load_state()
        self._recovery_pending = self._state.get("phase") in {
            "agent_running",
            "recovery_pending",
        }

    def _workflow_paths(self) -> dict[str, str]:
        return {
            "backlog": self.backlog_path,
            "todo": self.todo_path,
            "review": self.review_path,
            "done": self.done_path,
        }

    def _ticket_store(self) -> TicketStore:
        for state, configured_path in self._workflow_paths().items():
            directory = self.repo / configured_path
            if not directory.is_dir():
                raise WorkflowBlockedError(
                    f"managed workflow directory is unavailable: {directory} "
                    f"({state})"
                )
        try:
            store = load_ticket_store(self.repo, self._workflow_paths())
            self._workflow_validation_succeeded = True
            return store
        except TicketError as error:
            raise WorkflowBlockedError(str(error)) from error
        except FileNotFoundError as error:
            raise WorkflowBlockedError(
                "managed workflow changed while it was being observed"
            ) from error

    def _selected_ticket(
        self, store: TicketStore, bound_execution: bool
    ) -> Ticket | None:
        selected_id = self._state.get("selected_ticket_id")
        if bound_execution:
            if not isinstance(selected_id, str):
                raise DevlegateError(
                    "invalid execution state: selected ticket binding is incomplete"
                )
            selected = store.by_id.get(selected_id)
            if selected is None:
                raise DevlegateError(
                    "persisted selected ticket is not in managed storage: "
                    f"{selected_id}"
                )
            if selected.state not in {"todo", "review"}:
                raise DevlegateError(
                    f"selected ticket {selected.id} has unexpected recovery state "
                    f"{selected.state}"
                )
            body = self._state.get("selected_ticket_body")
            if not isinstance(body, str):
                raise DevlegateError(
                    "invalid execution state: selected ticket binding is incomplete"
                )
            selected = dataclasses.replace(selected, body=body)
            return selected
        return store.selected()

    @staticmethod
    def _repository_root() -> Path:
        result = subprocess.run(
            ["git", "-C", str(Path.cwd()), "rev-parse", "--show-toplevel"],
            text=True,
            capture_output=True,
        )
        if result.returncode:
            raise DevlegateError(
                f"current directory is not a git repository: {Path.cwd()}"
            )
        return Path(result.stdout.strip()).resolve()

    def _validate(self) -> None:
        if not self.read_only:
            if not self.agent_prompt_file.is_file():
                raise DevlegateError(
                    f"agent prompt file not found: {self.agent_prompt_file}"
                )
            if not self.recovery_prompt_file.is_file():
                raise DevlegateError(
                    f"recovery prompt file not found: {self.recovery_prompt_file}"
                )
            if not self.poll_interval.isdecimal():
                raise DevlegateError("POLL_INTERVAL must be an integer")
            if not self.opencode_model:
                raise DevlegateError("OPENCODE_MODEL is required")
            if shutil.which(self.opencode_bin) is None:
                raise DevlegateError(
                    f"OpenCode executable not found: {self.opencode_bin}"
                )
        branch = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode:
            if not self.read_only:
                raise DevlegateError("detached HEAD is not supported")
            self.current_branch = "<detached>"
        else:
            self.current_branch = branch.stdout.strip()
        self.remote_branch = self.remote_branch or self.current_branch
        if not self.read_only and self.current_branch != self.remote_branch:
            raise DevlegateError(
                f"current branch '{self.current_branch}' does not match "
                f"REMOTE_BRANCH '{self.remote_branch}'"
            )
        if not self.read_only and _git(
            self.repo, "remote", "get-url", self.remote_name, check=False
        ).returncode:
            raise DevlegateError(f"git remote not found: {self.remote_name}")
        if not self.read_only:
            self._validate_state_access()

    def _validate_state_access(self) -> None:
        try:
            self._probe_state_access()
        except OSError as error:
            raise DevlegateError(
                f"state directory is not writable: {self.state_dir}: {error}"
            ) from error

    def _probe_state_access(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(
            dir=self.state_dir,
            prefix=f".{self._state_key}.access.",
            delete=True,
        ):
            pass

    def _lock(self):
        lock_root = self.state_dir / "locks"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_file = lock_root / f"{self._state_key}.lock"
        handle = lock_file.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise DevlegateError(
                "another devlegate instance is already running for this checkout"
            ) from error
        return handle

    def _load_state(self) -> dict[str, object]:
        try:
            with self._state_file.open() as state_file:
                state = json.load(state_file)
        except FileNotFoundError:
            return {"phase": "idle"}
        except (OSError, json.JSONDecodeError) as error:
            raise DevlegateError(
                f"cannot read state file {self._state_file}: {error}"
            ) from error
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise DevlegateError(f"invalid state file: {self._state_file}")
        self._validate_state_invariant(state, allow_legacy_unbound=True)
        return state

    @staticmethod
    def _validate_state_invariant(
        state: dict[str, object], *, allow_legacy_unbound: bool = False
    ) -> None:
        phase = state["phase"]
        if phase not in {
            "agent_pending",
            "agent_running",
            "recovery_pending",
            "recovery_running",
        }:
            if phase == "merge_pending":
                required_fields = ("local_head", "remote_head", "changed_paths")
                if not all(
                    isinstance(state.get(field), str) for field in required_fields
                ):
                    raise DevlegateError(
                        "invalid merge_pending state: synchronization coordinates "
                        "are incomplete"
                    )
            return
        required_fields = ("local_head", "remote_head", "changed_paths")
        if not all(isinstance(state.get(field), str) for field in required_fields):
            raise DevlegateError(
                f"invalid {phase} state: synchronization coordinates are incomplete"
            )
        has_id = isinstance(state.get("selected_ticket_id"), str) and bool(
            state["selected_ticket_id"]
        )
        has_body = isinstance(state.get("selected_ticket_body"), str)
        if (
            phase == "agent_pending"
            and state.get("selected_ticket_id") is None
            and state.get("selected_ticket_body") is None
        ):
            if allow_legacy_unbound:
                return
            raise DevlegateError(
                "invalid agent_pending state: selected ticket binding is incomplete"
            )
        if not has_id or not has_body:
            raise DevlegateError(
                f"invalid {phase} state: selected ticket binding is incomplete"
            )

    def _save_state(self, phase: str, **fields: object) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state: dict[str, object] = {**self._state, "phase": phase, **fields}
        if phase == "idle":
            state.pop("selected_ticket_id", None)
            state.pop("selected_ticket_body", None)
        self._validate_state_invariant(state)
        temporary_name = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                dir=self.state_dir,
                prefix=f".{self._state_key}.",
                suffix=".tmp",
                delete=False,
            ) as temporary:
                temporary_name = temporary.name
                json.dump(state, temporary)
                temporary.write("\n")
                temporary.flush()
                os.fsync(temporary.fileno())
            os.replace(temporary_name, self._state_file)
            directory_fd = os.open(self.state_dir, os.O_RDONLY)
            try:
                os.fsync(directory_fd)
            finally:
                os.close(directory_fd)
        except OSError as error:
            if temporary_name is not None:
                try:
                    os.unlink(temporary_name)
                except FileNotFoundError:
                    pass
            raise DevlegateError(
                f"cannot write state file {self._state_file}: {error}"
            ) from error
        self._state = state

    def _observe_worktree(self, status: str) -> bool:
        if status:
            fingerprint = _status_fingerprint(status)
            if fingerprint == self._dirty_fingerprint:
                return False
            self._dirty_fingerprint = fingerprint
            summary = _status_summary(status)
            _log("working tree became dirty; automatic work suspended")
            for line in status.rstrip().splitlines():
                _log(f"  {line}")
            for name in (
                "tracked_modified",
                "staged",
                "untracked",
                "deleted",
                "renamed",
                "conflicted",
            ):
                _log(f"{name.replace('_', ' ')}: {summary[name]}")
            return True
        elif self._dirty_fingerprint is not None:
            self._dirty_fingerprint = None
            _log("working tree is clean again")
            return True
        return False

    def _log_sync_failure(
        self, local_head: str, remote_ref: str, stderr: str = ""
    ) -> None:
        remote_result = _git(
            self.repo, "rev-parse", remote_ref, check=False
        )
        remote_head = remote_result.stdout.strip() or "<unknown>"
        base_result = _git(
            self.repo, "merge-base", local_head, remote_head, check=False
        )
        merge_base = base_result.stdout.strip() or "<none>"
        counts_result = _git(
            self.repo,
            "rev-list",
            "--left-right",
            "--count",
            local_head,
            remote_head,
            check=False,
        )
        counts = counts_result.stdout.strip().split()
        ahead = counts[0] if len(counts) == 2 else "unknown"
        behind = counts[1] if len(counts) == 2 else "unknown"
        if ahead != "unknown" and behind != "unknown":
            if ahead != "0" and behind != "0":
                classification = "histories have diverged"
            elif ahead != "0":
                classification = "local branch is ahead of remote"
            else:
                classification = "remote branch is ahead of local"
        else:
            classification = "fast-forward was rejected"
        _log("cannot fast-forward local checkout")
        _log(f"local HEAD: {local_head}")
        _log(f"remote HEAD: {remote_head}")
        _log(f"merge base: {merge_base}")
        _log(f"local ahead: {ahead}")
        _log(f"local behind: {behind}")
        _log(f"classification: {classification}")
        if stderr.strip():
            _log(f"git stderr: {stderr.strip()}")

    def run_once(self) -> int:
        self._workflow_validation_succeeded = False
        branch = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode:
            raise WorkflowBlockedError(
                f"current checkout is detached; expected branch "
                f"'{self.current_branch}'"
            )
        actual_branch = branch.stdout.strip()
        if actual_branch != self.current_branch:
            raise WorkflowBlockedError(
                f"current checkout branch '{actual_branch}' does not match "
                f"expected branch '{self.current_branch}'"
            )
        status = _git(self.repo, "status", "--porcelain").stdout
        dirty_changed = self._observe_worktree(status)
        if self._state.get("phase") == "recovery_running":
            self._save_state("recovery_failed")
            self._recovery_pending = False
            _log("interrupted recovery treated as failed recovery")
        if self._state.get("phase") == "recovery_failed":
            if status:
                if dirty_changed:
                    _log(
                        "recovery failed; working tree is still dirty; manual "
                        "intervention is required"
                    )
                return 1
            self._save_state("idle")
            self._recovery_pending = False
            _log("working tree cleaned manually; recovery state cleared")
        recovery = self._recovery_pending
        if status and not recovery:
            if dirty_changed:
                _log("working tree is dirty; refusing to pull or start the agent")
            return 1
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        state_phase = self._state.get("phase")
        legacy_unbound_pending = (
            state_phase == "agent_pending"
            and self._state.get("selected_ticket_id") is None
            and self._state.get("selected_ticket_body") is None
        )
        bound_agent_execution = state_phase == "agent_pending" and not (
            legacy_unbound_pending
        )
        if legacy_unbound_pending:
            _log(
                "legacy unbound agent_pending state detected; rebuilding "
                "scheduling state"
            )
        pending_sync = state_phase in {"agent_pending", "merge_pending"}
        pending_agent_execution = bound_agent_execution
        had_remote_change = False
        local_ahead = False
        if recovery:
            self._recovery_pending = False
            remote_head = local_head
            changed_paths = status.rstrip()
            self._save_state("recovery_running")
            _log("starting recovery for unfinished agent work")
        else:
            fetch = _git(
                self.repo,
                "fetch",
                "--prune",
                self.remote_name,
                self.remote_branch,
                check=False,
            )
            if fetch.returncode:
                _log(f"fetch failed: {fetch.stderr.strip() or 'unknown git error'}")
                return 1
            remote_result = _git(
                self.repo, "rev-parse", "--verify", remote_ref, check=False
            )
            if remote_result.returncode:
                _log(
                    f"remote branch not found: {remote_ref}; "
                    f"git stderr: {remote_result.stderr.strip() or 'unknown git error'}"
                )
                return 1
            remote_head = _git(self.repo, "rev-parse", remote_ref).stdout.strip()
            persisted_head = str(self._state.get("remote_head", ""))
            if pending_sync:
                persisted_local_head = str(self._state.get("local_head", ""))
                target_head = persisted_head
                if state_phase == "merge_pending":
                    if local_head not in {persisted_local_head, target_head}:
                        raise DevlegateError(
                            "persisted merge revision does not match the local HEAD"
                        )
                elif local_head != persisted_local_head:
                    persisted_local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            persisted_local_head,
                            local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    current_local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            local_head,
                            persisted_local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    if current_local_is_ancestor:
                        raise DevlegateError(
                            "current local HEAD is behind the persisted execution "
                            "HEAD; refusing rollback "
                            f"(persisted {persisted_local_head[:12]}, "
                            f"current {local_head[:12]}, remote {target_head[:12]})"
                        )
                    if not persisted_local_is_ancestor:
                        raise DevlegateError(
                            "current local HEAD diverged from the persisted execution "
                            f"history (persisted {persisted_local_head[:12]}, "
                            f"current {local_head[:12]}, remote {target_head[:12]})"
                        )

                target_is_current = target_head == remote_head
                target_is_ancestor = (
                    _git(
                        self.repo,
                        "merge-base",
                        "--is-ancestor",
                        target_head,
                        remote_head,
                        check=False,
                    ).returncode
                    == 0
                )
                if not target_is_current and not target_is_ancestor:
                    raise DevlegateError(
                        "new remote revision is not a descendant of the pending "
                        "revision"
                    )
                if target_is_ancestor:
                    target_head = remote_head
                    if target_head != persisted_head:
                        had_remote_change = True
                        _log(
                            f"superseding pending revision {persisted_head[:12]} "
                            f"with descendant {target_head[:12]}"
                        )
                local_ahead_pending = state_phase == "agent_pending" and (
                    local_head != target_head
                )
                preserve_local_ahead = False
                if local_ahead_pending:
                    local_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            local_head,
                            target_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    remote_is_ancestor = (
                        _git(
                            self.repo,
                            "merge-base",
                            "--is-ancestor",
                            target_head,
                            local_head,
                            check=False,
                        ).returncode
                        == 0
                    )
                    if not local_is_ancestor and not remote_is_ancestor:
                        self._log_sync_failure(local_head, remote_ref)
                        return 1
                    if remote_is_ancestor:
                        preserve_local_ahead = True
                        changed_paths = str(self._state.get("changed_paths", ""))
                        _log(
                            "resuming local-ahead pending execution; preserving "
                            f"local HEAD {local_head[:12]}"
                        )
                if local_head != target_head and not preserve_local_ahead:
                    changed_paths = _git(
                        self.repo,
                        "diff",
                        "--name-only",
                        local_head,
                        target_head,
                        check=False,
                    ).stdout.rstrip()
                    self._save_state(
                        "merge_pending",
                        local_head=local_head,
                        remote_head=target_head,
                        changed_paths=changed_paths,
                    )
                    merge = _git(
                        self.repo, "merge", "--ff-only", remote_ref, check=False
                    )
                    if merge.returncode:
                        self._log_sync_failure(
                            local_head, remote_ref, merge.stderr
                        )
                        return 1
                    local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
                    if local_head != target_head:
                        raise DevlegateError(
                            "fast-forward completed without reaching the target "
                            "revision"
                        )
                    _log(f"updated {self.current_branch} to {target_head[:12]}")
                else:
                    changed_paths = str(self._state.get("changed_paths", ""))
                remote_head = target_head
                synchronized_phase = (
                    "agent_pending" if bound_agent_execution else "merge_pending"
                )
                synchronized_fields = {
                    "local_head": local_head,
                    "remote_head": target_head,
                    "changed_paths": changed_paths,
                }
                self._save_state(synchronized_phase, **synchronized_fields)
                if state_phase == "merge_pending":
                    _log("resumed after completed merge")
            elif local_head == remote_head:
                if self._recovery_pending:
                    self._recovery_pending = False
                    self._save_state("idle")
                changed_paths = ""
            elif _git(
                self.repo,
                "merge-base",
                "--is-ancestor",
                local_head,
                remote_head,
                check=False,
            ).returncode == 0:
                had_remote_change = True
                changed_paths = _git(
                    self.repo,
                    "diff",
                    "--name-only",
                    local_head,
                    remote_head,
                    check=False,
                ).stdout.rstrip()
                self._save_state(
                    "merge_pending",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                )
                merge = _git(
                    self.repo, "merge", "--ff-only", remote_ref, check=False
                )
                if merge.returncode:
                    self._log_sync_failure(local_head, remote_ref, merge.stderr)
                    return 1
                actual_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
                if actual_head != remote_head:
                    raise DevlegateError(
                        "fast-forward completed without reaching the remote "
                        "revision"
                    )
                _log(
                    f"updated {self.current_branch} from {local_head[:12]} "
                    f"to {remote_head[:12]}"
                )
                local_head = actual_head
                self._save_state(
                    "merge_pending",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                )
            elif _git(
                self.repo,
                "merge-base",
                "--is-ancestor",
                remote_head,
                local_head,
                check=False,
            ).returncode == 0:
                local_ahead = True
                changed_paths = _git(
                    self.repo,
                    "diff",
                    "--name-only",
                    remote_head,
                    local_head,
                    check=False,
                ).stdout.rstrip()
                _log(
                    f"local branch is ahead of {self.remote_name}/"
                    f"{self.remote_branch}; preserving local HEAD {local_head[:12]}"
                )
            else:
                had_remote_change = True
                self._log_sync_failure(local_head, remote_ref)
                return 1
        ticket_store = self._ticket_store()
        bound_execution = recovery or bound_agent_execution
        execution_plan = self._make_execution_plan(self._state, ticket_store, False)
        if execution_plan.action == "blocked":
            raise DevlegateError(execution_plan.reason)
        if execution_plan.action == "none" and not recovery:
            selected_ticket = None
        elif bound_execution:
            selected_ticket = self._selected_ticket(ticket_store, True)
        else:
            assert execution_plan.ticket_id is not None
            selected_ticket = ticket_store.by_id[execution_plan.ticket_id]
        if not recovery:
            todo_fingerprint, todo_count = _todo_fingerprint(
                self.repo, self.todo_path
            )
            generation_is_same = (
                str(self._state.get("handled_remote_head", "")) == remote_head
                and str(self._state.get("handled_todo_fingerprint", ""))
                == todo_fingerprint
            )
            if selected_ticket is None and not pending_agent_execution:
                self._save_state(
                    "idle",
                    handled_remote_head=remote_head,
                    handled_todo_fingerprint=todo_fingerprint,
                )
                if todo_count and ticket_store.tickets:
                    _log(
                        "todo tickets are currently blocked by unfinished dependencies"
                    )
                elif local_ahead:
                    _log(
                        "local branch is ahead; no actionable ticket files in "
                        f"{self.todo_path}"
                    )
                elif not had_remote_change:
                    _log("no remote changes")
                else:
                    _log(f"no actionable ticket files in {self.todo_path}")
                return 0
            if generation_is_same and not pending_agent_execution:
                _log(
                    "no new work generation; unchanged todo is already "
                    "handled"
                )
                return 0
            self._save_state(
                "agent_pending",
                local_head=local_head,
                remote_head=remote_head,
                changed_paths=changed_paths,
                handled_remote_head=remote_head,
                handled_todo_fingerprint=todo_fingerprint,
                selected_ticket_id=selected_ticket.id,
                selected_ticket_body=selected_ticket.body,
            )

        prompt_values = {
            "REPO_ROOT": str(self.repo),
            "REMOTE_NAME": self.remote_name,
            "REMOTE_BRANCH": self.remote_branch,
            "BACKLOG_PATH": self.backlog_path,
            "TODO_PATH": self.todo_path,
            "REVIEW_PATH": self.review_path,
            "DONE_PATH": self.done_path,
            "TODO_DIRECTORY": str(self.repo / self.todo_path),
            "REVIEW_DIRECTORY": str(self.repo / self.review_path),
        }
        recovery_prompt = ""
        if recovery:
            recovery_prompt = "\n\n" + _render_prompt(
                self.recovery_prompt_file.read_text(), prompt_values
            ).strip("\n")
        self._save_state(
            "agent_running",
            selected_ticket_id=selected_ticket.id,
            selected_ticket_body=selected_ticket.body,
        )
        ticket_file = selected_ticket.path
        review_file = (
            self.repo / self.review_path / f"{selected_ticket.id}.md"
            if selected_ticket.state == "todo"
            else None
        )
        execution_context = (
            "\n\n--- Devlegate execution context ---\n"
            f"Repository root: {self.repo}\n"
            f"Remote: {self.remote_name}\n"
            f"Branch: {self.remote_branch}\n"
            f"Pulled revision: {local_head} -> {remote_head}\n"
            f"Assigned ticket ID: {selected_ticket.id}\n"
            f"Assigned ticket file: {ticket_file}\n"
            f"Assigned ticket state: {selected_ticket.state}\n"
            + (
                f"Review destination: {review_file}\n"
                if review_file is not None
                else "Review destination: already in review; do not move again\n"
            )
            + f"Changed paths from the pulled revision:\n{changed_paths or '<none>'}\n"
            "\n--- Assigned work ---\n"
            f"{selected_ticket.body}"
        )
        prompt = (
            _render_prompt(self.agent_prompt_file.read_text(), prompt_values)
            + execution_context
        ).rstrip("\n")
        prompt += recovery_prompt
        _log(f"running OpenCode for tickets in {self.todo_path}")
        command = [
            self.opencode_bin,
            "run",
            "--format",
            "json",
            "--auto",
            "--model",
            self.opencode_model,
        ]
        if self.opencode_agent:
            command += ["--agent", self.opencode_agent]
        with _preserve_terminal():
            agent_status = _run_opencode(command, prompt)
        if agent_status:
            phase = "recovery_failed" if recovery else "recovery_pending"
            self._recovery_pending = False if recovery else True
            self._save_state(phase)
            _log(f"agent exited with status {agent_status}")
            return agent_status
        if _git(self.repo, "status", "--porcelain").stdout:
            phase = "recovery_failed" if recovery else "recovery_pending"
            self._recovery_pending = False if recovery else True
            self._save_state(phase)
            _log("agent exited successfully but left uncommitted repository changes")
            return 1
        local_after = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        if _git(
            self.repo, "fetch", self.remote_name, self.remote_branch, check=False
        ).returncode:
            _log("post-agent fetch failed")
            return 1
        remote_after = _git(self.repo, "rev-parse", remote_ref).stdout.strip()
        if local_after != remote_after:
            self._save_state("idle")
            _log(
                "agent did not leave the local branch synchronized with the remote "
                "branch; expected it to commit and push"
            )
            return 1
        self._save_state("idle")
        _log("agent completed; local and remote branch heads are synchronized")
        return 0

    def run(self, once: bool) -> int:
        with self._lock():
            while True:
                workflow_blocked = False
                try:
                    status = self.run_once()
                except WorkflowBlockedError as error:
                    workflow_blocked = True
                    message = str(error)
                    fingerprint = hashlib.sha256(message.encode()).hexdigest()
                    if self._workflow_blocker_fingerprint is None:
                        _log(f"workflow became blocked: {message}")
                    elif self._workflow_blocker_fingerprint != fingerprint:
                        _log(f"workflow remains blocked: {message}")
                    self._workflow_blocker_fingerprint = fingerprint
                    status = 1
                except (OSError, subprocess.CalledProcessError) as error:
                    detail = getattr(error, "stderr", None) or str(error)
                    _log(f"run failed: {detail.strip()}")
                    status = 1
                else:
                    if (
                        self._workflow_blocker_fingerprint is not None
                        and self._workflow_validation_succeeded
                    ):
                        _log("workflow is valid again")
                        self._workflow_blocker_fingerprint = None
                if status and not workflow_blocked:
                    if not (status == 1 and self._dirty_fingerprint is not None):
                        _log(f"run failed with status {status}")
                if once:
                    return status
                time.sleep(int(self.poll_interval))

    def _status_snapshot_hook(self, _point: str) -> None:
        """Testing hook for deterministic snapshot-race simulations."""

    def _status_state_observation(self) -> tuple[dict[str, object], str]:
        try:
            raw = self._state_file.read_bytes()
        except FileNotFoundError:
            return {"phase": "idle"}, "missing"
        except OSError as error:
            raise DevlegateError(
                f"cannot read state file {self._state_file}: {error}"
            ) from error
        try:
            state = json.loads(raw)
        except (UnicodeDecodeError, json.JSONDecodeError) as error:
            raise DevlegateError(
                f"cannot read state file {self._state_file}: {error}"
            ) from error
        if not isinstance(state, dict) or not isinstance(state.get("phase"), str):
            raise DevlegateError(f"invalid state file: {self._state_file}")
        self._validate_state_invariant(state)
        return state, hashlib.sha256(raw).hexdigest()

    def _status_git_observation(self) -> dict[str, object]:
        branch_result = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        branch = (
            branch_result.stdout.strip()
            if branch_result.returncode == 0
            else "<detached>"
        )
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        remote_result = _git(
            self.repo, "rev-parse", "--verify", remote_ref, check=False
        )
        return {
            "branch": branch,
            "local_head": local_head,
            "remote_ref": remote_ref,
            "remote_head": (
                remote_result.stdout.strip() if remote_result.returncode == 0 else None
            ),
            "status": _git(self.repo, "status", "--porcelain").stdout,
        }

    def _collect_status_attempt(
        self, *, allow_workflow_blocked: bool = False
    ) -> StatusSnapshot:
        state_before, state_token_before = self._status_state_observation()
        self._status_snapshot_hook("after-state-before")
        git_before = self._status_git_observation()
        self._status_snapshot_hook("after-git-before")
        try:
            workflow_token_before = _workflow_fingerprint(
                self.repo, self._workflow_paths()
            )
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            raise DevlegateError(f"cannot observe workflow files: {error}") from error
        self._status_snapshot_hook("after-workflow-before")

        ticket_error: DevlegateError | None = None
        ticket_store = None
        try:
            ticket_store = self._ticket_store()
        except DevlegateError as error:
            ticket_error = error
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            ticket_error = DevlegateError(f"cannot read ticket storage: {error}")

        self._status_snapshot_hook("before-verify")
        try:
            workflow_token_after = _workflow_fingerprint(
                self.repo, self._workflow_paths()
            )
        except FileNotFoundError as error:
            raise SnapshotChanged from error
        except OSError as error:
            raise DevlegateError(f"cannot observe workflow files: {error}") from error
        git_after = self._status_git_observation()
        state_after, state_token_after = self._status_state_observation()
        if (
            state_token_before != state_token_after
            or git_before != git_after
            or workflow_token_before != workflow_token_after
        ):
            raise SnapshotChanged
        if ticket_error is not None and not allow_workflow_blocked:
            raise ticket_error
        return self._make_status_snapshot(
            state_after, git_after, ticket_store, workflow_error=ticket_error
        )

    def _make_status_snapshot(
        self,
        state: dict[str, object],
        git_observation: dict[str, object],
        ticket_store: TicketStore | None,
        workflow_error: DevlegateError | None = None,
    ) -> StatusSnapshot:
        if ticket_store is None:
            return StatusSnapshot(
                phase=str(state["phase"]),
                bound_ticket_id=None,
                persisted_body_present=False,
                branch=str(git_observation["branch"]),
                local_head=str(git_observation["local_head"]),
                remote_ref=str(git_observation["remote_ref"]),
                remote_head=(
                    str(git_observation["remote_head"])
                    if git_observation["remote_head"] is not None
                    else None
                ),
                working_tree_clean=not bool(git_observation["status"]),
                counts=tuple(
                    (state_name, 0)
                    for state_name in ("backlog", "todo", "review", "done")
                ),
                runnable=(),
                blocked=(),
                review=(),
                next_ticket=None,
                plan=ExecutionPlan(
                    "blocked", str(workflow_error or "workflow is invalid")
                ),
            )
        counts = tuple(
            (
                state_name,
                sum(ticket.state == state_name for ticket in ticket_store.tickets),
            )
            for state_name in ("backlog", "todo", "review", "done")
        )
        runnable = tuple(
            (ticket.id, ticket.title) for ticket in ticket_store.runnable
        )
        blocked = tuple(
            (
                ticket.id,
                ticket.title,
                tuple(
                    (dependency, ticket_store.by_id[dependency].state)
                    for dependency in sorted(ticket.depends_on)
                    if ticket_store.by_id[dependency].state != "done"
                ),
            )
            for ticket in ticket_store.tickets
            if ticket.state == "todo"
            and any(
                ticket_store.by_id[dependency].state != "done"
                for dependency in ticket.depends_on
            )
        )
        review = tuple(
            (ticket.id, ticket.title)
            for ticket in ticket_store.tickets
            if ticket.state == "review"
        )
        selected_id = state.get("selected_ticket_id")
        bound_phase = str(state["phase"]) in {
            "agent_pending",
            "agent_running",
            "recovery_pending",
            "recovery_running",
        }
        plan = self._make_execution_plan(
            state,
            ticket_store,
            bool(git_observation["status"]),
            str(git_observation["branch"]) == self.remote_branch,
        )
        next_ticket = (
            (
                ticket_store.by_id[plan.ticket_id].id,
                ticket_store.by_id[plan.ticket_id].title,
            )
            if plan.ticket_id is not None and plan.ticket_id in ticket_store.by_id
            else None
        )
        return StatusSnapshot(
            phase=str(state["phase"]),
            bound_ticket_id=(
                selected_id if bound_phase and isinstance(selected_id, str) else None
            ),
            persisted_body_present=bound_phase
            and isinstance(state.get("selected_ticket_body"), str),
            branch=str(git_observation["branch"]),
            local_head=str(git_observation["local_head"]),
            remote_ref=str(git_observation["remote_ref"]),
            remote_head=(
                str(git_observation["remote_head"])
                if git_observation["remote_head"] is not None
                else None
            ),
            working_tree_clean=not bool(git_observation["status"]),
            counts=counts,
            runnable=runnable,
            blocked=blocked,
            review=review,
            next_ticket=next_ticket,
            plan=plan,
        )

    def _make_execution_plan(
        self,
        state: dict[str, object],
        ticket_store: TicketStore,
        dirty: bool,
        branch_ok: bool = True,
    ) -> ExecutionPlan:
        if not branch_ok:
            return ExecutionPlan(
                "blocked", "current checkout is not on the configured branch"
            )
        if dirty:
            return ExecutionPlan("blocked", "working tree is dirty")
        bound_phase = str(state["phase"]) in {
            "agent_pending",
            "agent_running",
            "recovery_pending",
            "recovery_running",
        }
        if bound_phase:
            ticket_id = state.get("selected_ticket_id")
            ticket = (
                ticket_store.by_id.get(ticket_id)
                if isinstance(ticket_id, str)
                else None
            )
            if ticket is None:
                return ExecutionPlan(
                    "blocked", "persisted selected ticket is not in managed storage"
                )
            if ticket.state not in {"todo", "review"}:
                return ExecutionPlan(
                    "blocked",
                    "selected ticket "
                    f"{ticket.id} has unexpected recovery state {ticket.state}",
                )
            return ExecutionPlan(
                "run-worker",
                "resume persisted bound execution",
                ticket.id,
                ticket.title,
                ticket.state,
                True,
            )
        selected = ticket_store.selected()
        if selected is None:
            return ExecutionPlan("none", "no runnable tickets")
        return ExecutionPlan(
            "run-worker",
            "deterministic runnable ticket selection",
            selected.id,
            selected.title,
            selected.state,
        )

    def _render_status_text(self, snapshot: StatusSnapshot) -> str:
        lines = [
            f"Devlegate {__version__}",
            "",
            "Execution:",
            f"  phase: {snapshot.phase}",
            f"  bound ticket: {snapshot.bound_ticket_id or 'none'}",
            (
                "  persisted body: "
                f"{'yes' if snapshot.persisted_body_present else 'no'}"
                if snapshot.bound_ticket_id
                else ""
            ),
            "",
            "Repository:",
            f"  branch: {snapshot.branch}",
            f"  local HEAD: {snapshot.local_head}",
            f"  known remote: {snapshot.remote_ref}",
            f"  known remote HEAD: {snapshot.remote_head or '<unknown>'}",
            f"  working tree: {'clean' if snapshot.working_tree_clean else 'dirty'}",
            "",
            "Tickets:",
        ]
        lines.extend(f"  {state}: {count}" for state, count in snapshot.counts)
        lines.extend(["", "Runnable:"])
        lines.extend(
            f"  {ticket_id}  {title}" for ticket_id, title in snapshot.runnable
        )
        if not snapshot.runnable:
            lines.append("  none")
        lines.append("Blocked:")
        if snapshot.blocked:
            for ticket_id, title, blockers in snapshot.blocked:
                lines.append(f"  {ticket_id}  {title}")
                lines.extend(
                    f"    by {dependency_id} [{state}]"
                    for dependency_id, state in blockers
                )
        else:
            lines.append("  none")
        lines.append("Review:")
        lines.extend(
            f"  {ticket_id}  {title}" for ticket_id, title in snapshot.review
        )
        if not snapshot.review:
            lines.append("  none")
        lines.extend(
            [
                "Next:",
                (
                    f"  {snapshot.next_ticket[0]}  {snapshot.next_ticket[1]}"
                    if snapshot.next_ticket
                    else "  none"
                ),
            ]
        )
        return "\n".join(lines)

    def status(self, json_output: bool = False) -> int:
        for _attempt in range(3):
            try:
                snapshot = self._collect_status_attempt()
                break
            except SnapshotChanged:
                continue
        else:
            raise DevlegateError(
                "project state changed while status snapshot was being collected"
            )
        if json_output:
            print(json.dumps(snapshot.as_dict(), indent=2, sort_keys=True))
        else:
            print(self._render_status_text(snapshot))
        return 0

    def plan(self, json_output: bool = False) -> int:
        for _attempt in range(3):
            try:
                snapshot = self._collect_status_attempt(allow_workflow_blocked=True)
                break
            except SnapshotChanged:
                continue
        else:
            raise DevlegateError(
                "project state changed while execution plan was being collected"
            )
        if json_output:
            print(json.dumps(snapshot.plan.as_dict(), indent=2, sort_keys=True))
        else:
            plan = snapshot.plan
            print(f"action: {plan.action}")
            print(f"reason: {plan.reason}")
            if plan.ticket_id is not None:
                print(f"ticket: {plan.ticket_id}  {plan.ticket_title}")
                print(f"ticket state: {plan.ticket_state}")
            print(f"bound: {'yes' if plan.bound else 'no'}")
        return 0

    def check(self) -> int:
        """Run read-only configuration and checkout diagnostics."""
        print(f"Devlegate {__version__} preflight")
        status = _git(self.repo, "status", "--porcelain").stdout
        todo_fingerprint, todo_count = _todo_fingerprint(self.repo, self.todo_path)
        ticket_store = None
        ticket_error = ""
        try:
            ticket_store = self._ticket_store()
        except DevlegateError as error:
            ticket_error = str(error)
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        remote_result = _git(self.repo, "rev-parse", remote_ref, check=False)
        remote_head = remote_result.stdout.strip()
        generation_differs = (
            str(self._state.get("handled_remote_head", "")) != remote_head
            or str(self._state.get("handled_todo_fingerprint", ""))
            != todo_fingerprint
        )
        checks = [
            ("configuration", True, ""),
            ("repository root", self.repo.is_dir(), str(self.repo)),
            ("branch", bool(self.current_branch), self.current_branch),
            (
                "remote",
                remote_result.returncode == 0,
                f"{self.remote_name}/{self.remote_branch}",
            ),
            (
                "OpenCode executable",
                shutil.which(self.opencode_bin) is not None,
                "",
            ),
            (
                "agent prompt",
                self.agent_prompt_file.is_file(),
                str(self.agent_prompt_file),
            ),
            (
                "recovery prompt",
                self.recovery_prompt_file.is_file(),
                str(self.recovery_prompt_file),
            ),
            ("state directory", self.state_dir.is_dir(), str(self.state_dir)),
            (
                "working tree clean",
                not bool(status),
                "",
            ),
            ("ticket schema", not ticket_error, ticket_error),
            ("dependency graph", not ticket_error, ticket_error),
        ]
        failed = False
        for name, passed, detail in checks:
            label = "OK" if passed else "FAIL"
            suffix = f": {detail}" if detail else ""
            print(f"{label:4}  {name}{suffix}")
            failed |= not passed
        if status:
            print("Dirty working tree details:")
            for line in status.rstrip().splitlines():
                print(f"  {line}")
            for name, count in _status_summary(status).items():
                print(f"{name.replace('_', ' ')}: {count}")
        print(f"local HEAD: {local_head}")
        print(f"known remote HEAD: {remote_head or '<unknown>'}")
        if remote_head:
            counts = _git(
                self.repo,
                "rev-list",
                "--left-right",
                "--count",
                local_head,
                remote_head,
                check=False,
            ).stdout.strip()
            print(f"ahead/behind: {counts or '<unknown>'}")
        print(f"todo files: {todo_count}")
        print(f"todo fingerprint: {todo_fingerprint}")
        if ticket_store is not None:
            counts = {
                state: sum(ticket.state == state for ticket in ticket_store.tickets)
                for state in ("backlog", "todo", "review", "done")
            }
            print("Ticket storage:")
            for state in ("backlog", "todo", "review", "done"):
                print(f"  {state}: {counts[state]}")
            print(f"Runnable tickets: {len(ticket_store.runnable)}")
            selected = ticket_store.selected()
            if selected is not None:
                print(f"Next runnable: {selected.id} - {selected.title}")
        elif ticket_error:
            print(f"Ticket storage: invalid: {ticket_error}")
        print(f"work generation differs from persisted: {generation_differs}")
        print(
            "remote information is from the existing remote-tracking ref; "
            "--check does not fetch"
        )
        if failed:
            print("\nNot ready.")
            return 1
        print("\nReady.")
        return 0


def build_parser() -> argparse.ArgumentParser:
    """Build the Devlegate argument parser."""
    parser = argparse.ArgumentParser(
        prog="devlegate",
        description="Workflow orchestrator for software-development repositories.",
    )
    parser.add_argument(
        "--version",
        action="version",
        version=f"%(prog)s {__version__}",
    )
    commands = parser.add_subparsers(dest="command", metavar="{run,check,status}")
    run_parser = commands.add_parser("run", help="synchronize and run one ticket")
    run_parser.add_argument(
        "--once", action="store_true", help="run one synchronization pass and exit"
    )
    run_parser.add_argument(
        "--env", metavar="FILE", type=Path, help="read a configuration file"
    )
    check_parser = commands.add_parser("check", help="validate setup readiness")
    check_parser.add_argument(
        "--env", metavar="FILE", type=Path, help="read a configuration file"
    )
    status_parser = commands.add_parser(
        "status", help="show a read-only observed project snapshot"
    )
    status_parser.add_argument(
        "--json", action="store_true", help="render the snapshot as JSON"
    )
    status_parser.add_argument(
        "--env", metavar="FILE", type=Path, help="read a configuration file"
    )
    plan_parser = commands.add_parser(
        "plan", help="show the read-only execution plan"
    )
    plan_parser.add_argument(
        "--json", action="store_true", help="render the plan as JSON"
    )
    plan_parser.add_argument(
        "--env", metavar="FILE", type=Path, help="read a configuration file"
    )
    return parser


def main() -> int:
    """Run the command-line interface."""
    argv = sys.argv[1:]
    if argv and argv[0] in {"--help", "-h", "--version"}:
        normalized_argv = argv
    elif not argv or argv[0].startswith("-"):
        if "--check" in argv:
            normalized_argv = [
                "check"
            ] + [argument for argument in argv if argument not in {"--check", "--once"}]
        else:
            normalized_argv = ["run", *argv]
    else:
        normalized_argv = argv
    args = build_parser().parse_args(normalized_argv)
    if args.command is None:
        build_parser().error("a command is required")
    env_file = args.env or Path.cwd() / ".env"
    try:
        devlegate = Devlegate(env_file, read_only=args.command in {"status", "plan"})
        if args.command == "status":
            return devlegate.status(args.json)
        if args.command == "plan":
            return devlegate.plan(args.json)
        if args.command == "check":
            return devlegate.check()
        return devlegate.run(args.once)
    except KeyboardInterrupt:
        return 130
    except DevlegateError as error:
        if args.command == "check":
            print(f"Devlegate {__version__} preflight")
            print(f"FAIL  configuration: {error}")
            print("\nNot ready.")
            return 1
        if isinstance(error, WorkflowBlockedError):
            print(f"devlegate: workflow blocked: {error}", file=sys.stderr)
            return 1
        print(f"devlegate: {error}", file=sys.stderr)
        return 1
