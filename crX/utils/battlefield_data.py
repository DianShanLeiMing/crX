"""Screenshot collection and reproducible battlefield crops; no model dependency."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import sys
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import cv2
import numpy as np
from PIL import Image

from crX.bot.coords import BATTLEFIELD_DATA_REGION_LTRB, GAME_SCREEN_SIZE, REFERENCE_SCREEN_SIZE
from crX.detection.battlefield import battlefield_image
from crX.utils.card_review import atomic_json

if TYPE_CHECKING:
    from threading import Event

MAX_FRAMES = 5000
SCHEMA = "crx-battlefield/v1"
FRAME_NAME = re.compile(r"[0-9]{6}\.png")


@dataclass(frozen=True)
class BatchSearch:
    root: Path
    directories: tuple[Path, ...]
    removed: int = 0
    warnings: tuple[str, ...] = ()


def _batch_sort_key(path: Path):
    # Both old compact names and new readable names must share chronological order.
    match = re.match(r"(\d{4})-?(\d{2})-?(\d{2})[_-](\d{2})-?(\d{2})-?(\d{2})", path.name)
    return ("".join(match.groups()) if match else "", path.name, str(path))


def find_batches(root: Path, *, stop: Event, cleanup_empty=False) -> BatchSearch:
    """Find capture batches without following links or descending into their images."""
    if not root.is_dir():
        raise ValueError(f"文件夹不存在：{root}")
    found = []
    empty = removed = 0
    warnings = []

    def scan_error(error):
        raise error

    for current, directories, files in os.walk(root, onerror=scan_error, followlinks=False):
        if stop.is_set():
            break
        directory = Path(current)
        directories[:] = [
            name for name in directories if not (directory / name).is_symlink() and not (directory / name).is_junction()
        ]
        if "metadata.json" not in files:
            continue
        path = directory / "metadata.json"
        try:
            if path.is_symlink() or path.stat().st_size > 8 * 1024 * 1024:
                continue
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(data, dict) and data.get("schema") == SCHEMA:
            directories.clear()
            if data.get("frames") == []:
                empty += 1
                if cleanup_empty:
                    try:
                        remove_empty_batch(PreparationSession.open(directory))
                        removed += 1
                    except (OSError, ValueError) as error:
                        warnings.append(f"空批次清理未完成：{directory}：{error}")
            else:
                found.append(directory)
    if not found and not empty and not stop.is_set():
        raise ValueError(f"在所选文件夹及子文件夹中没有找到采集批次：{root}")
    return BatchSearch(root, tuple(sorted(found, key=_batch_sort_key, reverse=True)), removed, tuple(warnings))


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
        stamp = time.strftime("%Y-%m-%d_%H-%M-%S")
        for number in range(1, 10000):
            directory = root / (stamp if number == 1 else f"{stamp}_{number:02d}")
            try:
                directory.mkdir()
                break
            except FileExistsError:
                continue
        else:
            raise ValueError("同一秒的批次数量过多，请稍后重试")
        (directory / "raw").mkdir()
        full_frame = crop == (0, 0, *screen_size)
        if not full_frame:
            (directory / "arena").mkdir()
        session = cls(
            directory,
            {
                "schema": SCHEMA,
                "session_id": directory.name,
                "image_mode": "full_frame" if full_frame else "legacy_crop",
                "battle_id": f"{directory.name}-id{uuid.uuid4().hex[:12]}",
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
        try:
            metadata_size = path.stat().st_size
        except FileNotFoundError as error:
            raise ValueError(
                f"没有找到批次文件：{path}。" "请进入采集批次文件夹选择 metadata.json；默认位于 crX-data/captures 下"
            ) from error
        if metadata_size > 8 * 1024 * 1024:
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
        next_index = _next_frame_index(self.metadata)
        name = f"{next_index:06d}.png"
        _write_png(self.directory / "raw" / name, frame)
        if self.metadata.get("image_mode") != "full_frame":
            _write_png(self.directory / "arena" / name, frame[top:bottom, left:right])
        self.metadata["frames"].append(
            {"name": name, "source": source, "source_frame": source_frame, "captured_at": time.time()}
        )
        self.metadata["next_frame_index"] = next_index + 1
        self.save()

    def frame(self, index: int) -> np.ndarray:
        return read_screenshot(_direct_file(self.directory / "raw", self.metadata["frames"][index]["name"]))


def _similar_battlefield(current: np.ndarray, previous: np.ndarray) -> bool:
    if current.shape == (*GAME_SCREEN_SIZE[::-1], 3) and previous.shape == current.shape:
        left, top, right, bottom = BATTLEFIELD_DATA_REGION_LTRB
        current, previous = current[top:bottom, left:right], previous[top:bottom, left:right]
    # Look for a local cluster of changes so a small troop is not averaged away by the arena background.
    changed = (cv2.absdiff(current, previous).max(axis=2) >= 16).astype(np.uint8)
    local_changes = cv2.boxFilter(changed, cv2.CV_32F, (12, 12), normalize=False, borderType=cv2.BORDER_CONSTANT)
    return bool(local_changes.max() < 4)


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
    result.metadata["battle_id"] = session.metadata.get("battle_id", session.directory.name)
    result.save()
    if created is not None:
        created(result)
    for index, entry in enumerate(session.metadata["frames"]):
        if stop.is_set():
            break
        result.append(session.frame(index), source=entry.get("source", ""), source_frame=entry.get("source_frame"))
        progress(len(result.metadata["frames"]))
    return result


@dataclass(frozen=True)
class AnnotationExport:
    directory: Path
    count: int
    skipped: int = 0
    excluded: int = 0


def prepare_annotation_images(session, destination: Path, *, stop: Event, progress) -> AnnotationExport:
    return organize_annotation_images([session], destination, stop=stop, progress=progress)


def organize_annotation_images(
    sessions, destination: Path, *, stop: Event, progress, expected_digests=None, export_id=None
) -> AnnotationExport:
    """Copy complete images into one flat directory, preserving existing files and labels."""
    if export_id is not None and (not isinstance(export_id, str) or not re.fullmatch(r"[0-9a-f]{32}", export_id)):
        raise ValueError("导出标识无效")
    sessions = [session for session in sessions if session.metadata["frames"]]
    for session in sessions:
        if tuple(session.metadata["screen_size"]) != GAME_SCREEN_SIZE:
            raise ValueError(f"批次 {session.directory.name} 不是 720×1280 原图，不能混入当前训练数据")
    target = destination / "images"
    target.mkdir(parents=True, exist_ok=True)
    if any(path.is_dir() for path in target.iterdir()):
        raise ValueError("images 中已有旧场次子目录，请选择新的输出目录；旧图片和标签保留")
    added = skipped = 0
    for session in sessions:
        battle = session.metadata.get("battle_id", session.directory.name)
        if not isinstance(battle, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", battle):
            raise ValueError("场次名称无效")
        for index, entry in enumerate(session.metadata["frames"]):
            if stop.is_set():
                return AnnotationExport(target, added, skipped)
            frame = battlefield_image(session.frame(index))
            if expected_digests is not None:
                source = (session.directory / "raw" / entry["name"]).resolve()
                if hashlib.sha256(frame.tobytes()).hexdigest() != expected_digests.get(source):
                    raise ValueError(f"图片已变化，请重新筛选图片：{source}")
            ok, encoded = cv2.imencode(".png", frame)
            if not ok:
                raise ValueError("无法编码截图")
            content = encoded.tobytes()
            stem = f"{battle}__{Path(entry['name']).stem}"
            if export_id is not None:
                stem += f"_export{export_id}"
            for duplicate in range(1, 100000):
                suffix = "" if duplicate == 1 else f"_dup{duplicate:03d}"
                path = target / f"{stem}{suffix}.png"
                created = False
                try:
                    with path.open("xb") as stream:
                        created = True
                        stream.write(content)
                    added += 1
                    break
                except FileExistsError:
                    if (
                        not path.is_symlink()
                        and path.is_file()
                        and (
                            (path.stat().st_size == len(content) and path.read_bytes() == content)
                            or (expected_digests is not None and np.array_equal(read_screenshot(path), frame))
                        )
                    ):
                        skipped += 1
                        break
                except Exception:
                    if created:
                        path.unlink(missing_ok=True)
                    raise
            else:
                raise ValueError(f"同名文件过多：{stem}")
            progress(added + skipped)
    return AnnotationExport(target, added, skipped)


def _next_frame_index(metadata: dict) -> int:
    minimum = max((int(entry["name"][:6]) for entry in metadata["frames"]), default=-1) + 1
    value = metadata.get("next_frame_index", minimum)
    if type(value) is not int or value < 0 or max(value, minimum) > 999999:
        raise ValueError("批次图片编号无效或已达到上限")
    return max(value, minimum)


@dataclass(frozen=True)
class FrameDeletion:
    session: PreparationSession
    index: int
    cleanup_error: str = ""


def remove_empty_batch(session: PreparationSession) -> None:
    """Remove only a validated empty capture batch and its deleted-image archive."""
    selected = session.directory.absolute()
    if selected.is_symlink() or selected.is_junction():
        raise ValueError("批次目录不能是链接")
    base = selected.resolve(strict=True)
    metadata_path = base / "metadata.json"
    if base == Path(base.anchor) or metadata_path.is_symlink():
        raise ValueError("批次路径无效")
    current = PreparationSession.open(base)
    if current.metadata != session.metadata or current.metadata["frames"]:
        raise ValueError("批次已变化或仍有图片，未删除目录")
    if any(path.name not in {"metadata.json", "raw", "arena", ".deleted"} for path in base.iterdir()):
        raise ValueError("批次含其他文件，已保留目录")
    directories = []
    files = []
    for name in ("raw", "arena", ".deleted"):
        directory = base / name
        if directory.is_symlink() or directory.is_junction():
            raise ValueError("批次子目录不能是链接")
        if not directory.exists():
            continue
        if not directory.is_dir():
            raise ValueError("批次子目录无效")
        directories.append(directory)
        for storage in directory.iterdir():
            if name != ".deleted":
                raise ValueError("批次仍有未登记图片或文件，已保留目录")
            if (
                storage.is_symlink()
                or storage.is_junction()
                or not storage.is_dir()
                or not re.fullmatch(r"[0-9]{6}-[A-Za-z0-9_-]+", storage.name)
            ):
                raise ValueError("已删除图片目录含其他内容，已保留批次")
            directories.append(storage)
            for image in storage.iterdir():
                if image.is_symlink() or not image.is_file() or image.name not in {"raw.png", "arena.png"}:
                    raise ValueError("已删除图片目录含其他内容，已保留批次")
                files.append(image)
    # Remove only known files within this validated batch, without recursive deletion.
    for path in files:
        path.unlink()
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        directory.rmdir()
    metadata_path.unlink()
    base.rmdir()


def delete_frame(session: PreparationSession, index: int) -> FrameDeletion:
    """Quarantine one frame, removing the entire batch after its final frame is deleted."""
    if type(index) is not int or not 0 <= index < len(session.metadata["frames"]):
        raise ValueError("请先选择要删除的图片")
    if session.directory.is_symlink() or session.directory.is_junction():
        raise ValueError("批次目录不能是链接")
    base = session.directory.resolve(strict=True)
    entry = session.metadata["frames"][index]
    paths = []
    for kind in ("raw", "arena"):
        directory = base / kind
        path = _direct_file(directory, entry["name"])
        if (
            directory.is_symlink()
            or directory.is_junction()
            or path.is_symlink()
            or not path.resolve().is_relative_to(base)
        ):
            raise ValueError("图片路径不能指向批次外部或链接")
        if path.exists():
            if not path.is_file():
                raise ValueError("图片路径不是文件")
            paths.append((kind, path))
        elif kind == "raw":
            raise ValueError(f"原图不存在：{path}")
    archive = base / ".deleted"
    if archive.is_symlink() or archive.is_junction() or not archive.resolve().is_relative_to(base):
        raise ValueError("已删除图片目录不能是链接")
    deleted = session.metadata.get("deleted_frames", [])
    if not isinstance(deleted, list):
        raise ValueError("已删除图片记录无效")
    metadata = dict(session.metadata)
    metadata["next_frame_index"] = _next_frame_index(session.metadata)
    metadata["frames"] = session.metadata["frames"][:index] + session.metadata["frames"][index + 1 :]
    archive.mkdir(exist_ok=True)
    storage = Path(tempfile.mkdtemp(prefix=f"{Path(entry['name']).stem}-", dir=archive))
    metadata["deleted_frames"] = [*deleted, {**entry, "deleted_directory": str(storage.relative_to(base))}]
    moved = []
    try:
        for kind, path in paths:
            backup = storage / f"{kind}.png"
            path.rename(backup)
            moved.append((path, backup))
        atomic_json(base / "metadata.json", metadata)
    except Exception:
        for path, backup in reversed(moved):
            if not path.exists():
                backup.rename(path)
        if not any(storage.iterdir()):
            storage.rmdir()
        raise
    result = PreparationSession(base, metadata)
    cleanup_error = ""
    if not metadata["frames"]:
        try:
            remove_empty_batch(result)
        except (OSError, ValueError) as error:
            cleanup_error = str(error)
    return FrameDeletion(result, min(index, max(0, len(metadata["frames"]) - 1)), cleanup_error)
