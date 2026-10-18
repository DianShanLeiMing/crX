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
    extras: dict[UIField, ComboConfig] | None = None


@dataclass
class ComboConfig:
    """Configuration for a combo box element."""

    key: UIField
    label: str
    values: list[str | int]
    default: str | int = ""
    size: tuple[int, int] = (5, 1)
    label_size: tuple[int, int] = (6, 1)
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
        tooltip="每场对战前随机更换指定卡组中的卡牌。",
        extras={
            UIField.DECK_NUMBER_SELECTION: ComboConfig(
                key=UIField.DECK_NUMBER_SELECTION,
                label="卡组位置",
                values=[1, 2, 3, 4, 5],
                default=2,
                label_size=(10, 1),
                tooltip="选择每场对战前需要随机更换的卡组位置（1–5）。",
            )
        },
    ),
    JobConfig(
        UIField.CYCLE_DECKS_USER_TOGGLE,
        "♻️ 轮换卡组",
        default=False,
        tooltip="每场对战之间依次切换不同卡组。",
        extras={
            UIField.MAX_DECK_SELECTION: ComboConfig(
                key=UIField.MAX_DECK_SELECTION,
                label="轮换卡组数量",
                values=[1, 2, 3, 4, 5, 6, 7, 8, 9, 10],
                default=2,
                label_size=(15, 1),
                tooltip="参与轮换的卡组数量（1–10）。",
            )
        },
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
for job in JOBS:
    for key, extra in (job.extras or {}).items():
        DEFAULTS[key.value] = extra.default
DEFAULTS.update(
    {
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
