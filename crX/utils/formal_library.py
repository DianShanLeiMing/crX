"""Human-approved, distributable card references; telemetry is only a suggestion."""

from __future__ import annotations

import hashlib
import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from crX.bot.card_data import CARD_FINGERPRINT_VARIANTS, CARD_NAMES_ZH, card_color_data
from crX.utils.card_review import atomic_json, load_card_thumbnail, sample_file

MIN_MATCHES = 100
MIN_BATTLES = 3
MIN_MARGIN = 100
MAX_BYTES = 2 * 1024 * 1024
MAX_ENTRIES = 2048
MAX_BATCH = 64
SIMILAR_DISTANCE = 100
_LOCK = threading.Lock()
_runtime_cache = (None, 0.0, [])


def formal_path() -> Path:
    if getattr(sys, "frozen", False):
        return Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent)) / "card_reference_library.json"
    return Path(__file__).resolve().parents[1] / "bot" / "card_reference_library.json"


def source_checkout() -> bool:
    return not getattr(sys, "frozen", False) and (Path(__file__).resolve().parents[2] / "pyproject.toml").is_file()


def _signature(card_id, fingerprint):
    value = json.dumps([card_id, fingerprint], separators=(",", ":")).encode()
    return hashlib.sha256(value).hexdigest()[:16]


def _read_entries(path: Path) -> list[dict]:
    if path.is_symlink():
        raise ValueError("正式参考库不能使用符号链接")
    if not path.exists():
        return []
    if path.stat().st_size > MAX_BYTES:
        raise ValueError("正式参考库过大")
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("正式参考库过大")
    document = json.loads(payload.decode("utf-8"))
    if not isinstance(document, dict):
        raise ValueError("正式参考库格式无效")
    entries = document.get("entries")
    if (
        type(document.get("version")) is not int
        or document["version"] != 1
        or not isinstance(entries, list)
        or len(entries) > MAX_ENTRIES
    ):
        raise ValueError("正式参考库格式无效")
    seen = set()
    for entry in entries:
        if (
            not isinstance(entry, dict)
            or set(entry) != {"id", "card_id", "fingerprint"}
            or not isinstance(entry.get("card_id"), str)
            or entry["card_id"] not in CARD_NAMES_ZH
        ):
            raise ValueError("正式参考库卡名无效")
        fingerprint = entry.get("fingerprint")
        if (
            not isinstance(fingerprint, list)
            or len(fingerprint) != 4
            or any(
                not isinstance(row, list)
                or len(row) != 16
                or any(type(value) is not int or not 0 <= value <= 891 for value in row)
                or sum(row) != 891
                for row in fingerprint
            )
        ):
            raise ValueError("正式参考库指纹无效")
        identifier = _signature(entry["card_id"], fingerprint)
        if entry.get("id") != identifier or identifier in seen:
            raise ValueError("正式参考库编号无效或重复")
        seen.add(identifier)
    return entries


def runtime_formal_entries() -> list[dict]:
    """Bound reads and reload after promotion; malformed supplements do not block play."""
    global _runtime_cache
    path, now = formal_path(), time.monotonic()
    if path != _runtime_cache[0] or now - _runtime_cache[1] >= 1:
        try:
            entries = _read_entries(path)
        except (OSError, ValueError, TypeError):
            entries = []
        _runtime_cache = (path, now, entries)
    return _runtime_cache[2]


def _bundled_references():
    aliases = {"spirit_empress_air": "spirit_empress", "spirit_empress_ground": "spirit_empress"}
    references = []
    for card_id, fingerprint in card_color_data.items():
        value = [list(row.values()) if isinstance(row, dict) else list(row) for row in fingerprint]
        references.append((aliases.get(card_id, card_id), value))
    for card_id, variants in CARD_FINGERPRINT_VARIANTS.items():
        references.extend((card_id, fingerprint) for fingerprint in variants)
    references.extend((entry["card_id"], entry["fingerprint"]) for entry in _read_entries(formal_path()))
    return references


def assess_references(root: Path, *, samples=None, refresh=True) -> dict[str, dict]:
    from crX.utils.card_library import read_library, read_match_stats, sample_fingerprint, sync_library
    from crX.utils.card_review import scan_samples

    if refresh:
        sync_library(root)
    if samples is None:
        samples, errors = scan_samples(root)
        if errors:
            raise ValueError("样本记录不完整，请先刷新检查")
    sources = {sample.key: sample for sample in samples}
    references = _bundled_references()
    matrix = np.array([fingerprint for _, fingerprint in references], dtype=np.int64)
    stats = read_match_stats(root)
    results = {}
    for entry in read_library(root):
        observations = stats.get(entry["id"], {})
        matches = observations.get("assists", 0)
        battles = observations.get("assist_battles", 0)
        margin = observations.get("assist_margin_total", 0) / matches if matches else 0
        distance = observations.get("assist_distance_total", 0) / matches if matches else 0
        result = {
            **entry,
            "status": "暂不建议",
            "covered": False,
            "reason": "需要先确认卡名并加入本地补库",
            "matches": matches,
            "battles": battles,
            "average_margin": margin,
            "average_distance": distance,
        }
        results[entry["id"]] = result
        near = np.flatnonzero(np.abs(matrix - np.array(entry["fingerprint"])).sum(axis=(1, 2)) <= SIMILAR_DISTANCE)
        if any(references[index][0] != entry["card_id"] for index in near):
            result["reason"] = "与其他卡牌的内置参考过于接近，请核对卡名"
            continue
        if entry.get("conflicts") or entry.get("disabled_reason") == "conflict":
            result["reason"] = "存在人工卡名冲突，请在全部列表更正卡名"
            continue
        if any(references[index][0] == entry["card_id"] for index in near):
            result.update(covered=True, reason="正式库已包含相同或近似参考，不再作为待补库候选")
            continue
        if entry["status"] != "active" or entry.get("manual_disabled") or entry.get("disabled_reason"):
            continue
        source = next(
            (
                sources[key]
                for key in entry["sources"]
                if key in sources and sources[key].card_id == entry["card_id"] and sources[key].status == "included"
            ),
            None,
        )
        if source is None:
            result["reason"] = "缺少可核对的人工确认样本"
            continue
        result["source_key"] = source.key
        try:
            if int(np.abs(sample_fingerprint(source) - np.array(entry["fingerprint"])).sum()) > SIMILAR_DISTANCE:
                raise ValueError("样本与补库指纹不一致，请重新确认")
        except (OSError, ValueError, TypeError) as error:
            result["reason"] = str(error)
            continue
        disagreements = [
            sample
            for sample in samples
            if sample.record.get("reason") == "builtin_disagreement"
            and sample.record.get("source") in (f"local_active:{entry['id']}", f"local_trial:{entry['id']}")
            and sample.card_id != entry["card_id"]
        ]
        if disagreements:
            result["reason"] = "正式库与本地样本识别不一致，请在待确认／全部列表核对卡名"
        elif matches < MIN_MATCHES:
            result["reason"] = f"补足识别 {matches} 次，建议至少 {MIN_MATCHES} 次；已覆盖 {battles} 个对局"
        elif battles < MIN_BATTLES:
            result["reason"] = f"补足识别来自 {battles} 个对局，建议至少 {MIN_BATTLES} 个对局"
        elif margin < MIN_MARGIN:
            result["reason"] = f"平均候选差距 {margin:.1f}，小于 {MIN_MARGIN}，易与其他卡牌混淆"
        else:
            result.update(
                status="建议纳入",
                reason=f"补足识别 {matches} 次，覆盖 {battles} 个对局，平均候选差距 {margin:.1f}；请人工核对识别结果",
            )
    return results


def annotate_references(root, samples):
    assessments = assess_references(root, samples=samples, refresh=False)
    for sample in samples:
        assessment = assessments.get(sample.review.get("library_id"))
        if assessment:
            sample.review.update(
                formal_status=assessment["status"],
                formal_reason=assessment["reason"],
                formal_covered=assessment["covered"],
            )


def prepare_promotion(root: Path, cards: set[str]) -> list[dict]:
    from crX.utils.card_review import scan_samples

    assessments = assess_references(root)
    samples, errors = scan_samples(root)
    if errors:
        raise ValueError("样本记录不完整，请先刷新检查")
    sources = {sample.key: sample for sample in samples}
    candidates = [
        entry for entry in assessments.values() if entry["card_id"] in cards and entry["status"] == "建议纳入"
    ]
    if len(candidates) > MAX_BATCH:
        raise ValueError(f"一次最多正式补库 {MAX_BATCH} 份参考，请分批选择")
    for entry in candidates:
        image = load_card_thumbnail(sources[entry["source_key"]], size=(64, 80))
        if image is None:
            raise ValueError("待纳入样本的截图缺失或损坏，请先检查")
        entry["preview"] = image
    return candidates


@contextmanager
def writer_lock(path):
    """An OS-owned lock is released even if a writer exits before journal recovery."""
    if path.is_symlink() or path.is_junction():
        raise ValueError("写入锁不能使用链接")
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    locked = False
    try:
        if os.fstat(descriptor).st_size == 0:
            os.write(descriptor, b"0")
        os.lseek(descriptor, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise ValueError("另一个窗口正在更新记录，请稍后重试") from error
        locked = True
        yield
    finally:
        if locked:
            os.lseek(descriptor, 0, os.SEEK_SET)
            if os.name == "nt":
                import msvcrt

                msvcrt.locking(descriptor, msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(descriptor, fcntl.LOCK_UN)
        os.close(descriptor)


def _write_entries(path, entries, expected):
    if len(entries) > MAX_ENTRIES:
        raise ValueError("正式参考库数量已达上限")
    value = {"version": 1, "entries": entries}
    if len(json.dumps(value, ensure_ascii=False, indent=2).encode()) + 1 > MAX_BYTES:
        raise ValueError("正式参考库过大")
    path = sample_file(path.parent, path.name)
    lock_path = sample_file(path.parent, path.name + ".lock")
    with writer_lock(lock_path):
        if _read_entries(path) != expected:
            raise ValueError("正式参考库已变化，请刷新后重新确认")
        atomic_json(path, value)


def confirm_promotion(root: Path, approved: list[dict]) -> str:
    """Recheck every approved appearance and persist only fingerprint data, never screenshots."""
    global _runtime_cache
    if not approved or len(approved) > MAX_BATCH:
        raise ValueError("请选择要正式补库的参考")
    with _LOCK:
        original = _read_entries(formal_path())
        assessments = assess_references(root)
        additions = []
        for selection in approved:
            current = assessments.get(selection["id"])
            if (
                current is None
                or current["card_id"] != selection["card_id"]
                or current["fingerprint"] != selection["fingerprint"]
            ):
                raise ValueError("样本已变化，请刷新后重新核对")
            if current["covered"]:
                continue
            if current["status"] != "建议纳入":
                raise ValueError(f"{CARD_NAMES_ZH[current['card_id']]}：{current['reason']}")
            fingerprint = current["fingerprint"]
            entry = {
                "id": _signature(current["card_id"], fingerprint),
                "card_id": current["card_id"],
                "fingerprint": fingerprint,
            }
            if not any(
                item["card_id"] == entry["card_id"]
                and int(np.abs(np.array(item["fingerprint"]) - np.array(fingerprint)).sum()) <= SIMILAR_DISTANCE
                for item in additions
            ):
                additions.append(entry)
        if not additions:
            return "所选参考已纳入内置库，无需重复补库。"
        if source_checkout():
            try:
                _write_entries(formal_path(), original + additions, original)
            except PermissionError:
                pass
            else:
                _runtime_cache = (None, 0.0, [])
                return f"已正式纳入 {len(additions)} 份参考；清空本地样本不会删除内置参考。项目文件已更新，可提交并随程序发布。"
        root.mkdir(parents=True, exist_ok=True)
        destination = sample_file(root, "formal-library-export.json")
        previous = _read_entries(destination)
        merged = {entry["id"]: entry for entry in previous + additions}
        _write_entries(destination, list(merged.values()), previous)
        return f"已导出 {len(additions)} 份参考到 {destination}；需合入项目内置库并发布，当前仍为本地试用。"
