"""Command-line interface and runtime for Devlegate."""

import argparse
import fcntl
import hashlib
import os
import shutil
import subprocess
import sys
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


class Devlegate:
    """Run the repository polling and agent execution workflow."""

    def __init__(self, env_file: Path) -> None:
        if not env_file.is_file():
            raise DevlegateError(
                f"configuration file not found: {env_file} "
                f"(copy devlegate's .env.example to $PWD/.env)"
            )
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
        default_prompt = Path(__file__).resolve().parents[2] / "agent-prompt.txt"
        self.agent_prompt_file = Path(
            setting("AGENT_PROMPT_FILE", str(default_prompt))
        )
        self.opencode_bin = setting("OPENCODE_BIN", "opencode")
        self.opencode_model = setting("OPENCODE_MODEL", "")
        self.opencode_agent = setting("OPENCODE_AGENT", "")
        self.state_dir = Path(
            config.get(
                "STATE_DIR",
                os.environ.get(
                    "STATE_DIR",
                    os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state")),
                ),
            )
        )
        self._validate()

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
        if not self.poll_interval.isdecimal():
            raise DevlegateError("POLL_INTERVAL must be an integer")
        if not self.opencode_model:
            raise DevlegateError("OPENCODE_MODEL is required")
        if shutil.which("flock") is None:
            raise DevlegateError("flock is required")
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

    def _lock(self):
        key = hashlib.sha256(str(self.repo).encode()).hexdigest()
        lock_root = self.state_dir / "devlegate"
        lock_root.mkdir(parents=True, exist_ok=True)
        lock_file = lock_root / f"{key}.lock"
        handle = lock_file.open("w")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as error:
            handle.close()
            raise DevlegateError(
                "another devlegate instance is already running for this checkout"
            ) from error
        return handle

    def run_once(self) -> int:
        if _git(self.repo, "status", "--porcelain").stdout:
            _log("working tree is dirty; refusing to pull or start the agent")
            return 1
        local_head = _git(self.repo, "rev-parse", "HEAD").stdout.strip()
        remote_ref = f"{self.remote_name}/{self.remote_branch}"
        if _git(
            self.repo,
            "fetch",
            "--prune",
            self.remote_name,
            self.remote_branch,
            check=False,
        ).returncode:
            _log("fetch failed")
            return 1
        if _git(self.repo, "rev-parse", "--verify", remote_ref, check=False).returncode:
            _log(f"remote branch not found: {remote_ref}")
            return 1
        remote_head = _git(self.repo, "rev-parse", remote_ref).stdout.strip()
        if local_head == remote_head:
            _log("no remote changes")
            return 0
        changed_paths = _git(
            self.repo, "diff", "--name-only", local_head, remote_head, check=False
        ).stdout.rstrip()
        if _git(self.repo, "merge", "--ff-only", remote_ref, check=False).returncode:
            _log("cannot fast-forward local checkout")
            return 1
        _log(
            f"updated {self.current_branch} from {local_head[:12]} "
            f"to {remote_head[:12]}"
        )
        if not _has_todo_files(self.repo, self.todo_path):
            _log(f"no actionable ticket files in {self.todo_path}")
            return 0
        prompt = (
            self.agent_prompt_file.read_text()
            + "\n\n--- Devlegate context ---\n"
            + f"Repository root: {self.repo}\nRemote: {self.remote_name}\n"
            + f"Branch: {self.remote_branch}\n"
            + f"Pulled revision: {local_head} -> {remote_head}\n"
            + f"Todo directory: {self.todo_path}\n"
            + f"Review directory: {self.review_path}\n"
            + "\nChanged paths from the pulled revision:\n"
            + f"{changed_paths or '<none>'}\n"
        ).rstrip("\n")
        _log(f"running OpenCode for tickets in {self.todo_path}")
        command = [self.opencode_bin, "run", "--auto", "--model", self.opencode_model]
        if self.opencode_agent:
            command += ["--agent", self.opencode_agent]
        agent = subprocess.run(command + [prompt], check=False)
        if agent.returncode:
            _log(f"agent exited with status {agent.returncode}")
            return agent.returncode
        if _git(self.repo, "status", "--porcelain").stdout:
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
            _log(
                "agent did not leave the local branch synchronized with the remote "
                "branch; expected it to commit and push"
            )
            return 1
        _log("agent completed; local and remote branch heads are synchronized")
        return 0

    def run(self, once: bool) -> int:
        with self._lock():
            while True:
                try:
                    status = self.run_once()
                except (OSError, subprocess.CalledProcessError) as error:
                    _log(f"run failed: {error}")
                    status = 1
                if status:
                    _log(f"run failed with status {status}")
                if once:
                    return status
                time.sleep(int(self.poll_interval))


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
        "--env", metavar="FILE", type=Path, help="read configuration from FILE"
    )
    return parser


def main() -> int:
    """Run the command-line interface."""
    args = build_parser().parse_args()
    env_file = args.env or Path.cwd() / ".env"
    try:
        return Devlegate(env_file).run(args.once)
    except KeyboardInterrupt:
        return 130
    except DevlegateError as error:
        print(f"devlegate: {error}", file=sys.stderr)
        return 1
