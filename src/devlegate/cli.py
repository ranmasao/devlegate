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
import textwrap
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import NoReturn

from devlegate import __version__
from devlegate.agent_protocol import AgentProtocolError, seed_project_env
from devlegate.daemon import HostingMode, run_service
from devlegate.host_installation import (
    HostInstallation,
    HostInstallationError,
    installation_path,
)
from devlegate.host_installation import lock as installation_lock
from devlegate.host_installation import read as read_installation
from devlegate.host_installation import read_locked as read_installation_locked
from devlegate.host_installation import remove as remove_installation
from devlegate.host_installation import write as write_installation
from devlegate.ipc_client import (
    IPCClientError,
    decode_drop_ack,
    decode_drop_candidates,
    decode_plan,
    decode_reconcile_ack,
    decode_reconcile_control_ack,
    decode_reconcile_resume_ack,
    decode_retry_ack,
    decode_retry_candidates,
    decode_status,
    request,
)
from devlegate.launcher import LaunchCommand, product_launcher
from devlegate.lifecycle_receipt import read as read_lifecycle_receipt
from devlegate.operational_log import service_log
from devlegate.output import add_output_arguments, emit, render_grid, render_table
from devlegate.platform_support import HOSTED_RUNTIME_ERROR, hosted_runtime_supported
from devlegate.project_registry import (
    ProjectRegistry,
    ProjectRegistryError,
    ProjectTarget,
    canonical_env_path,
    validate_alias,
)
from devlegate.runtime import (
    BlockedReason,
    DevlegateError,
    ExecutionPlan,
    StatusSnapshot,
    WorkflowBlockedError,
    _git,
)
from devlegate.runtime_locator import (
    RuntimeAuthorityPresent,
    RuntimeLocator,
    RuntimeLocatorError,
    repository_root,
)
from devlegate.service import ServiceEngine
from devlegate.service_diagnostics import read as read_service_failure
from devlegate.runtime_store import RuntimeStoreError, SQLiteRuntimeStore
from devlegate.systemd_supervisor import (
    SystemdSupervisor,
    SystemdSupervisorError,
    managed_unit_paths,
    notify_ready,
    unit_path,
)


def _service_engine(
    env_file: Path,
    *,
    repository: Path | None = None,
    read_only: bool = False,
    show_worker_output: bool = True,
) -> ServiceEngine:
    """Construct the canonical service engine."""
    options = {"read_only": read_only}
    if not show_worker_output:
        options["show_worker_output"] = False
    if repository is not None:
        options["repository"] = repository
    return ServiceEngine(env_file, **options)


@dataclasses.dataclass(frozen=True)
class ReadOnlyView:
    value: StatusSnapshot | ExecutionPlan
    service_state: str
    live_execution: dict[str, object] | None = None
    service_failure: dict[str, object] | None = None
    service_metadata: dict[str, object] | None = None


def _service_metadata(response: dict[str, object]) -> dict[str, object]:
    raw = response.get("service")
    if not isinstance(raw, dict):
        return {"version": "unknown"}
    version = raw.get("version")
    metadata: dict[str, object] = {
        "version": version if isinstance(version, str) else "unknown"
    }
    pid = raw.get("pid")
    if isinstance(pid, int) and not isinstance(pid, bool):
        metadata["pid"] = pid
    instance_id = raw.get("instance_id")
    if isinstance(instance_id, str):
        metadata["instance_id"] = instance_id
    lifecycle = raw.get("lifecycle")
    if isinstance(lifecycle, dict):
        metadata["lifecycle"] = lifecycle
        if isinstance(lifecycle.get("ready"), bool):
            metadata["ready"] = lifecycle["ready"]
    return metadata


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
            engine = _service_engine(
                env_file,
                repository=locator.repo,
                read_only=True,
            )
            if method == "status":
                view = engine.status_view
            else:
                view = engine.plan_view
            value = view()
            diagnostic, corrupt = read_service_failure(locator)
            failure = (
                {
                    "state": "unavailable",
                    "message": "service diagnostic unavailable/corrupt",
                }
                if corrupt
                else diagnostic.as_public_dict() if diagnostic is not None else None
            )
            return ReadOnlyView(value, "stopped", service_failure=failure)
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
            live_execution = response.get("live_execution")
            if not isinstance(live_execution, dict):
                live_execution = None
            elif "worker_identity" in live_execution:
                live_execution = {
                    key: item
                    for key, item in live_execution.items()
                    if key != "worker_identity"
                }
            return ReadOnlyView(
                value,
                "running",
                live_execution,
                service_metadata=_service_metadata(response),
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


def _select_candidate(
    candidates: tuple[dict[str, str], ...], heading: str, invalid_message: str
) -> dict[str, str] | None:
    print(f"{heading}:")
    for index, candidate in enumerate(candidates, 1):
        ticket_id = candidate["id"]
        title = candidate.get("title", "").strip()
        label = (
            ticket_id
            if not title or title == ticket_id
            else f"{ticket_id}  {title}"
        )
        print(f"  {index}) {label}")
        print(f"     {candidate['reason']}")
    print("  0) Cancel")
    answer = input("Select number (Enter = cancel): ").strip()
    if not answer or answer == "0":
        return None
    try:
        index = int(answer)
        if not 1 <= index <= len(candidates):
            raise ValueError
    except ValueError as error:
        raise DevlegateError(invalid_message) from error
    return candidates[index - 1]


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
            selected = _select_candidate(
                candidates, "Retry candidates", "invalid retry selection"
            )
            if selected is None:
                return 0
            ticket_id = selected["id"]
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
        f"retry accepted: {ticket_id}",
    )
    return 0


def _drop_daemon(env_file: Path, ticket_id: str | None, output_format: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    if ticket_id is None and not _interactive_terminal():
        raise DevlegateError(
            "interactive drop requires a terminal; specify a ticket ID:\n"
            "devlegate drop <ticket-id>"
        )
    if ticket_id is None and output_format != "table":
        raise DevlegateError(
            "machine-readable drop requires a ticket ID; "
            "specify devlegate drop <ticket-id>"
        )
    if ticket_id is not None and not ticket_id:
        raise DevlegateError("drop ticket ID must be non-empty")
    try:
        if not locator.daemon_authority_present():
            raise DevlegateError(
                "service is not running for this checkout; start `devlegate`"
            )
        candidates = decode_drop_candidates(
            request(locator.socket_path, "drop-candidates")
        )
        if ticket_id is None:
            if not candidates:
                raise DevlegateError("no current executions are droppable")
            selected = _select_candidate(
                candidates, "Drop candidates", "invalid drop selection"
            )
            if selected is None:
                return 0
        else:
            selected = next(
                (candidate for candidate in candidates if candidate["id"] == ticket_id),
                None,
            )
            if selected is None:
                raise DevlegateError(f"ticket {ticket_id} is not currently droppable")
        ticket_id = selected["id"]
        execution_id = selected["execution_id"]
        if not locator.daemon_authority_present():
            raise DevlegateError("service stopped before drop was submitted")
        result = request(
            locator.socket_path,
            "drop",
            {"ticket_id": ticket_id, "execution_id": execution_id},
            mutable=True,
        )
        decode_drop_ack(result, ticket_id, execution_id)
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    result = {
        "result": "dropped",
        "ticket_id": ticket_id,
        "execution_id": execution_id,
    }
    emit(result, output_format, f"drop accepted: {ticket_id}")
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
        f"reconciliation accepted: {ticket_id}",
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
        f"reconciliation resume accepted: {ticket_id}",
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
        f"control reconciliation accepted: {from_head} -> {to_head}",
    )
    return 0


def _matching_lifecycle_receipt(
    locator: RuntimeLocator,
    request_id: str,
    instance_id: str,
    action: str,
    state: str,
) -> dict[str, object] | None:
    receipt = read_lifecycle_receipt(locator)
    if receipt is None:
        return None
    if (
        receipt.get("request_id") == request_id
        and receipt.get("instance_id") == instance_id
        and receipt.get("action") == action
        and receipt.get("state") == state
    ):
        return receipt
    return None


def _wait_for_service_stop(
    locator: RuntimeLocator, request_id: str, instance_id: str
) -> None:
    while locator.daemon_authority_present():
        time.sleep(0.05)
    deadline = time.monotonic() + 10
    while time.monotonic() < deadline:
        if (
            _matching_lifecycle_receipt(
                locator, request_id, instance_id, "stop", "completed"
            )
            is not None
        ):
            return
        time.sleep(0.05)
    raise DevlegateError(
        "service authority disappeared without graceful stop completion"
    )


def _wait_for_service_restart(
    locator: RuntimeLocator, request_id: str, old_instance: str
) -> None:
    readiness_deadline: float | None = None
    while True:
        failed = read_lifecycle_receipt(locator)
        if (
            isinstance(failed, dict)
            and failed.get("request_id") == request_id
            and failed.get("state") == "failed"
        ):
            raise DevlegateError(
                f"restart replacement failed: {failed.get('error', 'unknown error')}"
            )
        handoff = _matching_lifecycle_receipt(
            locator, request_id, old_instance, "restart", "handoff"
        )
        completed = _matching_lifecycle_receipt(
            locator, request_id, old_instance, "restart", "completed"
        )
        if handoff is not None or completed is not None:
            if readiness_deadline is None:
                readiness_deadline = time.monotonic() + 10
        elif not locator.daemon_authority_present():
            failed = read_lifecycle_receipt(locator)
            if (
                isinstance(failed, dict)
                and failed.get("request_id") == request_id
                and failed.get("state") == "failed"
            ):
                raise DevlegateError(
                    "restart replacement failed: "
                    f"{failed.get('error', 'unknown error')}"
                )
            raise DevlegateError(
                "service authority disappeared without restart handoff completion"
            )
        if not locator.daemon_authority_present():
            raise DevlegateError("restart service authority was lost")
        try:
            response = request(locator.socket_path, "ping")
        except IPCClientError:
            if (
                readiness_deadline is not None
                and time.monotonic() >= readiness_deadline
            ):
                raise DevlegateError(
                    "replacement service did not become ready; "
                    f"receipt={read_lifecycle_receipt(locator)!r}"
                )
            time.sleep(0.05)
            continue
        if (
            response.get("service") == "devlegate"
            and response.get("instance_id") != old_instance
            and response.get("ready") is True
            and completed is not None
            and response.get("instance_id") == completed.get("replacement_instance_id")
        ):
            return
        if readiness_deadline is not None and time.monotonic() >= readiness_deadline:
            raise DevlegateError(
                "replacement service did not become ready; "
                f"receipt={read_lifecycle_receipt(locator)!r}"
            )
        time.sleep(0.05)


def _host_installation(*, required: bool = True) -> HostInstallation | None:
    try:
        installation = read_installation()
    except HostInstallationError as error:
        raise DevlegateError(str(error)) from error
    if installation is None and required:
        raise DevlegateError(
            "Devlegate host is not installed; run `devlegate host install "
            "--supervisor internal` or `--supervisor systemd`"
        )
    return installation


def _host_installation_locked(*, required: bool = True) -> HostInstallation | None:
    try:
        installation = read_installation_locked()
    except HostInstallationError as error:
        raise DevlegateError(str(error)) from error
    if installation is None and required:
        raise DevlegateError(
            "Devlegate host is not installed; run `devlegate host install "
            "--supervisor internal` or `--supervisor systemd`"
        )
    return installation


def _wait_for_runtime_stop(locator: RuntimeLocator) -> None:
    deadline = time.monotonic() + 10
    while locator.daemon_authority_present():
        if time.monotonic() >= deadline:
            raise DevlegateError("service authority remains after systemd stop")
        time.sleep(0.05)


def _managed_systemd_owner(locator: RuntimeLocator) -> SystemdSupervisor | None:
    supervisor = SystemdSupervisor()
    try:
        if not supervisor.inspect(locator) or not supervisor.status(locator):
            return None
    except SystemdSupervisorError as error:
        raise DevlegateError(str(error)) from error
    return supervisor


def _supervision_store(target: ProjectTarget) -> SQLiteRuntimeStore:
    store = SQLiteRuntimeStore(target.locator.state_dir, target.locator.state_key)
    try:
        store.probe()
    except RuntimeStoreError as error:
        raise DevlegateError(str(error)) from error
    return store


def _systemd_authority_established(
    target: ProjectTarget, supervisor: SystemdSupervisor | None = None
) -> tuple[SQLiteRuntimeStore, bool]:
    store = _supervision_store(target)
    try:
        authority = store.supervision_authority()
    except RuntimeStoreError as error:
        raise DevlegateError(str(error)) from error
    current = supervisor or SystemdSupervisor()
    path = unit_path(target.locator, current.unit_directory)
    if authority is not None:
        expected = {
            "authority": "systemd",
            "unit_name": path.name,
            "state_key": target.locator.state_key,
            "env_file": str(target.env_file.resolve()),
            "repository": str(target.repo.resolve()),
        }
        if authority != expected:
            raise DevlegateError(
                "persisted systemd authority does not match the selected project"
            )
        return store, True
    if path.exists():
        try:
            current.inspect(target.locator, env_file=target.env_file)
        except SystemdSupervisorError as error:
            raise DevlegateError(str(error)) from error
        return store, True
    return store, False


def _lifecycle_service(env_file: Path, output_format: str, intent: str) -> int:
    try:
        locator = RuntimeLocator.from_env(env_file)
    except RuntimeLocatorError as error:
        raise DevlegateError(str(error)) from error
    owner = _managed_systemd_owner(locator)
    if owner is not None:
        if intent == "stop":
            owner.stop(locator)
            _wait_for_runtime_stop(locator)
            result = {"result": "stopped", "service": "devlegate", "action": intent}
            emit(result, output_format, "service stopped")
            return 0
        owner.restart(locator)
        result = {"result": "restarted", "service": "devlegate", "action": intent}
        emit(result, output_format, "service restarted")
        return 0
    if not locator.daemon_authority_present():
        raise DevlegateError("service is not running")
    if intent == "stop":
        _stop_runtime(locator)
        result = {"result": "stopped", "service": "devlegate", "action": intent}
        emit(result, output_format, "service stopped")
    else:
        if not locator.daemon_authority_present():
            raise DevlegateError("service is not running")
        request_id = uuid.uuid4().hex
        try:
            response = request(
                locator.socket_path, intent, mutable=True, request_id=request_id
            )
        except IPCClientError as error:
            raise DevlegateError(str(error)) from error
        if response.get("accepted") is not True:
            raise DevlegateError(
                f"service IPC returned invalid {intent} acknowledgement"
            )
        instance_id = response.get("instance_id")
        if not isinstance(instance_id, str) or not instance_id:
            raise DevlegateError("service IPC returned no service instance identity")
        _wait_for_service_restart(locator, request_id, instance_id)
        result = {"result": "restarted", "service": "devlegate", "action": intent}
        emit(result, output_format, "service restarted")
    return 0


def _stop_runtime(locator: RuntimeLocator) -> None:
    if not locator.daemon_authority_present():
        return
    request_id = uuid.uuid4().hex
    try:
        response = request(
            locator.socket_path, "stop", mutable=True, request_id=request_id
        )
    except IPCClientError as error:
        raise DevlegateError(str(error)) from error
    if response.get("accepted") is not True:
        raise DevlegateError("service IPC returned invalid stop acknowledgement")
    instance_id = response.get("instance_id")
    if not isinstance(instance_id, str) or not instance_id:
        raise DevlegateError("service IPC returned no service instance identity")
    _wait_for_service_stop(locator, request_id, instance_id)


def _stop_service(env_file: Path, output_format: str) -> int:
    return _lifecycle_service(env_file, output_format, "stop")


def _restart_service(env_file: Path, output_format: str) -> int:
    return _lifecycle_service(env_file, output_format, "restart")


def _healthy_service(env_file: Path) -> dict[str, object] | None:
    locator = RuntimeLocator.from_env(env_file)
    if not locator.daemon_authority_present():
        return None
    try:
        response = request(locator.socket_path, "ping")
    except IPCClientError as error:
        raise DevlegateError(
            "service authority exists but its IPC endpoint is unavailable"
        ) from error
    if (
        response.get("service") != "devlegate"
        or response.get("protocol_version") != 1
    ):
        raise DevlegateError("service authority exists but its IPC health is invalid")
    return response


def _warn_service_version_mismatch(response: dict[str, object]) -> None:
    service_version = _service_metadata(response)["version"]
    if service_version == "unknown" or service_version == __version__:
        return
    print(
        f"Warning: the running service uses Devlegate {service_version}; "
        f"the current client is {__version__}.\n"
        "Stop and start the service to load the current Devlegate version."
    )


def _startup_fd() -> int | None:
    value = os.environ.get("DEVLEGATE_STARTUP_FD")
    if value is None:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _host_mode() -> HostingMode:
    value = os.environ.get("DEVLEGATE_HOST_MODE")
    if value is None:
        return HostingMode.DIRECT
    try:
        return HostingMode(value)
    except ValueError as error:
        raise DevlegateError(f"unsupported hosting mode: {value}") from error


def _project_target(
    *, alias: str | None, env_file: Path | None, required: bool = True
) -> ProjectTarget | None:
    registry = ProjectRegistry()
    try:
        if alias is not None:
            return registry.target_for_alias(alias)
        selected_env = env_file or Path.cwd() / ".env"
        registered_alias = registry.alias_for_env(selected_env)
        if registered_alias is None:
            if required:
                raise ProjectRegistryError(
                    "project is not registered; assign an alias with "
                    "`devlegate project alias <name> [path]`"
                )
            return None
        return registry.target_for_alias(registered_alias)
    except (ProjectRegistryError, RuntimeLocatorError) as error:
        raise DevlegateError(str(error)) from error


def _validate_registered_projects(registry: ProjectRegistry) -> None:
    try:
        aliases = sorted(registry.projects())
        targets = [registry.target_for_alias(alias) for alias in aliases]
    except (ProjectRegistryError, RuntimeLocatorError) as error:
        raise DevlegateError(
            f"cannot validate registered project inventory: {error}"
        ) from error
    active = [
        target.alias
        for target in targets
        if target.locator.daemon_authority_present()
    ]
    if active:
        formatted = ", ".join(f"@{alias}" for alias in active)
        raise DevlegateError(
            "cannot install host supervision while projects are active: "
            f"{formatted}; stop them first"
        )


def _install_host(args: argparse.Namespace) -> int:
    path = installation_path()
    requested = HostInstallation(args.supervisor)
    registry = ProjectRegistry()
    # Cross-resource mutations acquire the host lock before the registry lock.
    with installation_lock(exclusive=True, path=path):
        current = _host_installation_locked(required=False)
        if current is not None:
            if current.supervisor != requested.supervisor:
                raise DevlegateError(
                    "Devlegate host is already installed with "
                    f"{current.supervisor} supervision; uninstall it before "
                    f"installing {requested.supervisor} supervision"
                )
            result = {
                "result": "already_installed",
                "supervisor": current.supervisor,
                "installation_file": str(path),
            }
            emit(
                result,
                args.output_format,
                "Devlegate host is already installed with "
                f"{current.supervisor} supervision.",
            )
            return 0
    if requested.supervisor == "systemd":
        try:
            SystemdSupervisor().probe_user_manager()
        except SystemdSupervisorError as error:
            raise DevlegateError(str(error)) from error
    with installation_lock(exclusive=True, path=path):
        current = _host_installation_locked(required=False)
        if current is not None:
            if current.supervisor != requested.supervisor:
                raise DevlegateError(
                    "Devlegate host is already installed with "
                    f"{current.supervisor} supervision; uninstall it before "
                    f"installing {requested.supervisor} supervision"
                )
            result = {
                "result": "already_installed",
                "supervisor": current.supervisor,
                "installation_file": str(path),
            }
            emit(
                result,
                args.output_format,
                "Devlegate host is already installed with "
                f"{current.supervisor} supervision.",
            )
            return 0
        _validate_registered_projects(registry)
        try:
            write_installation(path, requested)
        except HostInstallationError as error:
            raise DevlegateError(str(error)) from error
    result = {
        "result": "installed",
        "supervisor": requested.supervisor,
        "installation_file": str(path),
    }
    emit(
        result,
        args.output_format,
        f"Devlegate host installed with {requested.supervisor} supervision.",
    )
    return 0


def _uninstall_host(args: argparse.Namespace) -> int:
    path = installation_path()
    registry = ProjectRegistry()
    with installation_lock(exclusive=True, path=path):
        current = _host_installation_locked(required=False)
        try:
            projects = registry.projects()
            residual = managed_unit_paths()
        except (ProjectRegistryError, SystemdSupervisorError) as error:
            raise DevlegateError(str(error)) from error
        if projects:
            aliases = ", ".join(f"@{alias}" for alias in sorted(projects))
            raise DevlegateError(
                "cannot uninstall Devlegate host integration while projects "
                f"are registered: {aliases}; decommission them with "
                "`devlegate project remove @ALIAS`"
            )
        if residual:
            names = ", ".join(str(item) for item in residual)
            raise DevlegateError(
                "cannot uninstall Devlegate host integration; residual managed "
                f"systemd units remain: {names}"
            )
        try:
            remove_installation(path)
        except HostInstallationError as error:
            if current is not None:
                try:
                    write_installation(path, current)
                except HostInstallationError:
                    pass
            raise DevlegateError(str(error)) from error
    result = {
        "result": "removed",
        "host_integration_removed": True,
        "software_removed": False,
        "project_data_preserved": True,
    }
    emit(
        result,
        args.output_format,
        "Devlegate host integration removed; software package/artifact "
        "remains installed.",
    )
    return 0


def _init_target(alias: str) -> tuple[str, Path, Path]:
    try:
        validate_alias(alias)
        repo = repository_root()
        if repo != Path.cwd().resolve():
            raise ProjectRegistryError(
                f"run devlegate init {alias} from repository root: {repo}"
            )
        env = repo / ".env"
        registry = ProjectRegistry()
        projects = registry.projects()
        existing = projects.get(alias)
        if existing is not None:
            raise ProjectRegistryError(
                f"project alias @{alias} is already registered for {existing}"
            )
        for registered_alias, registered_env in projects.items():
            if registered_alias == alias:
                continue
            registered_path = canonical_env_path(Path(registered_env))
            if registered_path == env:
                raise ProjectRegistryError(
                    f"project is already registered as @{registered_alias}"
                )
            if not registered_path.is_file():
                continue
            try:
                registered_repo = repository_root(registered_path.parent)
            except RuntimeLocatorError:
                continue
            if registered_repo == repo:
                raise ProjectRegistryError(
                    "repository is already registered as "
                    f"@{registered_alias}; canonical configuration is "
                    f"{repo / '.env'}"
                )
        return alias, env, repo
    except (ProjectRegistryError, RuntimeLocatorError) as error:
        raise DevlegateError(str(error)) from error


def _project_path(path: Path | None) -> Path:
    selected = path or Path.cwd()
    if selected.is_dir():
        try:
            return repository_root(selected) / ".env"
        except RuntimeLocatorError as error:
            raise ProjectRegistryError(str(error)) from error
    return canonical_env_path(selected)


def _remove_project(args: argparse.Namespace) -> int:
    if not args.alias.startswith("@"):
        raise DevlegateError("project remove requires @ALIAS")
    alias = args.alias.removeprefix("@")
    registry = ProjectRegistry()
    try:
        target = registry.target_for_alias(alias)
        supervisor = SystemdSupervisor()
        store, systemd_authority = _systemd_authority_established(target, supervisor)
        managed_unit = supervisor.inspect(target.locator, env_file=target.env_file)
        supervisor_removed = False
        if systemd_authority:
            supervisor.remove(target.locator, env_file=target.env_file)
            supervisor_removed = True
        elif target.locator.daemon_authority_present():
            if managed_unit and supervisor.status(target.locator):
                supervisor.remove(target.locator, env_file=target.env_file)
                supervisor_removed = True
            else:
                _stop_runtime(target.locator)
        if target.locator.daemon_authority_present():
            raise DevlegateError(
                f"cannot decommission @{alias}: service authority remains"
            )
        if managed_unit and not supervisor_removed:
            supervisor.remove(target.locator, env_file=target.env_file)
            supervisor_removed = True
        if supervisor.inspect(target.locator):
            raise DevlegateError(
                f"cannot decommission @{alias}: systemd unit remains registered"
            )
        if systemd_authority:
            try:
                store.clear_systemd_authority()
            except RuntimeStoreError as error:
                raise DevlegateError(str(error)) from error
        with installation_lock(exclusive=True):
            registry.unregister(alias, expected_env=target.env_file)
    except (ProjectRegistryError, SystemdSupervisorError) as error:
        raise DevlegateError(str(error)) from error
    value = {
        "result": "decommissioned",
        "alias": f"@{alias}",
        "env": str(target.env_file),
        "repo": str(target.repo),
        "runtime_stopped": True,
        "supervisor_removed": supervisor_removed,
        "project_data_preserved": True,
    }
    emit(
        value,
        args.output_format,
        f"removed @{alias} from this Devlegate installation; "
        "project data preserved",
    )
    return 0


def _project_command(args: argparse.Namespace) -> int:
    registry = ProjectRegistry()
    try:
        match args.project_action:
            case "list":
                projects = registry.projects()
                rows = [
                    {
                        "alias": alias,
                        "env": env,
                        "state": "ok" if Path(env).is_file() else "missing",
                    }
                    for alias, env in sorted(projects.items())
                ]
                if args.output_format != "table":
                    emit({"projects": rows}, args.output_format, "")
                else:
                    print("ALIAS       ENV                                      STATE")
                    for row in rows:
                        print(
                            f"{row['alias']:<11} {row['env']:<40} {row['state']}"
                        )
                return 0
            case "resolve":
                if not args.alias.startswith("@"):
                    raise ProjectRegistryError("project resolve requires @ALIAS")
                alias = args.alias.removeprefix("@")
                target = registry.target_for_alias(alias)
                value = {
                    "alias": f"@{alias}",
                    "env": str(target.env_file),
                    "repo": str(target.repo),
                }
                emit(value, args.output_format, str(target.env_file))
                return 0
            case "identify":
                env = _project_path(args.path)
                alias = registry.alias_for_env(env)
                if alias is None:
                    raise ProjectRegistryError(f"project is not registered: {env}")
                emit(
                    {"alias": f"@{alias}", "env": str(env)},
                    args.output_format,
                    f"@{alias}",
                )
                return 0
            case "alias":
                target = registry.register(args.alias, _project_path(args.path))
                emit(
                    {
                        "alias": f"@{target.alias}",
                        "env": str(target.env_file),
                        "repo": str(target.repo),
                    },
                    args.output_format,
                    f"registered @{target.alias}: {target.env_file}",
                )
                return 0
            case "rename":
                with installation_lock(exclusive=True):
                    registry.rename(args.old_alias, args.new_alias)
                emit(
                    {"alias": f"@{args.new_alias}"},
                    args.output_format,
                    f"renamed @{args.old_alias} to @{args.new_alias}",
                )
                return 0
            case "remove":
                return _remove_project(args)
            case _:
                raise ProjectRegistryError("unsupported project command")
    except ProjectRegistryError as error:
        raise DevlegateError(str(error)) from error


def _systemd_readiness_report() -> Callable[[], None] | None:
    if (
        _host_mode() is HostingMode.EXTERNAL
        and os.environ.get("DEVLEGATE_REQUIRE_NOTIFY") == "1"
    ):
        return lambda: notify_ready(required=True)
    return None


def _selector_argv(
    argv: list[str], parser: argparse.ArgumentParser
) -> tuple[list[str], str | None]:
    values = list(argv)
    alias: str | None = None
    if values and values[0].startswith("@"):
        alias = values.pop(0)[1:]
        try:
            validate_alias(alias)
        except ProjectRegistryError as error:
            parser.error(str(error))
        if values and values[0] == "--env":
            parser.error("project selectors --env and @ALIAS are mutually exclusive")
    if values and values[0] == "--env":
        if len(values) < 2:
            parser.error("argument --env: expected one path")
        if len(values) > 2 and values[2].startswith("@"):
            parser.error("project selectors --env and @ALIAS are mutually exclusive")
    return values, alias


def _systemd_service_command(args: argparse.Namespace) -> int:
    try:
        if args.service_action in {"install", "start", "restart"}:
            _host_installation()
        target = _project_target(
            alias=getattr(args, "project_alias", None),
            env_file=args.service_env,
        )
        assert target is not None
        supervisor = SystemdSupervisor()
        match args.service_action:
            case "install":
                path = supervisor.install(target.locator, target.env_file)
                print(f"systemd user unit installed: {path}")
            case "remove":
                path = supervisor.remove(target.locator, env_file=target.env_file)
                print(f"systemd user unit removed: {path}")
            case "start":
                supervisor.start(target.locator)
                print("Devlegate systemd service started.")
            case "stop":
                supervisor.stop(target.locator)
                print("Devlegate systemd service stopped.")
            case "restart":
                supervisor.restart(target.locator)
                print("Devlegate systemd service restarted.")
            case "status":
                active = supervisor.status(target.locator)
                print("active" if active else "inactive")
                return 0 if active else 3
            case _:
                raise DevlegateError("unsupported systemd service action")
    except (RuntimeLocatorError, SystemdSupervisorError) as error:
        raise DevlegateError(str(error)) from error
    return 0


def _notify_startup_failure(error: BaseException) -> None:
    fd = _startup_fd()
    if fd is None:
        return
    try:
        os.write(fd, f"FAILED {error}\n".encode())
    except OSError:
        pass
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _background_command(
    env_file: Path, launcher: LaunchCommand | None = None
) -> list[str]:
    return (launcher or product_launcher()).argv(
        "--env", str(env_file), "foreground"
    )


def _start_background(
    env_file: Path, *, launcher: LaunchCommand | None = None
) -> int:
    if not hosted_runtime_supported():
        raise DevlegateError(HOSTED_RUNTIME_ERROR)
    try:
        locator = RuntimeLocator.from_env(env_file)
        log_path = locator.service_log_path
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
        "DEVLEGATE_HOST_MODE": HostingMode.INTERNAL.value,
        "DEVLEGATE_STARTUP_FD": str(write_fd),
    }
    # The child uses the attached entry only as a process-hosting shim.
    command = _background_command(env_file, launcher)
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


def _start_systemd(target: ProjectTarget) -> int:
    supervisor = SystemdSupervisor()
    try:
        path = supervisor.install(target.locator, target.env_file)
        supervisor.start(target.locator)
        store = _supervision_store(target)
        store.establish_systemd_authority(
            unit_name=path.name,
            state_key=target.locator.state_key,
            env_file=target.env_file.resolve(),
            repository=target.repo.resolve(),
        )
    except RuntimeStoreError as error:
        raise DevlegateError(str(error)) from error
    except SystemdSupervisorError as error:
        raise DevlegateError(str(error)) from error
    print(f"service started; systemd unit: {path}")
    return 0


def _run_attached_target(
    target: ProjectTarget,
    *,
    host_mode: HostingMode,
    once: bool,
    startup_fd: int | None,
) -> int:
    if not hosted_runtime_supported():
        raise DevlegateError(HOSTED_RUNTIME_ERROR)
    if host_mode is not HostingMode.EXTERNAL:
        _store, established = _systemd_authority_established(target)
        if established:
            raise DevlegateError(
                "systemd authority is established for this project; "
                "use the managed service instead"
            )
    engine = _service_engine(
        target.env_file,
        repository=target.repo,
        show_worker_output=host_mode is HostingMode.DIRECT,
    )
    run_arguments = {
        "host_mode": host_mode,
        "once": once,
        "startup_fd": startup_fd,
        "startup_report": lambda: _startup_report(
            engine, "background" if startup_fd is not None else "direct"
        ),
    }
    readiness_report = _systemd_readiness_report()
    if readiness_report is not None:
        run_arguments["readiness_report"] = readiness_report
    return run_service(engine, **run_arguments)


def _execution_projection(
    snapshot: StatusSnapshot,
    live_execution: dict[str, object] | None,
    service_state: str = "stopped",
) -> dict[str, object]:
    phase = snapshot.phase
    stage = snapshot.execution_stage
    has_bound_execution = (
        snapshot.bound_ticket_id is not None and snapshot.execution_id is not None
    )
    if phase == "idle":
        state = "idle"
    elif phase == "agent_pending" and _service_owns_execution(
        snapshot, live_execution
    ):
        state = "preparing"
    elif (
        phase == "agent_running"
        and stage == "worker-launch"
        and _service_owns_execution(snapshot, live_execution)
    ):
        state = "starting"
    elif phase == "agent_running" and stage in {
        "post-worker",
        "pre-checkpoint",
        "checkpointing",
        "post-checkpoint",
        "publishing",
        "post-publication",
        "lifecycle",
    } and _service_owns_execution(snapshot, live_execution):
        state = "finalizing"
    elif (
        phase == "agent_running"
        and stage == "worker-running"
        and _service_owns_execution(snapshot, live_execution)
        and live_execution.get("identity_state") == "matching-live"
    ):
        state = "running"
    elif phase != "idle":
        known_stages = {
            "worker-launch",
            "worker-running",
            "post-worker",
            "pre-checkpoint",
            "checkpointing",
            "post-checkpoint",
            "publishing",
            "post-publication",
            "lifecycle",
        }
        incomplete_protocol = (
            snapshot.execution_id is None and stage not in known_stages
        )
        if (
            service_state == "running"
            and live_execution is None
            and incomplete_protocol
        ):
            state = "unverified"
        else:
            state = "recovery-required"
    else:
        state = "idle"
    title = snapshot.bound_ticket_title if snapshot.bound_ticket_id else None
    return {
        "state": state,
        "phase": phase,
        "stage": stage if has_bound_execution else None,
        "execution_id": snapshot.execution_id if has_bound_execution else None,
        "ticket_id": snapshot.bound_ticket_id,
        "ticket_title": title,
    }


def _service_owns_execution(
    snapshot: StatusSnapshot, live_execution: dict[str, object] | None
) -> bool:
    return (
        isinstance(live_execution, dict)
        and snapshot.bound_ticket_id is not None
        and snapshot.execution_id is not None
        and live_execution.get("ticket_id") == snapshot.bound_ticket_id
        and live_execution.get("execution_id") == snapshot.execution_id
        and live_execution.get("stage") == snapshot.execution_stage
        and live_execution.get("ownership") == "current-service"
    )


def _short_hash(value: str | None) -> str:
    return (value or "<unknown>")[:12]


def _blocked_reason_text(
    reason: BlockedReason,
) -> str:
    if reason.kind == "dependencies":
        return ", ".join(
            (
                f"{ticket_id} integration in progress"
                if state == "integration-in-progress"
                else f"{ticket_id} unfinished ({state})"
            )
            for ticket_id, state in reason.tickets
        )
    if reason.kind == "integration-in-progress":
        return f"{reason.ticket_id} integration in progress"
    if reason.kind == "review":
        return f"{reason.ticket_id} awaiting review"
    if reason.kind == "accepted":
        return f"{reason.ticket_id} accepted; integration pending"
    if reason.kind == "active-execution":
        return f"{reason.ticket_id} execution in progress"
    if reason.kind == "reconciliation":
        return "reconciliation in progress"
    if reason.kind == "accepted-integration":
        return "accepted integration recovery in progress"
    if reason.kind == "repository":
        return "repository is not admissible"
    return "scheduler admission is blocked"


def _render_status_text(
    snapshot: StatusSnapshot,
    service_state: str,
    execution: dict[str, object],
    service_failure: dict[str, object] | None = None,
    service_metadata: dict[str, object] | None = None,
) -> str:
    code = snapshot.code
    control = snapshot.control
    lines = [f"Devlegate {__version__}  •  service {service_state}"]
    if service_state == "running" and service_metadata is not None:
        lifecycle = service_metadata.get("lifecycle")
        intent = lifecycle.get("intent") if isinstance(lifecycle, dict) else None
        workers = lifecycle.get("workers") if isinstance(lifecycle, dict) else None
        active = workers.get("active") if isinstance(workers, dict) else 0
        if intent in {"stop", "restart"} and active:
            action = "stop" if intent == "stop" else "restart"
            lines.append(
                f"Status: running, will {action} at checkpoint "
                "(a worker is active)"
            )
        elif intent == "restart":
            lines.append("Status: restarting")
        elif intent == "stop":
            lines.append("Status: stopping")
        else:
            lines.append("Status: running")
        lines.extend(["", f"Service version: {service_metadata['version']}"])
        if "pid" in service_metadata:
            lines.append(f"Service PID: {service_metadata['pid']}")
        if (
            service_metadata["version"] != "unknown"
            and service_metadata["version"] != __version__
        ):
            lines.extend(
                [
                    "",
                    "Warning: the running service uses a different Devlegate version.",
                    "Stop and start the service to load the current Devlegate version.",
                ]
            )
    if service_failure is not None:
        if service_failure.get("state") == "unavailable":
            lines.extend(["", "Service failure:", "  Diagnostic unavailable/corrupt"])
        else:
            lines.extend(
                [
                    "",
                    "Service failure:",
                    f"  Stage: {service_failure.get('stage', '<unknown>')}",
                    f"  Error: {service_failure.get('exception_type', '<unknown>')}",
                    f"  Detail: {service_failure.get('message', '<unknown>')}",
                ]
            )
    if execution["state"] != "idle":
        ticket_id = execution.get("ticket_id")
        current = (
            f"Current: {ticket_id}  •  {execution['state']}"
            if ticket_id
            else f"Current: {execution['state']}"
        )
        lines.extend(["", current])
        title = execution.get("ticket_title")
        if title:
            lines.extend(
                textwrap.wrap(
                    str(title),
                    width=88,
                    initial_indent="  ",
                    subsequent_indent="  ",
                )
            )
        if execution["state"] == "unverified":
            lines.append("  Live ownership evidence is unavailable.")
        elif execution["state"] == "recovery-required":
            diagnostics = tuple(
                f"{label}={execution[key]}"
                for label, key in (
                    ("phase", "phase"),
                    ("stage", "stage"),
                    ("execution", "execution_id"),
                )
                if execution.get(key) is not None
            )
            lines.append(
                "  Recovery: "
                + (
                    ", ".join(diagnostics)
                    if diagnostics
                    else "operator verification required"
                )
            )
    if snapshot.lifecycle_integration is not None:
        ticket_id, state = snapshot.lifecycle_integration
        lines.extend(
            [
                "",
                render_table(
                    "Lifecycle",
                    (("Integration", f"{state} {ticket_id}"),),
                ),
            ]
        )
    repository_rows = [
        (
            "Code",
            code.branch or "<detached>",
            _short_hash(code.local_head),
            f"{code.remote_ref} @ {_short_hash(code.remote_head)}",
            "clean" if code.working_tree_clean else "dirty",
        )
    ]
    if control is None:
        repository_rows.append(("Control", "missing", "-", "-", "-"))
    else:
        repository_rows.append(
            (
                "Control",
                control.branch or "<detached>",
                _short_hash(control.local_head),
                f"{control.remote_ref} @ {_short_hash(control.remote_head)}",
                "clean" if control.working_tree_clean else "dirty",
            )
        )
    lines.extend(
        [
            "",
            render_grid(
                "Repositories",
                ("Plane", "Branch", "Local", "Remote", "Tree"),
                repository_rows,
            ),
            "",
            "Workflow  "
            + "  ·  ".join(
                f"{state.title()}: {count}" for state, count in snapshot.counts
            ),
        ]
    )
    if snapshot.eligible:
        lines.extend(
            ["", render_grid("Eligible", ("Ticket", "Title"), snapshot.eligible)]
        )
    if snapshot.blocked:
        lines.extend(
            [
                "",
                render_grid(
                    "Blocked",
                    ("Ticket", "Title", "Reason"),
                    tuple(
                        (
                            ticket_id,
                            title,
                            _blocked_reason_text(reason),
                        )
                        for ticket_id, title, reason in snapshot.blocked
                    ),
                ),
            ]
        )
    if snapshot.review:
        lines.extend(["", render_grid("Review", ("Ticket", "Title"), snapshot.review)])
    if snapshot.accepted:
        lines.extend(
            ["", render_grid("Accepted", ("Ticket", "Title"), snapshot.accepted)]
        )
    if snapshot.failed_executions:
        lines.extend(
            [
                "",
                render_grid(
                    "Failed executions",
                    ("Ticket", "Retryable", "Reason"),
                    tuple(
                        (
                            failure.ticket_id,
                            "yes" if failure.retryable else "no",
                            failure.display_reason,
                        )
                        for failure in snapshot.failed_executions
                    ),
                ),
            ]
        )
    if snapshot.reconciliation and snapshot.reconciliation.get("status") == "pending":
        reconciliation = snapshot.reconciliation
        lines.extend(
            [
                "",
                render_table(
                    "Reconciliation",
                    tuple(
                        (key.replace("_", " ").title(), value)
                        for key, value in reconciliation.items()
                        if key in {
                            "ticket_id",
                            "original_base",
                            "observed_product",
                            "worker_checkpoint",
                            "product_target_eligible",
                            "product_observation",
                        }
                    ),
                ),
            ]
        )
    return "\n".join(lines)


def _render_plan_text(plan: ExecutionPlan) -> str:
    lines = [
        f"Devlegate {__version__}",
        "",
        "Execution plan:",
        f"Plan: {plan.action} · {plan.reason}",
    ]
    if plan.ticket_id is not None:
        lines.append(f"Ticket: {plan.ticket_id} · {plan.ticket_title}")
        lines.append(f"Ticket state: {plan.ticket_state}")
    if plan.code:
        lines.append(
            f"Code: {plan.code.branch or '<detached>'} @ "
            f"{_short_hash(plan.code.local_head)}"
        )
    if plan.control:
        lines.append(
            f"Control: {plan.control.branch or '<detached>'} @ "
            f"{_short_hash(plan.control.local_head)}"
        )
    else:
        lines.append("Control: missing")
    lines.append(f"Bound: {'yes' if plan.bound else 'no'}")
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
        render_grid(
            "Checks:",
            ("Check", "State", "Detail"),
            tuple(
                (
                    check["name"],
                    "OK" if check["passed"] else "FAIL",
                    check["detail"] or "",
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
    for line in (
        "=========================================================================",
        "           D | L",
        "---< D E V L E G A T E >---",
        "          S.P.Q.R.",
        "",
        f"version : {__version__}",
        f"repo    : {engine.repo}",
        f"product : {product_branch} @ {product_head}",
        f"control : {control}",
        f"mode    : {mode}",
        f"pid     : {os.getpid()}",
        f"instance: {engine._state_key[:12]}",
        "=========================================================================",
    ):
        service_log(line)


class DevlegateArgumentParser(argparse.ArgumentParser):
    """Present syntax errors concisely while retaining argparse parsing."""

    _top_level_groups = (
        (
            "Service",
            (
                ("foreground", "run the persistent service attached to this terminal"),
                ("once", "run one service pass, then exit"),
                ("stop", "stop the persistent service"),
                ("restart", "restart the persistent service"),
                ("service", "manage an explicitly registered external service"),
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
                ("project", "inspect and register local projects"),
            ),
        ),
        (
            "Recovery",
            (
                ("retry", "retry a failed or recoverable execution"),
                ("drop", "retire a blocked execution without applying it"),
                ("reconcile", "perform explicit reconciliation"),
            ),
        ),
        ("Other", (("version", "show program version"),)),
    )

    def format_usage(self) -> str:
        if self.prog == "devlegate":
            return "usage: devlegate [--env FILE | @ALIAS] COMMAND ...\n"
        return super().format_usage()

    def add_subparsers(self, **kwargs):
        kwargs.setdefault("title", "Commands")
        kwargs.setdefault("metavar", "COMMAND")
        return super().add_subparsers(**kwargs)

    def format_help(self) -> str:
        if self.prog == "devlegate":
            lines = [
                "usage: devlegate [--env FILE | @ALIAS] COMMAND ...",
                "",
                "Run ticket-driven coding workflows in a Git repository. With no "
                "command,",
                "ensure the persistent background service is running.",
                "",
                "Options:",
                "  --env FILE       explicit project configuration file",
                "  @ALIAS           registered project alias",
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
        return _render_status_text(
            snapshot, "stopped", _execution_projection(snapshot, None)
        )

    def status(self, json_output: bool = False) -> int:
        snapshot = self.status_view()
        print(
            json.dumps(snapshot.as_dict(), indent=2, sort_keys=True)
            if json_output
            else _render_status_text(
                snapshot, "stopped", _execution_projection(snapshot, None)
            )
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
    commands.add_parser(
        "foreground",
        help="run the persistent service attached to this terminal",
        description="Run the persistent service attached to this terminal.",
    )
    commands.add_parser(
        "once",
        help="run one service pass attached to this terminal",
        description="Run one service pass attached to this terminal, then exit.",
    )
    version_parser = commands.add_parser(
        "version",
        help="show program version",
        description="Show the concise program and version identity.",
    )
    add_output_arguments(version_parser)
    host_parser = commands.add_parser(
        "host",
        help="manage per-user Devlegate host integration",
        description="Install or remove per-user host supervision policy.",
    )
    host_commands = host_parser.add_subparsers(
        dest="host_action", required=True, parser_class=DevlegateArgumentParser
    )
    host_install_parser = host_commands.add_parser(
        "install", help="install per-user host integration"
    )
    host_install_parser.add_argument(
        "--supervisor", choices=("internal", "systemd"), required=True
    )
    add_output_arguments(host_install_parser)
    host_uninstall_parser = host_commands.add_parser(
        "uninstall", help="remove per-user host integration"
    )
    add_output_arguments(host_uninstall_parser)
    init_parser = commands.add_parser(
        "init",
        help="initialize project-local agent workflow files",
        description="Create missing project-local agent workflow files.",
    )
    init_parser.add_argument("alias", help="local project alias without @")
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
    add_output_arguments(render_parser)
    stop_parser = commands.add_parser(
        "stop",
        help="orderly stop the persistent workflow service",
        description="Request an orderly shutdown of the persistent service.",
    )
    add_output_arguments(stop_parser)
    restart_parser = commands.add_parser(
        "restart",
        help="restart the persistent workflow service at a checkpoint",
        description="Restart the self-managed service after a graceful checkpoint.",
    )
    add_output_arguments(restart_parser)
    check_parser = commands.add_parser(
        "check",
        help="validate setup readiness",
        description="Validate project setup without running a worker.",
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
    service_parser = commands.add_parser(
        "service",
        help="manage an explicitly registered external service",
        description="Register and control a Devlegate external supervisor service.",
    )
    service_commands = service_parser.add_subparsers(
        dest="service_action", required=True, parser_class=DevlegateArgumentParser
    )
    for action in ("install", "remove", "start", "stop", "restart", "status"):
        action_parser = service_commands.add_parser(
            action,
            help=f"{action} the systemd user service",
            description=f"{action.capitalize()} the Devlegate systemd user service.",
        )
        action_parser.add_argument(
            "--supervisor",
            choices=("systemd",),
            default="systemd",
            help="external supervisor backend",
        )
    project_parser = commands.add_parser(
        "project",
        help="inspect and register local projects",
        description="Manage the local Devlegate project registry.",
    )
    project_commands = project_parser.add_subparsers(
        dest="project_action", required=True, parser_class=DevlegateArgumentParser
    )
    list_parser = project_commands.add_parser("list", help="list registered projects")
    add_output_arguments(list_parser)
    resolve_parser = project_commands.add_parser(
        "resolve", help="resolve a registered alias"
    )
    resolve_parser.add_argument("alias", help="registered alias, including @")
    add_output_arguments(resolve_parser)
    identify_parser = project_commands.add_parser(
        "identify", help="identify the registered project for a path"
    )
    identify_parser.add_argument("path", nargs="?", type=Path)
    add_output_arguments(identify_parser)
    alias_parser = project_commands.add_parser(
        "alias", help="register an existing project"
    )
    alias_parser.add_argument("alias", help="local project alias without @")
    alias_parser.add_argument("path", nargs="?", type=Path)
    add_output_arguments(alias_parser)
    rename_parser = project_commands.add_parser(
        "rename", help="rename a local project alias"
    )
    rename_parser.add_argument("old_alias")
    rename_parser.add_argument("new_alias")
    add_output_arguments(rename_parser)
    remove_parser = project_commands.add_parser(
        "remove", help="decommission a registered project without deleting it"
    )
    remove_parser.add_argument("alias", help="registered alias, including @")
    add_output_arguments(remove_parser)
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
    add_output_arguments(retry_parser)
    drop_parser = commands.add_parser(
        "drop",
        help="retire a blocked execution without applying it",
        description="Retire one blocked execution while preserving its evidence.",
    )
    drop_parser.add_argument(
        "ticket_id",
        nargs="?",
        help="ticket to drop; omit it to choose from current candidates",
    )
    add_output_arguments(drop_parser)
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
    add_output_arguments(resume_parser)
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
    add_output_arguments(init_parser)
    return parser


def _run_default_command(
    *,
    project_alias: str | None,
    service_env: Path | None,
    startup_fd: int | None,
) -> int:
    try:
        target = _project_target(alias=project_alias, env_file=service_env)
        assert target is not None
        env_file = target.env_file
        health = _healthy_service(env_file)
        if health is not None:
            _warn_service_version_mismatch(health)
            print("Devlegate service is already running.")
            return 0
        supervisor = SystemdSupervisor()
        _store, established = _systemd_authority_established(target, supervisor)
        if established:
            return _start_systemd(target)
        try:
            supervisor.probe_user_manager()
        except SystemdSupervisorError:
            print("systemd user supervision unavailable; running attached")
            return _run_attached_target(
                target,
                host_mode=HostingMode.DIRECT,
                once=False,
                startup_fd=startup_fd,
            )
        try:
            return _start_systemd(target)
        except DevlegateError as error:
            try:
                supervisor.remove(target.locator, env_file=target.env_file)
            except SystemdSupervisorError as cleanup_error:
                raise DevlegateError(
                    f"systemd startup failed and managed-unit cleanup failed: {cleanup_error}"
                ) from error
            print("systemd user supervision unavailable; running attached")
            return _run_attached_target(
                target,
                host_mode=HostingMode.DIRECT,
                once=False,
                startup_fd=startup_fd,
            )
    except KeyboardInterrupt:
        _notify_startup_failure(KeyboardInterrupt())
        return 130
    except DevlegateError as error:
        _notify_startup_failure(error)
        print(f"devlegate: {error}", file=sys.stderr)
        return 1


def _run_attached_command(
    args: argparse.Namespace,
    *,
    project_alias: str | None,
    startup_fd: int | None,
) -> int:
    try:
        target = _project_target(
            alias=project_alias,
            env_file=args.service_env,
        )
        assert target is not None
        host_mode = _host_mode()
        health = (
            None
            if os.environ.get("DEVLEGATE_RESTART_AUTHORITY_FD") is not None
            else _healthy_service(target.env_file)
        )
        if health is not None:
            _warn_service_version_mismatch(health)
            print("Devlegate service is already running.")
            return 0
        return _run_attached_target(
            target,
            host_mode=host_mode,
            once=args.command == "once",
            startup_fd=startup_fd,
        )
    except KeyboardInterrupt:
        _notify_startup_failure(KeyboardInterrupt())
        return 130
    except DevlegateError as error:
        _notify_startup_failure(error)
        print(f"devlegate: {error}", file=sys.stderr)
        return 1


def main() -> int:
    parser = build_parser()
    argv, project_alias = _selector_argv(sys.argv[1:], parser)
    args = parser.parse_args(argv)
    args.project_alias = project_alias
    startup_fd = _startup_fd()
    match args.command:
        case None:
            return _run_default_command(
                project_alias=project_alias,
                service_env=args.service_env,
                startup_fd=startup_fd,
            )
        case "version":
            if project_alias is not None or args.service_env is not None:
                parser.error("project selectors are not valid for version")
            value = {"program": "devlegate", "version": __version__}
            emit(
                value,
                args.output_format,
                f"Devlegate {__version__}",
            )
            return 0
        case "host":
            if project_alias is not None or args.service_env is not None:
                parser.error("project selectors are not valid for host commands")
            try:
                if args.host_action == "install":
                    return _install_host(args)
                if args.host_action == "uninstall":
                    return _uninstall_host(args)
                raise DevlegateError("unsupported host command")
            except DevlegateError as error:
                print(f"devlegate: {error}", file=sys.stderr)
                return 1
        case "foreground" | "once":
            return _run_attached_command(
                args,
                project_alias=project_alias,
                startup_fd=startup_fd,
            )
    if args.command == "control" and args.control_command != "init":
        DevlegateArgumentParser(prog="devlegate control").error(
            "a control command is required"
        )
    if args.command == "service":
        try:
            return _systemd_service_command(args)
        except DevlegateError as error:
            print(f"devlegate: {error}", file=sys.stderr)
            return 1
    if args.command == "reconcile" and args.reconcile_command is None:
        DevlegateArgumentParser(prog="devlegate reconcile").error(
            "a reconcile command is required"
        )
    if args.command == "project":
        if project_alias is not None or args.service_env is not None:
            parser.error("project selectors are not valid for project commands")
        try:
            return _project_command(args)
        except DevlegateError as error:
            print(f"devlegate: {error}", file=sys.stderr)
            return 1
    if args.command == "init":
        if project_alias is not None:
            parser.error("@ALIAS is not valid for init; provide a new alias")
        if args.service_env is not None:
            parser.error("--env is not valid for init; run it from the repository root")
        try:
            alias, env_file, repo = _init_target(args.alias)
            if not env_file.exists():
                try:
                    seed_project_env(env_file)
                except AgentProtocolError as error:
                    raise DevlegateError(str(error)) from error
            devlegate = Devlegate(env_file, read_only=True, repository=repo)
            value = devlegate.init_project_result(args.conflicts)
            target = ProjectRegistry().register(alias, env_file)
            value = {
                **value,
                "command": "init",
                "alias": f"@{target.alias}",
            }
            emit(
                value,
                args.output_format,
                "Initialized and registered Devlegate project; "
                f"rendered {value['rendered']} artifacts as @{target.alias}.",
            )
            return 0
        except (DevlegateError, ProjectRegistryError) as error:
            print(f"devlegate: {error}", file=sys.stderr)
            return 1
    env_file = (args.service_env or Path.cwd() / ".env").expanduser().resolve()
    try:
        target = _project_target(alias=project_alias, env_file=args.service_env)
        assert target is not None
        env_file = target.env_file
        devlegate = None
        if args.command in {"render", "check", "control"}:
            devlegate = Devlegate(env_file, read_only=True, repository=target.repo)
        if args.command == "render":
            value = devlegate.render_result(args.check)
            value = {**value, "command": "render"}
            emit(
                value,
                args.output_format,
                (
                    "Generated agent protocol artifacts are current."
                    if args.check
                    else f"Rendered {value['artifacts']} agent protocol artifacts "
                    f"({value['changed']} changed)."
                ),
            )
            return 0
        if args.command == "control":
            value = devlegate.control_init_result()
            value = {**value, "command": "control init"}
            control_messages = {
                "already_attached": "Control worktree already attached",
                "attached_existing_remote": "Control worktree attached",
                "initialized_new_control_plane": "Control plane initialized",
            }
            emit(
                value,
                args.output_format,
                f"{control_messages[value['result']]}: "
                f"{value['control_worktree']}",
            )
            return 0
        if args.command == "status":
            view = _read_only_view(env_file, "status")
            snapshot = view.value
            assert isinstance(snapshot, StatusSnapshot)
            execution = _execution_projection(
                snapshot, view.live_execution, view.service_state
            )
            payload = {
                "client": {"version": __version__},
                "service": {"state": view.service_state},
                **snapshot.as_dict(),
            }
            if view.service_metadata is not None:
                payload["service"].update(view.service_metadata)
            if view.service_failure is not None:
                payload["service"]["failure"] = view.service_failure
            payload["execution"] = {
                **payload["execution"],
                "state": execution["state"],
                "ticket_title": execution["ticket_title"],
            }
            if view.live_execution is not None:
                payload["live_execution"] = view.live_execution
            emit(
                payload,
                args.output_format,
                _render_status_text(
                    snapshot,
                    view.service_state,
                    execution,
                    view.service_failure,
                    view.service_metadata,
                ),
            )
            return (
                1
                if snapshot.plan.action == "blocked" or view.service_failure is not None
                else 0
            )
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
        if args.command == "restart":
            return _restart_service(env_file, args.output_format)
        if args.command == "retry":
            return _retry_daemon(env_file, args.ticket_id, args.output_format)
        if args.command == "drop":
            return _drop_daemon(env_file, args.ticket_id, args.output_format)
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
