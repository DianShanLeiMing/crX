"""Global exact deduplication and conservative, reviewable near-duplicate groups."""

from __future__ import annotations

import hashlib
import re
import uuid
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import cv2
import numpy as np

from crX.bot.coords import BATTLEFIELD_DATA_REGION_LTRB, GAME_SCREEN_SIZE
from crX.utils.battlefield_data import (
    AnnotationExport,
    PreparationSession,
    find_batches,
    organize_annotation_images,
    read_screenshot,
)
from crX.utils.dataset_split import FLAT_IMAGE_NAME, IMAGE_SUFFIXES, MAX_IMAGES, _read_input_profile

if TYPE_CHECKING:
    from pathlib import Path
    from threading import Event


@dataclass(frozen=True)
class CaptureImage:
    session_index: int
    frame_index: int
    path: Path
    digest: str
    stamp: tuple[int, int, int]


@dataclass(frozen=True)
class DuplicateGroup:
    kind: str
    representative: int
    members: tuple[int, ...]
    default_keep: tuple[int, ...]


@dataclass(frozen=True)
class DeduplicationPlan:
    root: Path
    sessions: tuple[PreparationSession, ...]
    images: tuple[CaptureImage, ...]
    groups: tuple[DuplicateGroup, ...]
    default_keep: tuple[int, ...]
    battle_groups: tuple[str, ...]

    @property
    def exact_duplicates(self) -> int:
        return len(self.images) - len(self.default_keep)


def _check_stop(stop: Event):
    if stop.is_set():
        raise ValueError("已停止全局去重，原始图片未修改")


def _stamp(path: Path):
    stat = path.stat()
    return stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns


def _comparison_view(image):
    # This window is ONLY for similarity features. Saved/training images stay full-screen.
    left, top, right, bottom = BATTLEFIELD_DATA_REGION_LTRB
    return image[top:bottom, left:right]


def _signature(image):
    gray = cv2.cvtColor(_comparison_view(image), cv2.COLOR_BGR2GRAY)
    tiny = cv2.resize(gray, (17, 16), interpolation=cv2.INTER_AREA)
    return np.packbits(tiny[:, 1:] > tiny[:, :-1]).reshape(32)


def _near_duplicate(first, second):
    delta = cv2.absdiff(_comparison_view(first), _comparison_view(second))
    if float(delta.mean()) > 3:
        return False
    # Even a 2x2 new/moving object blocks grouping. A whole-screen average alone is unsafe.
    changed = (delta.max(axis=2) > 12).astype(np.uint8)
    local = cv2.boxFilter(changed, cv2.CV_32F, (7, 7), normalize=False, borderType=cv2.BORDER_CONSTANT)
    return float(local.max()) < 4


def load_plan_image(plan: DeduplicationPlan, index: int):
    sample = plan.images[index]
    if _stamp(sample.path) != sample.stamp:
        raise ValueError(f"图片已变化，请重新全局去重：{sample.path}")
    image = read_screenshot(sample.path)
    if hashlib.sha256(image.tobytes()).hexdigest() != sample.digest:
        raise ValueError(f"图片已变化，请重新全局去重：{sample.path}")
    return image


def analyze_batches(root: Path, *, stop: Event, progress) -> DeduplicationPlan:
    root = root.resolve()
    paths = find_batches(root, stop=stop).directories
    _check_stop(stop)
    sessions = tuple(PreparationSession.open(path) for path in paths)
    total = sum(len(session.metadata["frames"]) for session in sessions)
    if not total:
        raise ValueError("所选目录的批次没有图片")
    if total > MAX_IMAGES:
        raise ValueError(f"每次最多分析 {MAX_IMAGES} 张图片，请分目录处理")
    images = []
    signatures = []
    exact = {}
    canonical = []
    views = {}
    view_order = []
    aliases = {}

    def group_root(group):
        aliases.setdefault(group, group)
        while aliases[group] != group:
            aliases[group] = aliases[aliases[group]]
            group = aliases[group]
        return group

    for session_index, session in enumerate(sessions):
        if session.metadata["frames"] and tuple(session.metadata["screen_size"]) != GAME_SCREEN_SIZE:
            raise ValueError(f"批次 {session.directory} 不是 720×1280 原图，不能混入当前训练数据")
        battle = session.metadata.get("battle_id", session.directory.name)
        if not isinstance(battle, str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,160}", battle):
            raise ValueError(f"场次标识无效：{session.directory}")
        group_root(battle)
        for frame_index, entry in enumerate(session.metadata["frames"]):
            _check_stop(stop)
            path = session.directory / "raw" / entry["name"]
            before = _stamp(path)
            image = session.frame(frame_index)
            if image.shape != (*GAME_SCREEN_SIZE[::-1], 3) or _stamp(path) != before:
                raise ValueError(f"图片尺寸或内容发生变化：{path}")
            digest = hashlib.sha256(image.tobytes()).hexdigest()
            index = len(images)
            images.append(CaptureImage(session_index, frame_index, path, digest, before))
            if digest not in exact:
                position = len(canonical)
                canonical.append(index)
                view_digest = hashlib.sha256(_comparison_view(image).tobytes()).hexdigest()
                if view_digest not in views:
                    views[view_digest] = []
                    view_order.append(view_digest)
                    signatures.append(_signature(image))
                views[view_digest].append(position)
                exact[digest] = [index]
            else:
                previous = images[exact[digest][0]]
                previous_session = sessions[previous.session_index]
                previous_battle = previous_session.metadata.get("battle_id", previous_session.directory.name)
                first, second = sorted((group_root(previous_battle), group_root(battle)))
                aliases[second] = first
                exact[digest].append(index)
            progress(f"全局扫描 {len(images)} / {total} 张 · 完全重复 {len(images) - len(canonical)} 张")

    _check_stop(stop)
    features = np.asarray(signatures, dtype=np.uint8)
    count = len(view_order)
    # LSH indexes the whole collection; it avoids native-resolution all-pairs comparisons.
    # Approximate search can miss some near duplicates. Exact hashing is exhaustive.
    neighbors = None
    if count > 64:
        search = cv2.flann.Index(features, {"algorithm": 6, "table_number": 12, "key_size": 12, "multi_probe_level": 2})
        neighbors, _distances = search.knnSearch(features, min(32, count), params={"checks": 64})

    @lru_cache(maxsize=8)
    def full_image(position):
        sample = images[canonical[position]]
        if _stamp(sample.path) != sample.stamp:
            raise ValueError(f"图片已变化，请重新全局去重：{sample.path}")
        return sessions[sample.session_index].frame(sample.frame_index)

    assigned = set()
    groups = []
    try:
        for view_index, view_digest in enumerate(view_order):
            _check_stop(stop)
            if view_index in assigned:
                continue
            assigned.add(view_index)
            position = views[view_digest][0]
            index = canonical[position]
            same = list(views[view_digest])
            candidates = (
                range(view_index + 1, count) if neighbors is None else sorted(set(map(int, neighbors[view_index])))
            )
            for other in candidates:
                _check_stop(stop)
                if other <= view_index or other >= count or other in assigned:
                    continue
                other_positions = views[view_order[other]]
                if _near_duplicate(full_image(position), full_image(other_positions[0])):
                    same.extend(other_positions)
                    assigned.add(other)
            defaults = tuple(canonical[item] for item in same)
            members = tuple(member for item in defaults for member in exact[images[item].digest])
            if len(members) > 1:
                groups.append(DuplicateGroup("near" if len(same) > 1 else "exact", index, members, defaults))
            progress(f"复核相似组 {view_index + 1} / {count} · 已发现 {len(groups)} 组")
    finally:
        full_image.cache_clear()
    _check_stop(stop)
    return DeduplicationPlan(
        root,
        sessions,
        tuple(images),
        tuple(groups),
        tuple(canonical),
        tuple(group_root(session.metadata.get("battle_id", session.directory.name)) for session in sessions),
    )


def export_selection(plan: DeduplicationPlan, keep: set[int], destination: Path, *, stop: Event, progress):
    _check_stop(stop)
    if not keep or any(type(index) is not int or not 0 <= index < len(plan.images) for index in keep):
        raise ValueError("请至少保留一张有效图片")
    if find_batches(plan.root, stop=stop).directories != tuple(session.directory for session in plan.sessions):
        raise ValueError("批次列表已变化，请重新全局去重")
    for session in plan.sessions:
        _check_stop(stop)
        if PreparationSession.open(session.directory).metadata != session.metadata:
            raise ValueError(f"批次已变化，请重新全局去重：{session.directory}")
    for sample in plan.images:
        _check_stop(stop)
        if _stamp(sample.path) != sample.stamp:
            raise ValueError(f"图片已变化，请重新全局去重：{sample.path}")
    digests = {}
    for index in keep:
        sample = plan.images[index]
        digests[sample.path.resolve()] = sample.digest
    _read_input_profile(destination.resolve())
    target = destination / "images"
    expected = {}
    for index in keep:
        sample = plan.images[index]
        key = (plan.battle_groups[sample.session_index], sample.path.stem)
        expected.setdefault(key, set()).add(sample.digest)
    seen = set()
    if target.is_dir():
        for path in target.iterdir():
            _check_stop(stop)
            if path.suffix.lower() not in IMAGE_SUFFIXES:
                continue
            match = FLAT_IMAGE_NAME.fullmatch(path.stem)
            if path.is_symlink() or not path.is_file() or match is None:
                raise ValueError("输出目录已有其他图片，请选择新的空目录，避免混入旧标注")
            digest = hashlib.sha256(read_screenshot(path).tobytes()).hexdigest()
            if digest not in expected.get((match["group"], match["frame"]), set()) or digest in seen:
                raise ValueError("输出目录含未选中或重复图片，请选择新的空目录；已有图片和标签不会删除")
            seen.add(digest)
    # Resume by source identity and pixels, regardless of which export identifier a file has.
    chosen = {
        (plan.images[index].session_index, plan.images[index].frame_index)
        for index in keep
        if plan.images[index].digest not in seen
    }
    sessions = []
    for session_index, session in enumerate(plan.sessions):
        entries = [entry for index, entry in enumerate(session.metadata["frames"]) if (session_index, index) in chosen]
        if entries:
            metadata = {**session.metadata, "frames": entries, "battle_id": plan.battle_groups[session_index]}
            sessions.append(PreparationSession(session.directory, metadata))
    result = organize_annotation_images(
        sessions,
        destination,
        stop=stop,
        progress=progress,
        expected_digests=digests,
        export_id=uuid.uuid4().hex,
    )
    return AnnotationExport(result.directory, result.count, result.skipped + len(seen), len(plan.images) - len(keep))
