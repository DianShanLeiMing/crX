"""Attach to one existing Android device without managing its host emulator."""

import re
import subprocess
import time

from crX.bot.coords import GAME_SCREEN_DPI, GAME_SCREEN_SIZE
from crX.emulators.adb_base import AdbBasedController, resolve_adb, validate_device_serial
from crX.emulators.base import CLASH_ROYALE_PACKAGE, EmulatorNotReadyError
from crX.utils.platform import Platform

SCREEN_WIDTH, SCREEN_HEIGHT = GAME_SCREEN_SIZE
SCREEN_DENSITY = GAME_SCREEN_DPI


class AdbController(AdbBasedController):
    supported_platforms = [Platform.WINDOWS, Platform.MACOS, Platform.LINUX]

    def __init__(self, logger, device_serial=None, adb_path=""):
        self.logger = logger
        self.adb_path = resolve_adb(adb_path)
        self.device_serial = device_serial
        self._auto_stop_on_del = False
        if not validate_device_serial(device_serial):
            raise ValueError("ADB 设备序列号无效")

    def create(self):
        raise NotImplementedError("请先在模拟器中创建实例")

    def start(self):
        if self.device_serial and ":" in self.device_serial:
            result = self.adb(["connect", self.device_serial])
            if result.returncode:
                raise EmulatorNotReadyError("ADB 连接失败")
        devices = self.discover_devices(self.adb_path)
        if not self.device_serial:
            if len(devices) != 1:
                raise EmulatorNotReadyError("请选择一个在线的 ADB 设备")
            self.device_serial = devices[0]
        if self.device_serial not in devices:
            raise EmulatorNotReadyError("所选 ADB 设备已离线或尚未授权")

    def configure(self):
        for command in (
            ["shell", "wm", "size", f"{SCREEN_WIDTH}x{SCREEN_HEIGHT}"],
            ["shell", "wm", "density", str(SCREEN_DENSITY)],
        ):
            if self.adb(command).returncode:
                raise EmulatorNotReadyError("无法设置所需的分辨率与 DPI")
        time.sleep(1)

    def verify_screen(self):
        # Actual capture geometry is authoritative, not the window dimensions.
        if self.screenshot().shape != (SCREEN_HEIGHT, SCREEN_WIDTH, 3):
            raise EmulatorNotReadyError("截图必须为 360x640 BGR 图像，请调整模拟器显示设置")
        density = self.adb(["shell", "wm", "density"])
        values = re.findall(r"(?:Physical|Override) density:\s*(\d+)", density.stdout or "")
        if density.returncode or not values or int(values[-1]) != SCREEN_DENSITY:
            raise EmulatorNotReadyError("当前生效的屏幕密度必须为 160 DPI")

    def restart(self):
        from crX.bot.nav import wait_for_clash_main_menu

        try:
            self.start()
            if not self.is_app_installed(CLASH_ROYALE_PACKAGE):
                raise EmulatorNotReadyError("所选实例中未安装皇室战争国服客户端")
            self.logger.change_status(f"已识别皇室战争国服：{CLASH_ROYALE_PACKAGE}")
            self.configure()
            self.stop()
            self.start_app(CLASH_ROYALE_PACKAGE)
            time.sleep(2)
            self.verify_screen()
            if not wait_for_clash_main_menu(self, self.logger, timeout=120):
                raise EmulatorNotReadyError("皇室战争未进入主界面，请检查登录状态和游戏语言")
            self.logger.change_status("皇室战争国服已就绪，已进入主界面")
            return True
        except (OSError, ValueError, subprocess.TimeoutExpired) as error:
            raise EmulatorNotReadyError(str(error)) from error

    def stop(self):
        # Only stop the selected game's process; never kill an ADB server or other instances.
        if self.device_serial:
            self.adb(["shell", "am", "force-stop", CLASH_ROYALE_PACKAGE])

    def reset_screen(self):
        self.adb(["shell", "wm", "size", "reset"])
        self.adb(["shell", "wm", "density", "reset"])

    def install_apk(self, apk_path):
        from pathlib import Path

        path = Path(apk_path).resolve(strict=True)
        if path.suffix.lower() != ".apk":
            raise ValueError("请选择 APK 文件")
        return self.adb(["install", str(path)])
