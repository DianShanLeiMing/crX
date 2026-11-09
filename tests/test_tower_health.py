from types import SimpleNamespace

import numpy as np

from crX.bot import fight, tower_health
from crX.bot.coords import BATTLE_HAND_BORDER_SAMPLES, ENEMY_PRINCESS_BAR_ANCHORS, ENEMY_PRINCESS_BAR_WIDTH
from crX.bot.state_detect import ELIXIR_COLOR, ELIXIR_COORDS, read_enemy_princess_bars
from crX.utils.logger import Logger


def battle_frame(left, right, *, vertical_offset=0):
    image = np.zeros((1280, 720, 3), dtype=np.uint8)
    for x, y, color in BATTLE_HAND_BORDER_SAMPLES:
        image[y, x] = color
    for (x, base_y), fill in zip(ENEMY_PRINCESS_BAR_ANCHORS, (left, right), strict=True):
        if fill is None:
            continue
        y = base_y + vertical_offset
        end = x + ENEMY_PRINCESS_BAR_WIDTH
        image[y - 5 : y, x:end] = (73, 50, 93)
        image[y - 5 : y, x : x + fill] = (94, 38, 226)
        image[y, x:end] = (35, 0, 117)
    return image


def test_measures_fill_under_tower_numbers_and_ignores_blue_and_short_bars():
    image = battle_frame(77, 2)
    image[132:143, 146:199] = 240
    assert read_enemy_princess_bars(image) == ((77, True), (2, True))

    x, y = ENEMY_PRINCESS_BAR_ANCHORS[1]
    image[y - 5 : y, x : x + ENEMY_PRINCESS_BAR_WIDTH] = (226, 100, 38)
    assert read_enemy_princess_bars(image)[1] == (None, True)

    image = battle_frame(77, None)
    image[y - 5 : y, x : x + 28] = (94, 38, 226)
    image[y, x : x + 28] = (35, 0, 117)
    assert read_enemy_princess_bars(image)[1] == (None, False)


def test_measures_lower_tower_bar_without_accepting_a_short_enemy_unit_bar():
    image = battle_frame(None, 33, vertical_offset=34)
    x, y = ENEMY_PRINCESS_BAR_ANCHORS[0]
    y += 29
    image[y - 5 : y, x : x + 52] = (94, 38, 226)
    image[y, x : x + 52] = (35, 0, 117)
    assert read_enemy_princess_bars(image) == ((None, False), (33, True))


def test_occlusion_is_unknown_and_suspicion_requires_low_fill_and_repeated_absence(monkeypatch):
    messages = []
    logger = Logger()
    monkeypatch.setattr(logger, "log", messages.append)
    monitor = tower_health.TowerHealthMonitor(logger)
    clock = iter(range(9))
    monkeypatch.setattr(tower_health, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    low = battle_frame(70, 10)
    missing = battle_frame(70, None)
    unreadable = battle_frame(70, 0)
    for image in (low, missing, unreadable, missing, missing):
        monitor.observe(image)
    assert "疑似已毁" not in messages[-1]
    monitor.observe(missing)
    assert "右 疑似已毁" in messages[-1]
    monitor.observe(low)
    assert "右 存在" in messages[-1] and "右侧比例更低" in messages[-1]


def test_elixir_wait_logs_from_its_existing_screenshot(monkeypatch):
    messages = []
    image = battle_frame(78, 47)
    y, x = ELIXIR_COORDS[3]
    image[y, x] = ELIXIR_COLOR
    calls = []

    def screenshot():
        calls.append(True)
        return image

    logger = Logger()
    monkeypatch.setattr(logger, "log", messages.append)
    monitor = tower_health.TowerHealthMonitor(logger)
    assert fight.wait_for_elixir(SimpleNamespace(screenshot=screenshot), logger, 4, tower_monitor=monitor) is True
    assert len(calls) == 1
    assert messages == ["敌方公主塔 | 左 存在 99%（78px） | 右 存在 59%（47px） | 右侧比例更低"]
