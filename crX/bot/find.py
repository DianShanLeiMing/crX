"""Pure screen-detection helpers.

Imports only from crX.detection.*, crX.utils.*, crX.bot.coords and the leaf pixel
predicates in state_detect, which does not import from find.
"""

from typing import Literal

import numpy as np

from crX.bot.coords import (
    BATTLE_RESULT_BUTTON_REGION,
    GAME_SCREEN_SIZE,
    OPPONENT_WINNER_LABEL_REGION,
    PLAYER_WINNER_LABEL_REGION,
    POPUP_CLOSE_BUTTON_SIZE,
    POPUP_CLOSE_REGION,
    SHOP_CATALOG_REGION,
    SHOP_DAILY_FIRST_COLUMN,
    SHOP_DAILY_LABEL_OFFSETS,
    SHOP_DIALOG_CLOSE_REGION,
    SHOP_FREE_CONFIRM_REGION,
    SHOP_RECOMMENDATIONS_REGION,
)
from crX.bot.state_detect import is_bright_red_close_button
from crX.detection.image_rec import find_image, pixel_is_equal


def find_fight_mode_icon(emulator, mode: str):
    expected_mode_types = ["Classic 1v1", "Classic 2v2", "Trophy Road"]
    if mode not in expected_mode_types:
        print(f'[!] Fatal error: Mode "{mode}" is not a valid mode type. Expected one of {expected_mode_types}.')
        return None
    mode2folder = {
        "Classic 1v1": "fight_mode_1v1",
        "Classic 2v2": "fight_mode_2v2",
        "Trophy Road": "fight_mode_trophy_road",
    }
    look_folder = mode2folder[mode]
    image = emulator.screenshot()
    fight_mode_1v1_button_location = find_image(image, look_folder, tolerance=0.9, show_image=False)
    if fight_mode_1v1_button_location is not None:
        return fight_mode_1v1_button_location
    return None


def find_post_battle_button(emulator):
    """Find the post-battle return button (确定 in the current Chinese client).

    Tries multiple detection methods in order:
    1. Pixel-based detection (fastest)
    2. Image recognition for the Chinese result-screen button

    Returns:
        tuple[int, int] | None: Button coordinates (x, y) or None if not found
    """
    return find_post_battle_button_in_image(emulator.screenshot())


def find_post_battle_button_in_image(iar):
    """Same-frame result button lookup for the post-battle navigation loop."""
    if iar is None or iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return None
    pixels = [iar[551][153], iar[553][205], iar[559][184], iar[560][173]]
    colors = [[255, 187, 104], [255, 187, 104], [255, 255, 255], [255, 255, 255]]
    pixel_match = True
    for i, p in enumerate(pixels):
        if not pixel_is_equal(p, colors[i], tol=20):
            pixel_match = False
            break
    if pixel_match:
        return (172, 556)
    return find_image(iar, "post_battle_button", tolerance=0.9, subcrop=BATTLE_RESULT_BUTTON_REGION)


def find_battle_outcome(emulator) -> Literal["win", "loss"] | None:
    """Locate 获胜者 above the opponent's or player's crowns on one result frame."""
    image = emulator.screenshot()
    if image is None or image.shape[:2] != GAME_SCREEN_SIZE[::-1]:
        return None
    if find_image(image, "post_battle_button", tolerance=0.9, subcrop=BATTLE_RESULT_BUTTON_REGION) is None:
        return None
    opponent_won = find_image(image, "battle_winner", tolerance=0.85, subcrop=OPPONENT_WINNER_LABEL_REGION) is not None
    player_won = find_image(image, "battle_winner", tolerance=0.85, subcrop=PLAYER_WINNER_LABEL_REGION) is not None
    if opponent_won == player_won:
        return None
    return "win" if player_won else "loss"


def locate_free_shop_offer_icon(emulator) -> tuple[int, int] | None:
    """Locate the Chinese free label in the daily section's first offer only."""
    image = emulator.screenshot()
    region = _daily_shop_offer_region(image)
    if region is None:
        return None
    coord = find_image(image, "shop_daily_free", tolerance=0.94, subcrop=region)
    if coord is None or not _is_bright_green(image, coord, (53, 26)):
        return None
    return coord[0] + 17, coord[1] + 12


def _shop_image_is_valid(image) -> bool:
    return image is not None and image.shape == (*GAME_SCREEN_SIZE[::-1], 3)


def find_daily_shop_heading(image) -> tuple[int, int] | None:
    if not _shop_image_is_valid(image):
        return None
    return find_image(image, "shop_daily_section", tolerance=0.92, subcrop=SHOP_CATALOG_REGION)


def _daily_shop_offer_region(image) -> tuple[int, int, int, int] | None:
    heading = find_daily_shop_heading(image)
    if heading is None:
        return None
    left, right = SHOP_DAILY_FIRST_COLUMN
    start, end = SHOP_DAILY_LABEL_OFFSETS
    top, bottom = heading[1] + start, min(SHOP_CATALOG_REGION[3], heading[1] + end)
    return (left, top, right, bottom) if bottom - top >= 26 else None


def find_collected_shop_offer(image) -> tuple[int, int] | None:
    region = _daily_shop_offer_region(image)
    if region is None:
        return None
    return find_image(image, "shop_daily_collected", tolerance=0.92, subcrop=region)


def _is_bright_green(image, coord, size) -> bool:
    x, y = coord
    width, height = size
    pixels = image[y : y + height, x : x + width].astype(np.int16)
    blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    # Grayscale template matching also matches dimmed labels behind a modal.
    return bool(np.mean((green > 150) & (green > blue + 35) & (green > red + 20)) > 0.25)


def find_popup_close(image) -> tuple[int, int] | None:
    """Locate a visible red X anywhere in the right half, using existing templates."""
    if not _shop_image_is_valid(image):
        return None
    coord = find_image(image, "shop_offer_dialog_close", tolerance=0.9, subcrop=POPUP_CLOSE_REGION)
    if coord is None or not is_bright_red_close_button(image, coord):
        return None
    width, height = POPUP_CLOSE_BUTTON_SIZE
    return coord[0] + width // 2, coord[1] + height // 2


def find_shop_offer_dialog_close(image) -> tuple[int, int] | None:
    if not _shop_image_is_valid(image):
        return None
    coord = find_image(image, "shop_offer_dialog_close", tolerance=0.92, subcrop=SHOP_DIALOG_CLOSE_REGION)
    return (coord[0] + 8, coord[1] + 10) if coord is not None else None


def find_free_shop_confirmation(image) -> tuple[int, int] | None:
    """Require the offer dialog AND its explicit, undimmed 免费 button."""
    if find_shop_offer_dialog_close(image) is None:
        return None
    coord = find_image(image, "shop_free_confirmation", tolerance=0.94, subcrop=SHOP_FREE_CONFIRM_REGION)
    if coord is None or not _is_bright_green(image, coord, (61, 29)):
        return None
    return coord[0] + 31, coord[1] + 14


def find_shop_recommendations(image) -> tuple[int, int] | None:
    if not _shop_image_is_valid(image):
        return None
    return find_image(image, "shop_recommendations", tolerance=0.92, subcrop=SHOP_RECOMMENDATIONS_REGION)
