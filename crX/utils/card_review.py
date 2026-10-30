"""Read immutable combat samples and store human review separately."""

from __future__ import annotations

import json
import os
import re
import tempfile
import time
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image

from crX.bot.card_data import CARD_NAMES_ZH
from crX.bot.coords import CARD_FINGERPRINT_SIZE, GAME_SCREEN_SIZE, HAND_CARD_SIZE, LEGACY_SCREEN_SIZE

STATUS_LABELS = {
    "pending": "待确认",
    "confirmed": "未启用",
    "trial": "待启用",
    "included": "已启用",
    "covered": "待启用",
    "conflict": "需复核",
    "ignored": "已忽略",
}
REASON_LABELS = {
    "unknown": "未知卡牌",
    "unknown_card": "未知卡牌",
    "low_margin": "候选接近",
    "no_available_cards_high_elixir": "手牌可用性异常",
    "trial_match": "试用指纹命中",
    "builtin_disagreement": "正式库与本地识别不一致",
}
SAMPLE_NAME = re.compile(r"sample-\d{3,}")
GROUP_HASH_DISTANCE = 8
GROUP_PIXEL_DIFFERENCE = 12
CARD_THUMBNAIL_SIZE = (32, 40)


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
        if self.review.get("work_result") in ("pending", "included", "covered", "ignored"):
            return self.review["work_result"]
        if self.review.get("library_status") in ("trial", "included", "covered", "conflict"):
            return self.review["library_status"]
        status = self.review.get("status")
        if status in ("pending", "confirmed", "ignored"):
            return status
        if self.review.get("confirmed_card_id") in CARD_NAMES_ZH:
            return "included" if self.review.get("included_in_reference_library") is True else "confirmed"
        return "pending"

    @property
    def card_id(self) -> str:
        return self.review.get("confirmed_card_id") or ""

    @property
    def uploaded(self) -> bool:
        identifier = self.review.get("formal_id")
        return (
            self.status == "included" and bool(identifier) and self.review.get("published_reference_id") == identifier
        )

    def file(self, kind: str) -> Path:
        return sample_file(self.directory, self.record["files"][kind])


def ignored_reason(sample: ReviewSample) -> str:
    """Explain omitted candidates without changing human labels or shared references."""
    if sample.review.get("work_result"):
        return sample.review.get("work_reason", "") if sample.status == "ignored" else ""
    if sample.status == "ignored":
        note = sample.review.get("note", "")
        return note.strip() if isinstance(note, str) and note.strip() else "手动忽略，未填写原因"
    if sample.review.get("formal_covered"):
        return "正式库已有相同或近似参考"
    if sample.status == "included" and sample.review.get("library_merged_into"):
        return "已合并到同卡近似参考"
    return ""


def validation_reason(sample: ReviewSample) -> str:
    """Explain activation or human-label conflicts, without offline score claims."""
    if sample.review.get("work_result"):
        return sample.review.get("work_reason", "")
    issue = sample.review.get("library_issue", "")
    if issue == "conflict":
        names = "、".join(CARD_NAMES_ZH.get(card, card) for card in sample.review.get("library_conflicts", []))
        return f"与已确认卡名 {names} 的图像近似，请在全部列表更正卡名"
    if issue == "limit":
        return "达到单卡8份上限，请清理后在全部列表重新确认卡名"
    if issue == "source":
        return "来源已撤销或更正，请检查人工标注"
    omission = ignored_reason(sample)
    if omission:
        target = sample.review.get("library_merged_into")
        return f"{omission}：{target}" if target and not sample.review.get("formal_covered") else omission
    if sample.status == "pending":
        return "确认准确卡名及版本后自动启用"
    if sample.status == "included":
        return "正式库已有参考" if sample.review.get("formal_covered") else "本地试用中"
    return (
        "已停用，请在全部列表重新确认卡名"
        if sample.review.get("library_id")
        else "未加入补库，请在全部列表重新确认卡名"
    )


def sample_progress(sample: ReviewSample) -> tuple[str, str, str]:
    """A match is an observation, never evidence that the prediction was correct."""
    if sample.review.get("work_result"):
        label = {
            "pending": "待确认",
            "included": "已上传" if sample.uploaded else "待上传",
            "covered": "已被覆盖",
            "ignored": "已忽略",
        }[sample.status]
        if sample.status == "pending" and "复核" in sample.review.get("work_reason", ""):
            label = "需复核"
        return label, "历史运行记录仅供观察", "当前使用" if sample.status == "included" else "未使用"
    matches = sample.review.get("library_runtime", {}).get("assists", 0)
    runtime = f"补足识别 {matches} 次" if type(matches) is int and matches > 0 else "尚无补足识别记录"
    enabled = sample.status == "included"
    label = STATUS_LABELS[sample.status]
    omission = " ".join(ignored_reason(sample).split())
    if omission:
        label = f"已忽略，{omission[:30]}{'…' if len(omission) > 30 else ''}"
    return label, runtime, "当前使用" if enabled else "未使用"


def is_library_candidate(sample: ReviewSample) -> bool:
    """Completed/duplicate references stay in history, outside the candidate workflow."""
    return (
        sample.card_id in CARD_NAMES_ZH
        and sample.status not in ("pending", "ignored")
        and not sample.review.get("formal_covered", False)
    )


def library_reference_samples(samples: list[ReviewSample], card_id: str) -> dict[str, list[ReviewSample]]:
    """One row per pending fingerprint, retaining every merged source for actions."""
    references: dict[str, list[ReviewSample]] = {}
    for sample in samples:
        if sample.card_id == card_id and is_library_candidate(sample):
            references.setdefault(sample.review.get("library_id") or sample.key, []).append(sample)
    for sources in references.values():
        sources.sort(key=lambda sample: (bool(sample.review.get("library_merged_into")), sample.key))
    return references


def library_card_summaries(samples: list[ReviewSample]) -> list[ReviewSample]:
    """Aggregate only pending references by card/version, counting each ID once."""
    by_card: dict[str, list[ReviewSample]] = {}
    for sample in samples:
        if is_library_candidate(sample):
            by_card.setdefault(sample.card_id, []).append(sample)
    summaries = []
    for card, candidates in by_card.items():
        representative = min(
            candidates,
            key=lambda sample: (
                sample.status != "included",
                bool(sample.review.get("library_merged_into")),
                sample.key,
            ),
        )
        entries = {sample.review["library_id"]: sample for sample in candidates if sample.review.get("library_id")}
        enabled = sum(sample.status == "included" for sample in entries.values())
        matches = sum(sample.review.get("library_runtime", {}).get("assists", 0) for sample in entries.values())
        recommended = sum(sample.review.get("formal_status") == "建议纳入" for sample in entries.values())
        formal_status = "建议纳入" if recommended else "暂不建议"
        formal_detail = f"待补库参考：建议纳入 {recommended} 份，其余暂不建议。"
        formal_detail += "\n" + "\n".join(
            f"{identifier}：{sample.review.get('formal_status', '暂不建议')}；{sample.review.get('formal_reason', '暂无可用运行记录')}"
            for identifier, sample in entries.items()
        )
        summaries.append(
            replace(
                representative,
                review={
                    **representative.review,
                    "summary_enabled": enabled,
                    "summary_matches": matches,
                    "formal_status": formal_status,
                    "formal_detail": formal_detail,
                },
            )
        )
    return summaries


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
                if "work_result" in review and (
                    review["work_result"] not in ("pending", "included", "covered", "ignored")
                    or not isinstance(review.get("formal_id", ""), str)
                    or len(review.get("formal_id", "")) > 128
                    or type(review.get("owns_reference", False)) is not bool
                    or type(review.get("coverage_policy", 0)) is not int
                    or not isinstance(review.get("work_reason", ""), str)
                    or not isinstance(review.get("published_reference_id", ""), str)
                    or len(review.get("published_reference_id", "")) > 128
                    or not isinstance(review.get("published_commit", ""), str)
                    or len(review.get("published_commit", "")) > 128
                    or type(review.get("images_cleaned", False)) is not bool
                ):
                    raise ValueError("样本处理结果无效")
                samples.append(ReviewSample(directory, name, record, review))
                seen.add(name)
            except (ValueError, KeyError, TypeError, AttributeError):
                errors += 1
    from crX.utils.card_library import read_library

    try:
        entries = read_library(root)
    except (OSError, ValueError, TypeError):
        entries = []
        errors += 1
    library_sources = {(source, entry["card_id"]): entry for entry in entries for source in entry["sources"]}
    confirmed = {(sample.key, sample.card_id) for sample in samples if sample.status not in ("pending", "ignored")}
    representatives = {
        entry["id"]: next((key for key in entry["sources"] if (key, entry["card_id"]) in confirmed), "")
        for entry in entries
    }
    for sample in samples:
        entry = library_sources.get((sample.key, sample.card_id))
        if entry and sample.status not in ("pending", "ignored"):
            sample.review = {
                **sample.review,
                "library_status": {
                    "trial": "trial",
                    "active": "included",
                    "covered": "covered",
                    "disabled": "conflict" if entry.get("disabled_reason") == "conflict" else "confirmed",
                }[entry["status"]],
                "library_id": entry["id"],
                "library_issue": entry.get("disabled_reason", ""),
                "library_conflicts": entry.get("conflicts", []),
                "library_runtime": entry.get("runtime", {}),
                "library_merged_into": (
                    representatives[entry["id"]] if representatives[entry["id"]] != sample.key else ""
                ),
            }
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
    from crX.utils.card_library import sync_library

    for root in {sample.directory.parent for sample in samples}:
        sync_library(root)
    return saved


def _group_features(sample: ReviewSample) -> tuple[int, np.ndarray] | None:
    """Only compare complete single-card crops, including their variant artwork."""
    if type(sample.record.get("slot")) is not int or not 0 <= sample.record["slot"] < 4:
        return None
    if sample.record.get("available") is not True:
        return None
    try:
        with Image.open(sample.file("card")) as image:
            return image_group_features(image)
    except (OSError, ValueError, KeyError, Image.DecompressionBombError):
        return None


def image_group_features(image: Image.Image) -> tuple[int, np.ndarray] | None:
    """Use the same conservative appearance comparison during capture and review."""
    if image.size not in (HAND_CARD_SIZE, CARD_FINGERPRINT_SIZE):
        return None
    pixels = np.asarray(image.convert("RGB").resize((18, 22)), dtype=np.int16)
    gray = np.asarray(image.convert("L").resize((9, 8)))
    bits = gray[:, 1:] > gray[:, :-1]
    return int.from_bytes(np.packbits(bits).tobytes()), pixels


def similar_card_images(first: tuple[int, np.ndarray], second: tuple[int, np.ndarray]) -> bool:
    """Tolerate small animation changes while requiring close full-card colors."""
    return bool(
        (first[0] ^ second[0]).bit_count() <= GROUP_HASH_DISTANCE
        and np.abs(first[1] - second[1]).mean() <= GROUP_PIXEL_DIFFERENCE
    )


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
            for reference in representatives:
                if reference.card_id and sample.card_id and reference.card_id != sample.card_id:
                    continue
                if similar_card_images(features, (reference.hash_value, reference.pixels)):
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


def scan_grouped_samples(root: Path, *, refresh_library=False) -> tuple[list[ReviewSample], int, dict[str, list[str]]]:
    from crX.utils.card_library import sync_library

    try:
        if refresh_library:
            sync_library(root)
    except (OSError, ValueError, TypeError):
        # Keep manual review usable when a local library/report needs repair.
        samples, errors = scan_samples(root)
        return samples, errors + 1, group_similar_samples(samples)
    samples, errors = scan_samples(root)
    from crX.utils.formal_library import annotate_references

    try:
        annotate_references(root, samples)
    except (OSError, ValueError, TypeError) as error:
        errors += 1
        for sample in samples:
            sample.review.update(formal_status="暂不建议", formal_reason=f"正式补库检查失败：{error}")
    return samples, errors, group_similar_samples(samples)


def load_card_thumbnail(sample: ReviewSample, *, size=CARD_THUMBNAIL_SIZE) -> Image.Image | None:
    """Read only the bounded card crop; callers create Tk images on the UI thread."""
    try:
        path = sample.file("card")
        if path.stat().st_size > 1024 * 1024:
            return None
        with Image.open(path) as image:
            if image.width > 1000 or image.height > 1000:
                return None
            preview = image.convert("RGB")
            preview.thumbnail(size, Image.Resampling.LANCZOS)
            return preview
    except (OSError, ValueError, KeyError, Image.DecompressionBombError):
        return None


def load_previews(sample: ReviewSample) -> dict:
    images = {}
    for kind, bounds in (("card", (120, 148)), ("hand", (470, 72)), ("frame", GAME_SCREEN_SIZE)):
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
    if frame is None or frame.shape not in ((*GAME_SCREEN_SIZE[::-1], 3), (*LEGACY_SCREEN_SIZE[::-1], 3)):
        raise ValueError("原始截图缺失或尺寸无效，需要 360×640 画面或 419×633 历史样本")
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
