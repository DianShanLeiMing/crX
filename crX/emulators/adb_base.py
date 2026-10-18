"""ADB transport shared by MuMu and generic Android devices."""

import logging
import re
import shlex
import shutil
import subprocess
import time
from pathlib import Path

import cv2
import numpy as np

from crX.emulators.base import BaseEmulatorController, EmulatorNotReadyError

logger = logging.getLogger(__name__)
ADB_TIMEOUT = 20


def validate_device_serial(serial: str | None) -> bool:
    if not serial:
        return True
    if re.fullmatch(r"[A-Za-z0-9._-]+:[0-9]{1,5}", serial):
        return 1 <= int(serial.rsplit(":", 1)[1]) <= 65535
    return bool(re.fullmatch(r"emulator-[0-9]+|[A-Za-z0-9]{6,}", serial))


def validate_package(package: str) -> None:
    if not re.fullmatch(r"[A-Za-z][A-Za-z0-9_]*(?:\.[A-Za-z0-9_]+)+", package):
        raise ValueError("Android 应用包名无效")


def resolve_adb(configured: str = "") -> str:
    if configured:
        path = Path(configured).expanduser().resolve()
        if not path.is_file() or path.name.lower() not in {"adb", "adb.exe"}:
            raise ValueError("请选择有效的 ADB 程序文件")
        return str(path)
    return shutil.which("adb") or "adb"


def run_command(args: list[str], *, binary=False, timeout=ADB_TIMEOUT):
    """No host shell; all user-influenced values remain separate arguments."""
    kwargs = {"creationflags": subprocess.CREATE_NO_WINDOW} if hasattr(subprocess, "CREATE_NO_WINDOW") else {}
    if binary:
        return subprocess.run(args, shell=False, capture_output=True, timeout=timeout, check=False, **kwargs)
    return subprocess.run(
        args,
        shell=False,
        capture_output=True,
        timeout=timeout,
        check=False,
        encoding="utf-8",
        errors="replace",
        **kwargs,
    )


class AdbBasedController(BaseEmulatorController):
    device_serial: str | None = None
    adb_path = "adb"

    @classmethod
    def find_adb(cls):
        return shutil.which("adb")

    @classmethod
    def discover_devices(cls, adb_path="") -> list[str]:
        try:
            result = run_command([resolve_adb(adb_path or cls.find_adb() or ""), "devices"])
        except (OSError, ValueError, subprocess.TimeoutExpired):
            return []
        if result.returncode:
            return []
        return [
            parts[0]
            for line in result.stdout.splitlines()
            if len(parts := line.split()) == 2 and parts[1] == "device" and validate_device_serial(parts[0])
        ]

    @classmethod
    def is_device_connected(cls, device_serial, adb_path=""):
        return bool(device_serial) and device_serial in cls.discover_devices(adb_path)

    def adb(self, command: str | list[str], binary_output=False):
        if not validate_device_serial(self.device_serial):
            raise ValueError("ADB 设备序列号无效")
        parts = shlex.split(command) if isinstance(command, str) else command
        if not parts:
            raise ValueError("ADB 指令不能为空")
        args = [self.adb_path]
        if parts[0] not in {"connect", "disconnect", "devices", "version", "start-server"}:
            if not self.device_serial:
                raise ValueError("执行设备指令前请先选择设备")
            args += ["-s", self.device_serial]
        return run_command([*args, *parts], binary=binary_output)

    def is_app_installed(self, package: str) -> bool:
        return self._check_app_installed(package)

    def _check_app_installed(self, package_name: str) -> bool:
        """Check if an app is installed via ADB.

        Args:
            package_name (str): The package name to check (e.g., "com.tencent.tmgp.supercell.clashroyale").

        Returns:
            bool: True if the app is installed, False otherwise.
        """
        logger.debug("Checking if app is installed: %s", package_name)
        validate_package(package_name)
        result = self.adb(["shell", "pm", "list", "packages", package_name])
        installed = result.stdout is not None and f"package:{package_name}" in result.stdout.splitlines()
        if installed:
            logger.info("App %s is installed", package_name)
        else:
            logger.info("App %s is NOT installed", package_name)
        return installed

    def list_devices(self) -> list[tuple[str, str]]:
        """List all ADB devices with their status.

        Returns:
            list of (serial, status) tuples, e.g., [("localhost:6520", "device"), ("emulator-5554", "offline")]
        """
        result = self.adb("devices")
        devices = []
        if result.stdout:
            for line in result.stdout.strip().splitlines()[1:]:
                parts = line.split()
                if len(parts) >= 2:
                    devices.append((parts[0], parts[1]))

        logger.info("Found %d ADB device(s): %s", len(devices), devices)

        for serial, status in devices:
            if status == "offline":
                logger.warning("Device %s is offline - may need reconnection", serial)
            elif status == "unauthorized":
                logger.warning("Device %s is unauthorized - check USB debugging authorization on device", serial)

        return devices

    def list_online_devices(self) -> list[str]:
        """List serials of online (status='device') devices only."""
        return [serial for serial, status in self.list_devices() if status == "device"]

    def click(self, x_coord: int, y_coord: int, clicks: int = 1, interval: float = 0.0):
        """Click on the screen using ADB input tap."""
        for _ in range(max(1, clicks)):
            self.adb(f"shell input tap {x_coord} {y_coord}")
            if clicks > 1:
                time.sleep(max(0.0, interval))

    def swipe(self, x_coord1: int, y_coord1: int, x_coord2: int, y_coord2: int, duration_ms: int | None = None):
        """Swipe on the screen using ADB input swipe."""
        command = ["shell", "input", "swipe", str(x_coord1), str(y_coord1), str(x_coord2), str(y_coord2)]
        if duration_ms is not None:
            if not 1 <= duration_ms <= 10000:
                raise ValueError("滑动时长必须为 1–10000 毫秒")
            command.append(str(duration_ms))
        self.adb(command)

    def screenshot(self) -> np.ndarray:
        """
        Capture a screenshot using ADB 'exec-out screencap -p'.

        Returns:
            np.ndarray: The screenshot as a CV2 (BGR) image.

        Raises:
            RuntimeError: If the ADB screencap command fails.
            ValueError: If the captured image data cannot be decoded.
        """
        result = self.adb("exec-out screencap -p", binary_output=True)

        if result.returncode != 0 or not result.stdout:
            err = result.stderr if result.stderr else b"Unknown ADB error"
            error_msg = err.decode("utf-8", "ignore")
            logger.error("Screenshot capture failed: %s", error_msg)
            raise RuntimeError(f"ADB 截图失败：{error_msg}")

        if len(result.stdout) > 32 * 1024 * 1024:
            raise ValueError("截图大小超过 32 MiB 限制")
        img_array = np.frombuffer(result.stdout, dtype=np.uint8)
        img = cv2.imdecode(img_array, cv2.IMREAD_COLOR)

        if img is None:
            logger.error("Failed to decode screenshot data (%d bytes)", len(result.stdout))
            raise ValueError("截图解码失败，图像数据可能已损坏或为空。")

        logger.debug("Screenshot captured: %dx%d", img.shape[1], img.shape[0])
        return img

    def start_app(self, package_name: str):
        """
        Start an app using ADB monkey command.

        Args:
            package_name (str): The package name to start.

        Returns:
            True if the app was launched.

        Raises:
            EmulatorNotReadyError: If the app is not installed.
        """
        validate_package(package_name)
        if not self._check_app_installed(package_name):
            raise EmulatorNotReadyError(f"模拟器中未安装应用：{package_name}")

        logger.info("Launching app: %s", package_name)
        self.adb(f"shell monkey -p {package_name} -c android.intent.category.LAUNCHER 1")
        return True
