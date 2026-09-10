import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import Callable

from devlegate.ipc_client import IPCClientError, request
from devlegate.runtime_locator import RuntimeLocator, read_env


class LiveService:
    """Small production-process harness for one foreground Devlegate service."""

    def __init__(self, cwd: Path, env_file: Path) -> None:
        self.cwd = cwd
        self.env_file = env_file
        self.locator = RuntimeLocator.from_config(
            env_file, cwd, read_env(env_file)
        )
        self.process: subprocess.Popen[str] | None = None
        self.stdout = ""
        self.stderr = ""

    def start(self) -> "LiveService":
        environment = {
            **os.environ,
            "PYTHONPATH": str(Path(__file__).parents[1] / "src"),
        }
        self.process = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "devlegate",
                "run",
                "--env",
                str(self.env_file),
            ],
            cwd=self.cwd,
            env=environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
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
                    if response == {"service": "devlegate", "protocol_version": 1}:
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
        }
        return subprocess.run(
            [
                sys.executable,
                "-m",
                "devlegate",
                *args,
                "--env",
                str(self.env_file),
            ],
            cwd=self.cwd,
            env=environment,
            text=True,
            capture_output=True,
            timeout=timeout,
        )

    def wait_for(
        self, predicate: Callable[[], bool], timeout: float = 15
    ) -> None:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if predicate():
                return
            time.sleep(0.05)
        raise AssertionError("service condition did not become true before timeout")

    def stop(self) -> None:
        if self.process is None:
            return
        if self.process.poll() is None:
            self.process.send_signal(signal.SIGTERM)
        try:
            self.stdout, self.stderr = self.process.communicate(timeout=10)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.stdout, self.stderr = self.process.communicate(timeout=5)
            raise AssertionError(
                f"service teardown timed out\nstdout:\n{self.stdout}\n"
                f"stderr:\n{self.stderr}"
            )
        assert self.process.returncode == 0, (
            f"service exited with {self.process.returncode}\n"
            f"stdout:\n{self.stdout}\nstderr:\n{self.stderr}"
        )
        assert not self.locator.socket_path.exists()
        assert not self.locator.daemon_authority_present()

    def _collect_output(self) -> None:
        if self.process is None or self.process.poll() is None:
            return
        self.stdout, self.stderr = self.process.communicate(timeout=2)

    def __enter__(self) -> "LiveService":
        self.start()
        try:
            self.wait_ready()
        except BaseException:
            if self.process is not None and self.process.poll() is None:
                self.process.kill()
            if self.process is not None:
                self.stdout, self.stderr = self.process.communicate(timeout=5)
            raise
        return self

    def __exit__(self, _type, _value, _traceback) -> None:
        self.stop()
