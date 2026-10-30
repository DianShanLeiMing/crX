"""Offline coverage for the construct-then-restart() boot contract (PR2).

Controllers construct cheaply and boot in restart(); a not-ready emulator raises
EmulatorNotReadyError from restart(). These tests pin how the two construction
sites react to that raise — without any hardware — by swapping the registry for a
fake controller whose restart() blows up.
"""

import pytest

from crX.emulators import EmulatorType
from crX.emulators.base import EmulatorNotReadyError
from crX.utils.logger import Logger


class _FakeNotReadyController:
    """Cheap to construct; restart() raises like a not-ready emulator."""

    constructed = 0

    def __init__(self, logger, **kwargs):
        type(self).constructed += 1
        self.logger = logger

    @classmethod
    def is_supported_on_current_platform(cls):
        return True

    def restart(self):
        raise EmulatorNotReadyError("fake emulator is not ready")


class _RecordingLogger(Logger):
    """Test double that inherits from Logger but records change_status calls.

    Lightweight to construct (Logger.__init__ only sets instance attrs + locks,
    doesn't spawn threads), and reuses all real Logger behavior without needing
    to duplicate its interface.
    """

    def __init__(self):
        super().__init__(stats=None, timed=False)
        self.statuses: list[str] = []

    def error(self, message):
        self.statuses.append(message)
        super().error(message)

    def change_status(self, status) -> None:
        """Override to record calls for assertions."""
        self.statuses.append(status)
        # Still call parent to maintain contract (calls _update_log)
        super().change_status(status)


def test_attach_emulator_reraises_when_restart_not_ready(monkeypatch):
    import crX.emulators as emulators_pkg

    monkeypatch.setattr(
        emulators_pkg,
        "get_emulator_registry",
        lambda: {EmulatorType.ADB: _FakeNotReadyController},
    )

    from tests._emulator_support import attach_emulator

    with pytest.raises(EmulatorNotReadyError):
        attach_emulator("adb", _RecordingLogger())


def test_setup_emulator_returns_none_and_reports_when_restart_not_ready(monkeypatch, tmp_path):
    from crX.bot import worker as worker_module

    monkeypatch.setattr("crX.utils.failure_samples.get_failure_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(
        worker_module,
        "get_emulator_registry",
        lambda: {EmulatorType.ADB: _FakeNotReadyController},
    )

    # Skip Process.__init__, but supply the stop event used by capture retries.
    worker = worker_module.WorkerProcess.__new__(worker_module.WorkerProcess)
    from threading import Event

    worker.shutdown_event = Event()
    logger = _RecordingLogger()

    result = worker._setup_emulator({"emulator": EmulatorType.ADB}, logger)

    assert result is None
    assert logger.statuses, "expected a user-facing status explaining the abort"
    assert any("not ready" in status.lower() for status in logger.statuses)
