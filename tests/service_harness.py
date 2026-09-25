# Copyright (c) 2026 Daniil Romanov
# Licensed under the EUPL-1.2.
# SPDX-License-Identifier: EUPL-1.2
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

from devlegate.host_installation import HostInstallation
from devlegate.host_installation import write as write_installation
from devlegate.ipc_client import IPCClientError, request
from devlegate.project_registry import ProjectRegistry
from devlegate.runtime_locator import RuntimeLocator, read_env


class LiveService:
    """Small production-process harness for one foreground Devlegate service."""

    def __init__(
        self,
        cwd: Path,
        env_file: Path,
        command: tuple[str, ...] | None = None,
    ) -> None:
        self.cwd = cwd
        self.env_file = env_file
        self.registry_home = env_file.parent / ".registry-config"
        ProjectRegistry(
            self.registry_home / "devlegate" / "projects.json"
        ).register(
            "test", env_file
        )
        write_installation(
            self.registry_home / "devlegate" / "installation.json",
            HostInstallation("internal"),
        )
        self.locator = RuntimeLocator.from_config(
            env_file, cwd, read_env(env_file)
        )
        self.process: subprocess.Popen[str] | None = None
        self.stdout = ""
        self.stderr = ""
        self.command = command
        self._output_dir: tempfile.TemporaryDirectory[str] | None = None
        self._stdout_file = None
        self._stderr_file = None
        self._abruptly_killed = False

    def start(self) -> "LiveService":
        if self._output_dir is not None:
            for stream in (self._stdout_file, self._stderr_file):
                if stream is not None:
                    stream.close()
            self._output_dir.cleanup()
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(self.registry_home),
        }
        command = self.command or (
            sys.executable,
            "-m",
            "devlegate",
            "--env",
            str(self.env_file),
            "foreground",
        )
        self._output_dir = tempfile.TemporaryDirectory(prefix="devlegate-service-")
        self._stdout_file = open(
            Path(self._output_dir.name) / "stdout", "w+", encoding="utf-8"
        )
        self._stderr_file = open(
            Path(self._output_dir.name) / "stderr", "w+", encoding="utf-8"
        )
        self.process = subprocess.Popen(
            list(command),
            cwd=self.cwd,
            env=environment,
            text=True,
            stdout=self._stdout_file,
            stderr=self._stderr_file,
        )
        self._abruptly_killed = False
        return self

    def wait_ready(self, timeout: float = 10) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                self._collect_output()
                raise AssertionError(
                    f"service exited before readiness\nstdout:\n{self.stdout}\n"
                    f"stderr:\n{self.stderr}"
                )
            if self.locator.daemon_authority_present():
                try:
                    response = request(
                        self.locator.socket_path, "ping", timeout=0.2
                    )
                except IPCClientError:
                    pass
                else:
                    if (
                        response.get("service") == "devlegate"
                        and response.get("protocol_version") == 1
                        and response.get("ready") is True
                    ):
                        return
            time.sleep(0.02)
        self._collect_output()
        raise AssertionError(
            f"service did not become ready\nstdout:\n{self.stdout}\n"
            f"stderr:\n{self.stderr}"
        )

    def cli(self, *args: str, timeout: float = 20) -> subprocess.CompletedProcess[str]:
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(self.registry_home),
        }
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "devlegate",
                "--env",
                str(self.env_file),
                *args,
            ],
            cwd=self.cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def start_cli(self, *args: str) -> subprocess.Popen[str]:
        """Start a real CLI client without coupling its lifetime to the service."""
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
            "XDG_CONFIG_HOME": str(self.registry_home),
        }
        return subprocess.Popen(
            [
                sys.executable,
                "-m",
                "devlegate",
                "--env",
                str(self.env_file),
                *args,
            ],
            cwd=self.cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )

    def wait_for(
        self, predicate: Callable[[], bool], timeout: float = 15
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.process is not None and self.process.poll() is not None:
                self._collect_output()
                raise AssertionError(
                    f"service exited while waiting (return code "
                    f"{self.process.returncode})\nstdout:\n{self.stdout}\n"
                    f"stderr:\n{self.stderr}"
                )
            if predicate():
                return
            time.sleep(0.05)
        self._collect_output()
        raise AssertionError(
            "service condition did not become true before timeout\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )

    def kill(self, timeout: float = 10) -> None:
        """Abruptly kill the service while preserving its durable artifacts."""
        if self.process is None or self.process.poll() is not None:
            return
        self.process.kill()
        self._abruptly_killed = True
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            raise AssertionError("service did not die after SIGKILL") from error
        self._collect_output()

    def restart(self, timeout: float = 10) -> "LiveService":
        self.kill(timeout=timeout)
        self.start()
        self.wait_ready(timeout=timeout)
        return self

    def stop(self) -> None:
        if self.process is None:
            return
        if not self.locator.daemon_authority_present():
            self.wait_exited()
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        try:
            self.process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)
            self._collect_output()
            raise AssertionError(
                f"service teardown timed out\nstdout:\n{self.stdout}\n"
                f"stderr:\n{self.stderr}"
            )
        self._collect_output()
        assert self.process.returncode == 0 or (
            self._abruptly_killed and self.process.returncode == -signal.SIGKILL
        ), (
            f"service exited with {self.process.returncode}\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )
        assert not self.locator.socket_path.exists()
        assert not self.locator.daemon_authority_present()

    def wait_exited(self, timeout: float = 10) -> None:
        """Wait for an already-stopping service to exit without signaling it."""
        if self.process is None:
            raise AssertionError("service was not started")
        try:
            self.process.wait(timeout=timeout)
        except subprocess.TimeoutExpired as error:
            self._collect_output()
            raise AssertionError(
                f"service did not exit naturally\nstdout:\n{self.stdout}\n"
                f"stderr:\n{self.stderr}"
            ) from error
        self._collect_output()
        assert self.process.returncode == 0 or (
            self._abruptly_killed and self.process.returncode == -signal.SIGKILL
        ), (
            f"service exited with {self.process.returncode}\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )
        assert not self.locator.socket_path.exists()
        assert not self.locator.daemon_authority_present()

    def _collect_output(self) -> None:
        if self.process is None or self.process.poll() is None:
            return
        for stream, target in (
            (self._stdout_file, "stdout"),
            (self._stderr_file, "stderr"),
        ):
            if stream is None:
                continue
            stream.flush()
            stream.seek(0)
            setattr(self, target, stream.read())

    def __enter__(self) -> "LiveService":
        self.start()
        try:
            self.wait_ready()
        except BaseException:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
            if self.process is not None:
                self.process.wait(timeout=5)
                self._collect_output()
            raise
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        try:
            self.stop()
        finally:
            for stream in (self._stdout_file, self._stderr_file):
                if stream is not None:
                    stream.close()
            if self._output_dir is not None:
                self._output_dir.cleanup()
