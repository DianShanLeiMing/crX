"""A battle that ends normally must still retain sustained no-play evidence."""

from functools import partial
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from crX.bot import fight, worker
from crX.bot.card_detection import HandSlotObservation
from crX.utils import failure_samples as failures
from crX.utils.logger import Logger
from tests.test_enemy_detect import battle_frame, mock_decision_hand


@pytest.mark.parametrize("random_mode", [False, True])
@pytest.mark.parametrize("cause", ["unknown_costs", "no_options", "no_available_slots"])
def test_normal_battle_end_keeps_one_exact_frame_stall_sample(tmp_path, monkeypatch, random_mode, cause):
    frame = battle_frame()
    ids = ("goblin_giant", "golem", "skeleton_barrel", "lava_hound")
    if cause == "no_options":
        ids = ("log", "poison", "arrows", "fireball")
    if cause == "unknown_costs":
        monkeypatch.setattr(fight, "get_card_elixir_cost", lambda _: None)
    slots = () if cause == "no_available_slots" else (0, 1, 2, 3)
    mock_decision_hand(monkeypatch, frame, slots=slots)
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda _e, _l, slot, _t: (ids[slot], (198, 672)))
    monkeypatch.setattr(
        fight,
        "observe_hand_slots",
        lambda _frame, **_kwargs: tuple(
            HandSlotObservation(slot, card, slot in slots) for slot, card in enumerate(ids)
        ),
    )
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda _: None)
    monkeypatch.setattr(fight, "stop_fight_capture", lambda: None)
    monkeypatch.setattr(fight.time, "sleep", lambda _: None)
    clock = [0.0]
    monkeypatch.setattr(fight.time, "monotonic", lambda: clock[0])
    moments = iter((0, 5, 10, 15, 20))

    def wait(*_args, **kwargs):
        clock[0] = next(moments)
        if clock[0] == 20:
            return "no battle"
        frame[0, 0] = int(clock[0])
        kwargs["on_decision_frame"](frame)
        return True

    monkeypatch.setattr(fight, "wait_for_elixir", wait)
    monkeypatch.setattr(failures, "get_failure_samples_dir", lambda: tmp_path)
    logger = Logger()
    statuses = Mock(wraps=logger.change_status)
    monkeypatch.setattr(logger, "change_status", statuses)
    failures.remember_failure_frame(logger, frame, reason="existing final failure")
    previous = logger.failure_frame
    logger.battle_play_stall_handler = partial(
        failures.record_battle_play_stall, logger, session_log_path="session.txt"
    )
    emulator = Mock()
    assert fight._fight_loop(emulator, logger, False, random_mode=random_mode)
    messages = [call.args[0] for call in statuses.call_args_list]
    assert any(message.startswith("暂缓出牌：") for message in messages)
    assert not any(message.startswith("本次出牌耗时") for message in messages)
    emulator.click.assert_not_called()
    records, errors = failures.scan_failures(tmp_path)
    assert errors == 0 and len(records) == 1
    data = records[0]["data"]
    assert data["state"] == "fight" and data["recovery_attempt"] == 0
    assert data["reason"] == "持续满费但未能出牌"
    assert data["details"]["stage"] == "battle_play_stall"
    assert data["details"]["elixir"] == 10 and data["details"]["wait_s"] == 10
    assert [card["card_id"] for card in data["details"]["hand"]] == list(ids)
    if cause == "unknown_costs":
        assert all(card["cost"] is None for card in data["details"]["hand"])
        assert data["details"]["decision"]["wait_reason"] == "unknown_cost"
    assert [card["available"] for card in data["details"]["hand"]] == [slot in slots for slot in range(4)]
    assert data["session_log_path"] == "session.txt" and data["capture_source"] == "failure_frame"
    evidence = cv2.imread(str(records[0]["directory"] / "frame.png"))
    assert evidence.shape == (1280, 720, 3)
    expected = frame.copy()
    expected[0, 0] = 10
    np.testing.assert_array_equal(evidence, expected)
    assert logger.failure_frame is previous
    assert logger.stats["cards_played"] == logger.stats["restarts_after_failure"] == 0


def test_full_elixir_wait_is_continuous_and_resets_after_a_play(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(fight.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fight.time, "sleep", lambda _: None)
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda _: None)
    monkeypatch.setattr(fight, "stop_fight_capture", lambda: None)
    monkeypatch.setattr(fight, "read_hand_slot_for_settlement", lambda *_: None)
    elixir = [10]
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: elixir[0])
    # The drop at 9s interrupts the first wait. A play at 26s rearms collection.
    steps = iter(((0, 10), (9, 9), (10, 10), (19, 10), (20, 10), (25, 10), (26, 10), (30, 10), (40, 10)))
    frame = battle_frame()

    def wait(*_args, **kwargs):
        step = next(steps, None)
        if step is None:
            return "no battle"
        clock[0], elixir[0] = step
        kwargs["on_decision_frame"](frame)
        return True

    monkeypatch.setattr(fight, "wait_for_elixir", wait)
    monkeypatch.setattr(fight, "play_a_card", lambda *_args, **_kwargs: clock[0] == 26)
    logger = Logger()
    reports = []
    logger.battle_play_stall_handler = lambda *args, **kwargs: reports.append((clock[0], kwargs))
    assert fight._fight_loop(Mock(), logger, False)
    assert [moment for moment, _ in reports] == [20, 40]
    assert all(details["wait_s"] == 10 for _, details in reports)


@pytest.mark.parametrize("frame", [None, np.zeros((1280, 720, 3), dtype=np.uint8)])
def test_missing_battle_frame_cannot_be_reported_as_a_stalled_hand(monkeypatch, frame):
    logger = Logger()
    logger.battle_play_stall_handler = Mock()
    watch = {"since": 0.0}
    monkeypatch.setattr(fight.time, "monotonic", lambda: 100.0)
    fight._report_battle_play_wait(logger, frame, watch)
    logger.battle_play_stall_handler.assert_not_called()
    assert "since" not in watch


def test_diagnostic_failure_does_not_break_or_repeat_the_battle(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr(fight.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: 10)
    monkeypatch.setattr(fight, "read_hand_slot_for_settlement", lambda *_: None)
    logger = Logger()
    logger.battle_play_stall_handler = Mock(side_effect=OSError("disk full"))
    frame, watch = battle_frame(), {}
    for moment in (0, 10, 20):
        clock[0] = moment
        fight._report_battle_play_wait(logger, frame, watch)
    logger.battle_play_stall_handler.assert_called_once()
    assert not logger.errored


def test_worker_wires_battle_stall_into_existing_failure_collection(tmp_path, monkeypatch):
    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.jobs, process.stats_queue, process.session_log_path = {}, None, "session.txt"
    monkeypatch.setattr(worker, "attach_worker_file_logging", lambda _: None)
    monkeypatch.setattr(worker, "finish_card_samples", lambda: None)
    monkeypatch.setattr(worker, "finish_fight_recording", lambda _: None)
    monkeypatch.setattr(failures, "get_failure_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(process, "_setup_emulator", lambda *_: object())

    def run(_emulator, _jobs, logger):
        assert "battle_play_stall_handler" not in logger.get_stats()
        logger.battle_play_stall_handler(battle_frame(), wait_s=10, elixir=10, hand=[])
        return True

    monkeypatch.setattr(process, "_run_bot_loop", run)
    process.run()
    records, errors = failures.scan_failures(tmp_path)
    assert errors == 0 and len(records) == 1
    assert records[0]["data"]["state"] == "fight"
    assert records[0]["data"]["session_log_path"] == "session.txt"
