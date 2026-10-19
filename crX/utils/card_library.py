"""Small local fingerprint library: independent candidates, validation and rollback."""

from __future__ import annotations

import hashlib
import json
import threading
import time
from collections import Counter
from typing import TYPE_CHECKING

import cv2
import numpy as np

from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils.card_review import atomic_json, read_json, sample_file

if TYPE_CHECKING:
    from pathlib import Path

MAX_PER_CARD = 8
MAX_TRIALS_PER_CARD = 5
MAX_ENTRIES = 2048
MAX_LIBRARY_BYTES = 2 * 1024 * 1024
_LOCK = threading.Lock()
_cache: tuple[Path | None, float, list[dict]] = (None, 0, [])


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
        if entry["status"] in ("trial", "active"):
            counts[entry["card_id"]] += 1
    if any(count > MAX_PER_CARD for count in counts.values()):
        raise ValueError("单卡本地指纹数量超限")
    return entries


def _write_library(root, entries):
    value = {"version": 1, "entries": entries}
    if (
        len(entries) > MAX_ENTRIES
        or len(json.dumps(value, ensure_ascii=False, indent=2).encode("utf-8")) + 1 > MAX_LIBRARY_BYTES
    ):
        raise ValueError("本地指纹记录已达上限，请先整理样本库")
    root.mkdir(parents=True, exist_ok=True)
    atomic_json(library_path(root), value)


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
            entries = [entry for entry in read_library() if entry["status"] in ("trial", "active")]
        except (OSError, ValueError, TypeError):
            entries = []
        _cache = (path, now, entries)
    return _cache[2]


def recheck_library(root: Path) -> int:
    """New human labels can invalidate a trial; never use predictions as truth."""
    from crX.utils.card_review import scan_samples

    if not library_path(root).exists():
        return 0
    with _LOCK:
        entries = read_library(root)
        samples, _ = scan_samples(root)
        sources = {sample.key: sample for sample in samples}
        evidence = _evidence(root)
        changed = 0
        for entry in entries:
            if entry["status"] not in ("trial", "active", "covered"):
                continue
            source = sources.get(entry["sources"][0])
            invalid_source = (
                source is None or source.card_id != entry["card_id"] or source.status in ("pending", "ignored")
            )
            result = _validate(entry, evidence)
            entry["validation"] = result
            if invalid_source or result["wrong"]:
                entry["status"] = "disabled"
                changed += 1
        _write_library(root, entries)
        return changed


def sample_fingerprint(sample) -> np.ndarray:
    from crX.bot.card_detection import COLORS_KEYS, extract_card_fingerprint

    slot = sample.record.get("slot")
    if type(slot) is not int or not 0 <= slot < 4 or sample.record.get("available") is not True:
        raise ValueError("请使用可正常出牌的单张手牌样本")
    path = sample.file("frame")
    if path.stat().st_size > 4 * 1024 * 1024:
        raise ValueError("原始截图过大")
    frame = cv2.imread(str(path))
    if frame is None or frame.shape != (633, 419, 3):
        raise ValueError("原始截图需要为 419×633")
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
        if sample.status not in ("confirmed", "trial", "included", "covered") or sample.card_id not in CARD_NAMES_ZH:
            continue
        try:
            fingerprint = sample_fingerprint(sample)
        except (OSError, ValueError, cv2.error):
            continue
        evidence.append((sample, fingerprint, base_match(fingerprint)))
    return evidence


def _validate(entry, evidence, retained=()) -> dict:
    """Hold out source sessions; reject any wrong-card match, require extra coverage."""
    from crX.bot.card_detection import CARD_MATCH_THRESHOLD

    source_sessions = {entry["sources"][0].split("/")[0]}
    result = {"correct": 0, "wrong": 0, "extra": 0, "other_cards": 0}
    for sample, fingerprint, (base_card, base_distance) in evidence:
        if sample.directory.name in source_sessions:
            continue
        same_card = sample.card_id == entry["card_id"]
        if not same_card:
            result["other_cards"] += 1
        distance = int(np.abs(fingerprint - np.array(entry["fingerprint"])).sum())
        if distance > CARD_MATCH_THRESHOLD or distance >= base_distance:
            continue
        result["correct" if same_card else "wrong"] += 1
        if same_card:
            retained_card, retained_distance = base_card, base_distance
            for other in retained:
                if other["id"] == entry["id"] or other["status"] != "active":
                    continue
                other_distance = int(np.abs(fingerprint - np.array(other["fingerprint"])).sum())
                if other_distance < retained_distance:
                    retained_card, retained_distance = other["card_id"], other_distance
            if distance < retained_distance and (
                retained_distance > CARD_MATCH_THRESHOLD or retained_card != sample.card_id
            ):
                result["extra"] += 1
    return result


def update_library(root: Path, selected, action: str) -> str:
    """One UI writer; the gameplay process only reads atomic snapshots."""
    if action not in ("trial", "validate", "revoke"):
        raise ValueError("补库操作无效")
    from crX.bot.card_detection import CARD_MATCH_THRESHOLD
    from crX.utils.card_review import scan_samples

    with _LOCK:
        entries = read_library(root)
        fresh, _ = scan_samples(root)
        keys = {sample.key for sample in selected}
        selected = [sample for sample in fresh if sample.key in keys]
        counts: Counter = Counter()
        if action == "trial":
            for sample in selected:
                if sample.status != "confirmed":
                    counts["skipped"] += 1
                    continue
                fingerprint = sample_fingerprint(sample)
                existing = next(
                    (
                        entry
                        for entry in entries
                        if entry["card_id"] == sample.card_id
                        and np.abs(np.array(entry["fingerprint"]) - fingerprint).sum() <= 100
                        and entry["status"] != "disabled"
                    ),
                    None,
                )
                if existing:
                    if sample.key not in existing["sources"] and len(existing["sources"]) < 128:
                        existing["sources"].append(sample.key)
                    counts["duplicate"] += 1
                    continue
                live = [
                    entry
                    for entry in entries
                    if entry["card_id"] == sample.card_id and entry["status"] in ("trial", "active")
                ]
                if (
                    len(live) >= MAX_PER_CARD
                    or sum(entry["status"] == "trial" for entry in live) >= MAX_TRIALS_PER_CARD
                ):
                    counts["limit"] += 1
                    continue
                base_card, distance = base_match(fingerprint)
                identifier = hashlib.sha256(sample.key.encode() + fingerprint.tobytes()).hexdigest()[:16]
                entry = {
                    "id": identifier,
                    "card_id": sample.card_id,
                    "fingerprint": fingerprint.tolist(),
                    "sources": [sample.key],
                    "status": "covered"
                    if base_card == sample.card_id and distance <= CARD_MATCH_THRESHOLD
                    else "trial",
                }
                entries = [prior for prior in entries if prior["id"] != identifier]
                entries.append(entry)
                counts[entry["status"]] += 1
        targets = [entry for entry in entries if keys.intersection(entry["sources"])]
        if action == "revoke":
            for entry in targets:
                entry["status"] = "disabled"
                counts["disabled"] += 1
        else:
            evidence = _evidence(root)
            for entry in targets:
                if entry["status"] not in ("trial", "active"):
                    continue
                result = _validate(entry, evidence, entries if action == "validate" else ())
                entry["validation"] = result
                if result["wrong"]:
                    entry["status"] = "disabled"
                    counts["failed"] += 1
                elif action == "validate":
                    if result["correct"] and result["extra"] and result["other_cards"]:
                        entry["status"] = "active"
                        counts["active"] += 1
                    elif result["correct"] and not result["extra"] and result["other_cards"]:
                        entry["status"] = "covered"
                        counts["redundant"] += 1
                    else:
                        counts["waiting"] += 1
        _write_library(root, entries)
    labels = {
        "trial": "新增试用",
        "covered": "原库已识别，无需补库",
        "duplicate": "合并近似候选",
        "limit": "达到单卡上限",
        "skipped": "跳过非待补库",
        "disabled": "已撤销",
        "failed": "验证误认，已停用",
        "active": "验证通过并保留",
        "waiting": "证据不足，继续待验证",
        "redundant": "已有指纹覆盖，无需重复保留",
    }
    return "；".join(f"{labels[key]} {value}" for key, value in counts.items()) or "没有可处理的候选"
