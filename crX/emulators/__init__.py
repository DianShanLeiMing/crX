"""Supported device adapters; gameplay is independent of the selected backend."""

from enum import StrEnum


class EmulatorType(StrEnum):
    MUMU = "MuMu"
    ADB = "ADB Device"


def get_emulator_registry():
    from crX.emulators.adb import AdbController
    from crX.emulators.mumu import MuMuEmulatorController

    return {EmulatorType.MUMU: MuMuEmulatorController, EmulatorType.ADB: AdbController}


def get_available_emulators():
    return [key for key, cls in get_emulator_registry().items() if cls.is_supported_on_current_platform()]
