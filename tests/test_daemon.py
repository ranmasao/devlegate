import signal
import threading

import pytest
from test_control_plane import control_fixture, invoke

import devlegate.cli as cli
import devlegate.daemon as daemon
from devlegate.cli import DevlegateError
from devlegate.service import ServiceEngine
from devlegate.worker_egress import WorkerRunResult


def make_engine(tmp_path, monkeypatch):
    working, config, state = control_fixture(tmp_path)
    assert invoke(working, "control", "init", config=config).returncode == 0
    monkeypatch.chdir(working)
    return ServiceEngine(config), config, state


def test_daemon_command_constructs_one_service_engine(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    calls = []

    class FakeServiceEngine:
        def __init__(self, env_file, *, read_only=False):
            calls.append(("init", env_file, read_only))

    def host(engine):
        calls.append(("host", engine))
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
    )
    monkeypatch.chdir(working)

    assert cli.main() == 0
    assert len(calls) == 2
    assert calls[0] == ("init", config, False)
    assert calls[1][0] == "host"
    assert isinstance(calls[1][1], FakeServiceEngine)


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_daemon_host_signal_handler_only_sets_stop_intent(monkeypatch, signum):
    installed = {}

    def install(signum, handler):
        if callable(handler):
            installed[signum] = handler

    class FakeServiceEngine:
        def serve(self, stop_event):
            installed[signum](signum, None)
            assert stop_event.is_set()
            return 0

    monkeypatch.setattr(daemon.signal, "signal", install)
    monkeypatch.setattr(daemon.signal, "getsignal", lambda _signum: signal.SIG_DFL)

    assert daemon.run_daemon(FakeServiceEngine()) == 0
    assert set(installed) == {signal.SIGINT, signal.SIGTERM}


def test_daemon_cli_remains_foreground_until_host_returns(tmp_path, monkeypatch):
    working, config, _state = control_fixture(tmp_path)
    entered = threading.Event()
    release = threading.Event()
    result = []

    class FakeServiceEngine:
        def __init__(self, _env_file, *, read_only=False):
            assert not read_only

    def host(_engine):
        entered.set()
        assert release.wait(10)
        return 0

    monkeypatch.setattr(cli, "ServiceEngine", FakeServiceEngine)
    monkeypatch.setattr(cli, "run_daemon", host)
    monkeypatch.setattr(
        cli.sys,
        "argv",
        ["devlegate", "daemon", "--env", str(config)],
    )
    monkeypatch.chdir(working)
    thread = threading.Thread(target=lambda: result.append(cli.main()), daemon=True)
    thread.start()
    assert entered.wait(10)
    assert thread.is_alive()
    release.set()
    thread.join(10)

    assert result == [0]


@pytest.mark.parametrize("signum", [signal.SIGINT, signal.SIGTERM])
def test_daemon_signal_wakes_poll_wait_without_second_iteration(
    tmp_path, monkeypatch, signum
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    stop_event = threading.Event()
    calls = []
    result = []

    def iteration():
        calls.append(True)
        entered.set()
        return 0

    monkeypatch.setattr(engine, "run_once", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)
    stop_event.set()
    thread.join(10)

    assert not thread.is_alive()
    assert result == [0]
    assert len(calls) == 1


def test_daemon_holds_project_lock_while_service_is_active(tmp_path, monkeypatch):
    engine, config, state = make_engine(tmp_path, monkeypatch)
    entered = threading.Event()
    release = threading.Event()
    stop_event = threading.Event()
    result = []

    def iteration():
        entered.set()
        assert release.wait(10)
        stop_event.set()
        return 0

    monkeypatch.setattr(engine, "run_once", iteration)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert entered.wait(10)

    second = ServiceEngine(config)
    before = dict(second._state)
    with pytest.raises(DevlegateError, match="another devlegate instance"):
        second.run(once=True)
    assert second.status().phase == "idle"
    assert second.plan().action == "run-worker"
    assert second._state == before

    release.set()
    thread.join(10)
    assert result == [0]
    assert state.exists()


def test_daemon_stop_during_worker_finishes_attempt_without_next_ticket(
    tmp_path, monkeypatch
):
    engine, _config, _state = make_engine(tmp_path, monkeypatch)
    worker_entered = threading.Event()
    worker_release = threading.Event()
    stop_event = threading.Event()
    worker_calls = []
    result = []

    def worker(_workspace, _prompt):
        worker_calls.append(True)
        worker_entered.set()
        assert worker_release.wait(10)
        return WorkerRunResult(1, None, None, None)

    monkeypatch.setattr(engine, "_run_worker", worker)
    thread = threading.Thread(
        target=lambda: result.append(engine.serve(stop_event)), daemon=True
    )
    thread.start()
    assert worker_entered.wait(10)
    stop_event.set()

    assert engine.service_snapshot().worker_running is True
    assert thread.is_alive()
    assert worker_calls == [True]

    worker_release.set()
    thread.join(10)
    assert not thread.is_alive()
    assert result == [0]
    assert worker_calls == [True]
    assert engine.service_snapshot().worker_running is False
    assert engine._state["phase"] == "idle"


def test_daemon_has_no_unix_daemonization_path():
    source = open(daemon.__file__, encoding="ascii").read()
    assert "os.fork" not in source
    assert "setsid" not in source
