from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from crX.bot import enemy_detect, fight
from crX.bot.card_data import CIRCULAR_SPELL_PROFILES, CircularSpellProfile
from crX.bot.coords import BATTLE_HAND_BORDER_SAMPLES, GAME_SCREEN_SIZE
from crX.bot.find import find_enemy_unit_markers
from crX.bot.spell_targeting import choose_circular_spell_target
from crX.bot.state_detect import ELIXIR_COLOR, ELIXIR_COORDS
from crX.bot.tower_health import TowerHealthMonitor
from crX.utils.logger import Logger
from tests.test_tower_health import battle_frame as tower_battle_frame

DARK_RED = (36, 10, 112)
RED = (94, 38, 226)
WHITE = (248, 248, 248)
YELLOW = (27, 210, 249)


def battle_frame():
    frame = np.full((*GAME_SCREEN_SIZE[::-1], 3), (165, 169, 172), dtype=np.uint8)
    for x, y, color in BATTLE_HAND_BORDER_SAMPLES:
        frame[y, x] = color
    return frame


def mock_decision_hand(monkeypatch, initial_frame, slots=(0,), elixir=10):
    """Provide the cached decision frame and use the device for pre-cast revalidation."""
    first = True

    def available(emulator, *, check_side, observe_frame, frame=None):
        nonlocal first
        if frame is None:
            frame = initial_frame if first else emulator.screenshot()
        first = False
        observe_frame(frame)
        return list(slots)

    monkeypatch.setattr(fight, "check_which_cards_are_available", available)
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: elixir)


def draw_badge(frame, x, y, *, level_color=WHITE, bar_color=None, friendly=False):
    rim, fill = ((104, 42, 20), (236, 147, 29)) if friendly else (DARK_RED, RED)
    frame[y - 4 : y + 16, x - 4 : x + 15] = rim
    frame[y - 2 : y + 14, x - 2 : x + 13] = fill
    frame[y : y + 12, x : x + 4] = level_color
    frame[y : y + 12, x + 6 : x + 10] = level_color
    if bar_color is not None:
        frame[y + 2 : y + 11, x + 14 : x + 55] = rim
        frame[y + 3 : y + 10, x + 14 : x + 54] = bar_color


def test_finds_both_forms_with_white_and_yellow_digits_across_the_battlefield():
    frame = battle_frame()
    frame[270:800, 265:295] = RED
    draw_badge(frame, 280, 432, level_color=YELLOW)
    draw_badge(frame, 370, 720, bar_color=RED)

    # A single digit on a differently shaped badge remains one marker.
    cv2.fillConvexPoly(frame, np.array([(480, 523), (493, 536), (480, 549), (467, 536)]), DARK_RED)
    cv2.fillConvexPoly(frame, np.array([(480, 527), (489, 536), (480, 545), (471, 536)]), RED)
    frame[530:542, 478:482] = WHITE

    markers = find_enemy_unit_markers(frame)
    assert [(marker.center, marker.has_health_bar) for marker in markers] == [
        ((285, 438), False),
        ((375, 726), True),
        ((480, 536), False),
    ]


def test_ignores_princess_tower_hud_friendly_badge_and_red_artwork():
    frame = battle_frame()
    draw_badge(frame, 159, 136, bar_color=RED)
    draw_badge(frame, 525, 186, bar_color=RED)
    draw_badge(frame, 240, 660, bar_color=(236, 147, 29), friendly=True)
    frame[310:450, 350:415] = RED
    frame[375:386, 380:385] = WHITE
    assert find_enemy_unit_markers(frame) == ()
    assert find_enemy_unit_markers(frame[::2, ::2]) == ()


def test_confirms_level_only_on_nearby_frame_and_clears_after_observation_gap(monkeypatch):
    clock = iter((0.0, 0.1, 0.3, 2.0))
    monkeypatch.setattr(enemy_detect, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    logger = Mock(spec=Logger)
    monitor = enemy_detect.EnemyUnitMonitor(logger)
    first = battle_frame()
    next_frame = battle_frame()
    draw_badge(first, 100, 440)
    draw_badge(next_frame, 111, 444)

    monitor.observe(first)
    monitor.observe(next_frame)
    assert monitor.markers == ()
    assert monitor.snapshot == enemy_detect.EnemyUnitSnapshot(0.0, ())

    monitor.observe(next_frame)
    assert [marker.center for marker in monitor.markers] == [(116, 450)]
    logger.log_recognition.assert_called_once_with(
        "敌方单位", "1 个 | 左路 1（敌方半场 1） | 中路 0 | 右路 0 | (116, 450)", persist=True
    )

    monitor.observe(next_frame)
    assert monitor.markers == ()
    assert monitor.snapshot == enemy_detect.EnemyUnitSnapshot(2.0, ())
    logger.log_recognition.assert_called_with("敌方单位", "当前无已确认标记", persist=True)


def test_locations_follow_lane_and_river_boundaries_and_log_same_count_transitions(monkeypatch):
    clock = iter((10.0, 10.25, 10.5, 11.5, 11.75))
    monkeypatch.setattr(enemy_detect, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    logger = Mock(spec=Logger)
    monitor = enemy_detect.EnemyUnitMonitor(logger)

    for left, middle, right in (
        ((319, 479), (370, 510), (400, 700)),
        ((320, 480), (370, 575), (400, 700)),
        ((320, 480), (370, 576), (400, 700)),
        ((320, 490), (374, 577), (410, 703)),
    ):
        frame = battle_frame()
        for x, y in (left, middle, right):
            draw_badge(frame, x - 5, y - 6, bar_color=RED)
        monitor.observe(frame)

    assert monitor.snapshot == enemy_detect.EnemyUnitSnapshot(
        11.5,
        (
            enemy_detect.EnemyUnitLocation((320, 490), "center", "river"),
            enemy_detect.EnemyUnitLocation((374, 577), "center", "friendly_half"),
            enemy_detect.EnemyUnitLocation((410, 703), "right", "friendly_half"),
        ),
    )
    calls = logger.log_recognition.call_args_list
    assert [call.kwargs["persist"] for call in calls] == [True, True, True, False]
    assert "左路 1（敌方半场 1） | 中路 1（河道 1） | 右路 1（我方半场 1）" in calls[0].args[1]
    assert "中路 2（河道 2）" in calls[1].args[1]
    assert "中路 2（河道 1、我方半场 1）" in calls[2].args[1]

    monitor.observe(None)
    assert monitor.snapshot is None


def test_elixir_wait_shares_a_capture_with_both_monitors_and_accepts_white_bar():
    frame = battle_frame()
    draw_badge(frame, 180, 435, bar_color=WHITE)
    y, x = ELIXIR_COORDS[3]
    frame[y, x] = ELIXIR_COLOR
    screenshot = Mock(return_value=frame)
    logger = Mock(spec=Logger)
    monitor = enemy_detect.EnemyUnitMonitor(logger)

    assert fight.wait_for_elixir(
        SimpleNamespace(screenshot=screenshot),
        logger,
        4,
        tower_monitor=TowerHealthMonitor(logger),
        enemy_monitor=monitor,
    )
    screenshot.assert_called_once_with()
    assert monitor.markers[0].has_health_bar
    assert [call.args[0] for call in logger.log_recognition.call_args_list] == ["公主塔", "敌方单位"]


@pytest.mark.parametrize(("card_id", "in_battle"), [("rocket", True), ("rocket", False), ("knight", True)])
def test_selected_spell_refreshes_before_click_even_inside_sample_interval(monkeypatch, card_id, in_battle):
    clock = iter((10.0, 10.1))
    monkeypatch.setattr(enemy_detect, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(fight, "last_three_cards", [])
    mock_decision_hand(monkeypatch, battle_frame())
    placement = (198, 672)
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: (card_id, placement))
    logger = Mock(spec=Logger)
    monitor = enemy_detect.EnemyUnitMonitor(logger)
    emulator = Mock()
    emulator.screenshot.return_value = battle_frame() if in_battle else None

    assert fight.play_a_card(emulator, logger, False, fight.BattleStrategy(), enemy_monitor=monitor) is in_battle
    actions = [entry[0] for entry in emulator.mock_calls]
    if card_id == "knight":
        assert actions == ["click", "click"]
        assert monitor.snapshot is not None and monitor.snapshot.sampled_at == 10.0
    elif in_battle:
        assert actions == ["screenshot", "click", "click"]
        assert monitor.snapshot == enemy_detect.EnemyUnitSnapshot(10.1, ())
        assert emulator.click.call_args.args == placement
    else:
        assert actions == ["screenshot"]
        emulator.click.assert_not_called()


@pytest.mark.parametrize(
    ("badges", "expected_target", "covered"),
    [
        ([(180, 600), (260, 600)], (225, 638), 2),
        ([(540, 500)], (545, 538), 1),
        ([(540, 320)], None, 0),
        ([(145, 245), (180, 267)], None, 0),
        ([(640, 600)], None, 0),
        ([], None, 0),
    ],
)
def test_arrows_aim_from_fresh_frame_or_hold(monkeypatch, badges, expected_target, covered):
    preset = (204, 292)
    monkeypatch.setattr(fight, "last_three_cards", [])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: ("arrows", preset))
    record = Mock()
    monkeypatch.setattr(fight, "log_play", record)
    logger = Mock(spec=Logger)
    monitor = enemy_detect.EnemyUnitMonitor(logger)
    old_frame = battle_frame()
    draw_badge(old_frame, 500, 700, bar_color=RED)
    monitor.observe(old_frame)
    frame = battle_frame()
    for x, y in badges:
        draw_badge(frame, x, y, bar_color=RED)
    emulator = Mock()
    mock_decision_hand(monkeypatch, frame)
    emulator.screenshot.return_value = frame

    assert fight.play_a_card(emulator, logger, True, fight.BattleStrategy(), enemy_monitor=monitor) == (
        expected_target is not None
    )
    if expected_target is None:
        emulator.screenshot.assert_not_called()
        emulator.click.assert_not_called()
        record.assert_not_called()
        return
    emulator.screenshot.assert_called_once_with()
    assert emulator.click.call_args.args == expected_target
    assert record.call_args.args[:3] == (0, *expected_target)
    assert any(
        f"{expected_target} | 防守，实时落点，估算覆盖 {covered} 个标记" in call.args[0]
        for call in logger.change_status.call_args_list
    )


def test_another_circular_spell_can_enable_targeting_through_its_profile(monkeypatch):
    monkeypatch.setitem(CIRCULAR_SPELL_PROFILES, "fireball", CircularSpellProfile(radius_px=36, marker_offset_y_px=20))
    monkeypatch.setattr(fight, "last_three_cards", [])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: ("fireball", (204, 292)))
    frame = battle_frame()
    draw_badge(frame, 180, 600, bar_color=RED)
    draw_badge(frame, 260, 600, bar_color=RED)
    emulator = Mock()
    mock_decision_hand(monkeypatch, frame)
    emulator.screenshot.return_value = frame
    logger = Mock(spec=Logger)

    assert fight.play_a_card(
        emulator, logger, False, fight.BattleStrategy(), enemy_monitor=enemy_detect.EnemyUnitMonitor(logger)
    )
    assert emulator.click.call_args.args == (185, 626)


@pytest.mark.parametrize(
    ("min_targets", "min_defensive_targets", "expected"),
    [
        (2, 1, ((292, 342), 2)),
        (3, 1, ((550, 712), 1)),
        (3, 2, None),
        (3, None, None),
    ],
)
def test_spell_coverage_thresholds_filter_candidates_before_ranking(min_targets, min_defensive_targets, expected):
    snapshot = enemy_detect.EnemyUnitSnapshot(
        10.0,
        (
            enemy_detect.EnemyUnitLocation((270, 310), "left", "enemy_half"),
            enemy_detect.EnemyUnitLocation((315, 310), "left", "enemy_half"),
            enemy_detect.EnemyUnitLocation((550, 680), "right", "friendly_half"),
        ),
    )
    profile = CircularSpellProfile(radius_px=36, min_targets=min_targets, min_defensive_targets=min_defensive_targets)
    assert choose_circular_spell_target(snapshot, profile) == expected


@pytest.mark.parametrize(
    ("card_id", "previous_badges", "current_badges", "elapsed", "target"),
    [
        ("fireball", [(180, 600)], [(183, 601)], 0.4, (188, 639)),
        ("fireball", [(180, 600)], [(180, 615)], 0.4, None),
        ("fireball", [(180, 600)], [(180, 600)], 1.2, None),
        ("fireball", [(180, 600), (199, 620)], [(185, 605), (198, 619)], 0.6, None),
        ("poison", [(180, 600)], [(180, 600)], 0.4, None),
        ("poison", [(180, 600), (260, 600)], [(180, 600), (260, 600)], 0.4, (225, 638)),
        ("zap", [(180, 600)], [(180, 620)], 0.4, (185, 658)),
        ("evo_zap", [], [(180, 620)], 0.4, (185, 658)),
        ("log", [], [(180, 620)], 0.4, None),
    ],
)
def test_spell_profiles_use_appropriate_targets_without_extra_capture(
    monkeypatch, card_id, previous_badges, current_badges, elapsed, target
):
    previous, current = battle_frame(), battle_frame()
    for frame, badges in ((previous, previous_badges), (current, current_badges)):
        for x, y in badges:
            draw_badge(frame, x, y, bar_color=RED)

    clock = iter((10.0, 10.0 + elapsed))
    monkeypatch.setattr(enemy_detect, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(fight, "last_three_cards", [])
    mock_decision_hand(monkeypatch, previous if previous_badges else current)
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: (card_id, (202, 374)))
    emulator = Mock()
    emulator.screenshot.return_value = current
    logger = Mock(spec=Logger)

    played = fight.play_a_card(
        emulator, logger, False, fight.BattleStrategy(), enemy_monitor=enemy_detect.EnemyUnitMonitor(logger)
    )
    assert emulator.screenshot.call_count <= 1
    assert played == (target is not None)
    if target is None:
        emulator.click.assert_not_called()
    else:
        assert emulator.click.call_args.args == target


@pytest.mark.parametrize(
    ("card_id", "fills", "badge", "target"),
    [
        ("rocket", (79, 79), False, (218, 326)),
        ("earthquake", (79, 79), False, (204, 292)),
        ("arrows", (8, 79), False, None),
        ("arrows", (7, 79), False, (172, 260)),
        ("fireball", (None, 5), False, (548, 260)),
        ("zap", (6, 2), False, (548, 260)),
        ("poison", (None, None), False, None),
        ("clone", (2, 2), False, None),
        ("arrows", (2, 79), True, (185, 658)),
    ],
)
def test_spell_tower_finisher_requires_fresh_readable_low_health(monkeypatch, card_id, fills, badge, target):
    frame = tower_battle_frame(*fills)
    if badge:
        draw_badge(frame, 180, 620, bar_color=RED)
    monkeypatch.setattr(fight, "last_three_cards", [])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: (card_id, (204, 292)))
    emulator = Mock()
    mock_decision_hand(monkeypatch, frame)
    emulator.screenshot.return_value = frame
    logger = Mock(spec=Logger)

    played = fight.play_a_card(
        emulator, logger, False, fight.BattleStrategy(), enemy_monitor=enemy_detect.EnemyUnitMonitor(logger)
    )
    assert played == (target is not None)
    if target is None:
        emulator.click.assert_not_called()
    else:
        assert emulator.click.call_args.args == target


def test_held_spells_try_another_hand_slot_without_tapping_or_resampling(monkeypatch):
    monkeypatch.setattr(fight, "last_three_cards", [])
    monkeypatch.setattr(fight.random, "choice", lambda candidates: candidates[0])
    cards = [("arrows", (204, 292)), ("fireball", (202, 374)), ("knight", (238, 680))]
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda _emu, _log, slot, _elapsed: cards[slot])
    emulator = Mock()
    mock_decision_hand(monkeypatch, tower_battle_frame(None, None), slots=(0, 1, 2))
    logger = Mock(spec=Logger)

    assert fight.play_a_card(
        emulator, logger, False, fight.BattleStrategy(), enemy_monitor=enemy_detect.EnemyUnitMonitor(logger)
    )
    emulator.screenshot.assert_not_called()
    assert [call.args for call in emulator.click.call_args_list] == [fight.HAND_CARDS_COORDS[2], (238, 680)]
    assert fight.last_three_cards == [2]


@pytest.mark.parametrize("mode", ["Classic 1v1", "Classic 2v2", "Trophy Road"])
@pytest.mark.parametrize("random_mode", [False, True])
@pytest.mark.parametrize("has_target", [False, True])
def test_spell_rules_apply_in_every_battle_mode(monkeypatch, mode, random_mode, has_target):
    frame = tower_battle_frame(79, 79)
    y, x = ELIXIR_COORDS[fight.ADVANCE_ELIXIR - 1]
    frame[y, x] = ELIXIR_COLOR
    if has_target:
        draw_badge(frame, 180, 620, bar_color=RED)
    monkeypatch.setattr(fight, "image_is_battle_over", lambda image: image is None)
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda *_: None)
    monkeypatch.setattr(fight, "last_three_cards", [])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: ("arrows", (204, 292)))
    monkeypatch.setattr(fight.time, "sleep", lambda *_: None)
    emulator = Mock()
    mock_decision_hand(monkeypatch, frame)
    emulator.screenshot.side_effect = [frame, frame, None] if has_target else [frame, None]
    logger = Logger()

    run = fight._random_fight_loop if random_mode else fight._fight_loop
    assert run(emulator, logger, False, mode)
    assert emulator.screenshot.call_count == 2 + int(has_target)
    if has_target:
        assert [call.args for call in emulator.click.call_args_list] == [fight.HAND_CARDS_COORDS[0], (185, 658)]
    else:
        emulator.click.assert_not_called()
    assert logger.get_cards_played() == int(has_target)
