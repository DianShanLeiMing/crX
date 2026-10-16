"""Lifecycle commands always use the national client in the selected device."""

import pytest

from crX.emulators.adb import AdbController
from crX.emulators.base import CLASH_ROYALE_PACKAGE
from crX.utils.logger import Logger


def test_restart_checks_stops_and_launches_the_national_client(monkeypatch):
    from crX.bot import nav

    device = AdbController(Logger(), "127.0.0.1:16416")
    calls = []
    monkeypatch.setattr(device, "start", lambda: None)
    monkeypatch.setattr(device, "is_app_installed", lambda package: calls.append(["check", package]) or True)
    monkeypatch.setattr(device, "configure", lambda: calls.append("configure"))
    monkeypatch.setattr(device, "adb", lambda args: calls.append(args))
    monkeypatch.setattr(device, "start_app", lambda package: calls.append(["launch", package]))
    monkeypatch.setattr(device, "verify_screen", lambda: calls.append("verify"))
    monkeypatch.setattr(nav, "wait_for_clash_main_menu", lambda *args, **kwargs: True)
    monkeypatch.setattr("crX.emulators.adb.time.sleep", lambda _: None)
    assert device.restart()
    assert calls == [
        ["check", CLASH_ROYALE_PACKAGE],
        "configure",
        ["shell", "am", "force-stop", CLASH_ROYALE_PACKAGE],
        ["launch", CLASH_ROYALE_PACKAGE],
        "verify",
    ]
    assert "国服已就绪" in device.logger.current_status


def test_stop_targets_only_the_national_client(monkeypatch):
    device = AdbController(Logger(), "127.0.0.1:16416")
    calls = []
    monkeypatch.setattr(device, "adb", lambda args: calls.append(args))
    device.stop()
    assert calls == [["shell", "am", "force-stop", CLASH_ROYALE_PACKAGE]]


def test_stop_without_a_selected_device_does_nothing(monkeypatch):
    device = AdbController(Logger())
    monkeypatch.setattr(device, "adb", lambda _: pytest.fail("Must not execute a device command without a target"))
    device.stop()
