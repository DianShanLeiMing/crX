"""Stable reference labels and one manually selected first choice per card."""

from __future__ import annotations

import json
import re
import threading
import time
from contextlib import nullcontext

from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json

_LOCK = threading.RLock()
_runtime_cache = (None, 0.0, {})
IDENTIFIER = re.compile(r"[0-9a-f]{16}\Z")
MAX_BYTES = 2 * 1024 * 1024


def preferences_path():
    return formal.formal_path().with_name("card_reference_preferences.json")


def read_document(path):
    if path.is_symlink() or path.is_junction():
        raise ValueError("参考配置不能使用链接")
    if not path.exists():
        return {"version": 1, "cards": {}}
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("参考配置过大")
    document = json.loads(payload)
    cards = document.get("cards") if isinstance(document, dict) else None
    if (
        not isinstance(document, dict)
        or set(document) != {"version", "cards"}
        or type(document.get("version")) is not int
        or document.get("version") != 1
        or not isinstance(cards, dict)
        or len(cards) > formal.MAX_ENTRIES
    ):
        raise ValueError("参考配置格式无效")
    total = 0
    for card, value in cards.items():
        if (
            card not in formal.CARD_NAMES_ZH
            or not isinstance(value, dict)
            or set(value) not in ({"preferred", "numbers"}, {"preferred", "numbers", "order"})
        ):
            raise ValueError("参考配置卡名无效")
        numbers = value.get("numbers")
        if (
            not isinstance(numbers, dict)
            or any(
                not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or type(n) is not int or not 0 <= n <= 100000
                for key, n in numbers.items()
            )
            or len(set(numbers.values())) != len(numbers)
        ):
            raise ValueError("参考编号配置无效")
        preferred = value.get("preferred")
        if preferred is not None and (preferred not in numbers or numbers[preferred] != 0):
            raise ValueError("首选参考配置无效")
        if any(n == 0 and key != preferred for key, n in numbers.items()):
            raise ValueError("首选参考只能有一份")
        if "order" in value:
            order = value["order"]
            if (
                not isinstance(order, list)
                or len(order) > len(numbers)
                or any(not isinstance(key, str) or key not in numbers for key in order)
                or len(set(order)) != len(order)
                or preferred in order
                and order[0] != preferred
            ):
                raise ValueError("参考排序配置无效")
        total += len(numbers)
    if total > formal.MAX_ENTRIES * 2:
        raise ValueError("参考编号配置过多")
    return document


def runtime_preferences():
    global _runtime_cache
    path, now = preferences_path(), time.monotonic()
    if path != _runtime_cache[0] or now - _runtime_cache[1] >= 1:
        try:
            cards = read_document(path)["cards"]
        except (OSError, ValueError, TypeError):
            cards = {}
        _runtime_cache = (path, now, cards)
    return _runtime_cache[2]


def card_preferences(references, saved=None):
    """Retain retired numbers so deleting a reference never renumbers another."""
    saved = saved or {}
    numbers = dict(saved.get("numbers", {}))
    known = set(numbers)
    preferred = saved.get("preferred")
    if not saved:
        preferred = next((ref.identifier for ref in references if ref.builtin), None)
    next_number = max(numbers.values(), default=0) + 1
    for ref in references:
        if ref.identifier not in numbers:
            numbers[ref.identifier] = 0 if ref.identifier == preferred else next_number
            if ref.identifier != preferred:
                next_number += 1
    active = {ref.identifier for ref in references}
    if "order" in saved:
        order = [key for key in saved["order"] if key in active]
    else:
        # Freeze the previous automatic order once; newly seen references always
        # follow the known ones, even when shared statistics already contain hits.
        existing = sorted(
            (ref for ref in references if ref.identifier in known),
            key=lambda ref: (-ref.hits, numbers[ref.identifier]),
        )
        order = [ref.identifier for ref in existing]
    order.extend(ref.identifier for ref in references if ref.identifier not in order)
    if preferred in order:
        order.remove(preferred)
        order.insert(0, preferred)
    return {"preferred": preferred, "numbers": numbers, "order": order}


def sort_references(references, value):
    ranks = {key: index for index, key in enumerate(value.get("order", []))}
    references.sort(
        key=lambda ref: (
            not ref.preferred,
            ranks.get(ref.identifier, len(ranks)),
            ref.number,
        )
    )


def register_references(cards):
    """Persist labels only; first-choice defaults preserve the original built-in order."""
    path = preferences_path()
    lock = formal.writer_lock(path.with_suffix(".json.lock")) if formal.source_checkout() else nullcontext()
    with _LOCK, lock:
        document = read_document(path)
        updated = {**document["cards"]}
        for card, references in cards.items():
            if references:
                updated[card] = card_preferences(references, updated.get(card))
        result = {"version": 1, "cards": updated}
        if result != document and formal.source_checkout():
            atomic_json(path, result)
        return result


def set_preferred(card, identifier, references):
    if not formal.source_checkout():
        raise ValueError("修改首选需要可写的源码项目")
    if identifier not in {ref.identifier for ref in references}:
        raise ValueError("参考已经变化，请刷新后重试")
    path = preferences_path()
    with _LOCK, formal.writer_lock(path.with_suffix(".json.lock")):
        document = read_document(path)
        value = card_preferences(references, document["cards"].get(card))
        previous = value["preferred"]
        if previous == identifier:
            return "当前参考已经是首选"
        number = value["numbers"][identifier]
        if previous:
            value["numbers"][previous] = number
        value["numbers"][identifier] = 0
        value["preferred"] = identifier
        if "order" in value:
            order = value["order"]
            index = order.index(identifier)
            if previous in order:
                previous_index = order.index(previous)
                order[previous_index], order[index] = identifier, previous
            else:
                order.pop(index)
                order.insert(0, identifier)
        document["cards"][card] = value
        atomic_json(path, document)
    return "已设置首选；命中数及来源备注保留，上传该卡牌后其他电脑生效"


def set_reference_order(card, ordered, expected, references):
    """Save an exact permutation, never silently replace a stale card selection."""
    if not formal.source_checkout():
        raise ValueError("修改参考顺序需要可写的源码项目")
    if card not in formal.CARD_NAMES_ZH or not references:
        raise ValueError("请选择有参考样本的有效卡牌")
    active = {ref.identifier for ref in references}
    if len(ordered) != len(active) or len(set(ordered)) != len(ordered) or set(ordered) != active:
        raise ValueError("参考已经变化，请刷新后重新排序")
    path = preferences_path()
    with _LOCK, formal.writer_lock(path.with_suffix(".json.lock")):
        document = read_document(path)
        value = card_preferences(references, document["cards"].get(card))
        for ref in references:
            ref.preferred = ref.identifier == value["preferred"]
            ref.number = value["numbers"][ref.identifier]
        sort_references(references, value)
        if tuple(ref.identifier for ref in references) != tuple(expected):
            raise ValueError("参考顺序已经变化，请刷新后重新排序")
        if value["preferred"] in active and ordered[0] != value["preferred"]:
            raise ValueError("首选固定在第一行，请使用交换首选调整")
        value["order"] = list(ordered)
        document["cards"][card] = value
        atomic_json(path, document)
    return "已保存参考顺序；编号与命中数保留，上传该卡牌后其他电脑生效"
