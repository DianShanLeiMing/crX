"""MuMu protocol tests without hardware."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from crX.emulators import adb_base, mumu
from crX.emulators.adb import AdbConnectionNotReadyError, AdbController
from crX.emulators.base import EmulatorNotReadyError
from crX.utils.logger import Logger


def test_parse_supported_info_formats():
    data = {"2": {"name": "test"}, "0": {"index": "0", "name": "MuMu"}}
    assert [v["index"] for v in mumu.parse_instances(json.dumps(data))] == ["0", "2"]
    assert mumu.parse_instances('{"index": "0", "error_code": 0}')[0]["index"] == "0"
    assert mumu.parse_instances('[{"index": "1"}]')[0]["index"] == "1"
    assert mumu.parse_instances('{"error": "not ready"}') == []


@pytest.mark.parametrize(
    "info",
    [
        {"adb_host_ip": "10.0.0.1", "adb_port": 16384},
        {"adb_host_ip": "example.com", "adb_port": 16384},
        {"adb_port": 0},
        {"adb_port": 99999},
        {"adb_port": "bad"},
    ],
)
def test_manager_endpoint_must_be_local_and_valid(info):
    with pytest.raises(EmulatorNotReadyError):
        mumu.adb_endpoint(info)


def test_dynamic_port_is_used():
    assert mumu.adb_endpoint({"adb_host_ip": "127.0.0.1", "adb_port": 16417}) == "127.0.0.1:16417"


def test_gbk_manager_output(monkeypatch):
    data = json.dumps({"0": {"index": "0", "name": "MuMu安卓设备"}}, ensure_ascii=False).encode("gb18030")
    monkeypatch.setattr(mumu, "run_command", lambda *a, **k: subprocess.CompletedProcess(a, 0, data, b""))
    assert mumu.parse_instances(mumu.run_manager(["MuMuManager.exe", "info"]).stdout)[0]["name"] == "MuMu安卓设备"


def test_launch_scopes_selected_instance_and_queries_port(monkeypatch):
    device = mumu.MuMuEmulatorController.__new__(mumu.MuMuEmulatorController)
    device.manager_path = Path("MuMuManager.exe")
    device.instance_index, device.adb_path, device.logger = "2", "adb", Logger()
    info = iter([{"is_process_started": False}, {"is_android_started": True, "adb_port": 16417}])
    monkeypatch.setattr(device, "_info", lambda: next(info))
    commands, connections = [], []
    monkeypatch.setattr(
        mumu, "run_manager", lambda args, **kw: commands.append(args) or subprocess.CompletedProcess(args, 0, "", "")
    )
    monkeypatch.setattr(mumu, "resolve_adb", lambda p: p)
    monkeypatch.setattr(AdbController, "start", lambda self: connections.append(self.device_serial))
    device.start()
    assert commands == [["MuMuManager.exe", "control", "-v", "2", "launch"]]
    assert connections == ["127.0.0.1:16417"]


def test_adb_uses_argument_vector_with_timeout(monkeypatch):
    calls = []
    monkeypatch.setattr(
        adb_base.subprocess,
        "run",
        lambda args, **kw: calls.append((args, kw)) or subprocess.CompletedProcess(args, 0, "", ""),
    )
    device = AdbController(Logger(), "127.0.0.1:16417")
    device.adb("shell input tap 120 330")
    args, options = calls[0]
    assert args == [device.adb_path, "-s", "127.0.0.1:16417", "shell", "input", "tap", "120", "330"]
    assert options["shell"] is False
    assert options["timeout"] == 20


def test_missing_game_does_not_modify_display(monkeypatch):
    device = AdbController(Logger(), "127.0.0.1:16417")
    monkeypatch.setattr(device, "start", lambda: None)
    monkeypatch.setattr(device, "is_app_installed", lambda *a: False)
    monkeypatch.setattr(device, "configure", lambda: pytest.fail("Must not modify the display when game is absent"))
    with pytest.raises(EmulatorNotReadyError, match="未安装"):
        device.restart()


def test_rotated_screenshot_is_rejected(monkeypatch):
    device = AdbController(Logger(), "127.0.0.1:16417")
    monkeypatch.setattr(device, "screenshot", lambda: np.zeros((360, 640, 3), dtype=np.uint8))
    with pytest.raises(EmulatorNotReadyError, match="360x640"):
        device.verify_screen()


def test_mumu_waits_for_adb_after_android_ready_and_rechecks_port(monkeypatch):
    device = mumu.MuMuEmulatorController.__new__(mumu.MuMuEmulatorController)
    device.instance_index, device.adb_path, device.logger = "2", "adb", Logger()
    ports = iter([16417, 16417, 16418, 16418])
    monkeypatch.setattr(
        device,
        "_info",
        lambda: {"is_process_started": True, "is_android_started": True, "adb_port": next(ports)},
    )
    monkeypatch.setattr(mumu, "run_manager", lambda *_a, **_k: pytest.fail("Must not relaunch a running instance"))
    monkeypatch.setattr(mumu, "resolve_adb", lambda path: path)
    waits, connections = [], []
    monkeypatch.setattr(mumu.time, "sleep", waits.append)

    def connect(self):
        connections.append(self.device_serial)
        if len(connections) < 3:
            raise AdbConnectionNotReadyError("所选 ADB 设备已离线或尚未授权")

    monkeypatch.setattr(AdbController, "start", connect)
    device.start()
    assert connections == ["127.0.0.1:16417", "127.0.0.1:16418", "127.0.0.1:16418"]
    assert waits == [1, 1]


@pytest.mark.parametrize("android_ready", [True, False])
def test_mumu_boot_wait_remains_bounded(monkeypatch, android_ready):
    device = mumu.MuMuEmulatorController.__new__(mumu.MuMuEmulatorController)
    device.instance_index, device.adb_path, device.logger = "0", "adb", Logger()
    monkeypatch.setattr(
        device,
        "_info",
        lambda: {
            "is_process_started": True,
            "is_android_started": android_ready,
            "adb_port": 16384,
        },
    )
    monkeypatch.setattr(mumu, "resolve_adb", lambda path: path)
    clock = [0]
    monkeypatch.setattr(mumu.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(mumu.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))

    def offline(_self):
        raise AdbConnectionNotReadyError("所选 ADB 设备已离线或尚未授权")

    monkeypatch.setattr(AdbController, "start", offline)
    message = "ADB 连接就绪超时" if android_ready else "Android 系统启动超时"
    with pytest.raises(EmulatorNotReadyError, match=message):
        device.start()
    assert clock[0] == 120


def test_mumu_does_not_retry_unrelated_start_errors(monkeypatch):
    device = mumu.MuMuEmulatorController.__new__(mumu.MuMuEmulatorController)
    device.instance_index, device.adb_path, device.logger = "0", "adb", Logger()
    monkeypatch.setattr(
        device,
        "_info",
        lambda: {
            "is_process_started": True,
            "is_android_started": True,
            "adb_port": 16384,
        },
    )
    monkeypatch.setattr(mumu, "resolve_adb", lambda path: path)
    monkeypatch.setattr(mumu.time, "sleep", lambda _: pytest.fail("Unrelated errors must propagate"))

    def fail(_self):
        raise EmulatorNotReadyError("配置无效")

    monkeypatch.setattr(AdbController, "start", fail)
    with pytest.raises(EmulatorNotReadyError, match="配置无效"):
        device.start()
