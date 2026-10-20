"""Only a confirmed empty crown bar stops battles; quotas and shop-only jobs survive."""

from pathlib import Path
from threading import Event

import pytest

from crX.__main__ import make_job_dictionary
from crX.bot import states, worker
from crX.bot.state_detect import check_if_battle_rewards_finished
from crX.interface.enums import UIField
from crX.utils.image_handler import open_from_path
from crX.utils.logger import Logger

FIXTURES = Path(__file__).parent / "fixtures"


class Screen:
    def __init__(self, filename):
        self.image = open_from_path(str(FIXTURES / filename))

    def screenshot(self):
        return self.image


@pytest.mark.parametrize("filename", ["rewards-finished-zh.png", "rewards-finished-floating-zh.png"])
def test_native_empty_crown_bar_is_detected(filename):
    assert check_if_battle_rewards_finished(Screen(filename))


@pytest.mark.parametrize(
    "filename",
    [
        "rewards-pending-zh.png",
        "main-zh.png",
        "shop-zh.png",
        "training-battle-zh.png",
        "training-result-zh.png",
        "battle-win-zh.png",
        "battle-loss-zh.png",
    ],
)
def test_other_screens_do_not_stop_rewards(filename):
    assert not check_if_battle_rewards_finished(Screen(filename))


def test_crown_bar_alone_is_not_enough_without_main_menu():
    screen = Screen("rewards-finished-zh.png")
    screen.image[:40] = 0
    assert not check_if_battle_rewards_finished(screen)


def test_unsupported_screen_geometry_is_not_a_finished_reward():
    screen = Screen("rewards-finished-zh.png")
    screen.image = screen.image[:101, :86]
    assert not check_if_battle_rewards_finished(screen)


def test_empty_bar_without_crown_does_not_stop():
    screen = Screen("rewards-finished-zh.png")
    screen.image[389:448, 167:198] = 0
    assert not check_if_battle_rewards_finished(screen)


def test_crown_without_empty_bar_does_not_stop():
    screen = Screen("rewards-pending-zh.png")
    crown = Screen("rewards-finished-zh.png").image
    screen.image[389:448, 167:198] = crown[389:448, 167:198]
    assert not check_if_battle_rewards_finished(screen)


@pytest.mark.parametrize("value", [None, "true", 1, 0])
def test_stop_option_requires_a_real_boolean(value):
    with pytest.raises(ValueError, match="勾选项"):
        make_job_dictionary({UIField.STOP_AFTER_REWARDS: value})


def test_old_settings_keep_instance_and_fight_limit_and_default_stop_to_off():
    jobs = make_job_dictionary({UIField.MUMU_INSTANCE: "1", UIField.MAX_FIGHTS: "7"})
    assert jobs[UIField.MUMU_INSTANCE] == "1"
    assert jobs[UIField.MAX_FIGHTS] == 7
    assert jobs[UIField.STOP_AFTER_REWARDS] is False


@pytest.fixture
def gameplay(monkeypatch):
    finished = Screen("rewards-finished-zh.png").image
    pending = Screen("rewards-pending-zh.png").image
    logger = Logger()
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = Event()
    monkeypatch.setattr(process.shutdown_event, "wait", lambda *args: False)
    monkeypatch.setattr(states.time, "sleep", lambda *args: None)
    monkeypatch.setattr(states, "check_if_battle_mode_is_selected", lambda *args: True)
    monkeypatch.setattr(states, "do_fight_state", lambda *args, **kwargs: True)
    monkeypatch.setattr(worker, "finish_fight_recording", lambda *args: None)

    class Device:
        completed = 0
        started = 0
        finish_after = 0

        def screenshot(self):
            return finished if self.completed >= self.finish_after else pending

        def restart(self):
            pytest.fail("Reward detection should not restart the game")

    device = Device()

    def start(*args):
        device.started += 1
        assert device.started <= 6, "Unexpected extra matches"
        return True

    def end(*args):
        device.completed += 1
        return True

    monkeypatch.setattr(states, "start_fight", start)
    monkeypatch.setattr(states, "end_fight_state", end)
    return process, device, logger


def test_already_exhausted_stops_before_first_battle(gameplay):
    process, device, logger = gameplay
    jobs = make_job_dictionary({UIField.STOP_AFTER_REWARDS: True})
    assert process._run_bot_loop(device, jobs, logger)
    assert device.started == logger.stats["completed_fights"] == 0
    assert "奖励已领完" in logger.current_status


def test_stops_after_rewards_finish_without_starting_another_battle(gameplay):
    process, device, logger = gameplay
    device.finish_after = 2
    jobs = make_job_dictionary({UIField.STOP_AFTER_REWARDS: True, UIField.MAX_FIGHTS: 5})
    assert process._run_bot_loop(device, jobs, logger)
    assert device.started == logger.stats["completed_fights"] == 2
    assert "奖励已领完" in logger.current_status
    assert "已完成 2 局" in logger.current_status


def test_quota_still_stops_before_reward_target(gameplay):
    process, device, logger = gameplay
    device.finish_after = 5
    jobs = make_job_dictionary({UIField.STOP_AFTER_REWARDS: True, UIField.MAX_FIGHTS: 2})
    assert process._run_bot_loop(device, jobs, logger)
    assert device.started == logger.stats["completed_fights"] == 2
    assert "达到执行局数" in logger.current_status


def test_disabled_stop_ignores_empty_bar(gameplay, monkeypatch):
    process, device, logger = gameplay

    def no_check(*args):
        pytest.fail("Disabled reward stop must not inspect rewards")

    monkeypatch.setattr(states, "check_if_battle_rewards_finished", no_check)
    jobs = make_job_dictionary({UIField.MAX_FIGHTS: 2})
    assert process._run_bot_loop(device, jobs, logger)
    assert device.started == logger.stats["completed_fights"] == 2


def test_one_flickering_positive_does_not_stop(monkeypatch):
    samples = iter([True, False])
    monkeypatch.setattr(states, "check_if_battle_rewards_finished", lambda *args: next(samples))
    monkeypatch.setattr(states.time, "sleep", lambda *args: None)
    monkeypatch.setattr(states, "check_if_battle_mode_is_selected", lambda *args: True)
    jobs = make_job_dictionary({UIField.STOP_AFTER_REWARDS: True})
    assert states.state_tree(None, Logger(), "select_battle_mode", jobs, states.RuntimeContext()) == "randomize_deck"


def test_shop_only_ignores_reward_stop(monkeypatch):
    def no_check(*args):
        pytest.fail("Shop-only jobs must not inspect battle rewards")

    monkeypatch.setattr(states, "check_if_battle_rewards_finished", no_check)
    jobs = make_job_dictionary(
        {
            UIField.CLASSIC_1V1_USER_TOGGLE: False,
            UIField.SHOP_DAILY_OFFER_USER_TOGGLE: True,
            UIField.STOP_AFTER_REWARDS: True,
        }
    )
    assert states.state_tree(None, Logger(), "select_battle_mode", jobs, states.RuntimeContext()) == "idle"


def test_worker_cleanup_preserves_reward_stop_reason(gameplay, monkeypatch):
    process, device, logger = gameplay
    process.jobs = make_job_dictionary({UIField.STOP_AFTER_REWARDS: True})
    process.stats_queue, process.session_log_path = None, "unused"
    monkeypatch.setattr(process, "_setup_emulator", lambda *args: device)
    monkeypatch.setattr(worker, "attach_worker_file_logging", lambda *args: None)
    monkeypatch.setattr(worker, "ProcessLogger", lambda *args: logger)
    process.run()
    assert device.started == 0
    assert "奖励已领完" in logger.current_status
