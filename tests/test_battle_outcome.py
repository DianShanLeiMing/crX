"""Result-screen winner labels replace battle-log navigation without blocking quotas."""

from pathlib import Path

import pytest

from crX.bot import fight, nav, states
from crX.bot.coords import BATTLE_RESULT_BUTTON_REGION
from crX.bot.find import find_battle_outcome
from crX.interface.enums import UIField
from crX.utils.image_handler import open_from_path
from crX.utils.logger import Logger

FIXTURES = Path(__file__).parent / "fixtures"


class ResultScreen:
    def __init__(self, filename):
        self.filename = filename
        self.image = open_from_path(str(FIXTURES / filename))
        self.on_main = False
        self.clicks = []

    def screenshot(self):
        if self.on_main:
            return open_from_path(str(FIXTURES / "post-battle-main-native.png"))
        return self.image

    def click(self, x, y):
        self.clicks.append((x, y))
        if self.filename == "training-result-zh.png":
            assert 166 < x < 254 and 558 < y < 593
        elif self.filename.startswith(("battle-loss-native-", "battle-win-native-")):
            assert 167 < x < 252 and 538 < y < 577
        else:
            assert 215 < x < 320 and 550 < y < 605
        self.on_main = True


@pytest.mark.parametrize("result", ["win", "loss"])
def test_attached_result_screens_identify_the_winning_side(result):
    screen = ResultScreen(f"battle-{result}-zh.png")
    assert find_battle_outcome(screen) == result


@pytest.mark.parametrize("frame", [0, 3])
@pytest.mark.parametrize("result", ["win", "loss"])
def test_native_frames_identify_the_winning_side_despite_animation(frame, result):
    assert find_battle_outcome(ResultScreen(f"battle-{result}-native-{frame}.png")) == result


@pytest.mark.parametrize(
    "filename",
    [
        "main-zh.png",
        "shop-zh.png",
        "training-start-zh.png",
        "training-exit-prompt-zh.png",
        "training-battle-zh.png",
        "training-result-zh.png",
        "replay-result-zh.png",
        "card_hand_bgr_regression.png",
    ],
)
def test_other_screens_do_not_get_a_guessed_outcome(filename):
    assert find_battle_outcome(ResultScreen(filename)) is None


def test_winner_label_without_result_button_is_not_a_result():
    screen = ResultScreen("battle-win-zh.png")
    x1, y1, x2, y2 = BATTLE_RESULT_BUTTON_REGION
    screen.image[y1:y2, x1:x2] = 0
    assert find_battle_outcome(screen) is None


def test_labels_on_both_sides_are_unknown():
    screen = ResultScreen("battle-loss-zh.png")
    screen.image[215:295, 125:290] = screen.image[35:115, 125:290].copy()
    assert find_battle_outcome(screen) is None


@pytest.fixture
def result_flow(monkeypatch):
    elapsed = [0.0]
    finished = []

    def sleep(seconds):
        elapsed[0] += seconds

    monkeypatch.setattr(fight.time, "monotonic", lambda: elapsed[0])
    monkeypatch.setattr(fight.time, "sleep", sleep)
    monkeypatch.setattr(fight, "is_recording", lambda: False)
    monkeypatch.setattr(fight, "finish_fight_recording", finished.append)

    def no_battle_log(*args, **kwargs):
        pytest.fail("Result collection must not open the battle log")

    monkeypatch.setattr(nav, "get_to_activity_log", no_battle_log)
    return elapsed, finished


@pytest.mark.parametrize(
    "filename,outcome,wins,losses",
    [
        ("battle-win-zh.png", "win", 1, 0),
        ("battle-loss-zh.png", "loss", 0, 1),
        ("battle-loss-native-0.png", "loss", 0, 1),
        ("battle-win-native-0.png", "win", 1, 0),
        ("training-result-zh.png", None, 0, 0),
    ],
)
def test_real_result_navigation_updates_statistics_and_completed_count(result_flow, filename, outcome, wins, losses):
    elapsed, finished = result_flow
    screen, logger, context = ResultScreen(filename), Logger(), states.RuntimeContext()
    jobs = {UIField.DISABLE_WIN_TRACK_TOGGLE: False}
    assert states.state_tree(screen, logger, "end_fight", jobs, context) == "shop_daily"
    assert len(screen.clicks) == 1
    assert finished == [outcome]
    assert logger.stats["wins"] == wins
    assert logger.stats["losses"] == losses
    assert logger.stats["completed_fights"] == context.completed_fights == 1
    assert logger.stats["restarts_after_failure"] == 0
    # Includes the result-click cooldown and three uninterrupted seconds on main.
    assert elapsed[0] < 8


def test_disabled_statistics_still_collect_recording_outcome(result_flow, monkeypatch):
    _, finished = result_flow
    monkeypatch.setattr(fight, "is_recording", lambda: True)
    logger = Logger()
    assert fight.end_fight_state(ResultScreen("battle-win-zh.png"), logger, True, True)
    assert finished == ["win"]
    assert logger.stats["wins"] == logger.stats["losses"] == 0


def test_disabled_statistics_and_recording_skip_recognition(result_flow, monkeypatch):
    _, finished = result_flow

    def no_recognition(*args):
        pytest.fail("Disabled outcome collection must not inspect winner labels")

    monkeypatch.setattr(fight, "read_battle_outcome", no_recognition)
    assert fight.end_fight_state(ResultScreen("battle-win-zh.png"), Logger(), False, True)
    assert finished == [None]


def test_recognition_error_does_not_block_a_completed_match(result_flow, monkeypatch):
    def broken_recognition(screen):
        raise ValueError("unreadable frame")

    monkeypatch.setattr(fight, "find_battle_outcome", broken_recognition)
    screen, logger, context = ResultScreen("battle-win-zh.png"), Logger(), states.RuntimeContext()
    assert states.state_tree(screen, logger, "end_fight", {}, context) == "shop_daily"
    assert context.completed_fights == 1
    assert logger.stats["wins"] == logger.stats["losses"] == 0
    assert not logger.errored


def test_outcome_needs_two_consecutive_matching_frames(result_flow, monkeypatch):
    samples = iter(["win", None, "loss", "loss"])
    monkeypatch.setattr(fight, "find_battle_outcome", lambda screen: next(samples))
    assert fight.read_battle_outcome(None, Logger()) == "loss"


def test_failed_return_to_main_does_not_count_or_label_recording(result_flow, monkeypatch):
    _, finished = result_flow

    def failed_return(screen, logger, on_result_screen=None):
        assert on_result_screen is not None
        on_result_screen()
        return False

    monkeypatch.setattr(fight, "get_to_main_after_fight", failed_return)
    logger, context = Logger(), states.RuntimeContext()
    assert states.state_tree(ResultScreen("battle-win-zh.png"), logger, "end_fight", {}, context) == "restart"
    assert finished == [None]
    assert context.completed_fights == logger.stats["wins"] == logger.stats["losses"] == 0
