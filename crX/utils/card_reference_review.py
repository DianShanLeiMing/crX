"""Explicit per-reference edits reuse the existing confirmed-label workflow."""

from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils.card_library import clear_filtered_samples, update_library
from crX.utils.card_review import library_reference_samples, save_reviews, scan_grouped_samples


def _current_sources(root, identifier, keys, card_id):
    samples, errors, _ = scan_grouped_samples(root, refresh_library=True)
    if errors:
        raise ValueError("样本记录不完整，请先刷新检查")
    sources = library_reference_samples(samples, card_id).get(identifier, [])
    if not sources or {sample.key for sample in sources} != set(keys):
        raise ValueError("参考样本已变化，请刷新后重新选择")
    return sources


def rename_reference(root, identifier, keys, card_id, new_card_id):
    if new_card_id not in CARD_NAMES_ZH:
        raise ValueError("请从卡牌名录选择准确卡名，并区分普通／觉醒／精英版本")
    sources = _current_sources(root, identifier, keys, card_id)
    # Keep individual notes when merged sources are corrected together.
    by_note = {}
    for sample in sources:
        by_note.setdefault(sample.review.get("note", ""), []).append(sample)
    saved = 0
    try:
        for note, group in by_note.items():
            saved += save_reviews(group, "confirmed", new_card_id, note)
        result = update_library(root, sources, "enable")
    except (OSError, ValueError, TypeError) as error:
        raise ValueError(f"已更正 {saved} 张来源的卡名；请刷新检查补库结果：{error}") from error
    return f"已更正 {saved} 张来源的卡名；{result}。"


def delete_reference(root, identifier, keys, card_id):
    sources = _current_sources(root, identifier, keys, card_id)
    return clear_filtered_samples(root, {sample.key for sample in sources})
