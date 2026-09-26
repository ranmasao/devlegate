# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
"""Test-only foreground service driver with deterministic crash points."""

import argparse
import os
import signal
from pathlib import Path

from devlegate.daemon import run_service
from devlegate.service import ServiceEngine


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--env", type=Path, required=True)
    args = parser.parse_args()
    engine = ServiceEngine(args.env)
    original_ready = engine.mark_service_ready

    def mark_ready():
        original_ready()
        if os.environ.get("DEVLEGATE_TEST_WAKE_AFTER_READY") == "1":
            engine.wake()

    engine.mark_service_ready = mark_ready
    point = os.environ.get("DEVLEGATE_TEST_CRASH_POINT")
    marker_value = os.environ.get("DEVLEGATE_TEST_CRASH_MARKER")
    marker = Path(marker_value) if marker_value else None
    release_marker_value = os.environ.get("DEVLEGATE_TEST_OPERATOR_RELEASE_MARKER")
    release_marker = Path(release_marker_value) if release_marker_value else None

    def crash() -> None:
        if marker is not None:
            marker.write_text(point or "unknown")
        os.kill(os.getpid(), signal.SIGKILL)

    original_save = engine._save_state

    def save(phase: str, **fields: object) -> None:
        original_save(phase, **fields)
        if point == phase and point in {"agent_pending", "merge_pending"}:
            crash()
        if point is not None and point == fields.get("execution_stage"):
            crash()

    engine._save_state = save

    if release_marker is not None:
        original_release = engine._release_operator_command

        def release() -> None:
            original_release()
            release_marker.touch()

        engine._release_operator_command = release

    if point == "merge_after_effect":
        original_git = engine._git_runtime

        def git(repo: Path, *git_args: str, check: bool = True):
            result = original_git(repo, *git_args, check=check)
            if (
                repo == engine.repo
                and git_args[:1] == ("merge",)
                and "--ff-only" in git_args
                and result.returncode == 0
            ):
                crash()
            return result

        engine._git_runtime = git
    elif point == "checkpoint_after_effect":
        from devlegate.execution_workspace import ExecutionWorkspaceManager

        original_checkpoint = ExecutionWorkspaceManager.checkpoint

        def checkpoint(manager, workspace, execution_id, **kwargs):
            result = original_checkpoint(manager, workspace, execution_id, **kwargs)
            crash()
            return result

        ExecutionWorkspaceManager.checkpoint = checkpoint
    elif point == "publication_after_effect":
        original_publish = engine._publish_execution_branch

        def publish(*publish_args, **publish_kwargs):
            result = original_publish(*publish_args, **publish_kwargs)
            crash()
            return result

        engine._publish_execution_branch = publish
    elif point == "lifecycle_after_effect":
        original_lifecycle = engine._apply_execution_lifecycle

        def lifecycle(report):
            result = original_lifecycle(report)
            crash()
            return result

        engine._apply_execution_lifecycle = lifecycle
    elif point in {
        "integration_before_effect",
        "integration_product_after_effect",
        "integration_control_commit_after_effect",
        "integration_control_push_after_effect",
    }:
        from devlegate import runtime

        if point == "integration_before_effect":
            original_integrate = engine._integrate_accepted

            def integrate(*integrate_args, **integrate_kwargs):
                crash()
                return original_integrate(*integrate_args, **integrate_kwargs)

            engine._integrate_accepted = integrate
        else:
            original_git = runtime._git

            def git(repo, *git_args, **git_kwargs):
                result = original_git(repo, *git_args, **git_kwargs)
                is_product_push = (
                    repo == engine.repo
                    and git_args[:1] == ("push",)
                    and any("refs/heads/main" in arg for arg in git_args)
                )
                is_control_commit = (
                    repo == engine.control_worktree
                    and git_args[:1] == ("commit",)
                )
                is_control_push = (
                    repo == engine.control_worktree
                    and git_args[:1] == ("push",)
                    and any("refs/heads/devlegate/control" in arg for arg in git_args)
                )
                if (
                    point == "integration_product_after_effect" and is_product_push
                ) or (
                    point == "integration_control_commit_after_effect"
                    and is_control_commit
                ) or (
                    point == "integration_control_push_after_effect" and is_control_push
                ):
                    crash()
                return result

            runtime._git = git
    elif point == "receipt_after_save":
        original_record = engine._record_operator_admission

        def record(command):
            original_record(command)
            crash()

        engine._record_operator_admission = record

    return run_service(engine)


if __name__ == "__main__":
    raise SystemExit(main())
