from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

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


@pytest.mark.parametrize(
    ("side", "label", "x", "filename"),
    [
        (0, "左", 132, "green_left_desktop_preview_20261110.png"),
        (1, "右", 502, "stone_right_720_native_20261108.png"),
        (0, "左", 125, "stone_right_720_native_20261108_mirrored.png"),
        (1, "右", 510, "green_left_desktop_preview_20261110_mirrored.png"),
    ],
)
def test_confirms_destroyed_from_repeated_ruins_without_prior_low_health(monkeypatch, side, label, x, filename):
    messages = []
    logger = Logger()
    monkeypatch.setattr(logger, "log", messages.append)
    clock = iter(range(7))
    monkeypatch.setattr(tower_health, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    template_dir = Path(__file__).resolve().parents[1] / "crX/detection/reference_images/princess_tower_ruins"
    template = cv2.imread(str(template_dir / filename))
    assert template is not None
    h, w = template.shape[:2]
    fills: list[int | None] = [65, 13]
    fills[side] = None
    missing = battle_frame(*fills)
    ruins = missing.copy()
    ruins[244 : 244 + h, x : x + w] = template
    monitor = tower_health.TowerHealthMonitor(logger)
    for image in (ruins, battle_frame(65, 13), ruins):
        monitor.observe(image)
        assert f"{label} 已摧毁" not in messages[-1]
    monitor.observe(ruins)
    assert f"{label} 已摧毁" in messages[-1]
    assert "暂无法比较" not in messages[-1]
    monitor.observe(missing)
    assert f"{label} 已摧毁" in messages[-1]
    monitor.observe(battle_frame(65, 13))
    assert f"{label} 已摧毁" in messages[-1]
    assert "比例更低" not in messages[-1]


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


@pytest.mark.parametrize("times", [(0, 3, 6), (0, 6, 14)])
def test_ruins_confirmation_survives_capture_gaps_and_occlusion(monkeypatch, times):
    messages = []
    logger = Logger()
    monkeypatch.setattr(logger, "log", messages.append)
    clock = iter(times)
    monkeypatch.setattr(tower_health, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    matches = iter((True, False, True))
    monkeypatch.setattr(tower_health, "image_has_enemy_princess_ruins", lambda *_: next(matches))
    monitor = tower_health.TowerHealthMonitor(logger)
    for _ in times:
        monitor.observe(battle_frame(None, 60))
    assert "左 已摧毁" in messages[-1]


@pytest.mark.parametrize("interruption", ["expired", "bar_returns", "not_in_battle"])
def test_ruins_evidence_expires_or_resets_on_contradicting_frames(monkeypatch, interruption):
    messages = []
    logger = Logger()
    monkeypatch.setattr(logger, "log", messages.append)
    clock = iter((0, 20, 40) if interruption == "expired" else (0, 3, 6))
    monkeypatch.setattr(tower_health, "time", SimpleNamespace(monotonic=lambda: next(clock)))
    monkeypatch.setattr(tower_health, "image_has_enemy_princess_ruins", lambda *_: True)
    monitor = tower_health.TowerHealthMonitor(logger)
    monitor.observe(battle_frame(None, 60))
    middle = battle_frame(60, 60) if interruption == "bar_returns" else battle_frame(None, 60)
    if interruption == "not_in_battle":
        middle[:] = 0
    monitor.observe(middle)
    monitor.observe(battle_frame(None, 60))
    assert "已摧毁" not in messages[-1]
