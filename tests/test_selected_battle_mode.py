"""Selected-mode recognition survives button themes without reopening the picker."""

from pathlib import Path
from unittest.mock import ANY, Mock

import cv2
import numpy as np
import pytest

from crX.bot import states
from crX.bot.coords import SELECTED_BATTLE_MODE_REGION
from crX.bot.state_detect import check_if_battle_mode_is_selected
from crX.interface.enums import UIField
from crX.utils.logger import Logger

FIXTURES = Path(__file__).parent / "fixtures"


def red_main():
    image = cv2.imread(str(FIXTURES / "selected-1v1-red-360-native.png"))
    assert image is not None
    return image


def test_native_red_button_is_classic_1v1_only():
    screen = Mock(screenshot=Mock(return_value=red_main()))
    assert check_if_battle_mode_is_selected(screen, "Classic 1v1")
    assert not check_if_battle_mode_is_selected(screen, "Classic 2v2")
    assert not check_if_battle_mode_is_selected(screen, "Trophy Road")


@pytest.mark.parametrize("background", [(0, 0, 0), (255, 255, 255), (220, 80, 20), (20, 60, 220)])
def test_selected_1v1_ignores_surrounding_fill_and_badge(background):
    image = red_main()
    # Preserve the shield and sword artwork; replace the rest of the mode button,
    # including its notification badge, with unrelated colors.
    artwork = np.zeros(image.shape[:2], dtype=np.uint8)
    cv2.fillPoly(
        artwork,
        [np.array([(284, 480), (305, 490), (305, 524), (266, 524), (266, 490)])],
        (255,),
    )
    left, top, right, bottom = SELECTED_BATTLE_MODE_REGION
    region = image[top:bottom, left:right]
    region[artwork[top:bottom, left:right] == 0] = background
    assert check_if_battle_mode_is_selected(Mock(screenshot=Mock(return_value=image)), "Classic 1v1")


def test_consecutive_rounds_do_not_reselect_the_red_1v1_button(monkeypatch):
    screen = Mock(screenshot=Mock(return_value=red_main()))
    select = Mock(side_effect=AssertionError("Already selected: do not open the picker"))
    monkeypatch.setattr(states, "select_mode", select)
    context = states.RuntimeContext()
    for _ in range(2):
        assert (
            states.state_tree(screen, Logger(), "select_battle_mode", {UIField.CLASSIC_1V1_USER_TOGGLE: True}, context)
            == "randomize_deck"
        )
    assert context.selected_mode == "Classic 1v1"
    select.assert_not_called()


@pytest.mark.parametrize("filename", ["selected-2v2-360-native.png", "selected-trophy-360-native.png"])
def test_a_different_mode_still_requires_selection(monkeypatch, filename):
    screen = Mock(screenshot=Mock(return_value=cv2.imread(str(FIXTURES / filename))))
    select = Mock(return_value=True)
    monkeypatch.setattr(states, "select_mode", select)
    assert (
        states.state_tree(
            screen, Logger(), "select_battle_mode", {UIField.CLASSIC_1V1_USER_TOGGLE: True}, states.RuntimeContext()
        )
        == "randomize_deck"
    )
    select.assert_called_once_with(screen, "Classic 1v1", ANY)


@pytest.mark.parametrize("image", [None, np.zeros((426, 240, 3), dtype=np.uint8)])
def test_missing_or_wrong_size_frame_is_not_a_selected_mode(image):
    assert not check_if_battle_mode_is_selected(Mock(screenshot=Mock(return_value=image)), "Classic 1v1")
