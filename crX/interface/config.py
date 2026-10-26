"""Job options and defaults."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from crX.interface.enums import UIField

MAX_FIGHT_LIMIT = 10000

THEME_LABELS = {
    "cosmo": "宇宙",
    "flatly": "扁平浅色",
    "litera": "清爽",
    "minty": "薄荷",
    "lumen": "明亮",
    "sandstone": "砂岩",
    "yeti": "雪地",
    "pulse": "紫韵",
    "united": "暖橙",
    "morph": "柔和",
    "journal": "纸页",
    "darkly": "经典深色",
    "superhero": "英雄深色",
    "solar": "暖色深色",
    "cyborg": "冷色深色",
    "vapor": "霓虹",
    "simplex": "简约",
    "cerculean": "天蓝",
}


@dataclass
class JobConfig:
    """Configuration for a job checkbox element."""

    key: UIField
    title: str
    default: bool = False
    tooltip: str = ""


JOBS = [
    JobConfig(
        UIField.SHOP_DAILY_OFFER_USER_TOGGLE,
        "🛒 商店每日免费领取",
        default=False,
        tooltip="领取商店每日精选中的免费商品。",
    ),
    JobConfig(UIField.CLASSIC_1V1_USER_TOGGLE, "⚔️ 经典 1v1", default=True, tooltip="进行经典 1v1 对战。"),
    JobConfig(UIField.CLASSIC_2V2_USER_TOGGLE, "👥 经典 2v2", default=False, tooltip="进行经典 2v2 对战。"),
    JobConfig(UIField.TROPHY_ROAD_USER_TOGGLE, "🏆 皇室征程", default=False, tooltip="进行皇室征程 1v1 对战。"),
    JobConfig(
        UIField.RANDOM_DECKS_USER_TOGGLE,
        "🎲 随机卡组",
        default=False,
        tooltip="每场对战前从勾选的已有卡组中随机选一套；同时启用轮换时，以轮换为准。",
    ),
    JobConfig(
        UIField.CYCLE_DECKS_USER_TOGGLE,
        "♻️ 轮换卡组",
        default=False,
        tooltip="按勾选先后顺序轮换已有卡组；优先于随机卡组。",
    ),
    JobConfig(
        UIField.RANDOM_PLAYS_USER_TOGGLE,
        "❔ 随机出牌",
        default=False,
        tooltip="对战中随机选择出牌位置，不使用默认出牌策略。",
    ),
    JobConfig(
        UIField.USE_EMOTES,
        "使用表情",
        default=False,
        tooltip="勾选后，对战中出牌成功时有一定概率发送表情；不勾选则不发送。",
    ),
    JobConfig(
        UIField.DISABLE_WIN_TRACK_TOGGLE,
        "📊 胜负统计",
        default=False,
        tooltip="每场对战结束后，在统计页更新胜负记录。",
    ),
]

DEFAULTS: dict[str, Any] = {job.key.value: job.default for job in JOBS}
DEFAULTS.update(
    {
        UIField.SELECTED_DECKS.value: [1, 2],
        UIField.EMULATOR.value: "MuMu",
        UIField.MUMU_PATH.value: "",
        UIField.MUMU_INSTANCE.value: "0",
        UIField.ADB_PATH.value: "",
        UIField.ADB_SERIAL.value: "",
        UIField.THEME_NAME.value: "darkly",
        UIField.RECORD_FIGHTS_TOGGLE.value: False,
        UIField.RECORDING_FOLDER_PATH.value: "",
        UIField.MAX_FIGHTS.value: 0,
        UIField.STOP_AFTER_REWARDS.value: False,
    }
)
USER_CONFIG_KEYS = tuple(DEFAULTS)


def selected_decks_from_settings(values: dict) -> list[int]:
    """Validate ordered deck slots, including the previous JSON number settings."""
    if UIField.SELECTED_DECKS in values:
        decks = values[UIField.SELECTED_DECKS]
    elif values.get(UIField.CYCLE_DECKS_USER_TOGGLE) and "max_deck_selection" in values:
        count = _legacy_deck_number(values["max_deck_selection"], 10)
        decks = list(range(1, count + 1))
    elif "deck_number_selection" in values:
        decks = [_legacy_deck_number(values["deck_number_selection"], 5)]
    else:
        decks = DEFAULTS[UIField.SELECTED_DECKS]
    if (
        not isinstance(decks, list)
        or len(decks) > 10
        or any(type(number) is not int or not 1 <= number <= 10 for number in decks)
        or len(set(decks)) != len(decks)
    ):
        raise ValueError("请选择不重复的卡组编号（1–10）")
    return decks.copy()


def _legacy_deck_number(value, maximum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int | str):
        raise ValueError("旧版卡组选择无效")
    try:
        number = int(value)
    except ValueError as error:
        raise ValueError("旧版卡组选择无效") from error
    if not 1 <= number <= maximum:
        raise ValueError("旧版卡组选择无效")
    return number
