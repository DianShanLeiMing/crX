"""Real 9:16 captures supplement the explicitly scaled historical replay fixtures."""

import subprocess
from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
import pytest

from crX.bot import card_detection as cards
from crX.bot.coords import GAME_SCREEN_DPI, GAME_SCREEN_SIZE
from crX.bot.find import (
    find_battle_outcome,
    find_collected_shop_offer,
    find_daily_shop_heading,
    find_post_battle_button_in_image,
)
from crX.bot.recorder import FRAME_H, FRAME_W
from crX.bot.state_detect import (
    check_if_battle_mode_is_selected,
    check_if_battle_rewards_finished,
    check_if_in_battle,
    check_if_on_card_page,
    check_if_on_shop,
    image_has_elixir,
    is_clash_main_menu_image,
    is_deck_full,
)
from crX.emulators.adb import AdbController
from crX.utils.card_review import export_candidate, save_review, scan_samples
from tests.test_card_review import make_sample

FIXTURES = Path(__file__).parent / "fixtures"


class Screen:
    def __init__(self, filename):
        self.image = cv2.imread(str(FIXTURES / filename))
        assert self.image is not None

    def screenshot(self):
        return self.image


def test_device_config_and_recording_share_native_geometry(monkeypatch):
    device = AdbController(Mock(), "127.0.0.1:16416")
    commands = []
    monkeypatch.setattr(
        device, "adb", lambda command: commands.append(command) or subprocess.CompletedProcess(command, 0)
    )
    monkeypatch.setattr("crX.emulators.adb.time.sleep", lambda _: None)
    device.configure()
    assert GAME_SCREEN_SIZE == (360, 640) == (FRAME_W, FRAME_H)
    assert GAME_SCREEN_DPI == 160
    assert commands == [["shell", "wm", "size", "360x640"], ["shell", "wm", "density", "160"]]


def test_native_main_and_empty_reward_survive_new_aspect_ratio():
    screen = Screen("main-360-native.png")
    assert is_clash_main_menu_image(screen.image)
    assert check_if_battle_rewards_finished(screen)
    screen.image = (screen.image * 0.5).astype(np.uint8)
    assert not is_clash_main_menu_image(screen.image)
    assert not check_if_battle_rewards_finished(screen)


def test_native_hand_availability_identities_and_full_elixir():
    screen = Screen("hand-360-native.png")
    assert check_if_in_battle(screen)
    assert cards.check_which_cards_are_available(screen) == [0, 1, 2, 3]
    assert [cards.identify_hand_cards(screen, slot) for slot in range(4)] == [
        "bats",
        "cannon",
        "barb_barrel",
        "rune_giant",
    ]
    assert all(image_has_elixir(screen.image, amount) for amount in range(1, 11))
    screen.image[610:] = 0
    assert not any(image_has_elixir(screen.image, amount) for amount in range(1, 11))


@pytest.mark.parametrize("filename", ["hand-360-cooldown-native.png", "hand-360-glow-native.png"])
def test_hand_cooldown_and_card_glow_do_not_end_the_battle(filename):
    screen = Screen(filename)
    assert check_if_in_battle(screen)
    screen.image = (screen.image * 0.4).astype(np.uint8)
    assert not check_if_in_battle(screen)


def test_native_training_result_button_and_non_battle_frames():
    screen = Screen("training-result-360-native.png")
    point = find_post_battle_button_in_image(screen.image)
    assert point is not None and 135 < point[0] < 225 and 560 < point[1] < 605
    for filename in (
        "training-result-360-native.png",
        "main-360-native.png",
        "shop-360-native.png",
        "cardpage-360-native.png",
    ):
        assert not check_if_in_battle(Screen(filename)), filename


def test_native_classic_result_reports_loss_before_confirmation():
    screen = Screen("battle-loss-360-native.png")
    assert find_battle_outcome(screen) == "loss"
    assert find_post_battle_button_in_image(screen.image) is not None
    assert not check_if_in_battle(screen)


@pytest.mark.parametrize("tag,mode", [("trophy", "Trophy Road"), ("1v1", "Classic 1v1"), ("2v2", "Classic 2v2")])
def test_native_selected_mode_icons_are_distinct(tag, mode):
    screen = Screen(f"selected-{tag}-360-native.png")
    assert check_if_battle_mode_is_selected(screen, mode)
    for other in ("Trophy Road", "Classic 1v1", "Classic 2v2"):
        if other != mode:
            assert not check_if_battle_mode_is_selected(screen, other)


def test_native_shop_heading_collected_state_and_dimmed_modal():
    screen = Screen("shop-360-native.png")
    assert check_if_on_shop(screen)
    assert find_daily_shop_heading(screen.image) is not None
    assert find_collected_shop_offer(screen.image) is not None
    screen.image = (screen.image * 0.4).astype(np.uint8)
    assert not check_if_on_shop(screen)


def test_native_card_page_full_deck_and_dimmed_modal():
    screen = Screen("cardpage-360-native.png")
    assert check_if_on_card_page(screen)
    assert is_deck_full(screen)
    screen.image = (screen.image * 0.4).astype(np.uint8)
    assert not check_if_on_card_page(screen)


def test_historical_card_sample_exports_without_changing_source(tmp_path):
    sample = make_sample(tmp_path)
    path = sample.file("frame")
    original = (FIXTURES / "hand-legacy-419.png").read_bytes()
    path.write_bytes(original)
    save_review(sample, "confirmed", "evo_cannon")
    sample = scan_samples(tmp_path)[0][0]
    fingerprint = cards.extract_card_fingerprint(cv2.imread(str(path)), 0)
    assert cards.recognize_card_fingerprint(fingerprint)["card_id"] == "evo_cannon"
    assert export_candidate(sample).is_file()
    assert path.read_bytes() == original
