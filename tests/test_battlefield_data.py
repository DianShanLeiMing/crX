"""Offline screenshot/crop fidelity, interruption and import coverage."""

import json
from threading import Event
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from PIL import Image

from crX.utils import battlefield_data as data


@pytest.fixture
def frame():
    pixels = np.zeros((640, 360, 3), dtype=np.uint8)
    pixels[:, :, 0] = np.arange(360, dtype=np.uint16) % 256
    pixels[:, :, 1] = (np.arange(640, dtype=np.uint16) % 256)[:, None]
    pixels[:, :, 2] = 233
    return pixels


def write_image(path, frame):
    Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)).save(path)


def test_open_wrong_folder_explains_which_batch_to_select(tmp_path):
    directory = tmp_path / "datasets"
    directory.mkdir()
    with pytest.raises(ValueError, match="datasets.*metadata.json.*captures"):
        data.PreparationSession.open(directory)
    assert list(directory.iterdir()) == []


def test_find_batches_handles_parent_nested_and_direct_folder(tmp_path, frame):
    root = tmp_path / "captures"
    first = data.PreparationSession.create(root)
    second = data.PreparationSession.create(root / "nested")
    first.append(frame, source="test")
    second.append(frame, source="test")
    unrelated = root / "not-a-batch"
    unrelated.mkdir()
    (unrelated / "metadata.json").write_text('{"schema":"other"}', encoding="utf-8")
    # A valid batch's image folders must not be searched as more batches.
    (first.directory / "raw" / "metadata.json").write_text(json.dumps({"schema": data.SCHEMA}), encoding="utf-8")
    found = data.find_batches(root, stop=Event())
    assert set(found.directories) == {first.directory, second.directory}
    assert data.find_batches(first.directory, stop=Event()).directories == (first.directory,)
    with pytest.raises(ValueError, match="没有找到采集批次"):
        data.find_batches(unrelated, stop=Event())
    stop = Event()
    stop.set()
    assert data.find_batches(root, stop=stop).directories == ()


def test_native_capture_keeps_hd_detail_and_old_batches_stay_readable(tmp_path, frame):
    native = cv2.resize(frame, (720, 1280), interpolation=cv2.INTER_NEAREST)
    native[300, 300] = (1, 2, 3)
    session = data.PreparationSession.create(tmp_path / "native")
    device = SimpleNamespace(screenshot=lambda: frame, screenshot_native=lambda: native)
    data.collect_screenshots(session, lambda: device, count=1, interval=1, stop=Event(), progress=lambda _count: None)
    assert session.metadata["screen_size"] == [720, 1280]
    assert session.metadata["crop_ltrb"] == [0, 120, 720, 1030]
    np.testing.assert_array_equal(session.frame(0), native)
    arena = np.array(Image.open(session.directory / "arena" / "000000.png"))[:, :, ::-1]
    np.testing.assert_array_equal(arena, native[120:1030])
    old = data.PreparationSession.create(tmp_path / "old", (0, 60, 360, 515), screen_size=(360, 640))
    old.append(frame, source="historical")
    reopened = data.PreparationSession.open(old.directory)
    recropped = data.recrop_session(
        reopened, tmp_path / "recrop", (10, 70, 350, 500), stop=Event(), progress=lambda _count: None
    )
    assert recropped.metadata["screen_size"] == [360, 640]
    np.testing.assert_array_equal(recropped.frame(0), frame)


def test_original_crop_and_screen_mapping_are_exact(tmp_path, frame):
    session = data.PreparationSession.create(tmp_path / "中文目录", (13, 62, 351, 510), screen_size=(360, 640))
    session.append(frame, source="device", source_frame=3)
    opened = data.PreparationSession.open(session.directory)
    assert np.array_equal(opened.frame(0), frame)
    arena = np.array(Image.open(session.directory / "arena" / "000000.png"))
    assert np.array_equal(arena[:, :, ::-1], frame[62:510, 13:351])
    assert opened.metadata["crop_size"] == [338, 448]
    assert opened.metadata["crop_ltrb"][:2] == [13, 62]
    assert opened.metadata["frames"][0]["source_frame"] == 3


@pytest.mark.parametrize("crop", [(0, 0, 721, 1280), (20, 0, 10, 640), (0, -1, 360, 640), (0, 0, True, 640)])
def test_invalid_crop_does_not_create_a_batch(tmp_path, crop):
    with pytest.raises(ValueError):
        data.PreparationSession.create(tmp_path / "data", crop)
    assert not (tmp_path / "data").exists()


@pytest.mark.parametrize("count,interval", [(0, 1), (5001, 1), (True, 1), (1, 0), (1, float("nan"))])
def test_invalid_collection_limits(count, interval):
    with pytest.raises(ValueError):
        data.validate_collection(count, interval)


def test_cancellation_keeps_a_reopenable_partial_batch(tmp_path, frame):
    session = data.PreparationSession.create(tmp_path)
    stop = Event()
    calls = []

    def screenshot():
        calls.append(1)
        return frame

    data.collect_screenshots(
        session,
        lambda: SimpleNamespace(screenshot=screenshot),
        count=120,
        interval=1,
        stop=stop,
        progress=lambda _count: stop.set(),
    )
    assert len(calls) == 1
    assert len(data.PreparationSession.open(session.directory).metadata["frames"]) == 1
    assert np.array_equal(session.frame(0), frame)


def test_similar_capture_ignores_hud_and_compares_to_last_saved_frame(tmp_path, frame):
    session = data.PreparationSession.create(tmp_path)
    hud_only = frame.copy()
    hud_only[:20] = 0
    small_change = hud_only.copy()
    small_change[100:104, 100:104] = 0
    visible_change = small_change.copy()
    visible_change[104:108, 100:104] = 0
    frames = iter((frame, hud_only, small_change, visible_change))
    skipped = data.collect_screenshots(
        session,
        lambda: SimpleNamespace(screenshot=lambda: next(frames)),
        count=4,
        interval=0.2,
        stop=Event(),
        progress=lambda _count: None,
        skip_similar=True,
    )
    assert skipped == 2
    assert [entry["source_frame"] for entry in session.metadata["frames"]] == [0, 3]


def test_cancel_before_connecting_never_touches_device(tmp_path):
    stop = Event()
    stop.set()
    session = data.PreparationSession.create(tmp_path)
    data.collect_screenshots(
        session, lambda: pytest.fail("should not connect"), count=1, interval=1, stop=stop, progress=None
    )
    assert session.metadata["frames"] == []


def test_folder_import_does_not_modify_sources_or_resize(tmp_path, frame):
    source = tmp_path / "screenshots"
    source.mkdir()
    write_image(source / "01.png", frame)
    Image.new("RGB", (419, 633)).save(source / "02.png")
    before = (source / "01.png").read_bytes()
    session = data.PreparationSession.create(tmp_path / "output")
    with pytest.raises(ValueError, match="360×640"):
        data.import_screenshots(session, source, count=10, interval=1, stop=Event(), progress=lambda _count: None)
    assert len(data.PreparationSession.open(session.directory).metadata["frames"]) == 1
    assert (source / "01.png").read_bytes() == before
    assert len(list(source.iterdir())) == 2


def test_png_fight_pack_sampling_uses_manifest_fps(tmp_path, frame):
    pack = tmp_path / "pack"
    (pack / "frames").mkdir(parents=True)
    (pack / "manifest.json").write_text(json.dumps({"fps": 3}), encoding="utf-8")
    for index in range(7):
        write_image(pack / "frames" / f"{index:06d}.png", frame)
    session = data.PreparationSession.create(tmp_path / "output")
    data.import_screenshots(session, pack, count=2, interval=1, stop=Event(), progress=lambda _count: None)
    assert [entry["source_frame"] for entry in session.metadata["frames"]] == [0, 3]


def test_video_sampling_releases_capture(tmp_path, frame, monkeypatch):
    video = tmp_path / "capture.mkv"
    video.touch()
    state = {"index": -1, "released": False}

    def grab():
        state["index"] += 1
        return state["index"] < 7

    cap = SimpleNamespace(
        isOpened=lambda: True,
        get=lambda _key: 3,
        grab=grab,
        retrieve=lambda: (True, frame),
        release=lambda: state.update(released=True),
    )
    monkeypatch.setattr(data.cv2, "VideoCapture", lambda _path: cap)
    session = data.PreparationSession.create(tmp_path / "output")
    data.import_screenshots(session, video, count=3, interval=1, stop=Event(), progress=lambda _count: None)
    assert [entry["source_frame"] for entry in session.metadata["frames"]] == [0, 3, 6]
    assert state["released"]


def test_video_import_bounds_duration_using_actual_fps(tmp_path, frame, monkeypatch):
    records = []
    session = SimpleNamespace(metadata={"frames": records}, append=lambda _frame, **entry: records.append(entry))
    cap = SimpleNamespace(
        isOpened=lambda: True,
        get=lambda _key: 1,
        grab=lambda: True,
        retrieve=lambda: (True, frame),
        release=lambda: None,
    )
    monkeypatch.setattr(data.cv2, "VideoCapture", lambda _path: cap)
    data._import_video(
        session, tmp_path / "video.mkv", count=1000, interval=1, stop=Event(), progress=lambda _count: None
    )
    assert len(records) == 600
    assert records[-1]["source_frame"] == 599


def test_recrop_creates_new_batch_and_leaves_old_metadata_and_images(tmp_path, frame):
    original = data.PreparationSession.create(tmp_path)
    original.append(frame, source="device")
    metadata_before = (original.directory / "metadata.json").read_bytes()
    arena_before = (original.directory / "arena" / "000000.png").read_bytes()
    result = data.recrop_session(original, tmp_path, (10, 20, 300, 400), stop=Event(), progress=lambda _count: None)
    assert result.directory != original.directory
    assert result.metadata["crop_ltrb"] == [10, 20, 300, 400]
    assert np.array_equal(result.frame(0), frame)
    assert (original.directory / "metadata.json").read_bytes() == metadata_before
    assert (original.directory / "arena" / "000000.png").read_bytes() == arena_before


def test_metadata_cannot_reference_images_outside_batch(tmp_path):
    session = data.PreparationSession.create(tmp_path)
    session.metadata["frames"] = [{"name": "../outside.png"}]
    session.save()
    with pytest.raises(ValueError, match="路径"):
        data.PreparationSession.open(session.directory)


def test_capture_rejects_non_bgr_dtype_before_writing(tmp_path):
    session = data.PreparationSession.create(tmp_path)
    with pytest.raises(ValueError, match="BGR"):
        session.append(np.zeros((640, 360, 3), dtype=np.float32), source="device")
    assert not list((session.directory / "raw").iterdir())


def test_application_capture_factory_only_reads_device_after_dispatch(monkeypatch):
    from crX import __main__ as application
    from crX.interface.config import DEFAULTS
    from crX.interface.enums import UIField

    calls = []
    fake_device = SimpleNamespace(
        start=lambda: calls.append("start"),
        is_app_installed=lambda _package: True,
        configure=lambda: calls.append("configure"),
        verify_screen=lambda: calls.append("verify"),
    )
    monkeypatch.setattr(application, "AdbController", lambda *_args: fake_device)
    app = application.BotApplication.__new__(application.BotApplication)
    values = DEFAULTS | {UIField.EMULATOR: "ADB Device"}
    app.ui = SimpleNamespace(get_all_values=lambda: values.copy())
    app.logger = None
    app.process = None
    app._device_busy = False
    factory = app._prepare_dataset_capture()
    assert calls == []
    assert factory() is fake_device
    assert calls == ["start", "configure", "verify"]
    app._device_busy = True
    with pytest.raises(ValueError, match="设备检查"):
        app._prepare_dataset_capture()
    app._device_busy = False
    fake_device.is_app_installed = lambda _package: False
    with pytest.raises(RuntimeError, match="未安装"):
        app._prepare_dataset_capture()()
    assert calls == ["start", "configure", "verify", "start"]
