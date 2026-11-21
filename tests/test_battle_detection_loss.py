"""Unknown frames must trigger bounded recovery rather than a completed fight."""

from contextlib import nullcontext
from types import SimpleNamespace

import numpy as np
import pytest

from crX.bot import fight, states
from crX.interface.enums import UIField
from crX.utils.logger import Logger


@pytest.fixture
def unknown_battle(monkeypatch):
    monkeypatch.setattr(fight, "card_sample_session", lambda *_: nullcontext())
    monkeypatch.setattr(fight, "wait_for_battle_start", lambda *_: True)
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda *_: None)
    monkeypatch.setattr(fight, "_maybe_start_fight_recording", lambda *_: None)
    elapsed = 0.0

    def sleep(seconds):
        nonlocal elapsed
        elapsed += seconds

    monkeypatch.setattr(fight.time, "sleep", sleep)
    monkeypatch.setattr(fight.time, "monotonic", lambda: elapsed)
    return SimpleNamespace(screenshot=lambda: None)


@pytest.mark.parametrize("random_mode", [False, True])
def test_unconfirmed_battle_does_not_advance_to_result_or_count_completion(unknown_battle, random_mode):
    logger, context = Logger(), states.RuntimeContext(selected_mode="Classic 1v1")
    assert (
        states.state_tree(unknown_battle, logger, "fight", {UIField.RANDOM_PLAYS_USER_TOGGLE: random_mode}, context)
        == "restart"
    )
    assert context.completed_fights == context.completed_jobs == 0
    assert logger.stats["completed_fights"] == logger.stats["classic_1v1_fights"] == 0


def test_elixir_wait_does_not_report_unknown_frames_as_battle_end(unknown_battle):
    logger = Logger()
    assert fight.wait_for_elixir(unknown_battle, logger, 7) == "restart"
    assert "尚未确认结算" in logger.current_status
    assert logger.failure_frame is not None
    assert logger.failure_frame["details"]["stage"] == "battle_detection_lost"
    assert logger.failure_frame["details"]["wait_s"] == fight.BATTLE_DETECTION_LOSS_S


def test_detection_loss_keeps_the_last_observed_frame_before_recovery(unknown_battle):
    frames = []

    def capture():
        frame = np.zeros((1280, 720, 3), dtype=np.uint8)
        frame[0, 0, 0] = len(frames) + 1
        frames.append(frame)
        return frame

    unknown_battle.screenshot = capture
    logger = Logger()
    assert fight.wait_for_elixir(unknown_battle, logger, 7) == "restart"
    assert logger.failure_frame is not None
    evidence = logger.failure_frame["image"]
    np.testing.assert_array_equal(evidence, frames[-1])
    assert logger.failure_frame["details"]["reason"] == "连续未检测到战斗画面且未确认结算"
    capture()
    assert evidence[0, 0, 0] == len(frames) - 1
    frames[-2][:] = 255
    assert evidence[0, 0, 0] != 255
