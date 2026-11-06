"""Screenshot collection and reproducible battlefield crops; no model dependency."""

from __future__ import annotations

import json
import math
import re
import sys
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image

from crX.bot.coords import BATTLEFIELD_DATA_REGION_LTRB, GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE
from crX.utils.card_review import atomic_json

if TYPE_CHECKING:
    from threading import Event

MAX_FRAMES = 5000
SCHEMA = "crx-battlefield/v1"
FRAME_NAME = re.compile(r"[0-9]{6}\.png")


def get_battlefield_data_dir() -> Path:
    """Keep collected data beside the project (or packaged app), independent of cwd."""
    project_dir = (
        Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[2]
    )
    return project_dir.parent / "crX-data"


def validate_crop(crop, screen_size=GAME_SCREEN_SIZE) -> tuple[int, int, int, int]:
    if len(crop) != 4 or any(type(value) is not int for value in crop):
        raise ValueError("裁剪范围必须为四个整数：左、上、右、下")
    left, top, right, bottom = crop
    width, height = screen_size
    if not (0 <= left < right <= width and 0 <= top < bottom <= height):
        raise ValueError(f"裁剪范围必须位于 {width}×{height} 图像内，右、下边界不包含在裁剪区域中")
    return left, top, right, bottom


def validate_collection(count: int, interval: float) -> None:
    if type(count) is not int or not 1 <= count <= MAX_FRAMES:
        raise ValueError(f"采集／导入上限必须为 1 到 {MAX_FRAMES} 张")
    if not math.isfinite(interval) or not 0.2 <= interval <= 60:
        raise ValueError("采样间隔必须为 0.2 到 60 秒")


def validate_frame(frame) -> None:
    if (
        not isinstance(frame, np.ndarray)
        or frame.shape not in ((*GAME_SCREEN_SIZE[::-1], 3), (*REFERENCE_SCREEN_SIZE[::-1], 3))
        or frame.dtype != np.uint8
    ):
        raise ValueError("只接受 720×1280 或历史 360×640 的 BGR 截图，不会自动拉伸图片")


def read_screenshot(path: Path) -> np.ndarray:
    if path.stat().st_size > 16 * 1024 * 1024:
        raise ValueError(f"图片超过 16 MiB：{path.name}")
    with Image.open(path) as image:
        if image.format not in {"PNG", "JPEG"} or image.size not in (GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE):
            raise ValueError(f"图片必须为 720×1280 或历史 360×640 的 PNG/JPEG：{path.name}")
        return cv2.cvtColor(np.array(image.convert("RGB")), cv2.COLOR_RGB2BGR)


def _write_png(path: Path, frame: np.ndarray) -> None:
    ok, data = cv2.imencode(".png", frame)
    if not ok:
        raise ValueError("无法编码截图")
    # Exclusive writes never replace another frame, including after interruption.
    with path.open("xb") as stream:
        stream.write(data.tobytes())


def _direct_file(directory: Path, name: str) -> Path:
    path = directory / name
    if not FRAME_NAME.fullmatch(name) or path.resolve().parent != directory.resolve():
        raise ValueError("批次图片路径无效")
    return path


class PreparationSession:
    """One manually selected battle/recording batch, with immutable raw frames."""

    def __init__(self, directory: Path, metadata: dict):
        self.directory = directory
        self.metadata = metadata

    @classmethod
    def create(
        cls, root: Path, crop=BATTLEFIELD_DATA_REGION_LTRB, *, screen_size=GAME_SCREEN_SIZE
    ) -> PreparationSession:
        if tuple(screen_size) not in (GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE):
            raise ValueError("批次截图尺寸无效")
        crop = validate_crop(crop, screen_size)
        root.mkdir(parents=True, exist_ok=True)
        directory = Path(tempfile.mkdtemp(prefix=time.strftime("%Y%m%d-%H%M%S-"), dir=root))
        (directory / "raw").mkdir()
        (directory / "arena").mkdir()
        session = cls(
            directory,
            {
                "schema": SCHEMA,
                "session_id": directory.name,
                "screen_size": list(screen_size),
                "crop_ltrb": list(crop),
                "crop_size": [crop[2] - crop[0], crop[3] - crop[1]],
                "coordinate_mapping": "screen_x = crop_x + left; screen_y = crop_y + top",
                "frames": [],
            },
        )
        session.save()
        return session

    @classmethod
    def open(cls, directory: Path) -> PreparationSession:
        path = directory / "metadata.json"
        if path.stat().st_size > 8 * 1024 * 1024:
            raise ValueError("批次元数据过大")
        data = json.loads(path.read_text(encoding="utf-8"))
        if (
            not isinstance(data, dict)
            or data.get("schema") != SCHEMA
            or data.get("screen_size") not in (list(GAME_SCREEN_SIZE), list(REFERENCE_SCREEN_SIZE))
        ):
            raise ValueError("请选择 crX 战场数据批次目录")
        validate_crop(data.get("crop_ltrb", []), data["screen_size"])
        frames = data.get("frames")
        if not isinstance(frames, list) or len(frames) > MAX_FRAMES:
            raise ValueError("批次图片列表无效")
        seen = set()
        for entry in frames:
            if not isinstance(entry, dict) or not isinstance(entry.get("name"), str):
                raise ValueError("批次图片记录无效")
            name = entry["name"]
            _direct_file(directory / "raw", name)
            _direct_file(directory / "arena", name)
            if name in seen:
                raise ValueError("批次存在重复图片记录")
            seen.add(name)
        return cls(directory, data)

    def save(self) -> None:
        atomic_json(self.directory / "metadata.json", self.metadata)

    def adapt_geometry(self, frame: np.ndarray) -> None:
        validate_frame(frame)
        size = [frame.shape[1], frame.shape[0]]
        previous_size = self.metadata["screen_size"]
        if size != previous_size:
            if self.metadata["frames"]:
                raise ValueError("同一批次不能混用不同分辨率的截图")
            crop = self.metadata["crop_ltrb"]
            crop = [round(value * size[index % 2] / previous_size[index % 2]) for index, value in enumerate(crop)]
            validate_crop(crop, size)
            self.metadata.update(screen_size=size, crop_ltrb=crop, crop_size=[crop[2] - crop[0], crop[3] - crop[1]])

    def append(self, frame: np.ndarray, *, source: str, source_frame: int | None = None) -> None:
        self.adapt_geometry(frame)
        if len(self.metadata["frames"]) >= MAX_FRAMES:
            raise ValueError("已达到单批次图片上限")
        left, top, right, bottom = validate_crop(self.metadata["crop_ltrb"], self.metadata["screen_size"])
        name = f"{len(self.metadata['frames']):06d}.png"
        _write_png(self.directory / "raw" / name, frame)
        _write_png(self.directory / "arena" / name, frame[top:bottom, left:right])
        self.metadata["frames"].append(
            {"name": name, "source": source, "source_frame": source_frame, "captured_at": time.time()}
        )
        self.save()

    def frame(self, index: int) -> np.ndarray:
        return read_screenshot(_direct_file(self.directory / "raw", self.metadata["frames"][index]["name"]))


def _similar_battlefield(current: np.ndarray, previous: np.ndarray) -> bool:
    # Look for a local cluster of changes so a small troop is not averaged away by the arena background.
    changed = (cv2.absdiff(current, previous).max(axis=2) >= 16).astype(np.uint8)
    local_changes = cv2.boxFilter(changed, cv2.CV_32F, (12, 12), normalize=False, borderType=cv2.BORDER_CONSTANT)
    return bool(local_changes.max() < 18)


def collect_screenshots(session, device_factory, *, count, interval, stop: Event, progress, skip_similar=False) -> int:
    validate_collection(count, interval)
    if stop.is_set():
        return 0
    device = device_factory()
    previous = None
    skipped = 0
    for index in range(count):
        if stop.is_set():
            break
        started = time.monotonic()
        capture = getattr(device, "screenshot_native", device.screenshot)
        frame = capture()
        session.adapt_geometry(frame)
        left, top, right, bottom = session.metadata["crop_ltrb"]
        arena = frame[top:bottom, left:right]
        if skip_similar and previous is not None and _similar_battlefield(arena, previous):
            skipped += 1
        else:
            session.append(frame, source="device", source_frame=index)
            if skip_similar:
                previous = arena.copy()
            progress(len(session.metadata["frames"]))
        if index + 1 < count and stop.wait(max(0, interval - (time.monotonic() - started))):
            break
    return skipped


def _import_video(session, path: Path, *, count, interval, stop, progress) -> None:
    cap = cv2.VideoCapture(str(path))
    try:
        if not cap.isOpened():
            raise ValueError("无法打开录制视频")
        fps = cap.get(cv2.CAP_PROP_FPS)
        if not math.isfinite(fps) or not 0 < fps <= 240:
            raise ValueError("视频帧率无效，无法按时间抽帧")
        step = max(1, round(fps * interval))
        index = 0
        # Decode sequentially, with bounded input frames; seeking may be inaccurate for some codecs.
        frame_limit = math.ceil(fps * 60 * 10)
        while index < frame_limit and len(session.metadata["frames"]) < count and not stop.is_set():
            if not cap.grab():
                break
            if index % step == 0:
                ok, frame = cap.retrieve()
                if not ok:
                    raise ValueError("录制视频帧解码失败")
                session.append(frame, source=str(path), source_frame=index)
                progress(len(session.metadata["frames"]))
            index += 1
    finally:
        cap.release()


def import_screenshots(session, source: Path, *, count, interval, stop: Event, progress) -> None:
    """Import a screenshot, image folder or one existing fight pack; originals stay untouched."""
    validate_collection(count, interval)
    if source.is_file():
        if source.suffix.lower() in {".mkv", ".mp4"}:
            _import_video(session, source, count=count, interval=interval, stop=stop, progress=progress)
            return
        files = [source]
        step = 1
    else:
        video = source / "capture.mkv"
        if video.is_file():
            _import_video(session, video, count=count, interval=interval, stop=stop, progress=progress)
            return
        frames_dir = source / "frames"
        is_pack = frames_dir.is_dir()
        directory = frames_dir if is_pack else source
        step = 1
        if is_pack:
            manifest = source / "manifest.json"
            if manifest.stat().st_size > 1024 * 1024:
                raise ValueError("录制包元数据过大")
            data = json.loads(manifest.read_text(encoding="utf-8"))
            fps = data.get("fps") if isinstance(data, dict) else None
            if (
                isinstance(fps, bool)
                or not isinstance(fps, int | float)
                or not math.isfinite(fps)
                or not 0 < fps <= 240
            ):
                raise ValueError("录制包缺少有效帧率")
            step = max(1, round(fps * interval))
        files = sorted(path for path in directory.iterdir() if path.suffix.lower() in {".png", ".jpg", ".jpeg"})
    if not files:
        raise ValueError("未找到 PNG/JPEG 截图；请选择图片目录或已解压的单场录制包")
    for index, path in enumerate(files[::step]):
        if stop.is_set() or index >= count:
            break
        session.append(read_screenshot(path), source=str(path), source_frame=index * step)
        progress(len(session.metadata["frames"]))


def recrop_session(session, root: Path, crop, *, stop: Event, progress, created=None) -> PreparationSession:
    """Write a new batch so a changed crop cannot invalidate an earlier batch."""
    result = PreparationSession.create(root, crop, screen_size=session.metadata["screen_size"])
    result.metadata["parent_session"] = str(session.directory)
    result.save()
    if created is not None:
        created(result)
    for index, entry in enumerate(session.metadata["frames"]):
        if stop.is_set():
            break
        result.append(session.frame(index), source=entry.get("source", ""), source_frame=entry.get("source_frame"))
        progress(len(result.metadata["frames"]))
    return result
