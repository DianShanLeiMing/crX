"""Native four-slot perception, planning-only memory and current-frame execution."""

from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from crX.bot import card_detection as detection
from crX.bot import card_samples, fight
from crX.bot.battle_strategy import BattlePlayState, HandCard, PlayOption, rank_hand_plays
from crX.bot.card_detection import HandSlotObservation
from crX.bot.coords import HAND_CARD_TOP_LEFTS, HAND_EMPTY_SLOT_SAMPLES
from crX.bot.enemy_detect import EnemyUnitLocation, EnemyUnitSnapshot
from crX.bot.state_detect import image_has_grayscale_hand_slot
from crX.utils.logger import Logger

FIXTURES = Path(__file__).parent / "fixtures"
# Original 720x1280 BGR frames 3 and 7 from the Nov 24 passive FFV1 recording.
FULL_TOWERS = ((79, True), (79, True))
IDS = ("lava_hound", "skeleton_barrel", "golem", "goblin_giant")


def native_hand(gray=False):
    filename = "hand-gray-native-20261124.png" if gray else "hand-four-slots-native-20261124.png"
    frame = cv2.imread(str(FIXTURES / filename))
    assert frame is not None and frame.shape == (1280, 720, 3)
    return frame


@pytest.mark.parametrize("available", [(0, 1, 2, 3), ()])
def test_native_identities_are_independent_of_available_slot_list(available):
    frame = native_hand()
    before = frame.copy()
    observations = detection.observe_hand_slots(frame, available_slots=available)
    assert tuple(slot.slot for slot in observations) == (0, 1, 2, 3)
    assert tuple(slot.card_id for slot in observations) == IDS
    assert tuple(slot.available for slot in observations) == tuple(slot in available for slot in range(4))
    assert not any(slot.empty for slot in observations)
    np.testing.assert_array_equal(frame, before)


def test_native_gray_art_is_unknown_even_if_a_color_reference_would_accept_it(monkeypatch):
    frame = native_hand(gray=True)
    matcher = Mock(side_effect=AssertionError("Color matching gray artwork is unsafe"))
    monkeypatch.setattr(detection, "_recognize_hand_slot", matcher)
    observations = detection.observe_hand_slots(frame)
    assert len(observations) == 4
    assert all(image_has_grayscale_hand_slot(frame, slot) for slot in range(4))
    assert all(slot.card_id is None and not slot.available and slot.reason == "grayscale" for slot in observations)
    matcher.assert_not_called()


def test_empty_and_low_margin_positions_are_not_card_identities(monkeypatch):
    frame = native_hand()
    left, top = HAND_CARD_TOP_LEFTS[2]
    for dx, dy, color in HAND_EMPTY_SLOT_SAMPLES:
        frame[top + dy, left + dx] = color
    monkeypatch.setattr(
        detection, "_recognize_hand_slot", lambda *_: ([], {"card_id": "knight", "candidate_margin": 99})
    )
    observations = detection.observe_hand_slots(frame, available_slots=(0, 1, 2, 3))
    assert observations[2].empty and not observations[2].available and observations[2].card_id is None
    assert all(observations[slot].card_id is None and observations[slot].reason == "low_margin" for slot in (0, 1, 3))


@pytest.mark.parametrize("frame", [None, np.zeros((640, 360, 3), dtype=np.uint8)])
def test_wrong_geometry_cannot_create_hand_observations(frame):
    assert detection.observe_hand_slots(frame) == ()


def test_unavailable_observation_is_not_queued_for_card_identity_review(tmp_path, monkeypatch):
    collector = card_samples.start_card_samples("Classic 1v1", tmp_path)
    monkeypatch.setattr(collector, "_flush_match_stats", lambda **_: None)
    frame = native_hand(gray=True)
    try:
        # A stale purple HUD probe cannot make desaturated artwork usable.
        monkeypatch.setattr(detection, "_card_purple_counts", lambda _: [104] * 4)
        assert detection.check_which_cards_are_available(None, frame=frame) == []
        assert detection.identify_hand_cards(None, 0) == "UNKNOWN"
        assert collector.stats["queued"] == 0
        assert all(not slot.available for slot in detection.observe_hand_slots(frame, available_slots=(0, 1, 2, 3)))
        assert not collector.offer(frame, 0, {"reason": "unknown", "available": True})
        assert not collector.offer(frame, None, {"reason": "unavailable_hand"})
    finally:
        card_samples.finish_card_samples()
    assert collector.stats["saved"] == 0 and not collector.directory.exists()


@pytest.mark.parametrize("random_mode", [False, True])
def test_color_identity_without_ready_slot_can_save_but_only_a_ready_current_match_can_tap(monkeypatch, random_mode):
    frame, ready, amount = native_hand(), [False], [7]
    monkeypatch.setattr(detection, "_card_purple_counts", lambda _: [104 if ready[0] else 0] * 4)
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: amount[0])
    monkeypatch.setattr(fight.time, "sleep", lambda _: None)
    monkeypatch.setattr(fight, "read_enemy_princess_bars", lambda _: FULL_TOWERS)
    monkeypatch.setattr(detection, "switch_side", lambda: (0, "left"))
    monitor = Mock()
    monitor.snapshot = EnemyUnitSnapshot(100, ())
    state = BattlePlayState()
    state.hand_waits[2] = ("golem", 3)
    emulator = Mock()
    logger = Logger()

    def play():
        if random_mode:
            return fight.play_random_available_card(
                emulator, logger, False, 0, decision_frame=frame, play_state=state, enemy_monitor=monitor
            )
        return fight.play_a_card(
            emulator,
            logger,
            False,
            fight.BattleStrategy(),
            decision_frame=frame,
            play_state=state,
            enemy_monitor=monitor,
        )

    assert not play()
    assert len(state.known_hand) == 4 and len(state.hand_observations) == 4
    assert state.advance_target is not None and state.advance_target.card_id == "golem"
    assert state.wait_reason == "saving" and state.advance_elixir() == 10
    emulator.click.assert_not_called()
    amount[0] = 10
    assert not play()
    emulator.click.assert_not_called()
    ready[0] = True
    assert play()
    assert emulator.click.call_args_list[0].args == fight.HAND_CARDS_COORDS[2]
    assert state.elixir_remaining == 2 and 2 not in state.known_hand


def test_gray_unknown_does_not_overwrite_memory_and_empty_or_replacement_clears_old_identity():
    heavy = HandCard(2, "golem", "king_lane", 8, (198, 672))
    state = BattlePlayState()
    state.observe_hand((heavy,))
    state.hand_waits[2] = ("golem", 3)
    state.observe_slots(detection.observe_hand_slots(native_hand(gray=True)))
    assert state.known_hand[2] == heavy and state.waiting_bonus(heavy) == 3
    assert rank_hand_plays((), EnemyUnitSnapshot(10, ()), FULL_TOWERS, 7, play_state=state) == []
    assert state.advance_target == heavy
    state.record_play(PlayOption(heavy, (198, 672), "advance"), 10, 10)
    state.observe_slots((HandSlotObservation(2, "golem", True),))
    assert 2 in state.awaiting_replacement and 2 not in state.known_hand
    state.observe_slots((HandSlotObservation(2, None, False, True, "empty"),))
    assert 2 not in state.awaiting_replacement and state.advance_target is None
    replacement = HandCard(2, "knight", "bridge_line", 3, (198, 672))
    state.observe_hand((replacement,))
    assert state.known_hand[2] == replacement and state.waiting_bonus(replacement) == 0


def test_stale_ready_art_after_a_tap_cannot_repopulate_the_played_slot(monkeypatch):
    frame = native_hand()
    monkeypatch.setattr(detection, "switch_side", lambda: (0, "left"))
    monkeypatch.setattr(detection, "_card_purple_counts", lambda _: [0, 0, 104, 0])
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: 10)
    monkeypatch.setattr(fight, "read_enemy_princess_bars", lambda _: FULL_TOWERS)
    monitor = Mock()
    monitor.snapshot = EnemyUnitSnapshot(100, ())
    now = [100.0]
    monkeypatch.setattr(fight.time, "monotonic", lambda: now[0])
    emulator, state = Mock(), BattlePlayState()
    assert fight.play_a_card(
        emulator, Logger(), False, fight.BattleStrategy(), decision_frame=frame, play_state=state, enemy_monitor=monitor
    )
    assert 2 in state.awaiting_replacement and 2 not in state.known_hand
    now[0] += 0.5  # The old artwork is not reused while the debit/hand transition is settling.
    assert not fight.play_a_card(
        emulator, Logger(), False, fight.BattleStrategy(), decision_frame=frame, play_state=state, enemy_monitor=monitor
    )
    assert emulator.click.call_count == 2 and 2 not in state.known_hand
    assert state.wait_reason == "hand_transition"
    now[0] += 2
    # A tap that never took effect does not lock the slot: a fresh ready match can retry.
    assert fight.play_a_card(
        emulator, Logger(), False, fight.BattleStrategy(), decision_frame=frame, play_state=state, enemy_monitor=monitor
    )
    assert emulator.click.call_count == 4


def test_wait_reasons_distinguish_defense_unknown_cost_and_unsupported_plays():
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    threat = EnemyUnitLocation((180, 620), "left", "friendly_half")
    state = BattlePlayState()
    first = rank_hand_plays((knight,), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 7, play_state=state)[0]
    state.record_play(first, 7, 10)
    assert rank_hand_plays((knight,), EnemyUnitSnapshot(10.3, (threat,)), FULL_TOWERS, 4, play_state=state) == []
    assert state.wait_reason == "defense_observation"
    state = BattlePlayState()
    assert (
        rank_hand_plays(
            (HandCard(0, "mirror", "center_spell", None, None),),
            EnemyUnitSnapshot(10, ()),
            FULL_TOWERS,
            10,
            play_state=state,
        )
        == []
    )
    assert state.wait_reason == "unknown_cost"
    state = BattlePlayState()
    assert (
        rank_hand_plays(
            (HandCard(0, "log", "lane_spell", 2, (202, 374)),),
            EnemyUnitSnapshot(10, ()),
            FULL_TOWERS,
            10,
            play_state=state,
        )
        == []
    )
    assert state.wait_reason == "no_suitable_play"


def test_stall_diagnostic_distinguishes_current_gray_slots_from_planning_memory(monkeypatch):
    frame = native_hand(gray=True)
    state = BattlePlayState()
    state.observe_hand((HandCard(2, "golem", "king_lane", 8, (198, 672)),))
    state.wait_reason = "uncertain_hand"
    clock = [0.0]
    monkeypatch.setattr(fight.time, "monotonic", lambda: clock[0])
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: 10)
    logger, watch = Logger(), {}
    logger.battle_play_stall_handler = Mock()
    fight._report_battle_play_wait(logger, frame, watch, state)
    clock[0] = 10
    fight._report_battle_play_wait(logger, frame, watch, state)
    details = logger.battle_play_stall_handler.call_args.kwargs
    assert len(details["hand"]) == 4 and all(slot["card_id"] is None for slot in details["hand"])
    assert details["decision"]["wait_reason"] == "uncertain_hand"
    assert details["decision"]["known_hand"] == [{"slot": 2, "card_id": "golem", "cost": 8}]


def test_gray_unknown_is_not_mistaken_for_a_ready_card_recognition_failure():
    log = HandCard(0, "log", "lane_spell", 2, (202, 374))
    gray = [HandSlotObservation(slot, None, False, reason="grayscale") for slot in (1, 2, 3)]
    state = BattlePlayState()
    state.observe_slots((HandSlotObservation(0, "log", True), *gray))
    assert rank_hand_plays((log,), EnemyUnitSnapshot(10, ()), FULL_TOWERS, 10, play_state=state) == []
    assert state.wait_reason == "no_suitable_play"
    state.observe_slots((HandSlotObservation(0, None, True, reason="low_margin"), *gray))
    assert rank_hand_plays((), EnemyUnitSnapshot(11, ()), FULL_TOWERS, 10, play_state=state) == []
    assert state.wait_reason == "uncertain_hand"
    state.observe_slots(tuple(HandSlotObservation(slot, None, False, reason="grayscale") for slot in range(4)))
    assert rank_hand_plays((), EnemyUnitSnapshot(12, ()), FULL_TOWERS, 4, play_state=state) == []
    assert state.wait_reason == "insufficient_elixir"
