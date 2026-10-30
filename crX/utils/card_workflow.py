"""One human approval, durable review results and recoverable formal-library edits."""

from __future__ import annotations

import json
import threading
import time
from collections import Counter
from contextlib import contextmanager

import numpy as np

from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils import card_library as local
from crX.utils import formal_library as formal
from crX.utils.card_grouping import group_review_samples
from crX.utils.card_review import (
    SAMPLE_NAME,
    atomic_json,
    load_card_thumbnail,
    sample_file,
    scan_samples,
)

REVIEW_TABS = {
    "待确认": "pending",
    "待上传": "included",
}
COVERAGE_POLICY = 2
MAX_STATE_BYTES = 16 * 1024 * 1024
_LOCK = threading.RLock()


def _read_state(path):
    if path.is_symlink() or path.is_junction() or (path.exists() and path.stat().st_size > MAX_STATE_BYTES):
        raise ValueError("处理记录过大或包含链接")
    if not path.exists():
        return {}
    with path.open("rb") as stream:
        payload = stream.read(MAX_STATE_BYTES + 1)
    if len(payload) > MAX_STATE_BYTES:
        raise ValueError("处理记录过大")
    value = json.loads(payload.decode("utf-8"))
    if not isinstance(value, dict):
        raise ValueError("处理记录格式无效")
    return value


def _session(root, name):
    path = sample_file(root, name)
    if path.is_symlink() or path.is_junction() or not path.is_dir():
        raise ValueError("样本目录包含链接或已经变化")
    return path


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or root.is_junction():
        raise ValueError("样本根目录不能使用链接")
    with _LOCK, formal._LOCK:
        path = sample_file(root, "card-review.lock")
        with formal.writer_lock(path):
            _recover(root)
            yield


def _recover(root):
    """Finish an authorized write interrupted between the library and annotations."""
    path = sample_file(root, "card-review-transaction.json")
    if not path.exists():
        return
    transaction = _read_state(path)
    if transaction.get("version") != 1 or transaction.get("target") != str(formal.formal_path().resolve()):
        raise ValueError("待恢复的入库记录与当前正式库不一致，请人工检查")
    before, after = transaction["before"], transaction["after"]
    plans = transaction["annotations"]
    if not isinstance(plans, dict):
        raise ValueError("入库恢复记录格式无效")
    paths = {}
    for session, plan in plans.items():
        destination = sample_file(_session(root, session), "annotations.json")
        current = _read_state(destination)
        if current not in (plan["before"], plan["after"]):
            raise ValueError("标注已被其他窗口修改，请人工检查未完成的入库记录")
        paths[destination] = plan["after"]
    current = formal._read_entries(formal.formal_path())
    if current != after:
        if current != before:
            raise ValueError("正式库已变化，请人工检查未完成的入库记录")
        formal._write_entries(formal.formal_path(), after, before)
    for destination, reviews in paths.items():
        atomic_json(destination, reviews)
    formal._runtime_cache = (None, 0.0, [])
    path.unlink()


def _commit(root, before, after, updates):
    plans = {}
    for sample, changes in updates:
        directory = _session(root, sample.directory.name)
        if directory.resolve() != sample.directory.resolve() or not SAMPLE_NAME.fullmatch(sample.name):
            raise ValueError("样本来源已变化，请刷新后重试")
        if directory.name not in plans:
            original = _read_state(sample_file(directory, "annotations.json"))
            plans[directory.name] = {"before": original, "after": dict(original)}
        plan = plans[directory.name]
        # Historical review.json remains read-only; copy its annotation on first edit.
        original_review = _read_state(sample_file(directory, "review.json")).get(sample.name, {})
        current = plan["after"].get(sample.name, original_review)
        plan["after"][sample.name] = {**current, **changes}
    if before != after and not formal.source_checkout():
        raise ValueError("当前安装版不能直接更新内置正式库；记录保留待确认，请使用可写的源码项目入库")
    transaction = {
        "version": 1,
        "target": str(formal.formal_path().resolve()),
        "before": before,
        "after": after,
        "annotations": plans,
    }
    if len(json.dumps(transaction, ensure_ascii=False).encode("utf-8")) > MAX_STATE_BYTES:
        raise ValueError("本次处理记录过大，请分批操作")
    path = sample_file(root, "card-review-transaction.json")
    atomic_json(path, transaction)
    try:
        _recover(root)
    except (OSError, ValueError):
        # A failed library write leaves the saved card choice pending and retryable.
        current = formal._read_entries(formal.formal_path())
        if (current != after or before == after) and all(
            _read_state(sample_file(_session(root, name), "annotations.json")) == plan["before"]
            for name, plan in plans.items()
        ):
            path.unlink(missing_ok=True)
        raise


def _result(result, card_id, reason, *, reference="", owned=False, note=None):
    value = {
        "work_result": result,
        "status": "confirmed" if result in ("included", "covered") else result,
        "confirmed_card_id": card_id or None,
        "work_reason": reason,
        "formal_id": reference,
        "owns_reference": owned,
        "coverage_policy": COVERAGE_POLICY,
        "reviewed_at": time.time(),
    }
    if note is not None:
        value["note"] = note.strip()[:2000]
    return value


def _scan(root):
    samples, errors = scan_samples(root)
    if errors:
        raise ValueError("样本记录不完整，请修复后再处理")
    return samples


def _fresh(root, selected):
    if not selected or len(selected) > formal.MAX_BATCH:
        raise ValueError(f"一次请选择 1 至 {formal.MAX_BATCH} 张样本")
    current = {sample.key: sample for sample in _scan(root)}
    result = []
    for previous in selected:
        sample = current.get(previous.key)
        if (
            sample is None
            or sample.record != previous.record
            or any(
                sample.review.get(key) != previous.review.get(key)
                for key in ("reviewed_at", "confirmed_card_id", "work_result", "formal_id")
            )
        ):
            raise ValueError("样本或标注已变化，请刷新后重新选择")
        result.append(sample)
    return result


def _references(entries):
    """Use the same base/variant references and aliases as the shipped recognizer."""
    from crX.bot import card_detection as detection

    result = [
        (detection.CARD_DETECTION_ALIASES.get(name, name), value.tolist(), "")
        for name, value in zip(detection._REFERENCE_NAMES, detection._REFERENCE_MATRIX, strict=True)
    ]
    result.extend((entry["card_id"], entry["fingerprint"], entry["id"]) for entry in entries)
    return result


def _near(fingerprint, references):
    result = []
    for card, value, identifier in references:
        distance = int(np.abs(fingerprint - np.array(value, dtype=np.int64)).sum())
        if distance <= formal.SIMILAR_DISTANCE:
            result.append((card, identifier, distance))
    return result


def _recognized_reference(fingerprint, references, card_id):
    """Reuse a correct formal prediction with the existing diagnostic margin."""
    from crX.bot.card_detection import CARD_MATCH_THRESHOLD, DIAGNOSTIC_MIN_MARGIN

    if not references:
        return None
    distances = np.abs(np.array([value for _, value, _ in references], dtype=np.int64) - fingerprint).sum(axis=(1, 2))
    index = int(distances.argmin())
    card, _, identifier = references[index]
    distance = int(distances[index])
    if card != card_id or distance > CARD_MATCH_THRESHOLD:
        return None
    other_distance = min(
        (int(value) for (other, _, _), value in zip(references, distances, strict=True) if other != card_id),
        default=float("inf"),
    )
    if other_distance - distance < DIAGNOSTIC_MIN_MARGIN:
        return None
    return card, identifier, distance


def _evidence(samples, excluded):
    result = []
    for sample in samples:
        if sample.key in excluded or not sample.card_id or sample.status == "ignored":
            continue
        try:
            result.append((sample.card_id, local.sample_fingerprint(sample).tolist(), sample.key))
        except (OSError, ValueError):
            continue
    return result


def _remember(updates, sample, changes):
    previous = updates.get(sample.key, (sample, {}))[1]
    sample.review = {**sample.review, **changes}
    updates[sample.key] = (sample, {**previous, **changes})


def _coverage_match(value, references, card_id):
    near = _near(value, references)
    if any(card != card_id for card, _, _ in near):
        return None
    return next((item for item in near if item[0] == card_id), None) or _recognized_reference(
        value, references, card_id
    )


def _reassess_history(samples, entries, candidates, updates):
    """Apply the admission rule once, retaining a replacement for every source."""
    remaining = list(entries)
    values = {}
    for entry in entries:
        if entry["id"] not in candidates:
            continue
        sources = [sample for sample in samples if sample.review.get("formal_id") == entry["id"]]
        if not sources:
            continue
        references = _references([item for item in remaining if item["id"] != entry["id"]])
        replacements = []
        for sample in sources:
            if sample.card_id != entry["card_id"]:
                break
            try:
                if sample.key not in values:
                    if load_card_thumbnail(sample) is None:
                        break
                    values[sample.key] = local.sample_fingerprint(sample)
                match = _coverage_match(values[sample.key], references, sample.card_id)
            except (OSError, ValueError):
                break
            if match is None:
                break
            replacements.append(
                (
                    sample,
                    _result("covered", sample.card_id, "正式库已能正确识别当前样本，无需新增参考", reference=match[1]),
                )
            )
        else:
            remaining = [item for item in remaining if item["id"] != entry["id"]]
            for sample, changes in replacements:
                _remember(updates, sample, changes)
    for sample in samples:
        if (
            sample.review.get("coverage_policy") != COVERAGE_POLICY
            or sample.review.get("work_reason") == "历史参考已加入正式库"
        ):
            changes: dict[str, object] = {"coverage_policy": COVERAGE_POLICY}
            if sample.status == "included":
                changes["work_reason"] = "已加入正式库"
            _remember(updates, sample, changes)
    return remaining


def _cleanup_uploaded_images(root, samples, entries):
    """Caller holds the review lock; retain manifests, fingerprints and notes."""
    by_id = {entry["id"]: entry for entry in entries}
    plans = []
    for sample in samples:
        entry = by_id.get(sample.review.get("formal_id"))
        if (
            not sample.uploaded
            or sample.review.get("images_cleaned") is True
            or entry is None
            or entry["card_id"] != sample.card_id
        ):
            continue
        directory = _session(root, sample.directory.name)
        if directory.resolve() != sample.directory.resolve():
            raise ValueError("样本来源已变化，已停止图片清理")
        paths = []
        for kind in ("card", "hand", "frame"):
            name = f"{sample.name}-{kind}.png"
            if not SAMPLE_NAME.fullmatch(sample.name) or sample.record["files"].get(kind) != name:
                raise ValueError("样本图片命名异常，已停止图片清理")
            path = sample_file(directory, name)
            if path.is_symlink() or path.is_junction() or (path.exists() and not path.is_file()):
                raise ValueError("样本图片包含链接或异常文件，已停止清理")
            paths.append(path)
        plans.append((sample, paths))
    selected = {sample.key for sample, _paths in plans}
    protected = {
        sample.file(kind).resolve()
        for sample in samples
        if sample.key not in selected
        for kind in ("card", "hand", "frame")
    }
    if any(path.resolve() in protected for _sample, paths in plans for path in paths):
        raise ValueError("图片仍被其他样本引用，已保留图片，请检查样本记录")
    for start in range(0, len(plans), formal.MAX_BATCH):
        updates = []
        for sample, paths in plans[start : start + formal.MAX_BATCH]:
            directory = _session(root, sample.directory.name)
            for path in paths:
                if (
                    path.is_symlink()
                    or path.is_junction()
                    or path.resolve().parent != directory.resolve()
                    or (path.exists() and not path.is_file())
                ):
                    raise ValueError("样本图片路径已变化，已停止清理")
                path.unlink(missing_ok=True)
            updates.append((sample, {"images_cleaned": True}))
        _commit(root, entries, entries, updates)


def scan_review(root, *, refresh_library=False, reassess_history=True, cleanup_uploaded=None):
    """Migrate old approvals once using the same coverage rule as new approvals."""
    if not root.exists():
        return [], 0, {}
    with _locked(root):
        samples, errors = scan_samples(root)
        if errors:
            return samples, errors, group_review_samples(samples)
        entries = formal._read_entries(formal.formal_path())
        references = _references(entries)
        updates = {}
        protected = {
            sample.review.get("formal_id")
            for sample in samples
            if sample.review.get("work_reason") == "历史参考已加入正式库"
        }
        candidates = {
            sample.review.get("formal_id")
            for sample in samples
            if sample.review.get("coverage_policy") != COVERAGE_POLICY
            and sample.review.get("work_result") == "included"
            and sample.review.get("owns_reference") is True
        } - protected
        for sample in samples:
            if sample.review.get("work_result") in {"pending", "included", "covered", "ignored"}:
                identifier = sample.review.get("formal_id")
                if (
                    sample.review["work_result"] in ("included", "covered")
                    and identifier
                    and identifier not in {entry["id"] for entry in entries}
                ):
                    _remember(updates, sample, _result("pending", sample.card_id, "关联正式参考已移出，请重新核对"))
                elif (
                    reassess_history
                    and sample.review.get("coverage_policy") != COVERAGE_POLICY
                    and sample.status in ("pending", "covered")
                    and sample.card_id in CARD_NAMES_ZH
                ):
                    try:
                        match = _coverage_match(local.sample_fingerprint(sample), references, sample.card_id)
                    except (OSError, ValueError):
                        match = None
                    if match:
                        _remember(
                            updates,
                            sample,
                            _result(
                                "covered",
                                sample.card_id,
                                "正式库已能正确识别当前样本，无需新增参考",
                                reference=match[1],
                            ),
                        )
                continue
            result, reason, identifier, owned = "pending", "请核对卡名及版本后确认并入库", "", False
            if sample.status == "ignored":
                result, reason = "ignored", sample.review.get("note") or "手动忽略，未填写原因"
            elif sample.card_id:
                reason = "历史标注已保留，请确认并入库"
                try:
                    value = local.sample_fingerprint(sample)
                    identifier = formal._signature(sample.card_id, value.tolist())
                    if any(entry["id"] == identifier for entry in entries):
                        result, reason, owned = "included", "已加入正式库", True
                    else:
                        near = _near(value, references)
                        if any(card != sample.card_id for card, _, _ in near):
                            reason = "历史样本存在其他卡名的近似参考，请复核"
                            identifier = ""
                        elif match := next((item for item in near if item[0] == sample.card_id), None):
                            result, reason, identifier = "covered", "正式库已有同卡近似参考", match[1]
                        elif match := _recognized_reference(value, references, sample.card_id):
                            result, reason, identifier = "covered", "正式库已能正确识别当前样本，无需新增参考", match[1]
                        else:
                            identifier = ""
                except (OSError, ValueError) as error:
                    reason, identifier = f"历史标注已保留；样本检查失败：{error}", ""
            _remember(updates, sample, _result(result, sample.card_id, reason, reference=identifier, owned=owned))
        remaining = (
            _reassess_history(samples, entries, candidates, updates)
            if reassess_history and formal.source_checkout()
            else entries
        )
        if reassess_history and not formal.source_checkout():
            # Shipped files are immutable; normalize source records without removal.
            _reassess_history(samples, entries, set(), updates)
        for sample in samples:
            if sample.status == "included" and sample.review.get("work_reason") != "已加入正式库":
                _remember(updates, sample, {"work_reason": "已加入正式库"})
        from crX.utils.card_upload import published_reference_ids

        published = published_reference_ids()
        for sample in samples:
            identifier = sample.review.get("formal_id")
            if sample.status == "included" and not sample.uploaded and identifier in published:
                _remember(
                    updates, sample, {"published_reference_id": identifier, "published_commit": published[identifier]}
                )
        if updates:
            backup = sample_file(root, "formal-before-coverage.json")
            if remaining != entries and not backup.exists():
                atomic_json(backup, {"version": 1, "entries": entries})
            _commit(root, entries, remaining, list(updates.values()))
        if cleanup_uploaded is None:
            cleanup_uploaded = reassess_history
        if cleanup_uploaded:
            _cleanup_uploaded_images(root, _scan(root), remaining)
        if refresh_library:
            local.sync_library(root)
        samples, errors = scan_samples(root)
        return samples, errors, group_review_samples(samples)


def _plan(root, samples, card_id, entries, *, excluded=()):
    references = _references(entries)
    evidence = _evidence(_scan(root), {sample.key for sample in samples} | set(excluded))
    additions, updates = [], []
    counts = Counter()
    for sample in samples:
        try:
            if load_card_thumbnail(sample, size=(64, 80)) is None:
                raise ValueError("卡牌裁图缺失或损坏")
            value = local.sample_fingerprint(sample)
            near = _near(value, references + evidence)
            conflicts = sorted({card for card, _, _ in near if card != card_id})
            if conflicts:
                names = "、".join(CARD_NAMES_ZH.get(card, card) for card in conflicts)
                changes = _result("pending", card_id, f"需复核：与 {names} 的参考指纹过近")
                counts["conflict"] += 1
            elif match := next((item for item in _near(value, references) if item[0] == card_id), None):
                changes = _result("covered", card_id, "正式库已有同卡近似参考", reference=match[1])
                counts["covered"] += 1
            elif match := _recognized_reference(value, references, card_id):
                changes = _result("covered", card_id, "正式库已能正确识别当前样本，无需新增参考", reference=match[1])
                counts["covered"] += 1
            else:
                identifier = formal._signature(card_id, value.tolist())
                entry = {"id": identifier, "card_id": card_id, "fingerprint": value.tolist()}
                additions.append(entry)
                references.append((card_id, value.tolist(), identifier))
                changes = _result("included", card_id, "已加入正式库", reference=identifier, owned=True)
                counts["included"] += 1
        except (OSError, ValueError) as error:
            changes = _result("pending", card_id, f"样本检查失败：{error}")
            counts["failed"] += 1
        updates.append((sample, changes))
    return additions, updates, counts


def confirm_samples(root, selected, card_id, note=""):
    if card_id not in CARD_NAMES_ZH:
        raise ValueError("请选择准确卡名，并区分普通／觉醒／精英版本")
    with _locked(root):
        samples = _fresh(root, selected)
        if any(sample.status != "pending" for sample in samples):
            raise ValueError("请先将样本恢复为待确认")
        original = formal._read_entries(formal.formal_path())
        # Retain the human choice even if the formal destination is not writable.
        _commit(
            root,
            original,
            original,
            [(sample, _result("pending", card_id, "等待入库", note=note)) for sample in samples],
        )
        additions, updates, counts = _plan(root, samples, card_id, original)
        try:
            _commit(root, original, original + additions, updates)
        except (OSError, ValueError) as error:
            if (
                not sample_file(root, "card-review-transaction.json").exists()
                and formal._read_entries(formal.formal_path()) == original
            ):
                _commit(
                    root,
                    original,
                    original,
                    [(sample, _result("pending", card_id, f"入库失败：{error}")) for sample in samples],
                )
            raise
        local.sync_library(root)
        labels = {"included": "已入库", "covered": "已被覆盖", "conflict": "需复核", "failed": "检查失败"}
        return "；".join(f"{labels[key]} {counts[key]} 张" for key in labels if counts[key])


def change_result(root, selected, result, note=""):
    if result not in ("pending", "ignored"):
        raise ValueError("处理状态无效")
    with _locked(root):
        samples = _fresh(root, selected)
        if any(sample.status == "included" for sample in samples):
            raise ValueError("已入库参考请使用更正或移出操作")
        entries = formal._read_entries(formal.formal_path())
        reason = (note.strip()[:2000] or "手动忽略，未填写原因") if result == "ignored" else "已恢复待确认"
        updates = [
            (sample, _result(result, sample.card_id, reason, note=note if result == "ignored" else None))
            for sample in samples
        ]
        _commit(root, entries, entries, updates)
        local.sync_library(root)
        return f"已{'忽略' if result == 'ignored' else '恢复待确认'} {len(samples)} 张样本"


def edit_formal_reference(root, selected, *, new_card_id=None):
    """Edit only an owned supplemental reference, updating every linked source."""
    if new_card_id is not None and new_card_id not in CARD_NAMES_ZH:
        raise ValueError("请选择准确卡名及版本")
    with _locked(root):
        chosen = _fresh(root, selected)
        identifiers = {sample.review.get("formal_id") for sample in chosen}
        if len(identifiers) != 1 or not all(
            sample.status == "included" and sample.review.get("owns_reference") is True for sample in chosen
        ):
            raise ValueError("请仅选择本流程入库的同一份正式参考")
        identifier = identifiers.pop()
        original = formal._read_entries(formal.formal_path())
        if not any(entry["id"] == identifier for entry in original):
            raise ValueError("正式参考已变化，请刷新")
        sources = [sample for sample in _scan(root) if sample.review.get("formal_id") == identifier]
        remaining = [entry for entry in original if entry["id"] != identifier]
        if new_card_id is None:
            updates = [(sample, _result("pending", sample.card_id, "正式参考已移出，请重新核对")) for sample in sources]
            _commit(root, original, remaining, updates)
            message = f"已移出 1 份正式参考，{len(sources)} 张关联样本恢复待确认"
        else:
            additions, updates, counts = _plan(root, sources, new_card_id, remaining)
            if counts["failed"] or counts["conflict"]:
                raise ValueError(
                    next(change["work_reason"] for _, change in updates if change["work_result"] == "pending")
                )
            _commit(root, original, remaining + additions, updates)
            message = f"已更正 1 份正式参考及 {len(sources)} 张关联来源的卡名"
        local.sync_library(root)
        return message


def delete_samples(root, selected):
    with _locked(root):
        samples = _fresh(root, selected)
        return local.clear_filtered_samples(root, {sample.key for sample in samples})


def reference_details(root, sample):
    """Provide a real source crop when available; never invent an image from a hash."""
    entries = formal._read_entries(formal.formal_path())
    value = local.sample_fingerprint(sample)
    references = _references(entries)
    distances = [int(np.abs(value - np.array(fingerprint)).sum()) for _, fingerprint, _ in references]
    if not distances:
        return {"text": "正式库没有可对照的参考", "image": None}
    target = sample.review.get("formal_id")
    all_samples = _scan(root)
    conflict_index = None
    if sample.status == "pending" and sample.card_id and "复核" in sample.review.get("work_reason", ""):
        formal_conflicts = [
            index
            for index, (card, _, _) in enumerate(references)
            if card != sample.card_id and distances[index] <= formal.SIMILAR_DISTANCE
        ]
        for source in all_samples:
            if (
                source.key == sample.key
                or not source.card_id
                or source.card_id == sample.card_id
                or source.status == "ignored"
            ):
                continue
            try:
                distance = int(np.abs(value - local.sample_fingerprint(source)).sum())
            except (OSError, ValueError):
                continue
            if distance <= formal.SIMILAR_DISTANCE and not formal_conflicts:
                return {
                    "text": f"冲突来源：{CARD_NAMES_ZH[source.card_id]}\n指纹距离 {distance}\n样本：{source.key}",
                    "image": load_card_thumbnail(source, size=(108, 132)),
                }
        if formal_conflicts:
            conflict_index = min(formal_conflicts, key=lambda index: distances[index])
    index = (
        conflict_index
        if conflict_index is not None
        else next(
            (index for index, (_, _, identifier) in enumerate(references) if target and identifier == target), None
        )
    )
    if index is None:
        index = int(np.argmin(distances))
    card, _, identifier = references[index]
    sources = [
        source
        for source in all_samples
        if source.key != sample.key and identifier and source.review.get("formal_id") == identifier
    ]
    image = next(
        (preview for source in sources if (preview := load_card_thumbnail(source, size=(108, 132))) is not None), None
    )
    if image is None and sample.review.get("owns_reference") and target == identifier:
        image = load_card_thumbnail(sample, size=(108, 132))
    text = (
        f"正式参考：{CARD_NAMES_ZH.get(card, card)}\n指纹距离 {distances[index]}（近似阈值 {formal.SIMILAR_DISTANCE}）"
    )
    if image is None:
        text += "\n正式库只保存颜色指纹，暂无关联原图"
    return {"text": text, "image": image}
