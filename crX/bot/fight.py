"""random module for randomizing fight plays"""

import collections
import random
import time
from collections.abc import Callable
from typing import Literal

from crX.bot.battle_strategy import (
    ADVANCE_ELIXIR,
    PURPOSE_LABELS,
    BattlePlayState,
    HandCard,
    PlayOption,
    rank_hand_plays,
)
from crX.bot.card_data import CIRCULAR_SPELL_PROFILES, get_card_display_name, get_card_elixir_cost
from crX.bot.card_detection import (
    check_which_cards_are_available,
    create_default_bridge_iar,
    get_card_group,
    get_play_coords_for_card,
    image_has_hero_champion_ability,
    read_hand_slot_for_settlement,
    switch_side,
    trigger_hero_champion_ability,
)
from crX.bot.card_samples import card_sample_session
from crX.bot.coords import (
    EMOTE_BUTTON_COORD,
    EMOTE_ICON_COORDS,
    ENEMY_PRINCESS_BAR_WIDTH,
    HAND_CARDS_COORDS,
    QUICKMATCH_POPUP_BUTTON_COORD,
    START_FIGHT_BUTTON_COORD,
)
from crX.bot.enemy_detect import EnemyUnitMonitor
from crX.bot.find import find_battle_outcome
from crX.bot.nav import finish_pending_battle_rewards, get_to_main_after_fight, wait_for_battle_start
from crX.bot.recorder import (
    finish_fight_recording,
    is_recording,
    log_play,
    start_fight_recording,
    stop_fight_capture,
)
from crX.bot.spell_targeting import MIN_STABILITY_INTERVAL_S, low_princess_tower_target, stable_marker_positions
from crX.bot.state_detect import (
    image_has_elixir,
    image_is_battle_over,
    image_is_in_battle,
    read_battle_elixir,
    read_enemy_princess_bars,
)
from crX.bot.tower_health import TowerHealthMonitor
from crX.interface.enums import BATTLE_MODE_LABELS
from crX.utils.failure_samples import remember_failure_frame
from crX.utils.logger import Logger
from crX.utils.versioning import __version__

ELIXIR_WAIT_TIMEOUT = 40  # too high but someone got errors with that so idk
ABILITY_TRIGGER_DELAY_S = 3
BATTLE_OUTCOME_WAIT_S = 3
BATTLE_DETECTION_LOSS_S = 12


def _maybe_start_fight_recording(
    emulator, logger, recording_flag: bool, fight_mode_chosen: str, custom_path: str | None = None
) -> None:
    """Begin opt-in training-data capture for 1v1-type fights only (Trophy Road / Classic 1v1, never 2v2)."""
    if recording_flag and fight_mode_chosen in ["Classic 1v1", "Trophy Road"]:
        pack_mode = "1v1_trophy" if fight_mode_chosen == "Trophy Road" else "1v1_classic"
        start_fight_recording(emulator, pack_mode, __version__, logger=logger, custom_path=custom_path)


def do_fight_state(
    emulator,
    logger: Logger,
    random_fight_mode,
    fight_mode_chosen,
    called_from_launching=False,
    recording_flag: bool = False,
    custom_path: str | None = None,
    use_emotes: bool = False,
) -> bool:
    """Handle the entirety of a battle state (start fight, do fight, end fight)."""

    logger.change_status("正在等待对战开始")

    # Wait for battle start
    if wait_for_battle_start(emulator, logger) is False:
        logger.change_status("等待对战开始超时")
        return False

    logger.change_status("开始执行对战操作")
    logger.log(f'This is the fight mode: "{fight_mode_chosen}"')

    # Recording is started/stopped inside the fight loops themselves so each pack
    # brackets exactly the in-battle play loop (no pre-battle wait or post-fight nav).
    with card_sample_session(fight_mode_chosen):
        # Both play modes observe the same cached availability/recognition frame.
        if (
            not random_fight_mode
            and _fight_loop(emulator, logger, recording_flag, fight_mode_chosen, custom_path, use_emotes=use_emotes)
            is False
        ):
            logger.change_status("对战操作执行失败")
            return False

        if (
            random_fight_mode
            and _random_fight_loop(
                emulator, logger, recording_flag, fight_mode_chosen, custom_path, use_emotes=use_emotes
            )
            is False
        ):
            logger.change_status("对战操作执行失败")
            return False

    # Only log the fight if not called from the start
    if not called_from_launching:
        if fight_mode_chosen == "Trophy Road":
            logger.increment_trophy_road_fights()
        elif fight_mode_chosen == "Classic 1v1":
            logger.increment_classic_1v1_fights()
        elif fight_mode_chosen == "Classic 2v2":
            logger.increment_classic_2v2_fights()

    return True


def start_fight(emulator, logger, mode) -> bool:
    """Start a fight with the specified mode.

    Args:
        emulator: The emulator controller
        logger: Logger instance
        mode: Fight mode - must be one of "Classic 1v1", "Classic 2v2", or "Trophy Road"

    Returns:
        bool: True if fight started successfully, False otherwise
    """
    # Validate mode parameter
    logger.log(f'Input mode type: "{type(mode)}"')
    logger.log(f"Input mode value: {mode}")
    valid_modes = ["Classic 1v1", "Classic 2v2", "Trophy Road"]
    logger.log(f"Valid modes: {valid_modes}")
    if mode not in valid_modes:
        logger.log(f"The valid modes for start_fight() are: {valid_modes}")
        logger.log(f"But start_fight() got an invalid mode: '{mode}'")
        return False

    logger.change_status(f"正在开始 {BATTLE_MODE_LABELS.get(mode, mode)} 对战")

    # Check if on clash main menu
    logger.log("Checking if on main menu before starting fight...")
    if not finish_pending_battle_rewards(emulator, logger):
        logger.change_status("当前不在主界面，无法开始对战")
        return False

    # For all modes (1v1 and 2v2), use the same start button
    # Mode is already set by select_mode() in states.py, just click start button
    emulator.click(START_FIGHT_BUTTON_COORD[0], START_FIGHT_BUTTON_COORD[1])
    logger.log(f"Clicked Start button at {START_FIGHT_BUTTON_COORD}")

    # 2v2 needs a second popup after Start
    if mode == "Classic 2v2":
        logger.change_status("经典 2v2：正在点击快速匹配……")
        time.sleep(3)
        emulator.click(QUICKMATCH_POPUP_BUTTON_COORD[0], QUICKMATCH_POPUP_BUTTON_COORD[1])
        logger.log(f"Clicked Quickmatch button at {QUICKMATCH_POPUP_BUTTON_COORD}")

    return True


def send_emote(emulator, logger: Logger):
    """Method to do an emote in a fight"""
    logger.change_status("正在发送表情")

    # click emote button
    emulator.click(EMOTE_BUTTON_COORD[0], EMOTE_BUTTON_COORD[1])
    time.sleep(0.33)

    emote_coord = random.choice(EMOTE_ICON_COORDS)
    emulator.click(emote_coord[0], emote_coord[1])


def _maybe_send_emote(emulator, logger, use_emotes: bool):
    if use_emotes and random.randint(0, 9) == 1:
        send_emote(emulator, logger)


def _tap_card_and_target(emulator, card_index, play_coord, *, wait_between=False):
    card_coord = HAND_CARDS_COORDS[card_index]
    tap_pair = getattr(type(emulator), "tap_card_and_target", None)
    if tap_pair is not None:
        tap_pair(emulator, card_coord, play_coord, wait_between=wait_between)
        return
    emulator.click(*card_coord)
    if wait_between:
        time.sleep(0.1)
    emulator.click(*play_coord)


def play_random_available_card(
    emulator,
    logger,
    recording_flag: bool,
    elapsed_s: float,
    *,
    enemy_monitor: EnemyUnitMonitor | None = None,
    decision_frame=None,
    play_state: BattlePlayState | None = None,
) -> bool:
    """Use battle priorities, with random choice among equivalent hand slots."""
    decision = _pick_card_play(
        emulator, logger, elapsed_s, enemy_monitor, decision_frame=decision_frame, play_state=play_state
    )
    if decision is None:
        return False
    play, elixir = decision
    _tap_card_and_target(emulator, play.card.slot, play.point, wait_between=True)
    if play_state is not None:
        play_state.record_play(play, elixir, time.monotonic())
    time.sleep(0.1)
    _remember_played_slot(play.card.slot)

    if recording_flag:
        log_play(play.card.slot, *play.point, elapsed_s)
    logger.add_card_played()
    return True


def wait_for_elixir(
    emulator,
    logger,
    elixir_wait_amount,
    WAIT_THRESHOLD=5000,  # noqa: N803
    PLAY_THRESHOLD=10000,  # noqa: N803
    recording_flag: bool = False,
    *,
    tower_monitor: TowerHealthMonitor | None = None,
    enemy_monitor: EnemyUnitMonitor | None = None,
    on_decision_frame=None,
    play_state: BattlePlayState | None = None,
) -> Literal["restart", "no battle"] | bool:
    """Wait for a playable battle frame, sharing it across the HUD checks."""
    start_time = time.monotonic()
    battle_detection_lost_since = None
    last_logged_second = -1
    ability_available_since = None

    while True:
        image = emulator.screenshot()
        now = time.monotonic()
        if not image_is_in_battle(image):
            if play_state is not None:
                play_state.settlement_reading = None
            if image_is_battle_over(image):
                logger.change_status("对战已结束，停止等待圣水")
                return "no battle"
            if battle_detection_lost_since is None:
                battle_detection_lost_since = now
                logger.change_status("等待圣水时暂未检测到战斗画面，继续等待")
            elif now - battle_detection_lost_since >= BATTLE_DETECTION_LOSS_S:
                logger.change_status("连续多次未检测到战斗画面，尚未确认结算，请求恢复")
                remember_failure_frame(
                    logger,
                    image,
                    reason="连续未检测到战斗画面且未确认结算",
                    stage="battle_detection_lost",
                    wait_s=round(now - battle_detection_lost_since, 2),
                )
                return "restart"
            ability_available_since = None
            time.sleep(0.5)
            continue

        battle_detection_lost_since = None
        if tower_monitor is not None:
            tower_monitor.observe(image)
        if enemy_monitor is not None:
            enemy_monitor.observe(image, force=on_decision_frame is not None)

        wait_time = now - start_time
        limit = play_state.elixir_limit(now) if play_state is not None else None
        if play_state is not None and limit is not None and play_state.pending_card is not None:
            observed = read_battle_elixir(image)
            replacement = (
                read_hand_slot_for_settlement(image, play_state.pending_card.slot) if observed == limit else None
            )
            if play_state.observe_settlement(observed, replacement):
                limit = None
        if limit == 0:
            continue
        if (limit is None or limit >= elixir_wait_amount) and image_has_elixir(image, elixir_wait_amount):
            logger.change_status(f"耗时 {wait_time:.2f} 秒，已等到 {elixir_wait_amount} 点圣水。")
            break

        snapshot = enemy_monitor.snapshot if enemy_monitor is not None else None
        if (
            on_decision_frame is not None
            and snapshot is not None
            and image_has_elixir(image, 1)
            and (
                any(unit.zone != "enemy_half" for unit in snapshot.units)
                or low_princess_tower_target(read_enemy_princess_bars(image)) is not None
            )
        ):
            break

        elapsed_second = int(wait_time)
        if elapsed_second != last_logged_second:
            logger.change_status(
                f"正在等待 {elixir_wait_amount} 点圣水，已等待 {elapsed_second} 秒……",
            )
            last_logged_second = elapsed_second

        card_indices = check_which_cards_are_available(emulator, frame=image)
        if limit is None and image_has_hero_champion_ability(image):
            if ability_available_since is None:
                ability_available_since = now
                logger.change_status(
                    f"英雄／冠军技能已就绪，将在 {ABILITY_TRIGGER_DELAY_S} 秒后触发",
                )
            elif now - ability_available_since >= ABILITY_TRIGGER_DELAY_S:
                trigger_hero_champion_ability(emulator, logger)
                ability_available_since = None
                # The ability can consume elixir or change the battlefield.
                continue
        else:
            ability_available_since = None

        card_inhand = len(card_indices)
        action_offset, _ = switch_side()
        if action_offset > PLAY_THRESHOLD and card_inhand > 0:
            logger.change_status("战场活动较多，立即出牌")
            break

        if action_offset > WAIT_THRESHOLD and card_inhand == 4:
            logger.change_status("所有手牌均可使用")
            break

        if wait_time > ELIXIR_WAIT_TIMEOUT:
            logger.change_status(status="等待圣水超时")
            return "restart"

    if on_decision_frame is not None:
        on_decision_frame(image)
    return True


def end_fight_state(
    emulator,
    logger: Logger,
    recording_flag,
    disable_win_tracker_toggle=True,
    *,
    on_win: Callable[[], None] | None = None,
):
    """Capture a best-effort outcome before leaving the result screen."""
    needs_outcome = is_recording() or not disable_win_tracker_toggle or on_win is not None
    outcome = None

    def capture_outcome():
        nonlocal outcome
        outcome = read_battle_outcome(emulator, logger)

    if get_to_main_after_fight(emulator, logger, on_result_screen=capture_outcome if needs_outcome else None) is False:
        logger.log("对战结束后返回主界面失败")
        finish_fight_recording(None)
        return False

    if outcome == "win" and on_win is not None:
        on_win()
    if needs_outcome:
        result = {"win": "胜利", "loss": "失败"}.get(outcome, "未知（不计入胜率）")
        logger.change_status(f"本局对战结果：{result}")
        if not disable_win_tracker_toggle:
            if outcome == "win":
                logger.add_win()
            elif outcome == "loss":
                logger.add_loss()
    else:
        logger.log("未启用胜负统计或录制，跳过胜负识别")
    finish_fight_recording(outcome)
    return True


def read_battle_outcome(emulator, logger: Logger) -> Literal["win", "loss"] | None:
    """Require two matching frames; auxiliary recognition must not block gameplay."""
    logger.change_status("正在识别结算页胜负")
    deadline = time.monotonic() + BATTLE_OUTCOME_WAIT_S
    previous = None
    while time.monotonic() < deadline:
        try:
            outcome = find_battle_outcome(emulator)
        except Exception:
            logger.log("结算页胜负识别异常，本局结果记为未知")
            return None
        if outcome is not None and outcome == previous:
            return outcome
        previous = outcome
        time.sleep(0.3)
    return None


# main fight loops

# Keep the three most recently played distinct hand slots, oldest first.
last_three_cards = collections.deque(maxlen=3)


def select_card_index(card_indices, last_three_cards):
    if not card_indices:
        raise ValueError("可用卡牌位置不能为空")

    # First preference: Cards not in the last_three_cards queue
    preferred_cards = [index for index in card_indices if index not in last_three_cards]

    # Second preference: Cards not among the last two added to the queue
    if not preferred_cards and len(last_three_cards) == 3:
        preferred_cards = [index for index in card_indices if index not in list(last_three_cards)[-2:]]

    # Third preference: Any card except the most recently added one
    if not preferred_cards and last_three_cards:
        preferred_cards = [index for index in card_indices if index != last_three_cards[-1]]

    # Fallback: If all else fails, consider all cards
    if not preferred_cards:
        preferred_cards = card_indices

    return random.choice(preferred_cards)


def _remember_played_slot(card_index: int) -> None:
    if card_index in last_three_cards:
        last_three_cards.remove(card_index)
    last_three_cards.append(card_index)


def _pick_card_play(
    emulator,
    logger,
    elapsed_s: float,
    enemy_monitor: EnemyUnitMonitor | None,
    *,
    decision_frame=None,
    play_state: BattlePlayState | None = None,
) -> tuple[PlayOption, int] | None:
    if enemy_monitor is None:
        enemy_monitor = EnemyUnitMonitor(logger)

    def inspect_hand(frame=None):
        captured_frame = None

        def remember(image):
            nonlocal captured_frame
            captured_frame = image

        slots = check_which_cards_are_available(emulator, check_side=True, observe_frame=remember, frame=frame)
        # Gate the frame before relying on hand highlights or enemy markers.
        if not slots or not image_is_in_battle(captured_frame):
            return None
        hand = []
        for slot in slots:
            card_id, preset = get_play_coords_for_card(emulator, logger, slot, elapsed_s)
            if card_id and card_id != "UNKNOWN":
                hand.append(HandCard(slot, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), preset))
        return captured_frame, hand

    observation = inspect_hand(decision_frame)
    if observation is None:
        return None
    frame, hand = observation
    if decision_frame is None or enemy_monitor.snapshot is None:
        enemy_monitor.observe(frame, force=True)
    snapshot = enemy_monitor.snapshot
    if snapshot is None:
        return None

    def rank(frame, hand, snapshot, *, stable_positions=None):
        elixir = read_battle_elixir(frame)
        previous_target = play_state.advance_target if play_state is not None else None
        if play_state is not None:
            now = time.monotonic()
            limit = play_state.elixir_limit(now)
            if limit is not None:
                elixir = min(elixir, limit)
        options = rank_hand_plays(
            hand,
            snapshot,
            read_enemy_princess_bars(frame),
            elixir,
            stable_positions=stable_positions,
            play_state=play_state,
        )
        if (
            play_state is not None
            and play_state.advance_target is not None
            and (
                previous_target is None
                or (play_state.advance_target.slot, play_state.advance_target.card_id)
                != (previous_target.slot, previous_target.card_id)
            )
        ):
            logger.change_status(
                f"推进存费：等待 {play_state.advance_elixir()} 点圣水后使用"
                f"{get_card_display_name(play_state.advance_target.card_id)}（当前约 {elixir}）"
            )
        return options, elixir

    options, elixir = rank(frame, hand, snapshot)
    if not options:
        return None

    def select(options):
        priority = max(option.priority for option in options)
        tied = [option for option in options if option.priority == priority]
        slot = select_card_index([option.card.slot for option in tied], last_three_cards)
        return next(option for option in tied if option.card.slot == slot)

    play = select(options)
    if play.card.is_spell:
        profile = CIRCULAR_SPELL_PROFILES.get(play.card.card_id)
        if play.covered and profile is not None and profile.requires_stable_markers:
            remaining = snapshot.sampled_at + MIN_STABILITY_INTERVAL_S - time.monotonic()
            if remaining > 0:
                # Wait before capturing so the casting frame is still fresh at input time.
                time.sleep(remaining)
        # Read all slots again from the casting frame, so a delayed or changed
        # hand cannot cast a different card using the preview's coordinates.
        latest = inspect_hand()
        if latest is None:
            logger.change_status("本次法术已跳过：复核未确认战斗画面或可用手牌")
            return None
        fresh_frame, hand = latest
        enemy_monitor.observe(fresh_frame, force=True)
        fresh = enemy_monitor.snapshot
        if fresh is None:
            return None
        stable = stable_marker_positions(
            tuple(unit.position for unit in fresh.units),
            tuple(unit.position for unit in snapshot.units),
            fresh.sampled_at - snapshot.sampled_at,
        )
        options, elixir = rank(fresh_frame, hand, fresh, stable_positions=stable)
        if not options:
            logger.change_status("本次法术已跳过：复核后没有合适的出牌方案")
            return None
        play = select(options)

    note = PURPOSE_LABELS[play.purpose]
    if play.card.is_spell and play.covered:
        note += f"，实时落点，估算覆盖 {play.covered} 个标记"
    if play.tower_fill is not None:
        note += f"，血条约 {play.tower_fill / ENEMY_PRINCESS_BAR_WIDTH:.1%}"
    if play.waiting_bonus:
        note += f"，已等待至少 {play.waiting_bonus} 轮"
    cost = f"{play.card.cost} 费，剩余约 {elixir - play.card.cost}" if play.card.cost is not None else "费用未知"
    logger.change_status(
        f"已计算卡牌 {get_card_display_name(play.card.card_id)} 的出牌位置 {play.point} | {note} | {cost}"
    )
    return play, elixir


def play_a_card(
    emulator,
    logger,
    recording_flag: bool,
    battle_strategy: "BattleStrategy",
    *,
    use_emotes: bool = False,
    enemy_monitor: EnemyUnitMonitor | None = None,
    decision_frame=None,
    play_state: BattlePlayState | None = None,
) -> bool:
    decision = _pick_card_play(
        emulator,
        logger,
        battle_strategy.get_elapsed_time(),
        enemy_monitor,
        decision_frame=decision_frame,
        play_state=play_state,
    )
    if decision is None:
        return False
    play, elixir = decision

    click_and_play_card_start_time = time.perf_counter()
    _tap_card_and_target(emulator, play.card.slot, play.point)
    if play_state is not None:
        play_state.record_play(play, elixir, time.monotonic())
    _remember_played_slot(play.card.slot)
    click_and_play_card_time_taken = time.perf_counter() - click_and_play_card_start_time
    if recording_flag:
        log_play(play.card.slot, *play.point, battle_strategy.get_elapsed_time())

    logger.change_status(f"已完成出牌，耗时 {click_and_play_card_time_taken:.3f} 秒")
    logger.add_card_played()

    _maybe_send_emote(emulator, logger, use_emotes)
    return True


_PHASE_THRESHOLDS_1V1 = {
    "early": (6000, 9000),
    "single": (6000, 9000),
    "double": (7000, 10000),
    "triple": (8000, 11000),
}

_PHASE_THRESHOLDS_2V2 = {
    "early": (7000, 13000),
    "single": (7000, 14000),
    "double": (8000, 15000),
    "triple": (9000, 16000),
}


class BattleStrategy:
    """Manages battle timing and elixir selection strategy.

    Encapsulates the sophisticated elixir selection logic that changes
    based on battle phase, eliminating the need for global variables.
    """

    def __init__(self, fight_mode: str = "Classic 1v1"):
        self.start_time = None

        is_2v2 = fight_mode == "Classic 2v2"
        self.phase_thresholds = _PHASE_THRESHOLDS_2V2 if is_2v2 else _PHASE_THRESHOLDS_1V1

    def start_battle(self):
        """Call when battle begins to start timing."""
        self.start_time = time.time()

    def get_elapsed_time(self):
        """Get seconds elapsed since battle start."""
        return time.time() - self.start_time if self.start_time else 0

    def get_battle_phase(self):
        """Determine current battle phase based on elapsed time."""
        elapsed = self.get_elapsed_time()
        if elapsed < 7:
            return "early"
        elif elapsed < 90:
            return "single"
        elif elapsed < 200:
            return "double"
        else:
            return "triple"

    def get_thresholds(self):
        """Get (WAIT_THRESHOLD, PLAY_THRESHOLD) for current battle phase."""
        phase = self.get_battle_phase()
        return self.phase_thresholds[phase]


def _fight_loop(
    emulator,
    logger: Logger,
    recording_flag: bool,
    fight_mode: str = "Classic 1v1",
    custom_path: str | None = None,
    *,
    use_emotes: bool = False,
    random_mode: bool = False,
) -> bool:
    """Method for handling dynamically timed fight"""
    create_default_bridge_iar(emulator)
    _maybe_start_fight_recording(emulator, logger, recording_flag, fight_mode, custom_path)
    last_three_cards.clear()
    prev_cards_played = logger.get_cards_played()

    # Initialize battle strategy and start timing
    battle_strategy = BattleStrategy(fight_mode)
    battle_strategy.start_battle()
    is_1v1 = fight_mode in ("Classic 1v1", "Trophy Road")
    tower_monitor = TowerHealthMonitor(logger) if is_1v1 else None
    enemy_monitor = EnemyUnitMonitor(logger)
    play_state = BattlePlayState()

    decision_frame = None

    def remember_decision_frame(image):
        nonlocal decision_frame
        decision_frame = image

    timeout = 300 if random_mode else 360
    fight_deadline = time.monotonic() + timeout
    while True:
        if time.monotonic() >= fight_deadline:
            logger.change_status(f"对战执行超时（{timeout // 60} 分钟）")
            return False
        # A routine play needs a reserve, but confirmed pressure wakes the
        # planner before this threshold. Reuse the decision frame for the hand.
        decision_frame = None
        wait_threshold, play_threshold = battle_strategy.get_thresholds()

        wait_output = wait_for_elixir(
            emulator,
            logger,
            play_state.advance_elixir() if play_state.advance_target is not None else ADVANCE_ELIXIR,
            wait_threshold,
            play_threshold,
            recording_flag,
            tower_monitor=tower_monitor,
            enemy_monitor=enemy_monitor,
            on_decision_frame=remember_decision_frame,
            play_state=play_state,
        )

        if wait_output == "restart":
            logger.change_status("等待圣水时发生错误")
            return False

        if wait_output == "no battle":
            logger.change_status("当前已不在对战中")
            break

        play_start_time = time.time()
        if random_mode:
            played = play_random_available_card(
                emulator,
                logger,
                recording_flag,
                battle_strategy.get_elapsed_time(),
                enemy_monitor=enemy_monitor,
                decision_frame=decision_frame,
                play_state=play_state,
            )
            if played:
                _maybe_send_emote(emulator, logger, use_emotes)
        else:
            played = play_a_card(
                emulator,
                logger,
                recording_flag,
                battle_strategy,
                use_emotes=use_emotes,
                enemy_monitor=enemy_monitor,
                decision_frame=decision_frame,
                play_state=play_state,
            )
        if not played:
            logger.change_status("暂缓出牌，继续观察战场……")
            snapshot = enemy_monitor.snapshot
            if snapshot is None or all(unit.zone == "enemy_half" for unit in snapshot.units):
                time.sleep(0.2)
        # play_time_taken = str(time.time() - play_start_time)[:4]
        logger.change_status(
            f"本次出牌耗时 {str(time.time() - play_start_time)[:4]} 秒",
        )

    # Fight over: freeze capture so the pack excludes post-fight nav (manifest/outcome written later).
    stop_fight_capture()
    logger.change_status("对战已完成")
    cards_played = logger.get_cards_played()
    logger.change_status(f"本场对战共出牌约 {cards_played - prev_cards_played} 次")

    return True


def _random_fight_loop(
    emulator,
    logger,
    recording_flag: bool = False,
    fight_mode_chosen: str = "Trophy Road",
    custom_path: str | None = None,
    *,
    use_emotes: bool = False,
) -> bool:
    return _fight_loop(
        emulator, logger, recording_flag, fight_mode_chosen, custom_path, use_emotes=use_emotes, random_mode=True
    )


if __name__ == "__main__":
    pass
