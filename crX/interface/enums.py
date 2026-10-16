"""Persisted settings and displayed statistics for crX."""

from enum import StrEnum


class UIField(StrEnum):
    SHOP_DAILY_OFFER_USER_TOGGLE = "shop_daily_offer_user_toggle"
    CLASSIC_1V1_USER_TOGGLE = "classic_1v1_user_toggle"
    CLASSIC_2V2_USER_TOGGLE = "classic_2v2_user_toggle"
    TROPHY_ROAD_USER_TOGGLE = "trophy_road_user_toggle"
    RANDOM_DECKS_USER_TOGGLE = "random_decks_user_toggle"
    DECK_NUMBER_SELECTION = "deck_number_selection"
    CYCLE_DECKS_USER_TOGGLE = "cycle_decks_user_toggle"
    MAX_DECK_SELECTION = "max_deck_selection"
    MAX_FIGHTS = "max_fights"
    STOP_AFTER_REWARDS = "stop_after_rewards"
    RANDOM_PLAYS_USER_TOGGLE = "random_plays_user_toggle"
    DISABLE_WIN_TRACK_TOGGLE = "disable_win_track_toggle"
    RECORD_FIGHTS_TOGGLE = "record_fights_toggle"
    RECORDING_FOLDER_PATH = "recording_folder_path"
    THEME_NAME = "theme_name"
    EMULATOR = "emulator"
    ADB_PATH = "adb_path"
    ADB_SERIAL = "adb_serial"
    MUMU_PATH = "mumu_path"
    MUMU_INSTANCE = "mumu_instance"


BATTLE_MODE_LABELS = {
    "Classic 1v1": "经典 1v1",
    "Classic 2v2": "经典 2v2",
    "Trophy Road": "皇室征程",
}

BATTLE_STAT_LABELS = {
    "completed_fights": "已完成局数",
    "wins": "胜场",
    "losses": "负场",
    "winrate": "胜率",
    "current_win_streak": "当前连胜",
    "best_win_streak": "最高连胜",
    "classic_1v1_fights": "经典 1v1",
    "classic_2v2_fights": "经典 2v2",
    "trophy_road_1v1_fights": "皇室征程",
    "cards_played": "出牌次数",
    "card_randomizations": "随机卡组次数",
    "card_cycles": "卡组轮换次数",
    "shop_offer_collections": "商店免费领取次数",
    "restarts_after_failure": "故障恢复次数",
    "time_since_start": "运行时长",
}

START_JOB_GROUPS = (
    (
        UIField.CLASSIC_1V1_USER_TOGGLE,
        UIField.CLASSIC_2V2_USER_TOGGLE,
        UIField.TROPHY_ROAD_USER_TOGGLE,
        UIField.SHOP_DAILY_OFFER_USER_TOGGLE,
    ),
)


def has_start_ready_job(values: dict[str, object]) -> bool:
    return any(bool(values.get(field.value, False)) for field in START_JOB_GROUPS[0])
