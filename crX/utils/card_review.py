"""Read immutable combat samples and store human review separately."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.coords import HAND_CARD_SIZE

STATUS_LABELS = {"pending": "待确认", "confirmed": "已确认 · 待补库", "included": "已补库", "ignored": "已忽略"}
REASON_LABELS = {
    "unknown": "未知卡牌",
    "unknown_card": "未知卡牌",
    "low_margin": "候选接近",
    "no_available_cards_high_elixir": "手牌可用性异常",
}
SAMPLE_NAME = re.compile(r"sample-\d{3,}")


def sample_file(directory: Path, filename: str) -> Path:
    """Only allow direct files within the selected session, including symlinks."""
    path = directory / filename
    if not filename or Path(filename).name != filename or path.resolve().parent != directory.resolve():
        raise ValueError("样本文件路径无效")
    return path


def read_json(path: Path) -> dict:
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path.name} 格式无效")
    return value


def atomic_json(path: Path, value: dict) -> None:
    descriptor, temporary = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2)
            stream.write("\n")
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


@dataclass
class ReviewSample:
    directory: Path
    name: str
    record: dict
    review: dict

    @property
    def key(self) -> str:
        return f"{self.directory.name}/{self.name}"

    @property
    def status(self) -> str:
        status = self.review.get("status")
        if status in ("pending", "confirmed", "ignored"):
            return status
        if self.review.get("confirmed_card_id") in CARD_NAMES_ZH:
            return "included" if self.review.get("included_in_reference_library") is True else "confirmed"
        return "pending"

    @property
    def card_id(self) -> str:
        return self.review.get("confirmed_card_id") or ""

    def file(self, kind: str) -> Path:
        return sample_file(self.directory, self.record["files"][kind])


def scan_samples(root: Path) -> tuple[list[ReviewSample], int]:
    """A live JSONL can have an incomplete last line; keep the completed records."""
    samples = []
    errors = 0
    if not root.exists():
        return samples, errors
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir() or directory.is_symlink():
            continue
        source = directory / "samples.jsonl"
        if not source.is_file() or source.is_symlink():
            continue
        reviews = {}
        for filename in ("review.json", "annotations.json"):
            try:
                reviews.update(read_json(sample_file(directory, filename)))
            except (OSError, ValueError, UnicodeError):
                errors += 1
        try:
            lines = source.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError):
            errors += 1
            continue
        seen = set()
        for line in lines:
            try:
                record = json.loads(line)
                if not isinstance(record, dict):
                    raise ValueError("样本格式无效")
                for field in ("reason", "mode"):
                    if not isinstance(record.get(field, ""), str):
                        raise ValueError("样本信息无效")
                candidates = record.get("candidates", [])
                if not isinstance(candidates, list) or any(
                    not isinstance(candidate, dict) or not isinstance(candidate.get("card_id"), str)
                    for candidate in candidates
                ):
                    raise ValueError("候选格式无效")
                filename = record["files"]["card"]
                name = filename.removesuffix("-card.png")
                if not SAMPLE_NAME.fullmatch(name) or filename != name + "-card.png":
                    raise ValueError("样本编号无效")
                for kind in ("card", "hand", "frame"):
                    sample_file(directory, record["files"][kind])
                if name in seen:
                    continue
                review = reviews.get(name, {})
                if not isinstance(review, dict):
                    raise ValueError("标注格式无效")
                if not isinstance(review.get("confirmed_card_id") or "", str):
                    raise ValueError("标注卡名无效")
                samples.append(ReviewSample(directory, name, record, review))
                seen.add(name)
            except (ValueError, KeyError, TypeError, AttributeError):
                errors += 1
    return samples, errors


def save_review(sample: ReviewSample, status: str, card_id: str = "", note: str = "") -> None:
    save_reviews([sample], status, card_id, note)


def save_reviews(samples: list[ReviewSample], status: str, card_id: str = "", note: str = "") -> int:
    """Preflight the selection, then atomically replace each session's annotations."""
    if status not in ("pending", "confirmed", "ignored"):
        raise ValueError("标注状态无效")
    if status == "confirmed" and card_id not in CARD_NAMES_ZH:
        raise ValueError("请从卡牌名录中选择准确的卡名（区分觉醒／精英版本）")
    annotation = {
        "status": status,
        "confirmed_card_id": card_id if status == "confirmed" else None,
        "note": note.strip()[:2000],
        "reviewed_at": time.time(),
    }
    sessions = {}
    for sample in samples:
        if not SAMPLE_NAME.fullmatch(sample.name):
            raise ValueError("样本编号无效")
        path = sample_file(sample.directory, "annotations.json")
        if path not in sessions:
            sessions[path] = (read_json(path), set())
        reviews, names = sessions[path]
        reviews[sample.name] = annotation
        names.add(sample.name)
    total = sum(len(names) for _, names in sessions.values())
    saved = 0
    for path, (reviews, names) in sessions.items():
        try:
            atomic_json(path, reviews)
        except (OSError, ValueError) as error:
            raise OSError(
                f"已保存 {saved}/{total} 张；{path.parent.name} 保存失败：{error}。请刷新后重试未保存条目。"
            ) from error
        saved += len(names)
    return saved


def _group_features(sample: ReviewSample) -> tuple[int, np.ndarray] | None:
    """Only compare complete single-card crops, including their variant artwork."""
    if type(sample.record.get("slot")) is not int or not 0 <= sample.record["slot"] < 4:
        return None
    if sample.record.get("available") is not True:
        return None
    try:
        with Image.open(sample.file("card")) as image:
            if image.size != HAND_CARD_SIZE:
                return None
            pixels = np.asarray(image.convert("RGB").resize((18, 22)), dtype=np.int16)
            gray = np.asarray(image.convert("L").resize((9, 8)))
        bits = gray[:, 1:] > gray[:, :-1]
        hash_value = int.from_bytes(np.packbits(bits).tobytes())
        return hash_value, pixels
    except (OSError, ValueError, KeyError, Image.DecompressionBombError):
        return None


@dataclass
class _GroupReference:
    key: str
    hash_value: int
    pixels: np.ndarray
    card_id: str


def group_similar_samples(samples: list[ReviewSample]) -> dict[str, list[str]]:
    """Suggest conservative visual groups; never infer a label or chain matches."""
    groups: dict[str, list[str]] = {}
    representatives: list[_GroupReference] = []
    for sample in samples:
        features = _group_features(sample)
        matched = None
        if features is not None:
            hash_value, pixels = features
            for reference in representatives:
                if reference.card_id and sample.card_id and reference.card_id != sample.card_id:
                    continue
                if (hash_value ^ reference.hash_value).bit_count() <= 6 and np.abs(
                    pixels - reference.pixels
                ).mean() <= 12:
                    matched = reference
                    break
        if matched is None:
            groups[sample.key] = [sample.key]
            if features is not None:
                representatives.append(_GroupReference(sample.key, *features, sample.card_id))
        else:
            groups[matched.key].append(sample.key)
            if sample.card_id:
                matched.card_id = sample.card_id
    return groups


def scan_grouped_samples(root: Path) -> tuple[list[ReviewSample], int, dict[str, list[str]]]:
    samples, errors = scan_samples(root)
    return samples, errors, group_similar_samples(samples)


def load_previews(sample: ReviewSample) -> dict:
    images = {}
    for kind, bounds in (("card", (120, 148)), ("hand", (470, 72)), ("frame", (419, 633))):
        try:
            with Image.open(sample.file(kind)) as image:
                if image.width > 1000 or image.height > 1000:
                    raise ValueError("图片尺寸异常")
                preview = image.convert("RGB")
                preview.thumbnail(bounds)
                if kind == "card":
                    preview = preview.resize((preview.width * 2, preview.height * 2))
                images[kind] = preview
        except (OSError, ValueError, KeyError, Image.DecompressionBombError):
            images[kind] = None
    return images


def export_candidate(sample: ReviewSample) -> Path:
    """Export a draft only; never mutate or hot-load the recognizer's references."""
    import cv2

    from crX.bot.card_detection import COLORS_KEYS, extract_card_fingerprint

    if sample.status not in ("confirmed", "included") or sample.card_id not in CARD_NAMES_ZH:
        raise ValueError("请先确认卡名并保存")
    slot = sample.record.get("slot")
    if type(slot) is not int or not 0 <= slot < 4 or sample.record.get("available") is not True:
        raise ValueError("只有确认可用的单张手牌样本才能导出指纹；此样本可保留用于诊断")
    frame = cv2.imread(str(sample.file("frame")))
    if frame is None or frame.shape != (633, 419, 3):
        raise ValueError("原始截图缺失或尺寸无效，需要 419×633 的战斗截图")
    fingerprint = extract_card_fingerprint(frame, slot)
    path = sample_file(sample.directory, f"{sample.name}-candidate.json")
    atomic_json(
        path,
        {
            "card_id": sample.card_id,
            "card_name": CARD_NAMES_ZH[sample.card_id],
            "fingerprint": [[corner[color] for color in COLORS_KEYS] for corner in fingerprint],
            "source": sample.key,
            "color_order": "BGR",
            "validated": False,
            "note": "人工确认后的补库候选，仍需不同画面验证；未写入运行时识别库。",
        },
    )
    return path
