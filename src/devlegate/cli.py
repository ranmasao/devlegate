"""Command-line interface for Devlegate."""

import argparse
import json
import os
import select
import subprocess
import sys
import time
from pathlib import Path
from typing import NoReturn

from devlegate import __version__
from devlegate import runtime as _runtime
from devlegate.agent_protocol import AgentProtocolError, seed_project_env
from devlegate.daemon import run_service
from devlegate.ipc_client import (
    IPCClientError,
    decode_plan,
    decode_reconcile_ack,
    decode_reconcile_control_ack,
    decode_reconcile_resume_ack,
    decode_retry_ack,
    decode_retry_candidates,
    decode_status,
    request,
)
from devlegate.runtime import (
    DevlegateError,
    ExecutionPlan,
    StatusSnapshot,
    WorkflowBlockedError,
)
from devlegate.runtime_locator import (
    RuntimeAuthorityPresent,
    RuntimeLocator,
    RuntimeLocatorError,
)
from devlegate.service import ServiceEngine


def _todo_fingerprint(repo: Path, todo_path: str) -> tuple[str, int]:
    return _runtime._todo_fingerprint(repo, todo_path)


# Keep the historical helper imports available to callers of devlegate.cli.
_preserve_terminal = _runtime._preserve_terminal
_run_opencode = _runtime._run_opencode
MAX_STDOUT_EVENT_BYTES = _runtime.MAX_STDOUT_EVENT_BYTES
_git = _runtime._git


def _service_engine(env_file: Path, *, read_only: bool = False) -> ServiceEngine:
    """Construct the canonical service engine."""
    return ServiceEngine(env_file, read_only=read_only)


def _read_only_view(env_file: Path, method: str) -> StatusSnapshot | ExecutionPlan:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error

    response: dict[str, object] | None = None
    ipc_error: IPCClientError | None = None
    try:
        response = request(locator.socket_path, method)
    except IPCClientError as error:
        ipc_error = error

    try:
        guard = locator.absence_guard()
        with guard:
            engine = _service_engine(env_file, read_only=True)
            if method == "status":
                view = getattr(engine, "status_view", engine.status)
            else:
                view = getattr(engine, "plan_view", engine.plan)
            return view()
    except RuntimeAuthorityPresent as error:
        if ipc_error is not None:
            if ipc_error.application:
                raise DevlegateError(str(ipc_error)) from ipc_error
            raise DevlegateError(str(error)) from ipc_error
        assert response is not None
        try:
            return (
                decode_status(response) if method == "status" else decode_plan(response)
            )
        except IPCClientError as decode_error:
            raise DevlegateError(str(decode_error)) from decode_error
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error


def _interactive_terminal() -> bool:
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (OSError, ValueError):
        return False


def _retry_daemon(env_file: Path, ticket_id: str | None) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    if ticket_id is None and not _interactive_terminal():
        raise DevlegateError(
            "interactive retry requires a terminal; specify a ticket ID:\n"
            "devlegate retry <ticket-id>"
        )
    if ticket_id is not None and not ticket_id:
        raise DevlegateError("retry ticket ID must be non-empty")
    try:
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service is not running for this checkout; start `devlegate`"
            )
        if ticket_id is None:
            candidates = decode_retry_candidates(
                request(locator.socket_path, "retry-candidates")
            )
            if not candidates:
                raise DevlegateError(
                    "no current executions are retryable or recoverable"
                )
            print("Retry candidates:")
            for index, candidate in enumerate(candidates, 1):
                print(f"  {index}) {candidate['id']}  {candidate['title']}")
                print(f"     {candidate['reason']}")
            print("  0) Cancel")
            answer = input("Select number (Enter = cancel): ").strip()
            if not answer or answer == "0":
                return 0
            try:
                index = int(answer)
                if not 1 <= index <= len(candidates):
                    raise ValueError
            except ValueError as error:
                raise DevlegateError("invalid retry selection") from error
            ticket_id = candidates[index - 1]["id"]
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service stopped before retry was submitted"
            )
        result = request(
            locator.socket_path,
            "retry",
            {"ticket_id": ticket_id},
            mutable=True,
        )
        decode_retry_ack(result, ticket_id)
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    print(f"retry accepted: {ticket_id}")
    return 0


def _reconcile_daemon(env_file: Path, ticket_id: str, onto: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service is not running for this checkout; start `devlegate`"
            )
        result = request(
            locator.socket_path,
            "reconcile-update-base",
            {"ticket_id": ticket_id, "onto": onto},
            mutable=True,
        )
        decode_reconcile_ack(result, ticket_id, onto)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    print(f"reconciliation accepted: {ticket_id}")
    return 0


def _reconcile_resume_daemon(env_file: Path, ticket_id: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service is not running for this checkout; start `devlegate`"
            )
        result = request(
            locator.socket_path,
            "reconcile-resume",
            {"ticket_id": ticket_id},
            mutable=True,
        )
        decode_reconcile_resume_ack(result, ticket_id)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    print(f"reconciliation resume accepted: {ticket_id}")
    return 0


def _reconcile_control(env_file: Path, from_head: str, to_head: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service is not running for this checkout; start `devlegate`"
            )
        result = request(
            locator.socket_path,
            "reconcile-control",
            {"from": from_head, "to": to_head},
            mutable=True,
        )
        decode_reconcile_control_ack(result, from_head, to_head)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    print(f"control reconciliation accepted: {from_head} -> {to_head}")
    return 0


def _stop_service(env_file: Path) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    if not locator.daemon_authority_present():
        raise DevlegateError("service is not running")
    try:
        response = request(locator.socket_path, "stop", mutable=True)
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    if set(response) != {"accepted"} or response["accepted"] is not True:
        raise DevlegateError("service IPC returned invalid stop acknowledgement")
    print("service stop accepted")
    return 0


def _healthy_service(env_file: Path) -> bool:
    locator = RuntimeLocator.from_env(env_file)
    if not locator.daemon_authority_present():
        return False
    try:
        response = request(locator.socket_path, "ping")
    except IPCClientError as error:
        raise DevlegateError(
            "service authority exists but its IPC endpoint is unavailable"
        ) from error
    if response != {"service": "devlegate", "protocol_version": 1}:
        raise DevlegateError("service authority exists but its IPC health is invalid")
    return True


def _startup_fd() -> int | None:
    value = os.environ.get("DEVLEGATE_STARTUP_FD")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _notify_startup_failure(error: BaseException) -> None:
    fd = _startup_fd()
    if fd is None:
        return
    try:
        os.write(fd, f"FAILED {error}\n".encode("utf-8"))
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _start_background(env_file: Path) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
        log_path = locator.state_dir / "logs" / f"{locator.state_key}.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    except OSError as error:
        raise DevlegateError(
            f"cannot prepare service log {log_path}: {error}"
        ) from error

    read_fd, write_fd = os.pipe()
    environment = {
        **os.environ,
        "DEVLEGATE_STARTUP_FD": str(write_fd),
    }
    command = [
        sys.executable,
        "-m",
        "devlegate",
        "foreground",
        "--env",
        str(env_file),
    ]
    child: subprocess.Popen[bytes] | None = None
    try:
        with log_path.open("a", encoding="utf-8") as log:
            child = subprocess.Popen(
                command,
                cwd=locator.repo,
                env=environment,
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
                start_new_session=True,
                close_fds=True,
                pass_fds=(write_fd,),
            )
    except OSError as error:
        os.close(read_fd)
        raise DevlegateError(f"cannot start background service: {error}") from error
    finally:
        os.close(write_fd)

    assert child is not None
    deadline = time.monotonic() + 10
    message = b""
    try:
        while time.monotonic() < deadline:
            remaining = max(0, deadline - time.monotonic())
            readable, _writeable, _exceptional = select.select(
                [read_fd], [], [], remaining
            )
            if readable:
                message += os.read(read_fd, 4096)
                if b"\n" in message:
                    break
            if child.poll() is not None:
                break
        line = message.splitlines()[0].decode("utf-8", "replace") if message else ""
        if line == "READY" and child.poll() is None:
            print(f"service started; log: {log_path}")
            return 0
        if child.poll() is None:
            child.terminate()
            try:
                child.wait(timeout=5)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait(timeout=5)
        detail = line.removeprefix("FAILED ") or "service exited before readiness"
        raise DevlegateError(f"service startup failed: {detail}; log: {log_path}")
    finally:
        os.close(read_fd)


def _render_status_text(snapshot: StatusSnapshot) -> str:
    code = snapshot.code
    control = snapshot.control
    lines = [
        f"Devlegate {__version__}",
        "",
        "Execution:",
        f"  phase: {snapshot.phase}",
        f"  bound ticket: {snapshot.bound_ticket_id or 'none'}",
        (
            f"  persisted body: {'yes' if snapshot.persisted_body_present else 'no'}"
            if snapshot.bound_ticket_id
            else ""
        ),
        "",
        "Code plane:",
        f"  branch: {code.branch or '<detached>'}",
        f"  local HEAD: {code.local_head}",
        f"  known remote: {code.remote_ref}",
        f"  known remote HEAD: {code.remote_head or '<unknown>'}",
        f"  working tree: {'clean' if code.working_tree_clean else 'dirty'}",
        "Control plane:",
        (
            "  worktree: missing (run 'devlegate control init')"
            if control is None
            else f"  branch: {control.branch or '<detached>'}"
        ),
        "",
        "Tickets:",
    ]
    if control is not None:
        lines.extend(
            [
                f"  local HEAD: {control.local_head}",
                f"  known remote: {control.remote_ref}",
                f"  known remote HEAD: {control.remote_head or '<unknown>'}",
                f"  working tree: {'clean' if control.working_tree_clean else 'dirty'}",
            ]
        )
    lines.extend(f"  {state}: {count}" for state, count in snapshot.counts)
    lines.extend(["", "Runnable:"])
    lines.extend(f"  {ticket_id}  {title}" for ticket_id, title in snapshot.runnable)
    if not snapshot.runnable:
        lines.append("  none")
    lines.append("Failed executions:")
    lines.extend(
        f"  {failure.ticket_id}  execution: failed  "
        f"retryable: {'yes' if failure.retryable else 'no'}  reason: "
        f"{failure.display_reason}"
        for failure in snapshot.failed_executions
    )
    if not snapshot.failed_executions:
        lines.append("  none")
    if (
        snapshot.reconciliation is not None
        and snapshot.reconciliation.get("status") == "pending"
    ):
        reconciliation = snapshot.reconciliation
        product_branch = reconciliation.get("product_branch", "") or "detached"
        product_local_head = reconciliation.get("product_local_head", "")
        product_dirty = reconciliation.get("product_dirty", False)
        target_eligible = reconciliation.get("product_target_eligible", False)
        product_observation = reconciliation.get("product_observation", "")
        lines.extend(
            [
                "Reconciliation required:",
                f"  ticket: {reconciliation['ticket_id']}",
                f"  original base: {reconciliation['original_base']}",
                f"  observed product: {reconciliation['observed_product']}",
                f"  worker checkpoint: {reconciliation['worker_checkpoint']}",
                f"  product branch: {product_branch}",
                f"  product local HEAD: {product_local_head}",
                f"  product dirty: {product_dirty}",
                f"  update-base eligible: {target_eligible}",
                f"  product observation: {product_observation}",
            ]
        )
    lines.append("Blocked:")
    if snapshot.blocked:
        for ticket_id, title, blockers in snapshot.blocked:
            lines.append(f"  {ticket_id}  {title}")
            lines.extend(
                f"    by {dependency_id} [{state}]" for dependency_id, state in blockers
            )
    else:
        lines.append("  none")
    lines.append("Review:")
    lines.extend(f"  {ticket_id}  {title}" for ticket_id, title in snapshot.review)
    if not snapshot.review:
        lines.append("  none")
    lines.append("Accepted:")
    lines.extend(f"  {ticket_id}  {title}" for ticket_id, title in snapshot.accepted)
    if not snapshot.accepted:
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


def _render_plan_text(plan: ExecutionPlan) -> str:
    lines = [f"Devlegate {__version__}", "Execution plan:", f"  action: {plan.action}"]
    if plan.ticket_id is not None:
        lines.extend(
            [
                f"  ticket: {plan.ticket_id}  {plan.ticket_title}",
                f"  ticket state: {plan.ticket_state}",
            ]
        )
    if plan.code:
        lines.extend(
            [
                f"  code branch: {plan.code.branch or '<detached>'}",
                f"  code local HEAD: {plan.code.local_head}",
                f"  code known remote: {plan.code.remote_ref}",
                f"  code known remote HEAD: {plan.code.remote_head or '<unknown>'}",
            ]
        )
    if plan.control:
        lines.extend(
            [
                f"  control branch: {plan.control.branch or '<detached>'}",
                f"  control local HEAD: {plan.control.local_head}",
                f"  control known remote: {plan.control.remote_ref}",
                "  control known remote HEAD: "
                f"{plan.control.remote_head or '<unknown>'}",
            ]
        )
    else:
        lines.append("  control worktree: missing")
    lines.extend(
        [f"  reason: {plan.reason}", f"  bound: {'yes' if plan.bound else 'no'}"]
    )
    return "\n".join(lines)


def _short_git_identity(repo: Path) -> tuple[str, str]:
    branch = _git(repo, "symbolic-ref", "--short", "HEAD", check=False)
    head = _git(repo, "rev-parse", "HEAD", check=False)
    return branch.stdout.strip() or "<detached>", head.stdout.strip()[:12]


def _startup_report(engine: ServiceEngine, mode: str) -> None:
    product_branch, product_head = _short_git_identity(engine.repo)
    control_repo = getattr(engine, "control_worktree", None)
    if isinstance(control_repo, Path) and control_repo.is_dir():
        control_branch, control_head = _short_git_identity(control_repo)
        control = f"{control_branch} @ {control_head}"
    else:
        control = f"{engine.control_branch} @ <unavailable>"
    print(
        "=========================================================================",
        flush=True,
    )
    print("           D | L", flush=True)
    print("---< D E V L E G A T E >---", flush=True)
    print("          S.P.Q.R.", flush=True)
    print("", flush=True)
    print(f"version : {__version__}", flush=True)
    print(f"repo    : {engine.repo}", flush=True)
    print(f"product : {product_branch} @ {product_head}", flush=True)
    print(f"control : {control}", flush=True)
    print(f"mode    : {mode}", flush=True)
    print(f"pid     : {os.getpid()}", flush=True)
    print(f"instance: {engine._state_key[:12]}", flush=True)
    print(
        "=========================================================================",
        flush=True,
    )


class DevlegateArgumentParser(argparse.ArgumentParser):
    """Present syntax errors concisely while retaining argparse parsing."""

    _top_level_groups = (
        (
            "Service",
            (
                ("foreground", "run the persistent service attached to this terminal"),
                ("once", "run one service pass, then exit"),
                ("stop", "stop the persistent service"),
                ("status", "show current workflow status"),
                ("plan", "show the next workflow plan"),
            ),
        ),
        (
            "Project",
            (
                ("init", "initialize project-local workflow files"),
                ("render", "render project-local workflow files"),
                ("check", "validate setup readiness"),
                ("control", "manage workflow history"),
            ),
        ),
        (
            "Recovery",
            (
                ("retry", "retry a failed or recoverable execution"),
                ("reconcile", "perform explicit reconciliation"),
            ),
        ),
        ("Other", (("version", "show program version"),)),
    )

    def format_usage(self) -> str:
        if self.prog == "devlegate":
            return "usage: devlegate [--env FILE]\n  devlegate COMMAND ...\n"
        return super().format_usage()

    def format_help(self) -> str:
        if self.prog == "devlegate":
            lines = [
                "usage: devlegate [--env FILE]",
                "  devlegate COMMAND ...",
                "",
                "Run ticket-driven coding workflows in a Git repository. With no "
                "command,",
                "ensure the persistent background service is running.",
                "",
                "Options:",
                "  --env FILE       configuration file for bare background startup",
            ]
            for title, commands in self._top_level_groups:
                lines.extend(["", f"{title}:"])
                lines.extend(
                    f"  {name:<15}{description}" for name, description in commands
                )
            lines.append("")
            return "\n".join(lines)
        result = super().format_help()
        return result

    def parse_known_args(self, args=None, namespace=None):
        if self.prog == "devlegate":
            values = list(sys.argv[1:] if args is None else args)
            choices = next(
                action.choices
                for action in self._subparsers._group_actions
                if action.dest == "command"
            )
            self._command_context = (
                values[0] if values and values[0] in choices else None
            )
        return super().parse_known_args(args, namespace)

    def error(self, message: str) -> NoReturn:
        if self.prog == "devlegate" and ": invalid choice: " in message:
            choice = message.split(": invalid choice: ", 1)[1]
            choice = choice.split(" (choose from", 1)[0]
            message = f"unknown command {choice}"
        elif message.startswith("unrecognized arguments: "):
            message = "unrecognized argument: " + message[
                len("unrecognized arguments: ") :
            ]
        program = self.prog
        command = getattr(self, "_command_context", None)
        if program == "devlegate" and command is not None:
            program = f"{program} {command}"
        print(f"{program}: {message}", file=sys.stderr)
        print(f"Try '{program} --help' for usage.", file=sys.stderr)
        raise SystemExit(2)


class Devlegate(ServiceEngine):
    """Legacy CLI-facing runtime surface; presentation remains here."""

    def _render_status_text(self, snapshot: StatusSnapshot) -> str:
        return _render_status_text(snapshot)

    def run_once(self, stop_event=None) -> int:
        # Preserve the historical CLI test hook while keeping runtime independent.
        runtime_git = _runtime._git
        _runtime._git = _git
        try:
            return super().run_once(stop_event)
        finally:
            _runtime._git = runtime_git

    def _run_worker(self, workspace, prompt):
        runtime_runner = _runtime._run_opencode
        _runtime._run_opencode = _run_opencode
        try:
            return super()._run_worker(workspace, prompt)
        finally:
            _runtime._run_opencode = runtime_runner

    def status(self, json_output: bool = False) -> int:
        snapshot = self.status_view()
        print(
            json.dumps(snapshot.as_dict(), indent=2, sort_keys=True)
            if json_output
            else _render_status_text(snapshot)
        )
        return 1 if snapshot.plan.action == "blocked" else 0

    def plan(self, json_output: bool = False) -> int:
        plan = self.plan_view()
        print(
            json.dumps(plan.as_dict(), indent=2, sort_keys=True)
            if json_output
            else _render_plan_text(plan)
        )
        return 0


def build_parser() -> argparse.ArgumentParser:
    parser = DevlegateArgumentParser(
        prog="devlegate",
        description=(
            "Run ticket-driven coding workflows in a Git repository. With no "
            "command, ensure the persistent background service is running."
        ),
    )
    parser.add_argument(
        "--env",
        dest="service_env",
        metavar="FILE",
        type=Path,
        help="configuration file for bare background startup",
    )
    commands = parser.add_subparsers(
        dest="command",
        title="commands",
        metavar="COMMAND",
        parser_class=DevlegateArgumentParser,
    )
    foreground_parser = commands.add_parser(
        "foreground",
        help="run the persistent service attached to this terminal",
        description="Run the persistent service attached to this terminal.",
    )
    foreground_parser.add_argument("--env", metavar="FILE", type=Path)
    once_parser = commands.add_parser(
        "once",
        help="run one service pass attached to this terminal",
        description="Run one service pass attached to this terminal, then exit.",
    )
    once_parser.add_argument("--env", metavar="FILE", type=Path)
    commands.add_parser(
        "version",
        help="show program version",
        description="Show the concise program and version identity.",
    )
    init_parser = commands.add_parser(
        "init",
        help="initialize project-local agent workflow files",
        description="Create missing project-local agent workflow files.",
    )
    init_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    init_parser.add_argument(
        "--conflicts",
        choices=("abort", "backup", "replace"),
        default="abort",
        help="how to handle existing generated files: abort, backup, or replace",
    )
    render_parser = commands.add_parser(
        "render",
        help="render project-local agent workflow files",
        description="Render project-local agent workflow files from their templates.",
    )
    render_parser.add_argument(
        "--check", action="store_true", help="check freshness without writing files"
    )
    render_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    stop_parser = commands.add_parser(
        "stop",
        help="orderly stop the persistent workflow service",
        description="Request an orderly shutdown of the persistent service.",
    )
    stop_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    check_parser = commands.add_parser(
        "check",
        help="validate setup readiness",
        description="Validate project setup without running a worker.",
    )
    check_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    for name in ("status", "plan"):
        command_parser = commands.add_parser(
            name,
            help=(
                "show current workflow status"
                if name == "status"
                else "show the next workflow plan"
            ),
            description=(
                "Show the current workflow status."
                if name == "status"
                else "Show what Devlegate plans to do next."
            ),
        )
        command_parser.add_argument(
            "--json", action="store_true", help="emit machine-readable JSON"
        )
        command_parser.add_argument(
            "--env",
            metavar="FILE",
            type=Path,
            help="configuration file to use instead of $PWD/.env",
        )
    retry_parser = commands.add_parser(
        "retry",
        help="retry a failed or recoverable execution",
        description="Retry a failed or recoverable ticket execution.",
    )
    retry_parser.add_argument(
        "ticket_id",
        nargs="?",
        help="ticket to retry; omit it to choose from current candidates",
    )
    retry_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    reconcile_parser = commands.add_parser(
        "reconcile",
        help="handle pending product-base changes",
        description="Handle a pending product-base change for an execution.",
    )
    reconcile_commands = reconcile_parser.add_subparsers(
        dest="reconcile_command", parser_class=DevlegateArgumentParser
    )
    update_base_parser = reconcile_commands.add_parser(
        "update-base",
        help="update an execution to a new product base",
        description="Update a ticket execution after the product base changes.",
    )
    update_base_parser.add_argument("ticket_id", help="ticket execution to update")
    update_base_parser.add_argument(
        "--onto", required=True, help="product branch to use as the new base"
    )
    resume_parser = reconcile_commands.add_parser(
        "resume",
        help="resume retained execution progress on the same product base",
        description=(
            "Resume retained execution progress without changing the product base."
        ),
    )
    resume_parser.add_argument("ticket_id", help="ticket execution to resume")
    resume_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    update_base_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    control_reconcile_parser = reconcile_commands.add_parser(
        "control",
        help="adopt an explicitly authorized divergent control history",
        description="Adopt an exact externally rewritten control history.",
    )
    control_reconcile_parser.add_argument(
        "--from", dest="from_head", required=True, help="expected local control HEAD"
    )
    control_reconcile_parser.add_argument(
        "--to",
        dest="to_head",
        required=True,
        help="expected fetched remote control HEAD",
    )
    control_reconcile_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    control_parser = commands.add_parser(
        "control",
        help="manage workflow history",
        description="Manage the separate Git history that stores workflow data.",
    )
    control_commands = control_parser.add_subparsers(
        dest="control_command", parser_class=DevlegateArgumentParser
    )
    init_parser = control_commands.add_parser(
        "init",
        help="initialize workflow history",
        description="Initialize or attach the separate workflow Git history.",
    )
    init_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args(sys.argv[1:])
    startup_fd = _startup_fd()
    if args.command is None:
        env_file = args.service_env or Path.cwd() / ".env"
        try:
            if _healthy_service(env_file):
                print("Devlegate service is already running.")
                return 0
            return _start_background(env_file)
        except KeyboardInterrupt:
            _notify_startup_failure(KeyboardInterrupt())
            return 130
        except DevlegateError as error:
            _notify_startup_failure(error)
            print(f"devlegate: {error}", file=sys.stderr)
            return 1
    if args.command == "version":
        if args.service_env is not None:
            parser.error("--env is only valid for bare background startup")
        print(f"devlegate {__version__}")
        return 0
    if args.command in {"foreground", "once"}:
        if args.service_env is not None:
            parser.error("--env is only valid for bare background startup")
        env_file = args.env or Path.cwd() / ".env"
        try:
            if _healthy_service(env_file):
                print("Devlegate service is already running.")
                return 0
            engine = _service_engine(env_file)
            return run_service(
                engine,
                once=args.command == "once",
                startup_fd=startup_fd,
                startup_report=lambda: _startup_report(
                    engine,
                    "background" if startup_fd is not None else args.command,
                ),
            )
        except KeyboardInterrupt:
            _notify_startup_failure(KeyboardInterrupt())
            return 130
        except DevlegateError as error:
            _notify_startup_failure(error)
            print(f"devlegate: {error}", file=sys.stderr)
            return 1
    if args.command == "control" and args.control_command != "init":
        DevlegateArgumentParser(prog="devlegate control").error(
            "a control command is required"
        )
    if args.command == "reconcile" and args.reconcile_command not in {
        "update-base",
        "control",
    }:
        DevlegateArgumentParser(prog="devlegate reconcile").error(
            "a reconcile command is required"
        )
    if args.service_env is not None:
        parser.error("--env before a command is only valid for bare background startup")
    env_file = args.env or Path.cwd() / ".env"
    try:
        project_env = Path.cwd() / ".env"
        if args.command == "init":
            repository_root = ServiceEngine._repository_root()
            if repository_root != Path.cwd().resolve():
                raise DevlegateError(
                    f"run devlegate from repository root: {repository_root}"
                )
        if args.command == "init" and args.env is None and not project_env.exists():
            try:
                seed_project_env(project_env)
            except AgentProtocolError as error:
                raise DevlegateError(str(error)) from error
        devlegate = None
        if args.command in {"init", "render", "check", "control"}:
            devlegate = Devlegate(env_file, read_only=True)
        if args.command == "init":
            return devlegate.init_project(args.conflicts)
        if args.command == "render":
            return devlegate.render(args.check)
        if args.command == "control":
            return devlegate.control_init()
        if args.command == "status":
            snapshot = _read_only_view(env_file, "status")
            print(
                json.dumps(snapshot.as_dict(), indent=2, sort_keys=True)
                if args.json
                else _render_status_text(snapshot)
            )
            return 1 if snapshot.plan.action == "blocked" else 0
        if args.command == "plan":
            plan = _read_only_view(env_file, "plan")
            print(
                json.dumps(plan.as_dict(), indent=2, sort_keys=True)
                if args.json
                else _render_plan_text(plan)
            )
            return 0
        if args.command == "check":
            return devlegate.check()
        if args.command == "stop":
            return _stop_service(env_file)
        if args.command == "retry":
            return _retry_daemon(env_file, args.ticket_id)
        if args.command == "reconcile":
            if args.reconcile_command == "control":
                return _reconcile_control(env_file, args.from_head, args.to_head)
            if args.reconcile_command == "resume":
                return _reconcile_resume_daemon(env_file, args.ticket_id)
            return _reconcile_daemon(env_file, args.ticket_id, args.onto)
    except KeyboardInterrupt:
        _notify_startup_failure(KeyboardInterrupt())
        return 130
    except DevlegateError as error:
        _notify_startup_failure(error)
        if args.command == "check":
            print(f"Devlegate {__version__} preflight")
            print(f"FAIL  configuration: {error}\n\nNot ready.")
            return 1
        if isinstance(error, WorkflowBlockedError):
            print(f"devlegate: workflow blocked: {error}", file=sys.stderr)
        else:
            print(f"devlegate: {error}", file=sys.stderr)
        return 1
