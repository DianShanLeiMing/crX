"""Recovery evidence must precede restart and never break quota or recovery."""

import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from crX.bot import nav, worker
from crX.emulators import EmulatorType
from crX.emulators.base import EmulatorNotReadyError
from crX.utils import failure_samples as failures
from crX.utils.logger import Logger


def native_frame():
    return cv2.imread(str(Path(__file__).parent / "fixtures/post-battle-attached-07.png"))


def test_cached_failure_is_immutable_and_saved_without_rescreenshot(tmp_path):
    frame = native_frame()
    original = frame.copy()
    logger = Logger()
    failures.remember_failure_frame(logger, frame, reason="战后返回主界面超时", stage="unknown", actions=5)
    assert "failure_frame" not in logger.get_stats()
    frame[:] = 0
    recorder = failures.FailureSampleRecorder(tmp_path)
    assert recorder.offer(None, logger, state="end_fight", attempt=1, session_log_path="session.txt")
    recorder.close()
    records, errors = failures.scan_failures(tmp_path)
    assert len(records) == 1 and errors == 0
    record = records[0]
    assert record["data"]["capture_source"] == "failure_frame"
    assert record["data"]["reason"] == "战后返回主界面超时"
    assert record["data"]["details"]["actions"] == 5
    assert record["data"]["session_log_path"] == "session.txt"
    assert np.array_equal(cv2.imread(str(record["directory"] / "frame.png")), original)
    assert logger.failure_frame is None
    assert failures.load_failure_preview(record) is not None


def test_navigation_timeout_remembers_the_actual_final_frame(monkeypatch):
    clock = [0]
    monkeypatch.setattr(nav.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(nav.time, "sleep", lambda seconds: clock.__setitem__(0, clock[0] + seconds))
    frame = np.zeros((633, 419, 3), dtype=np.uint8)

    class Device:
        def screenshot(self):
            return frame

        def click(self, *args):
            pytest.fail("Unknown frames must not be clicked")

    logger = Logger()
    assert not nav.get_to_main_after_fight(Device(), logger, timeout=1)
    assert logger.failure_frame is not None
    assert np.array_equal(logger.failure_frame["image"], frame)
    assert logger.failure_frame["details"]["stage"] == "unknown"


def test_missing_or_broken_screenshot_still_saves_metadata(tmp_path):
    recorder = failures.FailureSampleRecorder(tmp_path)
    assert recorder.offer(None, Logger(), state="start_fight", attempt=2)
    recorder.close()
    records, _ = failures.scan_failures(tmp_path)
    assert len(records) == 1
    assert records[0]["data"]["image_file"] is None
    assert records[0]["data"]["capture_error"]
    assert failures.load_failure_preview(records[0]) is None


def test_exception_reason_and_unexpected_resolution_are_kept(tmp_path):
    class Device:
        def screenshot(self):
            return np.zeros((120, 300, 3), dtype=np.uint8)

    recorder = failures.FailureSampleRecorder(tmp_path)
    recorder.offer(Device(), Logger(), state="fight", attempt=1, exception="Traceback:\nValueError: 截图尺寸不符")
    recorder.close()
    record = failures.scan_failures(tmp_path)[0][0]
    assert record["data"]["reason"] == "ValueError: 截图尺寸不符"
    assert record["data"]["image_size"] == [300, 120]
    assert record["data"]["capture_source"] == "before_recovery"


def test_capture_timeout_does_not_store_a_late_post_restart_frame(tmp_path, monkeypatch):
    release = threading.Event()

    class SlowDevice:
        def screenshot(self):
            release.wait(3)
            return native_frame()

    monkeypatch.setattr(failures, "CAPTURE_TIMEOUT_S", 0.02)
    recorder = failures.FailureSampleRecorder(tmp_path)
    started = time.monotonic()
    try:
        recorder.offer(SlowDevice(), Logger(), state="end_fight", attempt=1)
        assert time.monotonic() - started < 0.5
        recorder.close()
        records, _ = failures.scan_failures(tmp_path)
        assert records[0]["data"]["image_file"] is None
        assert "迟到截图" in records[0]["data"]["capture_error"]
    finally:
        release.set()


def test_limits_preserve_existing_evidence(tmp_path):
    marker = tmp_path / "keep.txt"
    marker.write_text("keep", encoding="utf-8")
    recorder = failures.FailureSampleRecorder(tmp_path, max_samples=1)
    for _ in range(2):
        logger = Logger()
        failures.remember_failure_frame(logger, native_frame(), reason="测试超时")
        recorder.offer(None, logger, state="end_fight", attempt=1)
    recorder.close()
    assert len(failures.scan_failures(tmp_path)[0]) == 1
    assert marker.read_text(encoding="utf-8") == "keep"
    recorder = failures.FailureSampleRecorder(tmp_path, max_bytes=1)
    recorder.offer(None, Logger(), state="end_fight", attempt=2)
    recorder.close()
    assert len(failures.scan_failures(tmp_path)[0]) == 1


def test_storage_error_does_not_escape_into_recovery(tmp_path):
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("keep", encoding="utf-8")
    recorder = failures.FailureSampleRecorder(blocker)
    assert recorder.offer(None, Logger(), state="end_fight", attempt=1)
    recorder.close()
    assert blocker.read_text(encoding="utf-8") == "keep"


def test_review_is_separate_and_reversible(tmp_path):
    recorder = failures.FailureSampleRecorder(tmp_path)
    recorder.offer(None, Logger(), state="end_fight", attempt=1)
    recorder.close()
    record = failures.scan_failures(tmp_path)[0][0]
    original = (record["directory"] / "failure.json").read_bytes()
    for status in ("handled", "ignored", "pending"):
        failures.save_failure_review(record, status, " 已确认缺少弹窗识别 ")
        saved = failures.scan_failures(tmp_path)[0][0]
        assert saved["review"]["status"] == status
        assert saved["review"]["note"] == "已确认缺少弹窗识别"
    assert (record["directory"] / "failure.json").read_bytes() == original
    assert not list(record["directory"].glob("*.tmp"))


def test_corrupt_records_and_escaping_preview_paths(tmp_path):
    recorder = failures.FailureSampleRecorder(tmp_path)
    recorder.offer(None, Logger(), state="end_fight", attempt=1)
    recorder.close()
    record = failures.scan_failures(tmp_path)[0][0]
    record["data"]["image_file"] = "../outside.png"
    assert failures.load_failure_preview(record) is None
    (record["directory"] / "failure.json").write_text("partial", encoding="utf-8")
    assert failures.scan_failures(tmp_path) == ([], 1)


def test_worker_samples_all_five_failures_before_restart_or_final_stop(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(failures, "get_failure_samples_dir", lambda: tmp_path)

    class Signal:
        def is_set(self):
            return False

        def wait(self, timeout):
            pass

    class Device:
        def screenshot(self):
            events.append("capture")
            return native_frame()

        def restart(self):
            events.append("restart")

    def tree(device, logger, state, jobs, context):
        if state == "restart":
            device.restart()
            return "end_fight"
        logger.error("测试失败")
        return "restart"

    monkeypatch.setattr(worker, "state_tree", tree)
    monkeypatch.setattr(worker, "finish_fight_recording", lambda *_: None)
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = Signal()
    process.session_log_path = "test-session.txt"
    logger = Logger()
    assert not process._run_bot_loop(Device(), {}, logger)
    assert events == ["capture", "restart"] * 4 + ["capture"]
    records, _ = failures.scan_failures(tmp_path)
    assert len(records) == 5
    assert sorted(record["data"]["recovery_attempt"] for record in records) == [1, 2, 3, 4, 5]
    assert logger.stats["completed_fights"] == 0


@pytest.mark.parametrize("cached", (True, False))
def test_startup_failure_flushes_evidence_before_worker_exits(tmp_path, monkeypatch, cached):
    frame = native_frame()
    captures = []

    class Device:
        def __init__(self, logger, **kwargs):
            self.logger = logger

        @classmethod
        def is_supported_on_current_platform(cls):
            return True

        def restart(self):
            if cached:
                failures.remember_failure_frame(
                    self.logger, frame, reason="战后返回主界面超时", stage="unknown", actions=1
                )
            raise EmulatorNotReadyError("皇室战争未进入主界面")

        def screenshot(self):
            captures.append(True)
            return frame

    monkeypatch.setattr(worker, "get_emulator_registry", lambda: {EmulatorType.ADB: Device})
    monkeypatch.setattr(failures, "get_failure_samples_dir", lambda: tmp_path)
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = threading.Event()
    process.session_log_path = "startup-session.txt"
    logger = Logger()
    assert process._setup_emulator({"emulator": EmulatorType.ADB}, logger) is None
    records, errors = failures.scan_failures(tmp_path)
    assert errors == 0 and len(records) == 1
    data = records[0]["data"]
    assert data["state"] == "start" and data["recovery_attempt"] == 1
    assert data["session_log_path"] == "startup-session.txt"
    assert "EmulatorNotReadyError" in data["exception"]
    assert np.array_equal(cv2.imread(str(records[0]["directory"] / "frame.png")), frame)
    assert captures == ([] if cached else [True])
    assert data["capture_source"] == ("failure_frame" if cached else "before_recovery")
    if cached:
        assert data["reason"] == "战后返回主界面超时"
        assert data["details"]["actions"] == 1
    assert "设备尚未就绪" in logger.current_status


def test_startup_sampling_error_preserves_original_failure(monkeypatch):
    from tests.test_emulator_setup_failfast import _FakeNotReadyController

    monkeypatch.setattr(worker, "get_emulator_registry", lambda: {EmulatorType.ADB: _FakeNotReadyController})
    monkeypatch.setattr(worker, "FailureSampleRecorder", lambda: (_ for _ in ()).throw(OSError("disk error")))
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = threading.Event()
    logger = Logger()
    assert process._setup_emulator({"emulator": EmulatorType.ADB}, logger) is None
    assert "fake emulator is not ready" in logger.current_status


def test_sampler_exception_cannot_break_worker_recovery(tmp_path, monkeypatch):
    class BrokenRecorder:
        def offer(self, *args, **kwargs):
            raise OSError("disk error")

        def close(self):
            pass

    monkeypatch.setattr(worker, "FailureSampleRecorder", BrokenRecorder)

    class Signal:
        def is_set(self):
            return False

        def wait(self, timeout):
            pass

    monkeypatch.setattr(worker, "state_tree", lambda *_: "restart")
    monkeypatch.setattr(worker, "finish_fight_recording", lambda *_: None)
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = Signal()
    logger = Logger()
    assert not process._run_bot_loop(None, {}, logger)
    assert "连续 5 次恢复失败" in logger.current_status


def make_failure(root):
    prior_keys = {record["key"] for record in failures.scan_failures(root)[0]}
    logger = Logger()
    failures.remember_failure_frame(
        logger, native_frame(), reason="[功能验证] 模拟战后超时", stage="unknown", actions=5
    )
    recorder = failures.FailureSampleRecorder(root)
    recorder.offer(None, logger, state="end_fight", attempt=1, session_log_path="validation-session.txt")
    recorder.close()
    return next(record for record in failures.scan_failures(root)[0] if record["key"] not in prior_keys)


def test_delete_selected_failures_preserves_other_records_and_logs(tmp_path):
    root = tmp_path / "failures"
    first = make_failure(root)
    failures.save_failure_review(first, "handled", "已处理")
    second = make_failure(root)
    untouched = {path.name: path.read_bytes() for path in second["directory"].iterdir()}
    log = tmp_path / "session.txt"
    log.write_text("keep log", encoding="utf-8")
    # Metadata does not grant deletion of arbitrary files.
    data = first["data"] | {"image_file": str(log), "session_log_path": str(log)}
    failures.atomic_json(first["directory"] / "failure.json", data)
    assert failures.delete_failure_samples(root, {first["key"]}) == 1
    assert not first["directory"].exists()
    assert {path.name: path.read_bytes() for path in second["directory"].iterdir()} == untouched
    assert log.read_text(encoding="utf-8") == "keep log"
    assert failures.delete_failure_samples(root, {first["key"]}) == 0
    assert failures.delete_failure_samples(root, set()) == 0


@pytest.mark.parametrize("key", ["../outside", "..", ".", "", "nested/record"])
def test_failure_deletion_rejects_invalid_selection_before_any_deletion(tmp_path, key):
    root = tmp_path / "failures"
    record = make_failure(root)
    with pytest.raises(ValueError):
        failures.delete_failure_samples(root, {record["key"], key})
    assert record["directory"].is_dir()
    assert failures.scan_failures(root)[0]


def test_failure_deletion_preflights_all_records_and_preserves_extra_files(tmp_path):
    first = make_failure(tmp_path)
    second = make_failure(tmp_path)
    extra = second["directory"] / "keep.txt"
    extra.write_text("user file", encoding="utf-8")
    with pytest.raises(ValueError, match="额外文件"):
        failures.delete_failure_samples(tmp_path, {first["key"], second["key"]})
    assert first["directory"].is_dir() and second["directory"].is_dir()
    assert extra.read_text(encoding="utf-8") == "user file"


@pytest.mark.parametrize("link_kind", ["file", "directory", "root"])
def test_failure_deletion_rejects_links(tmp_path, link_kind):
    root = tmp_path / "failures"
    record = make_failure(root)
    outside = tmp_path / "outside.txt"
    outside.write_text("keep", encoding="utf-8")
    target = root
    keys = {record["key"]}
    try:
        if link_kind == "file":
            picture = record["directory"] / "frame.png"
            picture.unlink()
            picture.symlink_to(outside)
        elif link_kind == "directory":
            (root / "linked").symlink_to(record["directory"], target_is_directory=True)
            keys = {"linked"}
        else:
            target = tmp_path / "linked-root"
            target.symlink_to(root, target_is_directory=True)
    except OSError:
        pytest.skip("Creating symlinks requires Windows developer mode or permission")
    with pytest.raises(ValueError, match="链接"):
        failures.delete_failure_samples(target, keys)
    assert record["directory"].is_dir()
    assert outside.read_text(encoding="utf-8") == "keep"
