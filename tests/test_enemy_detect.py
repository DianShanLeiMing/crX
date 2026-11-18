from types import SimpleNamespace
from unittest.mock import Mock

import cv2
import numpy as np

from crX.bot import enemy_detect, fight
from crX.bot.coords import BATTLE_HAND_BORDER_SAMPLES, GAME_SCREEN_SIZE
from crX.bot.find import find_enemy_unit_markers
from crX.bot.state_detect import ELIXIR_COLOR, ELIXIR_COORDS
from crX.bot.tower_health import TowerHealthMonitor
from crX.utils.logger import Logger

DARK_RED = (36, 10, 112)
RED = (94, 38, 226)
WHITE = (248, 248, 248)
YELLOW = (27, 210, 249)


def battle_frame():
    frame = np.full((*GAME_SCREEN_SIZE[::-1], 3), (165, 169, 172), dtype=np.uint8)
    for x, y, color in BATTLE_HAND_BORDER_SAMPLES:
        frame[y, x] = color
    return frame


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

    monitor.observe(next_frame)
    assert [marker.center for marker in monitor.markers] == [(116, 450)]
    logger.log_recognition.assert_called_once_with("敌方单位", "1 个 | (116, 450)", persist=True)

    monitor.observe(next_frame)
    assert monitor.markers == ()
    logger.log_recognition.assert_called_with("敌方单位", "当前无已确认标记", persist=True)


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
