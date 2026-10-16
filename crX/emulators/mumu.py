"""Thin MuMuManager adapter. All game I/O uses the shared ADB transport."""

from __future__ import annotations

import ipaddress
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

from crX.emulators.adb import AdbController
from crX.emulators.adb_base import resolve_adb, run_command
from crX.emulators.base import EmulatorNotReadyError
from crX.utils.platform import Platform, is_windows


def run_manager(args: list[str], *, timeout: int = 15):
    """MuMu Windows releases may emit either UTF-8 or GB18030 JSON."""
    result = run_command(args, binary=True, timeout=timeout)

    def decode(value: bytes) -> str:
        try:
            return value.decode("utf-8-sig")
        except UnicodeDecodeError:
            return value.decode("gb18030", errors="replace")

    return subprocess.CompletedProcess(result.args, result.returncode, decode(result.stdout), decode(result.stderr))


def find_manager(configured: str = "") -> Path:
    candidates = []
    if configured:
        path = Path(configured).expanduser().resolve()
        candidates = [path, path / "MuMuManager.exe", path / "nx_main/MuMuManager.exe", path / "shell/MuMuManager.exe"]
    else:
        found = shutil.which("MuMuManager.exe")
        if found:
            candidates.append(Path(found))
        if is_windows():
            import winreg

            for root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                for key_name in (
                    r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall",
                    r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall",
                ):
                    try:
                        with winreg.OpenKey(root, key_name) as key:
                            for index in range(winreg.QueryInfoKey(key)[0]):
                                with winreg.OpenKey(key, winreg.EnumKey(key, index)) as item:
                                    try:
                                        name = winreg.QueryValueEx(item, "DisplayName")[0]
                                        location = winreg.QueryValueEx(item, "InstallLocation")[0]
                                    except OSError:
                                        continue
                                    if "mumu" in str(name).lower() and location:
                                        base = Path(location)
                                        candidates += [base / "nx_main/MuMuManager.exe", base / "shell/MuMuManager.exe"]
                    except OSError:
                        continue
        for base in (
            os.environ.get("ProgramFiles", "C:/Program Files"),
            os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)"),
        ):
            candidates += [
                Path(base) / name / part / "MuMuManager.exe"
                for name in ("Netease/MuMuPlayer-12.0", "Netease/MuMu", "MuMuPlayer")
                for part in ("nx_main", "shell")
            ]
    for path in candidates:
        if path.is_file() and path.name.lower() == "mumumanager.exe":
            return path.resolve()
    raise EmulatorNotReadyError("未找到 MuMuManager.exe，请选择 MuMu 安装目录")


def parse_instances(output: str) -> list[dict]:
    # Some releases prepend a UTF-8 BOM or informational text.
    output = output.lstrip("\ufeff \r\n")
    data = json.loads(output)
    if isinstance(data, dict) and "index" in data:
        records = [data]
    elif isinstance(data, dict):
        records = [dict(value, index=value.get("index", key)) for key, value in data.items() if isinstance(value, dict)]
    elif isinstance(data, list):
        records = data
    else:
        raise ValueError("无法识别 MuMu 实例信息")
    result = []
    for record in records:
        if not isinstance(record, dict) or record.get("error_code", 0) != 0:
            continue
        index = str(record.get("index", ""))
        if index.isascii() and index.isdecimal() and 0 <= int(index) <= 10000:
            result.append(record | {"index": index})
    return sorted(result, key=lambda item: int(item["index"]))


def adb_endpoint(info: dict) -> str:
    host = str(info.get("adb_host_ip") or "127.0.0.1")
    # MuMu is a local adapter. Do not execute a manager-provided remote target.
    if host.lower() == "localhost":
        host = "127.0.0.1"
    try:
        address = ipaddress.ip_address(host)
        port = int(info.get("adb_port", 0))
    except (ValueError, TypeError) as error:
        raise EmulatorNotReadyError("MuMu 返回的 ADB 地址无效") from error
    if address.version != 4 or not address.is_loopback or not 1 <= port <= 65535:
        raise EmulatorNotReadyError("MuMu 必须提供本机 IPv4 ADB 端口")
    return f"{host}:{port}"


class MuMuEmulatorController(AdbController):
    supported_platforms = [Platform.WINDOWS]

    def __init__(self, logger, mumu_path="", instance_index="0", adb_path=""):
        self.manager_path = find_manager(mumu_path)
        self.instance_index = str(instance_index)
        if (
            not self.instance_index.isascii()
            or not self.instance_index.isdecimal()
            or not 0 <= int(self.instance_index) <= 10000
        ):
            raise ValueError("请选择一个有效的 MuMu 实例编号")
        bundled_adb = self.manager_path.parent / "adb.exe"
        super().__init__(logger, adb_path=adb_path or (str(bundled_adb) if bundled_adb.is_file() else ""))

    @classmethod
    def list_instances(cls, mumu_path="") -> list[dict]:
        manager = find_manager(mumu_path)
        result = run_manager([str(manager), "info", "-v", "all"])
        if result.returncode:
            raise EmulatorNotReadyError("MuMu 实例查询失败，请检查已安装的模拟器版本")
        return parse_instances(result.stdout)

    def _info(self):
        result = run_manager([str(self.manager_path), "info", "-v", self.instance_index])
        if result.returncode:
            raise EmulatorNotReadyError("无法查询所选 MuMu 实例")
        records = parse_instances(result.stdout)
        for info in records:
            if info["index"] == self.instance_index:
                return info
        raise EmulatorNotReadyError("所选 MuMu 实例不存在")

    def start(self):
        info = self._info()
        if not info.get("is_process_started"):
            result = run_manager([str(self.manager_path), "control", "-v", self.instance_index, "launch"], timeout=30)
            if result.returncode:
                raise EmulatorNotReadyError("MuMu 无法启动所选实例")
        deadline = time.monotonic() + 120
        while time.monotonic() < deadline:
            info = self._info()
            if info.get("is_android_started") and info.get("adb_port"):
                self.device_serial = adb_endpoint(info)
                self.adb_path = resolve_adb(self.adb_path)
                super().start()
                return
            self.logger.change_status(f"正在等待 MuMu 实例 {self.instance_index} 启动")
            time.sleep(1)
        raise EmulatorNotReadyError("MuMu 的 Android 系统启动超时（120 秒）")
