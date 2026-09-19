# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Command-line interface for Devlegate."""

import argparse
import dataclasses
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
from devlegate.output import add_output_arguments, emit, render_table
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


@dataclasses.dataclass(frozen=True)
class ReadOnlyView:
    value: StatusSnapshot | ExecutionPlan
    service_state: str


def _read_only_view(env_file: Path, method: str) -> ReadOnlyView:
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
            value = view()
            return ReadOnlyView(value, "stopped")
    except RuntimeAuthorityPresent as error:
        if ipc_error is not None:
            if ipc_error.application:
                raise DevlegateError(str(ipc_error)) from ipc_error
            raise DevlegateError(str(error)) from ipc_error
        assert response is not None
        try:
            value = (
                decode_status(response)
                if method == "status"
                else decode_plan(response)
            )
            return ReadOnlyView(value, "running")
        except IPCClientError as decode_error:
            raise DevlegateError(str(decode_error)) from decode_error
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error


def _interactive_terminal() -> bool:
    try:
        return os.isatty(sys.stdin.fileno()) and os.isatty(sys.stdout.fileno())
    except (OSError, ValueError):
        return False


def _retry_daemon(env_file: Path, ticket_id: str | None, output_format: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    if ticket_id is None and not _interactive_terminal():
        raise DevlegateError(
            "interactive retry requires a terminal; specify a ticket ID:\n"
            "devlegate retry <ticket-id>"
        )
    if ticket_id is None and output_format != "table":
        raise DevlegateError(
            "machine-readable retry requires a ticket ID; "
            "specify devlegate retry <ticket-id>"
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
    result = {"result": "accepted", "ticket_id": ticket_id}
    emit(
        result,
        output_format,
        render_table(f"retry accepted: {ticket_id}", result.items()),
    )
    return 0


def _reconcile_daemon(
    env_file: Path, ticket_id: str, onto: str, output_format: str
) -> int:
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
    result = {"result": "accepted", "ticket_id": ticket_id, "onto": onto}
    emit(
        result,
        output_format,
        render_table(f"reconciliation accepted: {ticket_id}", result.items()),
    )
    return 0


def _reconcile_resume_daemon(
    env_file: Path, ticket_id: str, output_format: str = "table"
) -> int:
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
    result = {"result": "accepted", "ticket_id": ticket_id}
    emit(
        result,
        output_format,
        render_table(f"reconciliation resume accepted: {ticket_id}", result.items()),
    )
    return 0


def _reconcile_control(
    env_file: Path, from_head: str, to_head: str, output_format: str
) -> int:
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
    result = {"result": "accepted", "from": from_head, "to": to_head}
    emit(
        result,
        output_format,
        render_table("control reconciliation accepted", result.items()),
    )
    return 0


def _stop_service(env_file: Path, output_format: str) -> int:
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
    result = {"result": "accepted", "service": "devlegate", "action": "stop"}
    emit(result, output_format, render_table("service stop accepted", result.items()))
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


def _render_status_text(
    snapshot: StatusSnapshot, service_state: str
) -> str:
    code = snapshot.code
    control = snapshot.control

    def list_table(title: str, rows: tuple[tuple[str, str], ...], empty: str) -> str:
        return render_table(title, rows or ((empty, "none"),))

    lines = [
        f"Devlegate {__version__}",
        "",
        render_table("Service", (("State", service_state),)),
        "",
        render_table(
            "Execution:",
            (
                ("Phase", snapshot.phase),
                ("Bound ticket", snapshot.bound_ticket_id),
                ("Persisted body", snapshot.persisted_body_present),
            ),
        ),
        "",
        render_table(
            "Code plane:",
            (
                ("Branch", code.branch or "<detached>"),
                ("Local HEAD", code.local_head),
                ("Known remote", code.remote_ref),
                ("Known remote HEAD", code.remote_head or "<unknown>"),
                ("Working tree", "clean" if code.working_tree_clean else "dirty"),
            ),
        ),
        "",
        render_table(
            "Control plane:",
            (
                (("Worktree", "missing (run 'devlegate control init')"),)
                if control is None
                else (
                    ("Branch", control.branch or "<detached>"),
                    ("Local HEAD", control.local_head),
                    ("Known remote", control.remote_ref),
                    ("Known remote HEAD", control.remote_head or "<unknown>"),
                    (
                        "Working tree",
                        "clean" if control.working_tree_clean else "dirty",
                    ),
                )
            ),
        ),
        "",
        render_table("Tickets:", snapshot.counts),
        "",
        list_table("Runnable:", snapshot.runnable, "Tickets"),
        "",
        list_table(
            "Failed executions:",
            tuple(
                (
                    failure.ticket_id,
                    "execution: failed; "
                    f"retryable: {'yes' if failure.retryable else 'no'}; "
                    f"reason: {failure.display_reason}",
                )
                for failure in snapshot.failed_executions
            ),
            "Executions",
        ),
    ]
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
                "",
                render_table(
                    "Reconciliation required:",
                    (
                        ("Ticket", reconciliation["ticket_id"]),
                        ("Original base", reconciliation["original_base"]),
                        ("Observed product", reconciliation["observed_product"]),
                        ("Worker checkpoint", reconciliation["worker_checkpoint"]),
                        ("Product branch", product_branch),
                        ("Product local HEAD", product_local_head),
                        ("Product dirty", product_dirty),
                        ("Update-base eligible", target_eligible),
                        ("Product observation", product_observation),
                    ),
                ),
            ]
        )
    lines.extend(
        [
            "",
            list_table(
                "Blocked:",
                tuple(
                    (
                        ticket_id,
                        f"{title}; blocked by "
                        + ", ".join(
                            f"{dependency_id} [{state}]"
                            for dependency_id, state in blockers
                        ),
                    )
                    for ticket_id, title, blockers in snapshot.blocked
                ),
                "Tickets",
            ),
            "",
            list_table("Review:", snapshot.review, "Tickets"),
            "",
            list_table("Accepted:", snapshot.accepted, "Tickets"),
            "",
            render_table(
                "Next:",
                (
                    ("Ticket", snapshot.next_ticket[0]),
                    ("Title", snapshot.next_ticket[1]),
                )
                if snapshot.next_ticket
                else (("Ticket", "none"),),
            ),
        ]
    )
    return "\n".join(lines)


def _render_plan_text(plan: ExecutionPlan) -> str:
    lines = [
        f"Devlegate {__version__}",
        render_table(
            "Execution plan:",
            (("Action", plan.action), ("Reason", plan.reason)),
        ),
    ]
    if plan.ticket_id is not None:
        lines.extend(
            [
                "",
                render_table(
                    "Ticket",
                    (
                        ("ID", plan.ticket_id),
                        ("Title", plan.ticket_title),
                        ("State", plan.ticket_state),
                    ),
                ),
            ]
        )
    if plan.code:
        lines.extend(
            [
                "",
                render_table(
                    "Code",
                    (
                        ("Branch", plan.code.branch or "<detached>"),
                        ("Local HEAD", plan.code.local_head),
                        ("Known remote", plan.code.remote_ref),
                        ("Known remote HEAD", plan.code.remote_head or "<unknown>"),
                    ),
                ),
            ]
        )
    if plan.control:
        lines.extend(
            [
                "",
                render_table(
                    "Control",
                    (
                        ("Branch", plan.control.branch or "<detached>"),
                        ("Local HEAD", plan.control.local_head),
                        ("Known remote", plan.control.remote_ref),
                        ("Known remote HEAD", plan.control.remote_head or "<unknown>"),
                    ),
                ),
            ]
        )
    else:
        lines.extend(["", render_table("Control", (("Worktree", "missing"),))])
    lines.extend(["", render_table("Execution", (("Bound", plan.bound),))])
    return "\n".join(lines)


def _render_check_text(result: dict[str, object]) -> str:
    lines = [
        f"Devlegate {__version__} preflight",
        "",
        render_table(
            "Ready." if result["ready"] else "Not ready.",
            (("State", "ready" if result["ready"] else "not ready"),),
        ),
        "",
        render_table(
            "Checks:",
            tuple(
                (
                    check["name"],
                    ("OK" if check["passed"] else "FAIL")
                    + (f": {check['detail']}" if check["detail"] else ""),
                )
                for check in result["checks"]
            ),
        ),
    ]
    labels = (
        ("local_head", "local HEAD"),
        ("known_remote_head", "known remote HEAD"),
        ("ahead_behind", "ahead/behind"),
        ("todo_files", "todo files"),
        ("todo_fingerprint", "todo fingerprint"),
        ("work_generation_differs", "work generation differs from persisted"),
    )
    lines.extend(
        [
            "",
            render_table(
                "Repository diagnostics:",
                tuple(
                    (label, _display_output_value(result[key]))
                    for key, label in labels
                ),
            ),
        ]
    )
    if result.get("dirty_files"):
        lines.extend(
            [
                "",
                render_table(
                    "Dirty working tree details:",
                    tuple(
                        (str(index), line)
                        for index, line in enumerate(result["dirty_files"], 1)
                    ),
                ),
                "",
                render_table(
                    "Dirty summary:",
                    tuple(
                        (name.replace("_", " "), count)
                        for name, count in result["dirty_summary"].items()
                    ),
                ),
            ]
        )
    if result.get("ticket_counts") is not None:
        ticket_rows = list(result["ticket_counts"].items())
        ticket_rows.append(("Runnable", result["runnable_tickets"]))
        lines.extend(["", render_table("Ticket storage:", ticket_rows)])
        if result.get("next_runnable") is not None:
            lines.extend(
                [
                    "",
                    render_table(
                        "Next runnable:",
                        (
                            ("ID", result["next_runnable"]["id"]),
                            ("Title", result["next_runnable"]["title"]),
                        ),
                    ),
                ]
            )
    lines.extend(
        [
            "",
            render_table(
                "Control diagnostics:",
                (
                    ("Control HEAD", result["control_head"]),
                    ("Remote source", result["remote_note"]),
                ),
            ),
        ]
    )
    return "\n".join(lines)


def _display_output_value(value: object) -> str:
    if value is None:
        return "none"
    if isinstance(value, bool):
        return "yes" if value else "no"
    return str(value)


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

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("title", "Commands")
        kwargs.setdefault("metavar", "COMMAND")
        return super().add_subparsers(**kwargs)

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
        return _render_status_text(snapshot, "stopped")

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
            else _render_status_text(snapshot, "stopped")
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
    version_parser = commands.add_parser(
        "version",
        help="show program version",
        description="Show the concise program and version identity.",
    )
    add_output_arguments(version_parser)
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
    add_output_arguments(init_parser)
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
    add_output_arguments(render_parser)
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
    add_output_arguments(stop_parser)
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
    add_output_arguments(check_parser)
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
        add_output_arguments(command_parser)
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
    add_output_arguments(retry_parser)
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
    add_output_arguments(resume_parser)
    update_base_parser.add_argument(
        "--env",
        metavar="FILE",
        type=Path,
        help="configuration file to use instead of $PWD/.env",
    )
    add_output_arguments(update_base_parser)
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
    add_output_arguments(control_reconcile_parser)
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
    add_output_arguments(init_parser)
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
        value = {"program": "devlegate", "version": __version__}
        emit(
            value,
            args.output_format,
            render_table(
                "Devlegate",
                (("Program", "devlegate"), ("Version", __version__)),
            ),
        )
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
    if args.command == "reconcile" and args.reconcile_command is None:
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
            value = devlegate.init_project_result(args.conflicts)
            value = {**value, "command": "init"}
            emit(
                value,
                args.output_format,
                render_table(f"init (rendered {value['rendered']})", value.items()),
            )
            return 0
        if args.command == "render":
            value = devlegate.render_result(args.check)
            value = {**value, "command": "render"}
            emit(value, args.output_format, render_table("render", value.items()))
            return 0
        if args.command == "control":
            value = devlegate.control_init_result()
            value = {**value, "command": "control init"}
            emit(value, args.output_format, render_table("control init", value.items()))
            return 0
        if args.command == "status":
            view = _read_only_view(env_file, "status")
            snapshot = view.value
            assert isinstance(snapshot, StatusSnapshot)
            payload = {
                "service": {"state": view.service_state},
                **snapshot.as_dict(),
            }
            emit(
                payload,
                args.output_format,
                _render_status_text(snapshot, view.service_state),
            )
            return 1 if snapshot.plan.action == "blocked" else 0
        if args.command == "plan":
            view = _read_only_view(env_file, "plan")
            plan = view.value
            assert isinstance(plan, ExecutionPlan)
            emit(plan.as_dict(), args.output_format, _render_plan_text(plan))
            return 0
        if args.command == "check":
            result, check_result = devlegate.check_result()
            emit(
                check_result,
                args.output_format,
                _render_check_text(check_result),
            )
            return result
        if args.command == "stop":
            return _stop_service(env_file, args.output_format)
        if args.command == "retry":
            return _retry_daemon(env_file, args.ticket_id, args.output_format)
        if args.command == "reconcile":
            if args.reconcile_command == "control":
                return _reconcile_control(
                    env_file, args.from_head, args.to_head, args.output_format
                )
            if args.reconcile_command == "resume":
                if args.output_format == "table":
                    return _reconcile_resume_daemon(env_file, args.ticket_id)
                return _reconcile_resume_daemon(
                    env_file, args.ticket_id, args.output_format
                )
            return _reconcile_daemon(
                env_file, args.ticket_id, args.onto, args.output_format
            )
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
