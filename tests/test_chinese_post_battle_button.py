"""Real Chinese client crops: battle results match; other 确定 dialogs do not."""

from pathlib import Path

import pytest

from crX.bot.find import find_post_battle_button
from crX.bot.state_detect import check_for_post_battle_button
from crX.utils.image_handler import open_from_path

FIXTURES = Path(__file__).parent / "fixtures"


class CapturedScreen:
    def __init__(self, filename: str):
        self.image = open_from_path(str(FIXTURES / filename))

    def screenshot(self):
        return self.image


@pytest.mark.parametrize("filename", ["replay-result-zh.png", "training-result-zh.png"])
def test_chinese_result_button_is_detected_and_clickable(filename):
    emulator = CapturedScreen(filename)
    coord = find_post_battle_button(emulator)
    assert coord is not None
    x, y = coord
    # The returned location must lie inside the visible button, not on its border.
    assert 143 < x < 218 and 564 < y < 600
    assert check_for_post_battle_button(emulator)


@pytest.mark.parametrize(
    "filename",
    [
        "training-start-zh.png",
        "training-exit-prompt-zh.png",
        "training-battle-zh.png",
        "main-zh.png",
        "shop-zh.png",
    ],
)
def test_other_screens_are_not_treated_as_battle_results(filename):
    emulator = CapturedScreen(filename)
    assert find_post_battle_button(emulator) is None
    assert not check_for_post_battle_button(emulator)
