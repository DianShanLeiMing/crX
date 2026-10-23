"""Worker retries one timed-out screen read, never input or whole gameplay states."""

import subprocess
from threading import Event
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from crX.bot import worker
from crX.emulators.adb import AdbController
from crX.interface.enums import UIField
from crX.utils.logger import Logger


def device_with_capture(monkeypatch, effects):
    device = AdbController(Logger(), "127.0.0.1:16416")
    device.adb_path = "adb"
    device.device_serial = "127.0.0.1:16416"
    command = ["adb", "-s", device.device_serial, "exec-out", "screencap", "-p"]
    frame = np.zeros((640, 360, 3), dtype=np.uint8)
    ok, encoded = cv2.imencode(".png", frame)
    assert ok
    result = subprocess.CompletedProcess(command, 0, encoded.tobytes(), b"")
    effects = [subprocess.TimeoutExpired(command, 20) if effect == "timeout" else result for effect in effects]
    run = Mock(side_effect=effects)
    monkeypatch.setattr("crX.emulators.adb_base.run_command", run)
    signal = Event()
    monkeypatch.setattr(signal, "wait", lambda _timeout: signal.is_set())
    return device, signal, run


def test_single_timeout_recovers_frame_and_restores_adapter(monkeypatch):
    device, signal, run = device_with_capture(monkeypatch, ["timeout", "ok"])
    original = device.screenshot
    with worker.screenshot_retry_policy(device, Logger(), signal):
        assert device.screenshot().shape == (640, 360, 3)
    assert run.call_count == 2
    assert device.screenshot == original and "screenshot" not in vars(device)
    assert all(call.args[0][-3:] == ["exec-out", "screencap", "-p"] for call in run.call_args_list)


def test_repeated_timeout_is_propagated_after_one_retry(monkeypatch):
    device, signal, run = device_with_capture(monkeypatch, ["timeout", "timeout"])
    with pytest.raises(subprocess.TimeoutExpired), worker.screenshot_retry_policy(device, Logger(), signal):
        device.screenshot()
    assert run.call_count == 2 and "screenshot" not in vars(device)


def test_stop_signal_prevents_retry(monkeypatch):
    device, signal, run = device_with_capture(monkeypatch, ["timeout"])
    signal.set()
    with pytest.raises(subprocess.TimeoutExpired), worker.screenshot_retry_policy(device, Logger(), signal):
        device.screenshot()
    assert run.call_count == 1


@pytest.mark.parametrize(
    "error", [RuntimeError("bad capture"), subprocess.TimeoutExpired(["adb", "shell", "input", "tap", "1", "2"], 20)]
)
def test_non_capture_errors_and_input_are_never_retried(error):
    device = Mock()
    original = device.screenshot = Mock(side_effect=error)
    with pytest.raises(type(error)), worker.screenshot_retry_policy(device, Logger(), Event()):
        device.screenshot()
    assert original.call_count == 1 and device.screenshot is original


def test_worker_does_not_replay_fight_state_or_restart_after_transient_timeout(monkeypatch):
    device, signal, run = device_with_capture(monkeypatch, ["timeout", "ok"])
    states = []

    def tree(emulator, _logger, state, _jobs, context):
        states.append(state)
        if state == "start":
            return "fight"
        assert state == "fight"
        emulator.screenshot()
        context.completed_fights += 1
        return "end_fight"

    monkeypatch.setattr(worker, "state_tree", tree)
    monkeypatch.setattr(worker, "FailureSampleRecorder", Mock(return_value=Mock()))
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = signal
    assert process._run_bot_loop(device, {UIField.MAX_FIGHTS: 1}, Logger())
    assert states == ["start", "fight"] and run.call_count == 2


def test_retry_policy_also_applies_to_restart_internal_capture(monkeypatch):
    device, signal, run = device_with_capture(monkeypatch, ["timeout", "ok"])

    def restart():
        device.screenshot()

    with worker.screenshot_retry_policy(device, Logger(), signal):
        restart()
    assert run.call_count == 2 and "screenshot" not in vars(device)
