# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Live worker process supervision and OpenCode execution."""

import dataclasses
import json
import os
import shutil
import signal
import subprocess
import sys
import tempfile
import termios
import threading
import time
from collections.abc import Callable, Mapping
from contextlib import contextmanager
from pathlib import Path

from devlegate.execution_containment import (
    ContainmentError,
    ContainmentProvider,
    default_containment_provider,
)
from devlegate.execution_workspace import ExecutionWorkspace
from devlegate.operational_log import ExecutionLog, open_execution_log, service_log
from devlegate.worker_egress import (
    OpenCodeRunResult,
    WorkerEgressParser,
    WorkerRunResult,
)


class WorkerSupervisionError(Exception):
    """A process-supervision failure that is not a workflow decision."""


class WorkerAdmissionClosed(WorkerSupervisionError):
    """A worker launch was rejected because the supervisor is draining."""


@dataclasses.dataclass(frozen=True)
class WorkerProcessIdentity:
    execution_id: str
    pid: int
    pgid: int
    sid: int
    boot_id: str
    start_time: int
    containment_id: str | None = None

    def as_dict(self) -> dict[str, object]:
        return dataclasses.asdict(self)


def _linux_boot_id() -> str:
    return Path("/proc/sys/kernel/random/boot_id").read_text().strip()


def _linux_process_start_time(pid: int) -> int:
    stat = Path(f"/proc/{pid}/stat").read_text()
    closing = stat.rfind(")")
    if closing < 0:
        raise WorkerSupervisionError("worker process identity is malformed")
    fields = stat[closing + 2 :].split()
    try:
        return int(fields[19])
    except (IndexError, ValueError) as error:
        raise WorkerSupervisionError("worker process identity is malformed") from error


def _capture_worker_identity(
    process, execution_id: str, containment_id: str | None = None
) -> WorkerProcessIdentity:
    if os.name != "posix" or not sys.platform.startswith("linux"):
        raise WorkerSupervisionError("strong worker process identity is unavailable")
    if process.poll() is not None:
        raise WorkerSupervisionError("worker exited before identity capture")
    pid = process.pid
    pgid = os.getpgid(pid)
    sid = os.getsid(pid)
    if pid <= 0 or pgid <= 0 or sid <= 0 or pgid != pid or sid != pid:
        raise WorkerSupervisionError("worker process group/session identity is invalid")
    boot_id = _linux_boot_id()
    start_time = _linux_process_start_time(pid)
    if not boot_id or start_time < 0:
        raise WorkerSupervisionError("worker process identity is incomplete")
    return WorkerProcessIdentity(
        execution_id, pid, pgid, sid, boot_id, start_time, containment_id
    )


def _worker_group_exists(pgid: int) -> bool:
    """Diagnostic-only legacy probe; never establishes execution ownership."""
    try:
        os.killpg(pgid, 0)
    except OSError:
        return False
    return True


def observe_worker_identity(identity: WorkerProcessIdentity) -> str:
    """Classify recorded worker ownership without mutating runtime state."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return "indeterminate"
    try:
        if _linux_boot_id() != identity.boot_id:
            return "absent"
        current_start = _linux_process_start_time(identity.pid)
    except WorkerSupervisionError:
        return "indeterminate"
    except OSError:
        # Legacy identities have no kernel ownership boundary to inspect.
        # A surviving group keeps recovery conservative; an absent group is
        # enough to classify the recorded leader as gone.
        return (
            "indeterminate"
            if _worker_group_exists(identity.pgid)
            else "absent"
        )
    if current_start != identity.start_time:
        return "absent"
    try:
        if (
            os.getpgid(identity.pid) != identity.pgid
            or os.getsid(identity.pid) != identity.sid
        ):
            return "indeterminate"
    except OSError:
        return "indeterminate"
    return "matching-live"


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


def _log(message: str) -> None:
    service_log(message)


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
    _write_rendered_worker_text(rendered, stream)


def _write_rendered_worker_text(rendered: str, stream) -> None:
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


# Caps raw JSONL events before decode and parsing while leaving normal events intact.
MAX_STDOUT_EVENT_BYTES = 1024 * 1024
WORKER_TERMINATION_TIMEOUT = 1.0
WORKER_WAIT_INTERVAL = 0.05


def _wait_process(process, timeout: float | None = None):
    """Wait for Popen-like objects without extending the process contract."""
    if timeout is None:
        return process.wait()
    try:
        return process.wait(timeout=timeout)
    except TypeError:
        # Preserve the requested bound for minimal test doubles that do not
        # accept Popen's timeout keyword.
        deadline = time.monotonic() + timeout
        while True:
            returncode = getattr(process, "returncode", None)
            if returncode is not None:
                return returncode
            if time.monotonic() >= deadline:
                raise subprocess.TimeoutExpired(process, timeout)
            time.sleep(min(WORKER_WAIT_INTERVAL, deadline - time.monotonic()))

_HOST_CONTROL_ENVIRONMENT = frozenset(
    {
        "NOTIFY_SOCKET",
        "WATCHDOG_PID",
        "WATCHDOG_USEC",
        "DEVLEGATE_HOST_MODE",
        "DEVLEGATE_REQUIRE_NOTIFY",
        "DEVLEGATE_STARTUP_FD",
        "DEVLEGATE_RESTART_AUTHORITY_FD",
        "DEVLEGATE_RESTART_AUTHORITY_KEY",
        "DEVLEGATE_RESTART_REQUEST",
        "DEVLEGATE_RESTART_INSTANCE",
        # Do not let a worker inherit the outer standalone launch identity.
        # Nested service restarts must resolve the worker's own checkpoint.
        "PEX",
        "SCIE",
        "SCIE_ARGV0",
        "__PEX_ENTRY_POINT__",
        "_PEX_CACHE_ACCESS_LOCK",
    }
)


def worker_environment(
    environment: Mapping[str, str] | None = None,
) -> dict[str, str]:
    """Copy the environment without service-owned process authority."""
    worker_env = dict(os.environ if environment is None else environment)
    for name in tuple(worker_env):
        if name in _HOST_CONTROL_ENVIRONMENT or name.startswith(
            ("PEX_", "SCIE_")
        ) or name.startswith("__PEX") or name.startswith("_PEX_"):
            worker_env.pop(name, None)
    return worker_env


def _run_opencode(
    command: list[str],
    prompt: str,
    *,
    cwd: Path | None = None,
    env: dict[str, str] | None = None,
    event_handler: Callable[[object], None] | None = None,
    stop_request: object | None = None,
    execution_id: str | None = None,
    worker_identity_handler: Callable[[WorkerProcessIdentity], None] | None = None,
    interruption_handler: Callable[[str], None] | None = None,
    execution_log: ExecutionLog | None = None,
    show_worker_output: bool = True,
    worker_started_handler: Callable[[], None] | None = None,
    containment=None,
) -> OpenCodeRunResult:
    """Run OpenCode headlessly and render its worker output as inert text."""
    try:
        if containment is not None:
            process = containment.spawn(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                cwd=cwd,
                env=env,
                start_new_session=True,
            )
    except (OSError, ContainmentError) as error:
        if containment is not None:
            try:
                containment.destroy()
            except (ContainmentError, OSError):
                pass
        return OpenCodeRunResult(
            -1, f"execution containment failed: {error}", None, False, False
        )
    if worker_started_handler is not None:
        worker_started_handler()
    output_lock = threading.Lock()
    transport_error: str | None = None
    prompt_error: str | None = None
    log_error: str | None = None

    def write(text: str, stream) -> None:
        nonlocal log_error
        with output_lock:
            rendered = _render_worker_text(text)
            if not rendered:
                return
            if execution_log is not None:
                try:
                    execution_log.write(rendered)
                except Exception as error:
                    log_error = f"execution log write failed: {error}"
                    if process.poll() is None:
                        try:
                            process.terminate()
                        except OSError:
                            pass
                    return
            if show_worker_output:
                _write_rendered_worker_text(rendered, stream)

    def consume_stdout() -> None:
        nonlocal transport_error
        assert process.stdout is not None
        while raw_line := process.stdout.readline(MAX_STDOUT_EVENT_BYTES + 1):
            if len(raw_line) > MAX_STDOUT_EVENT_BYTES:
                transport_error = "stdout event exceeds maximum size"
                _log(f"OpenCode protocol error: {transport_error}")
                if not raw_line.endswith(b"\n"):
                    while discarded := process.stdout.readline(
                        MAX_STDOUT_EVENT_BYTES + 1
                    ):
                        if discarded.endswith(b"\n"):
                            break
                continue
            try:
                event = json.loads(raw_line.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError):
                transport_error = "invalid JSON event on stdout"
                _log(f"OpenCode protocol error: {transport_error}")
                continue
            if event_handler is not None:
                event_handler(event)
            if (
                not isinstance(event, dict)
                or event.get("type") not in _OPENCODE_JSON_TYPES
            ):
                transport_error = "unsupported event on stdout"
                _log(f"OpenCode protocol error: {transport_error}")
                continue
            event_type = event["type"]
            if event_type == "text" or event_type == "reasoning":
                part = event.get("part")
                if not isinstance(part, dict) or not isinstance(part.get("text"), str):
                    transport_error = "text event has no text part"
                    _log(f"OpenCode protocol error: {transport_error}")
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
                    transport_error = "invalid tool event on stdout"
                    _log(f"OpenCode protocol error: {transport_error}")
                    continue
                tool = part["tool"]
                write(f"OpenCode tool: {tool}\n", sys.stdout)
                state = part["state"]
                if state.get("status") == "completed":
                    output = state.get("output")
                    if isinstance(output, str):
                        write(output, sys.stdout)
                elif state.get("status") == "error":
                    error = _extract_error_message(state.get("error"))
                    if error:
                        write(f"OpenCode tool failed: {error}", sys.stdout)
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
    stdin = getattr(process, "stdin", None)

    def deliver_prompt() -> None:
        nonlocal prompt_error
        if stdin is None:
            return
        try:
            remaining = prompt.encode("utf-8")
            while remaining:
                written = stdin.write(remaining)
                if not written:
                    raise OSError("worker stdin accepted no prompt bytes")
                remaining = remaining[written:]
            stdin.flush()
        except (BrokenPipeError, OSError, ValueError) as error:
            prompt_error = f"worker prompt delivery failed: {error}"
        finally:
            try:
                stdin.close()
            except (OSError, ValueError):
                pass

    prompt_thread = threading.Thread(target=deliver_prompt, daemon=True)
    prompt_thread.start()
    interruption_kind: str | None = None
    interruption_error: str | None = None
    worker_process_group = getattr(process, "pid", None)
    if worker_process_group is not None:
        try:
            worker_process_group = os.getpgid(process.pid)
        except (OSError, ProcessLookupError):
            worker_process_group = None

    def interrupt(kind: str) -> None:
        nonlocal interruption_error, interruption_kind
        if interruption_kind is not None:
            return
        if process.poll() is not None or worker_process_group is None:
            return
        if containment is not None:
            containment.request_graceful(process.pid)
        try:
            os.killpg(
                worker_process_group,
                signal.SIGINT if kind == "operator_abort" else signal.SIGTERM,
            )
        except (OSError, ProcessLookupError):
            return
        interruption_kind = kind
        if interruption_handler is not None:
            try:
                interruption_handler(kind)
            except Exception as error:
                interruption_error = f"interruption persistence failed: {error}"

    def finish_interrupted() -> int:
        try:
            returncode = _wait_process(process, WORKER_TERMINATION_TIMEOUT)
        except subprocess.TimeoutExpired:
            if containment is not None:
                containment.force_terminate()
            try:
                if worker_process_group is not None:
                    os.killpg(worker_process_group, signal.SIGKILL)
            except (OSError, ProcessLookupError):
                pass
            returncode = _wait_process(process)
        if containment is not None:
            containment.force_terminate()
        return returncode

    identity_error: str | None = None
    if worker_identity_handler is not None and execution_id is not None:
        if process.poll() is None:
            try:
                worker_identity_handler(
                    _capture_worker_identity(
                        process,
                        execution_id,
                        getattr(containment, "identity", None),
                    )
                )
            except Exception as error:
                if process.poll() is None:
                    identity_error = f"worker identity persistence failed: {error}"
                    try:
                        if worker_process_group is not None:
                            os.killpg(worker_process_group, signal.SIGTERM)
                    except (OSError, ProcessLookupError):
                        pass

    if identity_error is not None:
        returncode = finish_interrupted()
    elif stop_request is None:
        try:
            returncode = _wait_process(process)
        except KeyboardInterrupt:
            interrupt("operator_abort")
            returncode = finish_interrupted()
    else:
        while True:
            try:
                returncode = _wait_process(process, WORKER_WAIT_INTERVAL)
                break
            except subprocess.TimeoutExpired:
                kind = getattr(stop_request, "kind", None)
                if kind == "operator_abort":
                    interrupt(kind)
                    returncode = finish_interrupted()
                    break
    stdout_thread.join()
    stderr_thread.join()
    if prompt_thread.is_alive() and stdin is not None:
        try:
            stdin.close()
        except (OSError, ValueError):
            pass
    prompt_thread.join(WORKER_TERMINATION_TIMEOUT)
    if identity_error is not None:
        transport_error = identity_error
    elif log_error is not None:
        transport_error = log_error
    elif interruption_error is not None:
        transport_error = interruption_error
    elif prompt_error is not None and interruption_kind is None:
        transport_error = transport_error or prompt_error
    try:
        group_retired = (
            containment.wait_empty(WORKER_TERMINATION_TIMEOUT)
            if containment is not None
            else worker_identity_handler is None
        )
    except (ContainmentError, OSError):
        group_retired = False
    if not group_retired and interruption_kind is not None and containment is not None:
        containment.force_terminate()
        try:
            group_retired = containment.wait_empty(WORKER_TERMINATION_TIMEOUT)
        except (ContainmentError, OSError):
            group_retired = False
    if not group_retired:
        detail = getattr(containment, "diagnostics", lambda: "")()
        populated_error = "execution cgroup remains populated"
        if detail:
            populated_error = f"{populated_error}: {detail}"
        transport_error = transport_error or populated_error
    elif containment is not None:
        try:
            containment.destroy()
        except ContainmentError as error:
            transport_error = transport_error or str(error)
            group_retired = False
    return OpenCodeRunResult(
        returncode, transport_error, interruption_kind, group_retired, True
    )


class WorkerSupervisor:
    """Own live worker processes while the engine owns durable execution."""

    def __init__(
        self,
        opencode_bin: str,
        model: str,
        agent: str,
        *,
        state_dir: Path | None = None,
        state_key: str | None = None,
        show_worker_output: bool = True,
        containment_provider: ContainmentProvider | None = None,
    ) -> None:
        self.opencode_bin = opencode_bin
        self.opencode_model = model
        self.opencode_agent = agent
        self.state_dir = state_dir
        self.state_key = state_key
        self.show_worker_output = show_worker_output
        self.containment_provider = (
            containment_provider or default_containment_provider()
        )
        self._active: dict[str, object] = {}
        self._lock = threading.Lock()
        self._draining = False

    def begin_drain(self) -> bool:
        """Close admission once and return whether this call changed it."""
        with self._lock:
            if self._draining:
                return False
            self._draining = True
            return True

    @property
    def draining(self) -> bool:
        with self._lock:
            return self._draining

    @property
    def active_count(self) -> int:
        with self._lock:
            return len(self._active)

    def owns(self, execution_id: str) -> bool:
        with self._lock:
            return execution_id in self._active

    def observe(self, identity: WorkerProcessIdentity) -> str:
        if identity.containment_id:
            return self.containment_provider.observe(identity.containment_id)
        return observe_worker_identity(identity)

    def run(
        self,
        workspace: ExecutionWorkspace,
        prompt: str,
        *,
        execution_id: str,
        stop_request: object | None = None,
        identity_handler: Callable[[WorkerProcessIdentity], None] | None = None,
        interruption_handler: Callable[[str], None] | None = None,
    ) -> WorkerRunResult:
        """Run one validated worker and return typed process/egress results."""
        if not isinstance(workspace, ExecutionWorkspace):
            raise TypeError("worker workspace must be ExecutionWorkspace")
        if not isinstance(prompt, str):
            raise TypeError("worker prompt must be text")
        execution_path = workspace.path.resolve()
        command = [
            self.opencode_bin,
            "run",
            "--dir",
            str(execution_path),
            "--format",
            "json",
            "--model",
            self.opencode_model,
        ]
        if self.opencode_agent:
            command.extend(("--agent", self.opencode_agent))
        parser = WorkerEgressParser()
        config_dir = Path(tempfile.mkdtemp(prefix="devlegate-opencode-"))
        tool_dir = config_dir / "tools"
        tool_dir.mkdir()
        (tool_dir / "devlegate_report.ts").write_text(
            """import { tool } from "@opencode-ai/plugin"

export default tool({
  description: "Report the worker's semantic claim to Devlegate. " +
    "summary must be a concise human-readable description of implemented " +
    "changes, normally one to three short sentences; do not include test " +
    "counts, validation status, remaining work, questions, execution IDs, " +
    "or provenance. Put concrete unfinished work in remaining and required " +
    "external questions or decisions in questions.",
  args: {
    outcome: tool.schema.enum(["completed", "incomplete", "blocked"]),
    summary: tool.schema.string(),
    remaining: tool.schema.array(tool.schema.string()),
    questions: tool.schema.array(tool.schema.string()),
     report: tool.schema.string().optional(),
  },
  async execute() {
    return "devlegate_report accepted"
  },
})
"""
        )
        config = {
            "$schema": "https://opencode.ai/config.json",
            "permission": {
                "external_directory": {
                    "*": "deny",
                    f"{execution_path}/**": "allow",
                }
            },
        }
        environment = worker_environment()
        environment["PWD"] = str(execution_path)
        environment["OPENCODE_CONFIG_DIR"] = str(config_dir)
        environment["OPENCODE_CONFIG_CONTENT"] = json.dumps(config, sort_keys=True)
        with self._lock:
            if self._draining:
                raise WorkerAdmissionClosed("worker admission is closed")
            self._active[execution_id] = object()
        execution_log = None
        worker_started = False
        completion_logged = False

        def mark_worker_started() -> None:
            nonlocal worker_started
            worker_started = True

        try:
            if self.state_dir is not None and self.state_key is not None:
                execution_log = open_execution_log(
                    self.state_dir, self.state_key, execution_id
                )
                service_log(
                    "execution starting: "
                    f"ticket={workspace.ticket_id} execution={execution_id} "
                    f"log={execution_log.path}"
                )
            containment = self.containment_provider.create(execution_id)
            opencode_result = _run_opencode(
                command,
                prompt,
                cwd=execution_path,
                env=environment,
                event_handler=parser.consume,
                stop_request=stop_request,
                execution_id=execution_id,
                worker_identity_handler=identity_handler,
                interruption_handler=interruption_handler,
                execution_log=execution_log,
                show_worker_output=self.show_worker_output,
                worker_started_handler=mark_worker_started,
                containment=containment,
            )
            claim, egress_error = parser.finish()
            result = WorkerRunResult(
                opencode_result.process_returncode,
                opencode_result.transport_error,
                claim,
                egress_error,
                opencode_result.interruption_kind,
                opencode_result.worker_group_retired,
                opencode_result.process_started,
            )
            if execution_log is not None and worker_started:
                service_log(
                    "execution finished: "
                    f"ticket={workspace.ticket_id} execution={execution_id} "
                    f"log={execution_log.path}"
                )
                completion_logged = True
            return result
        except (ContainmentError, OSError) as error:
            return WorkerRunResult(-1, str(error), None, None, process_started=False)
        finally:
            if (
                execution_log is not None
                and worker_started
                and not completion_logged
            ):
                service_log(
                    "execution finished: "
                    f"ticket={workspace.ticket_id} execution={execution_id} "
                    f"log={execution_log.path}"
                )
            if execution_log is not None:
                try:
                    execution_log.close()
                except OSError:
                    pass
            with self._lock:
                self._active.pop(execution_id, None)
            shutil.rmtree(config_dir, ignore_errors=True)
