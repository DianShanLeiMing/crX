"""Human-confirmed fingerprints, multiple appearances and bounded match telemetry."""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import tempfile
import threading
import time
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.coords import GAME_SCREEN_SIZE, LEGACY_SCREEN_SIZE
from crX.utils.card_review import atomic_json, read_json, sample_file

MAX_PER_CARD = 8
SIMILAR_FINGERPRINT_DISTANCE = 100
MAX_ENTRIES = 2048
MAX_LIBRARY_BYTES = 2 * 1024 * 1024
_LOCK = threading.Lock()
_cache: tuple[Path | None, float, list[dict]] = (None, 0, [])
MAX_MATCH_OBSERVATIONS = 100000


def read_match_stats(root: Path) -> dict:
    path = sample_file(root, "runtime-matches.json")
    if path.exists() and path.stat().st_size > MAX_LIBRARY_BYTES:
        raise ValueError("运行匹配记录过大")
    records = read_json(path).get("entries", {})
    if not isinstance(records, dict) or len(records) > MAX_ENTRIES:
        raise ValueError("运行匹配记录无效")
    for identifier, values in records.items():
        if not re.fullmatch(r"[0-9a-f]{16}", identifier) or not isinstance(values, dict):
            raise ValueError("运行匹配记录无效")
        for field in ("matches", "distance_total", "margin_total"):
            limit = MAX_MATCH_OBSERVATIONS if field == "matches" else MAX_MATCH_OBSERVATIONS * 10000
            if type(values.get(field)) is not int or not 0 <= values[field] <= limit:
                raise ValueError("运行匹配计数无效")
    return records


def merge_match_stats(root: Path, observations: dict) -> None:
    """Only the gameplay collector writes; UI clearing is disabled during a fight."""
    records = read_match_stats(root)
    live_ids = {entry["id"] for entry in read_library(root)}
    records = {key: value for key, value in records.items() if key in live_ids}
    for identifier, values in observations.items():
        if identifier not in live_ids:
            continue
        current = records.setdefault(identifier, {"matches": 0, "distance_total": 0, "margin_total": 0})
        remaining = MAX_MATCH_OBSERVATIONS - current["matches"]
        count = min(remaining, values["matches"])
        if not count:
            continue
        for field in ("distance_total", "margin_total"):
            current[field] += round(values[field] * count / values["matches"])
        current["matches"] += count
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(sample_file(root, "runtime-matches.json"), {"entries": records})


def library_path(root: Path | None = None) -> Path:
    if root is None:
        from crX.bot.card_samples import get_card_samples_dir

        root = get_card_samples_dir()
    return sample_file(root, "fingerprints.json")


def read_library(root: Path | None = None) -> list[dict]:
    path = library_path(root)
    if path.exists() and path.stat().st_size > MAX_LIBRARY_BYTES:
        raise ValueError("本地指纹库过大")
    entries = read_json(path).get("entries", [])
    if not isinstance(entries, list) or len(entries) > MAX_ENTRIES:
        raise ValueError("本地指纹库格式无效")
    identifiers = set()
    counts: Counter = Counter()
    for entry in entries:
        if not isinstance(entry, dict):
            raise ValueError("本地指纹格式无效")
        fingerprint = entry.get("fingerprint")
        if (
            entry.get("card_id") not in CARD_NAMES_ZH
            or entry.get("status") not in ("trial", "active", "disabled", "covered")
            or not isinstance(entry.get("id"), str)
            or entry["id"] in identifiers
            or not isinstance(entry.get("sources"), list)
            or not entry["sources"]
            or len(entry["sources"]) > 128
            or any(not isinstance(source, str) or len(source) > 128 for source in entry["sources"])
            or not isinstance(fingerprint, list)
            or len(fingerprint) != 4
            or any(
                not isinstance(row, list)
                or len(row) != 16
                or any(type(value) is not int or not 0 <= value <= 891 for value in row)
                or sum(row) != 891
                for row in fingerprint
            )
        ):
            raise ValueError("本地指纹格式无效")
        identifiers.add(entry["id"])
        score = entry.get("score")
        if score is not None and (type(score) not in (int, float) or not math.isfinite(score) or not 0 <= score <= 100):
            raise ValueError("本地指纹评分无效")
        if type(entry.get("auto_selected", False)) is not bool:
            raise ValueError("本地指纹选用状态无效")
        if not isinstance(entry.get("validation", {}), dict) or not isinstance(entry.get("runtime", {}), dict):
            raise ValueError("本地指纹验证信息无效")
        if type(entry.get("manual_disabled", False)) is not bool or entry.get("disabled_reason", "") not in (
            "",
            "manual",
            "conflict",
            "source",
            "limit",
        ):
            raise ValueError("本地指纹停用信息无效")
        conflicts = entry.get("conflicts", [])
        if not isinstance(conflicts, list) or any(
            not isinstance(card, str) or card not in CARD_NAMES_ZH for card in conflicts
        ):
            raise ValueError("本地指纹冲突信息无效")
        if entry["status"] in ("trial", "active"):
            counts[entry["card_id"]] += 1
    if any(count > MAX_PER_CARD for count in counts.values()):
        raise ValueError("单卡本地指纹数量超限")
    return entries


def _write_library(root, entries):
    value = {"version": 2, "entries": entries}
    if (
        len(entries) > MAX_ENTRIES
        or len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) + 1 > MAX_LIBRARY_BYTES
    ):
        raise ValueError("本地指纹记录已达上限，请先整理样本库")
    root.mkdir(parents=True, exist_ok=True)
    path = library_path(root)
    previous = read_json(path)
    backup = sample_file(root, "fingerprints-before-multi.json")
    if previous and previous.get("version", 1) == 1 and not backup.exists():
        atomic_json(backup, previous)
    atomic_json(path, value)


def runtime_entries() -> list[dict]:
    """Refresh at most once per second; invalid files never affect the base library."""
    global _cache
    now = time.monotonic()
    try:
        path = library_path()
    except (OSError, ValueError):
        _cache = (None, now, [])
        return []
    if path != _cache[0] or now - _cache[1] >= 1:
        try:
            entries = [
                entry
                for entry in read_library()
                if entry["status"] == "active" and not entry.get("manual_disabled") and not entry.get("disabled_reason")
            ]
        except (OSError, ValueError, TypeError):
            entries = []
        _cache = (path, now, entries)
    return _cache[2]


def clear_samples(root: Path) -> int:
    """Delete only captured sessions; retain learned references and stop trials."""
    if not root.exists():
        return 0
    if root.is_symlink() or root.is_junction():
        raise ValueError("样本目录不能是链接")
    base = root.resolve(strict=True)
    with _LOCK:
        targets = []
        for directory in root.iterdir():
            if directory.is_symlink() or directory.is_junction() or not directory.is_dir():
                continue
            if not (directory / "samples.jsonl").is_file():
                continue
            if directory.resolve().parent != base:
                raise ValueError("样本目录超出允许范围")
            for current, folders, files in os.walk(directory, followlinks=False):
                for name in (*folders, *files):
                    child = Path(current) / name
                    if child.is_symlink() or child.is_junction() or not child.resolve().is_relative_to(base):
                        raise ValueError("样本中包含链接或外部路径，请先移除链接")
            targets.append(directory)
        entries = read_library(root)
        for entry in entries:
            if entry["status"] == "active":
                entry["sources_cleared"] = True
            elif entry["status"] in ("trial", "covered"):
                entry["status"] = "disabled"
        if entries:
            _write_library(root, entries)
        removed = 0
        for directory in targets:
            if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != base:
                raise ValueError("样本目录已变化，已停止清空")
            try:
                shutil.rmtree(directory)
            except OSError as error:
                raise OSError(f"已清空 {removed}/{len(targets)} 局样本；请刷新后重试：{error}") from error
            removed += 1
        return removed


def clear_filtered_samples(
    root: Path, keys: set[str], *, statuses: set[str] | None = None, preserve_selected_cards: bool = False
) -> int:
    """Clear an explicit UI snapshot; preflight paths before removing any records."""
    from crX.utils.card_review import sample_progress, scan_samples

    if not root.exists() or not keys:
        return 0
    if root.is_symlink() or root.is_junction():
        raise ValueError("样本目录不能是链接")
    base = root.resolve(strict=True)
    with _LOCK:
        samples, errors = scan_samples(root)
        if errors:
            raise ValueError("样本记录存在错误，请修复后再清空，避免丢失未解析的内容")
        protected_cards = {
            sample.card_id for sample in samples if preserve_selected_cards and sample_progress(sample)[2] == "当前使用"
        }
        selected = [
            sample
            for sample in samples
            if sample.key in keys
            and (statuses is None or sample.status in statuses)
            and sample.card_id not in protected_cards
        ]
        keys = {sample.key for sample in selected}
        plans = []
        for directory in {sample.directory for sample in selected}:
            if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != base:
                raise ValueError("样本目录超出允许范围或包含链接")
            names = {sample.name for sample in selected if sample.directory == directory}
            files = []
            for sample in selected:
                if sample.directory != directory:
                    continue
                for kind in ("card", "hand", "frame"):
                    if sample.record["files"][kind] != f"{sample.name}-{kind}.png":
                        raise ValueError("样本图片命名异常，已停止清空")
                files.extend(sample_file(directory, f"{sample.name}-{kind}.png") for kind in ("card", "hand", "frame"))
                files.append(sample_file(directory, f"{sample.name}-candidate.json"))
            source = sample_file(directory, "samples.jsonl")
            lines = source.read_text(encoding="utf-8").splitlines()
            remaining = [
                line for line in lines if json.loads(line)["files"]["card"].removesuffix("-card.png") not in names
            ]
            reviews = {}
            for filename in ("review.json", "annotations.json"):
                path = sample_file(directory, filename)
                if path.exists():
                    reviews[path] = {name: review for name, review in read_json(path).items() if name not in names}
            files.extend(sample_file(directory, filename) for filename in ("index.html", "summary.json"))
            if any(
                path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file())
                for path in [source, *reviews, *files]
            ):
                raise ValueError("样本包含链接或异常文件，已停止清空")
            plans.append((directory, source, remaining, reviews, files))
        prior_entries = read_library(root)
        removed_ids = {entry["id"] for entry in prior_entries if keys.intersection(entry["sources"])}
        entries = [entry for entry in prior_entries if entry["id"] not in removed_ids]
        match_stats = {key: value for key, value in read_match_stats(root).items() if key not in removed_ids}
        if selected:
            _write_library(root, entries)
            if sample_file(root, "runtime-matches.json").exists():
                atomic_json(sample_file(root, "runtime-matches.json"), {"entries": match_stats})
        for directory, source, remaining, reviews, files in plans:
            if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != base:
                raise ValueError("样本目录已变化，已停止清空")
            # The collector cannot run while the UI clears. Replace the manifest atomically.
            descriptor, temporary = tempfile.mkstemp(prefix="samples-", suffix=".tmp", dir=directory)
            try:
                with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
                    stream.write("\n".join(remaining) + ("\n" if remaining else ""))
                os.replace(temporary, source)
            finally:
                Path(temporary).unlink(missing_ok=True)
            for path, reviews_left in reviews.items():
                atomic_json(path, reviews_left)
            for path in files:
                if path.is_symlink() or path.resolve().parent != directory.resolve():
                    raise ValueError("样本文件已变化，已停止清空")
                path.unlink(missing_ok=True)
            if not remaining:
                source.unlink()
                for path, reviews_left in reviews.items():
                    if not reviews_left:
                        path.unlink()
                if not any(directory.iterdir()):
                    directory.rmdir()
        return len(selected)


def sample_fingerprint(sample) -> np.ndarray:
    from crX.bot.card_detection import COLORS_KEYS, extract_card_fingerprint

    slot = sample.record.get("slot")
    if type(slot) is not int or not 0 <= slot < 4 or sample.record.get("available") is not True:
        raise ValueError("请使用可正常出牌的单张手牌样本")
    path = sample.file("frame")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("原始截图过大")
    frame = cv2.imread(str(path))
    if frame is None or frame.shape not in ((*GAME_SCREEN_SIZE[::-1], 3), (*LEGACY_SCREEN_SIZE[::-1], 3)):
        raise ValueError("原始截图需要为 360×640 或 419×633 历史样本")
    return np.array([[corner[color] for color in COLORS_KEYS] for corner in extract_card_fingerprint(frame, slot)])


def base_match(fingerprint) -> tuple[str, int]:
    from crX.bot import card_detection as detection

    distances = np.abs(detection._REFERENCE_MATRIX - fingerprint).sum(axis=(1, 2))
    index = int(distances.argmin())
    card_id = detection._REFERENCE_NAMES[index]
    return detection.CARD_DETECTION_ALIASES.get(card_id, card_id), int(distances[index])


def _evidence(root):
    from crX.utils.card_review import scan_samples

    evidence = []
    samples, _ = scan_samples(root)
    for sample in samples:
        if (
            sample.status not in ("confirmed", "trial", "included", "covered", "conflict")
            or sample.card_id not in CARD_NAMES_ZH
        ):
            continue
        try:
            evidence.append((sample, sample_fingerprint(sample)))
        except (OSError, ValueError, cv2.error):
            continue
    return evidence


def sync_library(root: Path) -> None:
    """Enable confirmed appearances; only human-label conflicts can block them.

    Legacy trial/covered entries migrate here. Explicitly revoked and previously
    disabled entries stay off; runtime similarity never supplies a human label.
    """
    from crX.utils.card_review import scan_samples

    if not library_path(root).exists():
        return
    with _LOCK:
        entries = read_library(root)
        before = json.dumps(entries, sort_keys=True)
        samples, errors = scan_samples(root)
        if errors:
            raise ValueError("样本记录不完整，本轮不调整补库")
        sources = {sample.key: sample for sample in samples}
        evidence = _evidence(root)
        stats = read_match_stats(root)
        counts: Counter = Counter()
        for entry in entries:
            entry["runtime"] = stats.get(entry["id"], {"matches": 0, "distance_total": 0, "margin_total": 0})
            for obsolete in ("score", "validation", "auto_selected"):
                entry.pop(obsolete, None)
            reason = entry.get("disabled_reason", "")
            if entry.get("manual_disabled") or (
                entry["status"] == "disabled" and reason not in ("conflict", "source", "limit")
            ):
                entry["manual_disabled"] = True
                continue
            known = [sources[key] for key in entry["sources"] if key in sources]
            valid = (
                any(
                    sample.card_id == entry["card_id"] and sample.status not in ("pending", "ignored")
                    for sample in known
                )
                if known
                else entry.get("sources_cleared", False)
            )
            conflicts = (
                sorted(
                    {
                        sample.card_id
                        for sample, fingerprint in evidence
                        if sample.card_id != entry["card_id"]
                        and int(np.abs(fingerprint - np.array(entry["fingerprint"])).sum())
                        <= SIMILAR_FINGERPRINT_DISTANCE
                    }
                )
                if valid
                else []
            )
            entry["conflicts"] = conflicts
            if not valid:
                entry["status"], entry["disabled_reason"] = "disabled", "source"
            elif conflicts:
                entry["status"], entry["disabled_reason"] = "disabled", "conflict"
            elif counts[entry["card_id"]] >= MAX_PER_CARD:
                entry["status"], entry["disabled_reason"] = "disabled", "limit"
            else:
                entry["status"] = "active"
                entry.pop("disabled_reason", None)
                counts[entry["card_id"]] += 1
        if json.dumps(entries, sort_keys=True) != before:
            _write_library(root, entries)


def update_library(root: Path, selected, action: str) -> str:
    """Confirm-to-enable, explicit revoke, and near-duplicate merging."""
    if action not in ("enable", "trial", "validate", "revoke"):
        raise ValueError("补库操作无效")
    from crX.utils.card_review import scan_samples

    with _LOCK:
        entries = read_library(root)
        fresh, errors = scan_samples(root)
        if errors:
            raise ValueError("样本记录不完整，请先刷新检查")
        keys = {sample.key for sample in selected}
        selected = [sample for sample in fresh if sample.key in keys]
        counts: Counter = Counter()
        if action == "revoke":
            for entry in entries:
                if keys.intersection(entry["sources"]):
                    entry.update(status="disabled", manual_disabled=True, disabled_reason="manual")
                    counts["disabled"] += 1
        else:
            for sample in selected:
                if (
                    sample.status not in ("confirmed", "trial", "included", "covered", "conflict")
                    or sample.card_id not in CARD_NAMES_ZH
                ):
                    counts["skipped"] += 1
                    continue
                fingerprint = sample_fingerprint(sample)
                existing = next(
                    (
                        entry
                        for entry in entries
                        if entry["card_id"] == sample.card_id
                        and int(np.abs(np.array(entry["fingerprint"]) - fingerprint).sum())
                        <= SIMILAR_FINGERPRINT_DISTANCE
                    ),
                    None,
                )
                if existing:
                    if (
                        existing["status"] not in ("active", "trial")
                        and sum(
                            entry["card_id"] == sample.card_id and entry["status"] in ("active", "trial")
                            for entry in entries
                        )
                        >= MAX_PER_CARD
                    ):
                        counts["limit"] += 1
                        continue
                    if sample.key not in existing["sources"] and len(existing["sources"]) < 128:
                        existing["sources"].append(sample.key)
                    existing.update(status="active", manual_disabled=False)
                    counts["duplicate"] += 1
                    continue
                live = [
                    entry for entry in entries if entry["card_id"] == sample.card_id and entry["status"] != "disabled"
                ]
                if len(live) >= MAX_PER_CARD:
                    counts["limit"] += 1
                    continue
                identifier = hashlib.sha256(sample.key.encode() + fingerprint.tobytes()).hexdigest()[:16]
                entry = {
                    "id": identifier,
                    "card_id": sample.card_id,
                    "fingerprint": fingerprint.tolist(),
                    "sources": [sample.key],
                    "status": "active",
                }
                entries = [prior for prior in entries if prior["id"] != identifier]
                entries.append(entry)
                counts["active"] += 1
        _write_library(root, entries)
    sync_library(root)
    labels = {
        "active": "已补库",
        "duplicate": "合并近似样本",
        "limit": "达到单卡8份上限，请整理后重试",
        "skipped": "跳过未确认样本",
        "disabled": "已撤销",
    }
    return "；".join(f"{labels[key]} {value}" for key, value in counts.items()) or "没有可处理的样本"
