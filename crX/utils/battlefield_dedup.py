"""Exact deduplication and reviewable representative sampling of battle scenes."""

from __future__ import annotations

import hashlib
import re
import uuid
from collections import defaultdict, deque
from dataclasses import dataclass
from functools import lru_cache
from typing import TYPE_CHECKING

import cv2
import numpy as np

from crX.bot.coords import BATTLEFIELD_DATA_REGION_LTRB, GAME_SCREEN_SIZE, TOWER_HEALTH_TEXT_REGIONS_LTRB
from crX.utils.battlefield_data import (
    AnnotationExport,
    PreparationSession,
    find_batches,
    organize_annotation_images,
    read_screenshot,
)
from crX.utils.battlefield_filter_cache import FeatureCache
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
    recommended_keep: tuple[int, ...] = ()


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
        return len(self.images) - len(self.unique_keep)

    @property
    def unique_keep(self) -> tuple[int, ...]:
        return tuple(sorted(set(self.default_keep).union(*(group.default_keep for group in self.groups))))


def _check_stop(stop: Event):
    if stop.is_set():
        raise ValueError("已停止图片筛选，原始图片未修改")


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


def _scene_difference(first, second):
    """Offline-only features; full-resolution exports and runtime inputs are unchanged."""
    for left, top, right, bottom in TOWER_HEALTH_TEXT_REGIONS_LTRB:
        hp_delta = cv2.absdiff(first[top:bottom, left:right], second[top:bottom, left:right])
        if int((hp_delta.max(axis=2) > 16).sum()) >= 4:
            return False, True
    views = [_comparison_view(image) for image in (first, second)]
    delta = cv2.absdiff(*views)
    changed = delta.max(axis=2) > 12
    count = int(changed.sum())
    # Tiny new targets must not disappear behind a high whole-scene similarity score.
    if 0 < count <= 32 or float(delta.mean()) > 8:
        return False, True
    signed = views[0].astype(np.int16) - views[1].astype(np.int16)
    mass = float(np.abs(signed).sum())
    # Compact, one-sided changes are more likely a new object than a translated one.
    if 32 < count < 6000 and mass and abs(float(signed.sum())) / mass > 0.75:
        return False, True
    a, b = (cv2.resize(cv2.cvtColor(view, cv2.COLOR_BGR2GRAY), (180, 228)).astype(np.float32) for view in views)
    u, v = (cv2.GaussianBlur(image, (7, 7), 1.5) for image in (a, b))
    var_a = cv2.GaussianBlur(a * a, (7, 7), 1.5) - u * u
    var_b = cv2.GaussianBlur(b * b, (7, 7), 1.5) - v * v
    covariance = cv2.GaussianBlur(a * b, (7, 7), 1.5) - u * v
    ssim = ((2 * u * v + 6.5025) * (2 * covariance + 58.5225)) / ((u * u + v * v + 6.5025) * (var_a + var_b + 58.5225))
    similar = float(ssim.mean()) >= 0.90
    significant = float(changed.mean()) > 0.15 or float(delta.mean()) > 7
    return similar, significant


def load_plan_image(plan: DeduplicationPlan, index: int):
    sample = plan.images[index]
    if _stamp(sample.path) != sample.stamp:
        raise ValueError(f"图片已变化，请重新筛选图片：{sample.path}")
    image = read_screenshot(sample.path)
    if hashlib.sha256(image.tobytes()).hexdigest() != sample.digest:
        raise ValueError(f"图片已变化，请重新筛选图片：{sample.path}")
    return image


def analyze_batches(root: Path, *, stop: Event, progress) -> DeduplicationPlan:
    root = root.resolve()
    with FeatureCache(root) as cache:
        return _analyze_batches(root, stop=stop, progress=progress, cache=cache)


def _analyze_batches(root, *, stop, progress, cache):
    sessions = tuple(PreparationSession.open(path) for path in reversed(find_batches(root, stop=stop).directories))
    _check_stop(stop)
    total = sum(len(session.metadata["frames"]) for session in sessions)
    if not total:
        raise ValueError("所选目录的批次没有图片")
    if total > MAX_IMAGES:
        raise ValueError(f"每次最多分析 {MAX_IMAGES} 张图片，请分目录处理")
    images, features, canonical = [], [], []
    exact, aliases = {}, {}
    cached = 0

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
            feature = cache.image(path, before)
            if feature is None:
                image = session.frame(frame_index)
                if image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
                    raise ValueError(f"图片尺寸无效：{path}")
                view = _comparison_view(image)
                gray = cv2.cvtColor(view, cv2.COLOR_BGR2GRAY)
                quality = float(cv2.Laplacian(gray, cv2.CV_32F).var())
                feature = (
                    hashlib.sha256(image.tobytes()).hexdigest(),
                    hashlib.sha256(view.tobytes()).hexdigest(),
                    _signature(image).tobytes(),
                    quality,
                )
                cache.save_image(path, before, feature)
            else:
                cached += 1
            if _stamp(path) != before:
                raise ValueError(f"图片内容发生变化：{path}")
            digest = feature[0]
            index = len(images)
            images.append(CaptureImage(session_index, frame_index, path, digest, before))
            features.append(feature)
            if digest not in exact:
                canonical.append(index)
                exact[digest] = [index]
            else:
                previous = images[exact[digest][0]]
                previous_session = sessions[previous.session_index]
                previous_battle = previous_session.metadata.get("battle_id", previous_session.directory.name)
                first, second = sorted((group_root(previous_battle), group_root(battle)))
                aliases[second] = first
                exact[digest].append(index)
            progress(f"分析 {len(images)} / {total} 张 · 已复用 {cached} 张图片特征")

    @lru_cache(maxsize=8)
    def full_image(index):
        sample = images[index]
        if _stamp(sample.path) != sample.stamp:
            raise ValueError(f"图片已变化，请重新筛选图片：{sample.path}")
        image = sessions[sample.session_index].frame(sample.frame_index)
        if _stamp(sample.path) != sample.stamp:
            raise ValueError(f"图片已变化，请重新筛选图片：{sample.path}")
        return image

    def compare(first, second):
        key = ":".join(sorted((images[first].digest, images[second].digest)))
        result = cache.pair(key)
        if result is None:
            a, b = full_image(first), full_image(second)
            similar, significant = _scene_difference(a, b)
            result = (similar and not significant, similar and not significant and _near_duplicate(a, b))
            cache.save_pair(key, result)
        return result[0] if images[first].session_index == images[second].session_index else result[1]

    # A bounded hash index supplies candidates; it never decides which image to remove.
    # Missing a candidate leaves an extra image, rather than forcing a false match.
    buckets = defaultdict(lambda: deque(maxlen=64))
    clusters, view_clusters = [], {}
    try:
        for processed, index in enumerate(
            sorted(canonical, key=lambda item: (-features[item][3], images[item].digest))
        ):
            _check_stop(stop)
            signature = features[index][2]
            cluster_index = view_clusters.get(features[index][1])
            if cluster_index is None:
                candidates = set()
                for band, value in enumerate(signature):
                    candidates.update(buckets[(band, value)])
                ranked = sorted(
                    candidates,
                    key=lambda candidate: (
                        int(
                            np.unpackbits(
                                np.bitwise_xor(
                                    np.frombuffer(signature, np.uint8),
                                    np.frombuffer(features[clusters[candidate][0]][2], np.uint8),
                                )
                            ).sum()
                        ),
                        candidate,
                    ),
                )
                for candidate in ranked[:24]:
                    _check_stop(stop)
                    representative = clusters[candidate][0]
                    distance = int(
                        np.unpackbits(
                            np.bitwise_xor(
                                np.frombuffer(signature, np.uint8), np.frombuffer(features[representative][2], np.uint8)
                            )
                        ).sum()
                    )
                    if distance <= 16 and compare(representative, index):
                        cluster_index = candidate
                        break
            if cluster_index is None:
                cluster_index = len(clusters)
                clusters.append([index])
                for band, value in enumerate(signature):
                    buckets[(band, value)].append(cluster_index)
            else:
                clusters[cluster_index].append(index)
            view_clusters[features[index][1]] = cluster_index
            progress(f"按内容筛选 {processed + 1} / {len(canonical)} 张 · 保留 {len(clusters)} 张代表图")
    finally:
        full_image.cache_clear()
    _check_stop(stop)
    groups = []
    for cluster in clusters:
        defaults = tuple(sorted(cluster))
        members = tuple(sorted(member for item in defaults for member in exact[images[item].digest]))
        if len(members) > 1:
            kind = (
                "exact"
                if len(defaults) == 1
                else "near"
                if len({features[item][1] for item in defaults}) == 1
                else "scene"
            )
            groups.append(DuplicateGroup(kind, cluster[0], members, defaults, (cluster[0],)))
    groups.sort(key=lambda group: group.members[0])
    return DeduplicationPlan(
        root,
        sessions,
        tuple(images),
        tuple(groups),
        tuple(sorted(cluster[0] for cluster in clusters)),
        tuple(group_root(session.metadata.get("battle_id", session.directory.name)) for session in sessions),
    )


def export_selection(plan: DeduplicationPlan, keep: set[int], destination: Path, *, stop: Event, progress):
    _check_stop(stop)
    if not keep or any(type(index) is not int or not 0 <= index < len(plan.images) for index in keep):
        raise ValueError("请至少保留一张有效图片")
    if set(find_batches(plan.root, stop=stop).directories) != {session.directory for session in plan.sessions}:
        raise ValueError("批次列表已变化，请重新筛选图片")
    for session in plan.sessions:
        _check_stop(stop)
        if PreparationSession.open(session.directory).metadata != session.metadata:
            raise ValueError(f"批次已变化，请重新筛选图片：{session.directory}")
    for sample in plan.images:
        _check_stop(stop)
        if _stamp(sample.path) != sample.stamp:
            raise ValueError(f"图片已变化，请重新筛选图片：{sample.path}")
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
