"""MuMu protocol tests without hardware."""

import json
import subprocess
from pathlib import Path

import numpy as np
import pytest

from crX.emulators import adb_base, mumu
from crX.emulators.adb import AdbController
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
    monkeypatch.setattr(device, "screenshot", lambda: np.zeros((419, 633, 3), dtype=np.uint8))
    with pytest.raises(EmulatorNotReadyError, match="419x633"):
        device.verify_screen()
