"""Command-line interface and runtime for Devlegate."""

import argparse
import fcntl
import hashlib
import json
import os
import shutil
import string
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from devlegate import __version__


class DevlegateError(Exception):
    """A user-facing startup error."""


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


def _has_todo_files(repo: Path, todo_path: str) -> bool:
    todo_dir = repo / todo_path
    return todo_dir.is_dir() and any(
        item.is_file() and item.name != ".gitkeep" for item in todo_dir.rglob("*")
    )


def _todo_fingerprint(repo: Path, todo_path: str) -> tuple[str, int]:
    todo_dir = repo / todo_path
    entries: list[str] = []
    if todo_dir.is_dir():
        for item in todo_dir.rglob("*"):
            if item.is_file() and not item.is_symlink() and item.name != ".gitkeep":
                relative = item.relative_to(todo_dir).as_posix()
                digest = hashlib.sha256(item.read_bytes()).hexdigest()
                entries.append(f"{relative}\0{digest}\n")
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

    def __init__(self, env_file: Path) -> None:
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
        self.todo_path = setting("TODO_PATH", "kanban/todo")
        self.review_path = setting("REVIEW_PATH", "kanban/review")
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
        self._recovery_pending = False
        self._validate()
        self._state = self._load_state()
        self._recovery_pending = self._state.get("phase") in {
            "agent_running",
            "recovery_pending",
        }

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
            raise DevlegateError(f"OpenCode executable not found: {self.opencode_bin}")
        branch = _git(
            self.repo, "symbolic-ref", "--quiet", "--short", "HEAD", check=False
        )
        if branch.returncode:
            raise DevlegateError("detached HEAD is not supported")
        self.current_branch = branch.stdout.strip()
        self.remote_branch = self.remote_branch or self.current_branch
        if self.current_branch != self.remote_branch:
            raise DevlegateError(
                f"current branch '{self.current_branch}' does not match "
                f"REMOTE_BRANCH '{self.remote_branch}'"
            )
        if _git(
            self.repo, "remote", "get-url", self.remote_name, check=False
        ).returncode:
            raise DevlegateError(f"git remote not found: {self.remote_name}")
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
        return state

    def _save_state(self, phase: str, **fields: object) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        state: dict[str, object] = {**self._state, "phase": phase, **fields}
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

    def _observe_worktree(self, status: str) -> None:
        if status:
            fingerprint = _status_fingerprint(status)
            if fingerprint == self._dirty_fingerprint:
                return
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
        elif self._dirty_fingerprint is not None:
            self._dirty_fingerprint = None
            _log("working tree is clean again")

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
        status = _git(self.repo, "status", "--porcelain").stdout
        self._observe_worktree(status)
        if self._state.get("phase") == "recovery_running":
            self._save_state("recovery_failed")
            self._recovery_pending = False
            _log("interrupted recovery treated as failed recovery")
        if self._state.get("phase") == "recovery_failed":
            if status:
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
            _log("working tree is dirty; refusing to pull or start the agent")
            return 1
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        state_phase = self._state.get("phase")
        pending_execution = state_phase in {"agent_pending", "merge_pending"}
        had_remote_change = False
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
            if pending_execution:
                old_head = str(self._state.get("local_head", ""))
                target_head = persisted_head
                if state_phase == "merge_pending":
                    if local_head not in {old_head, target_head}:
                        raise DevlegateError(
                            "persisted merge revision does not match the local HEAD"
                        )
                elif local_head != target_head:
                    raise DevlegateError(
                        "persisted execution revision does not match the local HEAD"
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
                        _log(
                            f"superseding pending revision {persisted_head[:12]} "
                            f"with descendant {target_head[:12]}"
                        )
                if local_head != target_head:
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
                    local_head = target_head
                    _log(f"updated {self.current_branch} to {target_head[:12]}")
                else:
                    changed_paths = str(self._state.get("changed_paths", ""))
                remote_head = target_head
                self._save_state(
                    "agent_pending",
                    local_head=local_head,
                    remote_head=target_head,
                    changed_paths=changed_paths,
                )
                if state_phase == "merge_pending":
                    _log("resumed after completed merge")
            elif local_head == remote_head:
                if self._recovery_pending:
                    self._recovery_pending = False
                    self._save_state("idle")
                changed_paths = ""
            else:
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
                _log(
                    f"updated {self.current_branch} from {local_head[:12]} "
                    f"to {remote_head[:12]}"
                )
                local_head = remote_head
                self._save_state(
                    "agent_pending",
                    local_head=local_head,
                    remote_head=remote_head,
                    changed_paths=changed_paths,
                )
        if not recovery:
            todo_fingerprint, todo_count = _todo_fingerprint(
                self.repo, self.todo_path
            )
            generation_is_same = (
                str(self._state.get("handled_remote_head", "")) == remote_head
                and str(self._state.get("handled_todo_fingerprint", ""))
                == todo_fingerprint
            )
            if not todo_count:
                self._save_state(
                    "idle",
                    handled_remote_head=remote_head,
                    handled_todo_fingerprint=todo_fingerprint,
                )
                if not had_remote_change:
                    _log("no remote changes")
                else:
                    _log(f"no actionable ticket files in {self.todo_path}")
                return 0
            if generation_is_same and not pending_execution:
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
            )

        prompt_values = {
            "REPO_ROOT": str(self.repo),
            "REMOTE_NAME": self.remote_name,
            "REMOTE_BRANCH": self.remote_branch,
            "TODO_PATH": self.todo_path,
            "REVIEW_PATH": self.review_path,
            "TODO_DIRECTORY": str(self.repo / self.todo_path),
            "REVIEW_DIRECTORY": str(self.repo / self.review_path),
        }
        recovery_prompt = ""
        if recovery:
            recovery_prompt = "\n\n" + _render_prompt(
                self.recovery_prompt_file.read_text(), prompt_values
            ).strip("\n")
        self._save_state("agent_running")
        prompt = (
            _render_prompt(self.agent_prompt_file.read_text(), prompt_values)
            + "\n\n--- Devlegate context ---\n"
            + f"Repository root: {self.repo}\nRemote: {self.remote_name}\n"
            + f"Branch: {self.remote_branch}\n"
            + f"Pulled revision: {local_head} -> {remote_head}\n"
            + f"Todo directory: {self.todo_path}\n"
            + f"Review directory: {self.review_path}\n"
            + "\nChanged paths from the pulled revision:\n"
            + f"{changed_paths or '<none>'}\n"
        ).rstrip("\n")
        prompt += recovery_prompt
        _log(f"running OpenCode for tickets in {self.todo_path}")
        command = [self.opencode_bin, "run", "--auto", "--model", self.opencode_model]
        if self.opencode_agent:
            command += ["--agent", self.opencode_agent]
        agent = subprocess.run(command + [prompt], check=False)
        if agent.returncode:
            phase = "recovery_failed" if recovery else "recovery_pending"
            self._recovery_pending = False if recovery else True
            self._save_state(phase)
            _log(f"agent exited with status {agent.returncode}")
            return agent.returncode
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
                try:
                    status = self.run_once()
                except (OSError, subprocess.CalledProcessError) as error:
                    detail = getattr(error, "stderr", None) or str(error)
                    _log(f"run failed: {detail.strip()}")
                    status = 1
                if status:
                    _log(f"run failed with status {status}")
                if once:
                    return status
                time.sleep(int(self.poll_interval))

    def check(self) -> int:
        """Run read-only configuration and checkout diagnostics."""
        print(f"Devlegate {__version__} preflight")
        status = _git(self.repo, "status", "--porcelain").stdout
        todo_fingerprint, todo_count = _todo_fingerprint(self.repo, self.todo_path)
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
    parser.add_argument(
        "--once", action="store_true", help="run one synchronization pass and exit"
    )
    parser.add_argument(
        "--check", action="store_true", help="validate setup without running workflow"
    )
    parser.add_argument(
        "--env", metavar="FILE", type=Path, help="read configuration from FILE"
    )
    return parser


def main() -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args()
    env_file = args.env or Path.cwd() / ".env"
    try:
        devlegate = Devlegate(env_file)
        return devlegate.check() if args.check else devlegate.run(args.once)
    except KeyboardInterrupt:
        return 130
    except DevlegateError as error:
        if args.check:
            print(f"Devlegate {__version__} preflight")
            print(f"FAIL  configuration: {error}")
            print("\nNot ready.")
            return 1
        print(f"devlegate: {error}", file=sys.stderr)
        return 1
