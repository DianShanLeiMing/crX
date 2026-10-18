"""Small serial gameplay state machine; no device-specific policy here."""

import time
from dataclasses import dataclass, field

from crX.bot.deck import randomize_deck_state, select_deck_state
from crX.bot.fight import do_fight_state, end_fight_state, start_fight
from crX.bot.nav import select_mode
from crX.bot.recording_archiver import maybe_archive_recordings
from crX.bot.shop_daily_state import shop_daily_state
from crX.bot.state_detect import check_if_battle_mode_is_selected, check_if_battle_rewards_finished
from crX.interface.enums import UIField

ORDER = ("shop_daily", "select_battle_mode", "randomize_deck", "cycle_deck", "start_fight", "fight", "end_fight")
SHOP_INTERVAL_S = 3600
FIGHT_MODES = {
    UIField.CLASSIC_1V1_USER_TOGGLE: "Classic 1v1",
    UIField.CLASSIC_2V2_USER_TOGGLE: "Classic 2v2",
    UIField.TROPHY_ROAD_USER_TOGGLE: "Trophy Road",
}


@dataclass
class RuntimeContext:
    selected_mode: str | None = None
    mode_index: int = 0
    deck_numbers: dict[str, int] = field(default_factory=dict)
    last_shop_at: float | None = None
    completed_jobs: int = 0
    completed_fights: int = 0


def next_state(state: str) -> str:
    if state in {"start", "restart"}:
        return ORDER[0]
    return ORDER[(ORDER.index(state) + 1) % len(ORDER)]


def state_tree(emulator, logger, state: str, jobs: dict, context: RuntimeContext) -> str:
    logger.set_current_state(state, console=state not in {"start", "restart", "idle"})
    if state == "start":
        return next_state(state)
    if state == "restart":
        emulator.restart()
        return next_state(state)
    if state == "idle":
        time.sleep(1)
        return ORDER[0]
    success = True
    if state == "shop_daily":
        if jobs.get(UIField.SHOP_DAILY_OFFER_USER_TOGGLE) and (
            context.last_shop_at is None or time.monotonic() - context.last_shop_at >= SHOP_INTERVAL_S
        ):
            success = shop_daily_state(emulator, logger)
            if success:
                context.last_shop_at = time.monotonic()
                context.completed_jobs += 1
    elif state == "select_battle_mode":
        enabled = [mode for key, mode in FIGHT_MODES.items() if jobs.get(key)]
        if not enabled:
            context.selected_mode = None
            logger.change_status("等待下一次商店每日免费领取检查")
            return "idle"
        if jobs.get(UIField.STOP_AFTER_REWARDS) and check_if_battle_rewards_finished(emulator):
            time.sleep(0.4)
            if check_if_battle_rewards_finished(emulator):
                return "rewards_finished"
        context.selected_mode = enabled[context.mode_index % len(enabled)]
        context.mode_index += 1
        if not check_if_battle_mode_is_selected(emulator, context.selected_mode):
            success = select_mode(emulator, context.selected_mode, logger)
    elif state == "randomize_deck":
        if jobs.get(UIField.RANDOM_DECKS_USER_TOGGLE):
            success = randomize_deck_state(emulator, logger, jobs[UIField.DECK_NUMBER_SELECTION])
    elif state == "cycle_deck":
        if jobs.get(UIField.CYCLE_DECKS_USER_TOGGLE):
            count = jobs[UIField.MAX_DECK_SELECTION]
            mode = context.selected_mode
            if mode is None:
                raise ValueError("请先选择对战模式，再启用卡组轮换")
            success, chosen = select_deck_state(emulator, logger, context.deck_numbers.get(mode, 1), count)
            if success and chosen is not None:
                context.deck_numbers[mode] = chosen % count + 1
            else:
                success = False
    elif state == "start_fight":
        success = start_fight(emulator, logger, context.selected_mode)
    elif state == "fight":
        success = do_fight_state(
            emulator,
            logger,
            jobs.get(UIField.RANDOM_PLAYS_USER_TOGGLE, False),
            context.selected_mode,
            recording_flag=jobs.get(UIField.RECORD_FIGHTS_TOGGLE, False),
            custom_path=jobs.get(UIField.RECORDING_FOLDER_PATH) or None,
            use_emotes=jobs.get(UIField.USE_EMOTES, False),
        )
    elif state == "end_fight":
        success = end_fight_state(
            emulator,
            logger,
            jobs.get(UIField.RECORD_FIGHTS_TOGGLE, False),
            jobs.get(UIField.DISABLE_WIN_TRACK_TOGGLE, False),
        )
        if success:
            context.completed_jobs += 1
            context.completed_fights += 1
            logger.add_completed_fight()
            if jobs.get(UIField.RECORD_FIGHTS_TOGGLE):
                maybe_archive_recordings(logger=logger, custom_path=jobs.get(UIField.RECORDING_FOLDER_PATH) or None)
    else:
        raise ValueError(f"未知步骤：{state}")
    if success is False:
        logger.error(f"步骤 {state} 执行失败，正在尝试恢复")
        return "restart"
    return next_state(state)
