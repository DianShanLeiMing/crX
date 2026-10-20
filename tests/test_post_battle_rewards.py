"""Attached reward sequence and delayed overlays must drain before another match."""

from pathlib import Path

import cv2
import numpy as np
import pytest

from crX.bot import fight, nav, states
from crX.bot.coords import POST_BATTLE_REWARD_CONTINUE_COORD, START_FIGHT_BUTTON_COORD
from crX.bot.state_detect import detect_post_battle_reward, is_clash_main_menu_image
from crX.utils.logger import Logger

FIXTURES = Path(__file__).parent / "fixtures"


def image(name):
    return cv2.imread(str(FIXTURES / name))


@pytest.fixture
def clock(monkeypatch):
    elapsed = [0.0]
    monkeypatch.setattr(nav.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(nav.time, "sleep", lambda seconds: elapsed.__setitem__(0, elapsed[0] + seconds))
    return elapsed


class RewardSequence:
    def __init__(self, clock, scenes):
        self.clock = clock
        self.scenes = scenes
        self.index = 0
        self.entered = clock[0]
        self.clicks = []
        self.last_frame = None

    def screenshot(self):
        while self.index + 1 < len(self.scenes):
            _, delay = self.scenes[self.index]
            if delay is None or self.clock[0] - self.entered < delay:
                break
            self.index += 1
            self.entered = self.clock[0]
        self.last_frame = self.scenes[self.index][0]
        return self.last_frame

    def click(self, x, y):
        self.clicks.append((self.index, x, y))
        assert self.last_frame is not None
        assert not is_clash_main_menu_image(self.last_frame), "Never tap to advance a verified main menu"
        if detect_post_battle_reward(self.last_frame):
            assert (x, y) == POST_BATTLE_REWARD_CONTINUE_COORD
        else:
            assert 137 < x < 301 and 536 < y < 627
        _, delay = self.scenes[self.index]
        if delay is None and self.index + 1 < len(self.scenes):
            self.index += 1
            self.entered = self.clock[0]


@pytest.mark.parametrize("number", range(2, 11))
def test_attached_reward_stages(number):
    expected = "lucky_chest" if number <= 8 else "event_reward"
    assert detect_post_battle_reward(image(f"post-battle-attached-{number:02}.png")) == expected


def test_non_reward_screens_and_plain_backgrounds_are_not_clickable():
    for path in FIXTURES.glob("*.png"):
        if not path.name.startswith("post-battle-attached-") and path.name not in (
            "failure-chest-rainbow.png",
            "failure-reward-coins.png",
        ):
            assert detect_post_battle_reward(cv2.imread(str(path))) is None, path.name
    for color in ((20, 140, 235), (160, 15, 110), (100, 60, 30)):
        assert detect_post_battle_reward(np.full((640, 360, 3), color, dtype=np.uint8)) is None
    assert detect_post_battle_reward(None) is None
    assert detect_post_battle_reward(np.zeros((200, 100, 3), dtype=np.uint8)) is None


def test_single_visible_star_still_requires_the_chest_object():
    chest = image("post-battle-attached-02.png")
    chest[136:197, 129:193] = chest[136, 129]
    assert detect_post_battle_reward(chest) == "lucky_chest"
    chest[182:455, 86:275] = chest[182, 86]
    assert detect_post_battle_reward(chest) is None


def test_complete_result_chest_and_event_chain_counts_once(clock, monkeypatch):
    scenes = [(image("battle-win-native-0.png"), None)]
    scenes += [(image(f"post-battle-attached-{number:02}.png"), None) for number in range(2, 11)]
    scenes += [(image("post-battle-main-native.png"), None)]
    screen = RewardSequence(clock, scenes)
    logger, context = Logger(), states.RuntimeContext()
    outcomes = []
    monkeypatch.setattr(fight, "read_battle_outcome", lambda *_: "win")
    monkeypatch.setattr(fight, "is_recording", lambda: False)
    monkeypatch.setattr(fight, "finish_fight_recording", outcomes.append)
    assert states.state_tree(screen, logger, "end_fight", {}, context) == "shop_daily"
    assert len(screen.clicks) == 10
    assert context.completed_fights == logger.stats["completed_fights"] == logger.stats["wins"] == 1
    assert outcomes == ["win"]
    assert clock[0] - screen.entered >= nav.POST_BATTLE_MAIN_STABLE_S


def test_delayed_event_after_transient_main_is_not_a_success(clock):
    main = image("post-battle-main-native.png")
    screen = RewardSequence(
        clock,
        [
            (main, 2.0),
            (image("post-battle-attached-09.png"), None),
            (image("post-battle-attached-10.png"), None),
            (main, None),
        ],
    )
    assert nav.get_to_main_after_fight(screen, Logger())
    assert [index for index, _, _ in screen.clicks] == [1, 2]
    assert clock[0] >= 7


def test_main_then_reward_then_unknown_cannot_report_success(clock):
    main = image("post-battle-main-native.png")
    unknown = np.zeros_like(main)
    screen = RewardSequence(clock, [(main, 1.0), (image("post-battle-attached-10.png"), None), (unknown, None)])
    logger = Logger()
    assert not nav.get_to_main_after_fight(screen, logger, timeout=8)
    assert len(screen.clicks) == 1
    assert "未确认稳定主界面" in logger.current_status


def test_unknown_and_loading_frames_are_only_waited_for(clock):
    main = image("post-battle-main-native.png")
    screen = RewardSequence(clock, [(np.zeros_like(main), 2.0), (main, None)])
    assert nav.get_to_main_after_fight(screen, Logger())
    assert screen.clicks == []
    assert clock[0] >= 5


def test_result_retry_only_captures_outcome_once(clock):
    result = image("battle-win-native-0.png")
    main = image("post-battle-main-native.png")
    screen = RewardSequence(clock, [(result, 2.0), (main, None)])
    captured = []
    assert nav.get_to_main_after_fight(screen, Logger(), lambda: captured.append(True))
    assert len(screen.clicks) == 2
    assert captured == [True]


def test_stuck_reward_is_bounded_and_never_reports_main(clock):
    screen = RewardSequence(clock, [(image("post-battle-attached-02.png"), None)])
    assert not nav.get_to_main_after_fight(screen, Logger())
    assert len(screen.clicks) == nav.POST_BATTLE_MAX_ACTIONS
    assert clock[0] < nav.POST_BATTLE_TIMEOUT_S


def test_next_match_drains_late_rewards_before_start(clock, monkeypatch):
    screen = RewardSequence(
        clock,
        [
            (image("post-battle-attached-09.png"), None),
            (image("post-battle-attached-10.png"), None),
            (image("post-battle-main-native.png"), None),
        ],
    )
    original_click = screen.click
    starts = []

    def click(x, y):
        if (x, y) == START_FIGHT_BUTTON_COORD:
            assert is_clash_main_menu_image(screen.last_frame)
            starts.append(True)
        else:
            original_click(x, y)

    monkeypatch.setattr(screen, "click", click)
    assert fight.start_fight(screen, Logger(), "Classic 1v1")
    assert len(screen.clicks) == 2 and starts == [True]


def test_matchmaking_refuses_unknown_screens_without_taps(clock):
    screen = RewardSequence(clock, [(image("shop-paid-dialog-zh.png"), None)])
    assert not fight.start_fight(screen, Logger(), "Classic 1v1")
    assert screen.clicks == []


@pytest.mark.parametrize(
    ("filename", "expected"),
    [("failure-chest-rainbow.png", "lucky_chest"), ("failure-reward-coins.png", "event_reward")],
)
def test_failure_samples_are_recognized_rewards(filename, expected):
    assert detect_post_battle_reward(image(filename)) == expected
    assert not is_clash_main_menu_image(image(filename))


@pytest.mark.parametrize("filename", ["failure-main-currency.png", "failure-main-recovered.png"])
def test_failure_main_menus_ignore_currency_glints(filename):
    main = image(filename)
    assert is_clash_main_menu_image(main)
    main[:35] = 0  # Resource counters are not a reliable main-menu anchor.
    assert is_clash_main_menu_image(main)


def test_main_menu_fallback_requires_all_fixed_anchors():
    for region in ((129, 460, 236, 536), (318, 45, 352, 91), (129, 581, 236, 640)):
        main = image("failure-main-currency.png")
        left, top, right, bottom = region
        main[top:bottom, left:right] = 0
        assert not is_clash_main_menu_image(main)
    main = image("failure-main-currency.png")
    assert not is_clash_main_menu_image((main * 0.5).astype(np.uint8))


def test_daily_bonus_main_ignores_button_subtitle_but_requires_all_anchors():
    main = image("main-daily-bonus-360-native.png")
    assert is_clash_main_menu_image(main)
    main[508:532, 135:225] = 0  # Reward copy changes independently of the battle title.
    assert is_clash_main_menu_image(main)
    for left, top, right, bottom in ((115, 460, 245, 540), (318, 45, 352, 91), (150, 575, 210, 640)):
        incomplete = main.copy()
        incomplete[top:bottom, left:right] = 0
        assert not is_clash_main_menu_image(incomplete)
    assert not is_clash_main_menu_image((main * 0.5).astype(np.uint8))


def test_startup_wait_accepts_daily_bonus_main_without_clicking(clock, monkeypatch):
    screen = RewardSequence(
        clock, [(np.zeros((640, 360, 3), dtype=np.uint8), 11), (image("main-daily-bonus-360-native.png"), None)]
    )
    logger = Logger()
    messages = []
    monkeypatch.setattr(logger, "change_status", messages.append)
    assert nav.wait_for_clash_main_menu(screen, logger, timeout=15)
    assert screen.clicks == []
    assert messages == ["正在等待游戏主界面（已等待 0/15 秒）", "正在等待游戏主界面（已等待 10/15 秒）"]


def test_new_reward_backdrops_require_foreground_artwork():
    chest = image("failure-chest-rainbow.png")
    chest[233:425, 77:284] = chest[233, 77]
    assert detect_post_battle_reward(chest) is None
    coins = image("failure-reward-coins.png")
    coins[140:390, 130:290] = coins[140, 130]
    assert detect_post_battle_reward(coins) is None


def test_failed_reward_chain_reaches_stable_main_without_restart(clock):
    screen = RewardSequence(
        clock,
        [
            (image("failure-chest-rainbow.png"), None),
            (image("failure-reward-coins.png"), None),
            (image("failure-main-currency.png"), None),
        ],
    )
    assert nav.get_to_main_after_fight(screen, Logger())
    assert [index for index, _, _ in screen.clicks] == [0, 1]


def test_startup_wait_can_drain_recognized_rewards(clock):
    screen = RewardSequence(
        clock,
        [
            (image("failure-reward-coins.png"), None),
            (image("failure-main-currency.png"), None),
        ],
    )
    assert nav.wait_for_clash_main_menu(screen, Logger(), timeout=10)
    assert len(screen.clicks) == 1


def test_startup_unknown_timeout_remembers_exact_frame_without_clicks(clock, monkeypatch):
    unknown = np.zeros((640, 360, 3), dtype=np.uint8)
    screen = RewardSequence(clock, [(unknown, 2.0), (image("failure-main-recovered.png"), None)])
    monkeypatch.setattr(nav, "check_if_on_clan_voyage", lambda _: False)
    logger = Logger()
    assert not nav.wait_for_clash_main_menu(screen, logger, timeout=2)
    assert screen.clicks == []
    assert logger.failure_frame is not None
    assert np.array_equal(logger.failure_frame["image"], unknown)
    assert is_clash_main_menu_image(screen.screenshot())  # A later capture must not replace the failed frame.
