"""Pure screen-detection helpers.

Imports only from crX.detection.*, crX.utils.*, crX.bot.coords and the leaf pixel
predicates in state_detect, which does not import from find.
"""

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from crX.bot.coords import (
    BATTLE_RESULT_BUTTON_REGION,
    ENEMY_PRINCESS_UI_REGIONS_LTRB,
    ENEMY_UNIT_SEARCH_REGION_LTRB,
    GAME_SCREEN_SIZE,
    ITEM_DISTRIBUTION_COLLECT_ALL_REGION,
    ITEM_DISTRIBUTION_COLLECT_ALL_SIZE,
    OPPONENT_WINNER_LABEL_REGION,
    PLAYER_WINNER_LABEL_REGION,
    POPUP_CLOSE_BUTTON_SIZE,
    POPUP_CLOSE_REGION,
    QQ_AUTHORIZATION_HINT_REGION,
    QQ_AUTHORIZATION_TITLE_REGION,
    QQ_LOGIN_REGION,
    SHOP_CATALOG_REGION,
    SHOP_DAILY_FIRST_COLUMN,
    SHOP_DAILY_LABEL_OFFSETS,
    SHOP_DIALOG_CLOSE_REGION,
    SHOP_FREE_CONFIRM_REGION,
    SHOP_RECOMMENDATIONS_REGION,
    STARTUP_LOADING_BAR_SAMPLES,
    STARTUP_LOADING_LOGO_REGION,
    WECHAT_LOGIN_REGION,
)
from crX.bot.state_detect import is_bright_collect_all_button, is_bright_red_close_button, is_item_distribution_image
from crX.detection.image_rec import find_image, pixel_is_equal


@dataclass(frozen=True, slots=True)
class EnemyUnitMarker:
    """Level glyph bounds in native screen pixels and optional health-bar evidence."""

    bounds_ltrb: tuple[int, int, int, int]
    has_health_bar: bool

    @property
    def center(self) -> tuple[int, int]:
        left, top, right, bottom = self.bounds_ltrb
        return (left + right) // 2, (top + bottom) // 2


def _level_glyphs(glyph_mask: np.ndarray, red: np.ndarray, dark_red: np.ndarray) -> list[tuple[int, int, int, int]]:
    _, _, stats, _ = cv2.connectedComponentsWithStats(glyph_mask, connectivity=8)
    glyphs = []
    for candidate in stats[1:]:
        x, y, width, height, area = map(int, candidate)
        if not (2 <= width <= 20 and 9 <= height <= 22 and width <= 1.2 * height and 3 * area >= width * height):
            continue
        right, bottom = x + width, y + height
        # Seed from the digit so a badge touching red terrain stays separable.
        if x < 4 or y < 3 or right + 4 > red.shape[1] or bottom + 4 > red.shape[0]:
            continue
        context = red[y - 3 : bottom + 4, x - 4 : right + 4]
        rim = dark_red[y - 3 : bottom + 4, x - 4 : right + 4]
        rim_pixels = cv2.countNonZero(rim)
        bright_pixels = cv2.countNonZero(context) - rim_pixels
        lower_rim = cv2.countNonZero(dark_red[bottom : bottom + 4, x:right])
        if (
            bright_pixels < context.size * 0.07
            or rim_pixels < height
            or cv2.countNonZero(red[y - 3 : y, x:right]) < width
            or lower_rim < 2
            or cv2.countNonZero(red[y:bottom, x - 4 : x]) < height // 2
            or cv2.countNonZero(red[y:bottom, right : right + 4]) < height // 2
        ):
            continue
        # Darker badges show less bright fill but retain more of their rim.
        if bright_pixels < context.size * 0.18 and (
            rim_pixels < 3 * height or lower_rim < width or cv2.countNonZero(dark_red[y - 3 : y, x:right]) < width
        ):
            continue
        glyphs.append((x, y, right, bottom))
    return sorted(glyphs)


def _level_boxes(glyphs: list[tuple[int, int, int, int]]) -> list[tuple[int, int, int, int]]:
    """Merge the adjacent, aligned digits of a two-digit level once."""
    boxes = []
    used = set()
    for index, glyph in enumerate(glyphs):
        if index in used:
            continue
        left, top, right, bottom = glyph
        box = glyph
        height = bottom - top
        for other in range(index + 1, len(glyphs)):
            next_left, next_top, next_right, next_bottom = glyphs[other]
            gap = next_left - right
            if gap > max(3, height // 4):
                break
            if (
                other not in used
                and gap >= 0
                and abs(next_top - top) <= 2
                and abs(next_bottom - bottom) <= 2
                and next_right - left <= max(16, height * 1.5)
            ):
                box = (left, min(top, next_top), next_right, max(bottom, next_bottom))
                used.add(other)
                break
        boxes.append(box)
    return boxes


def _has_unit_health_bar(fill: np.ndarray, dark_red: np.ndarray, right: int, center_y: int) -> bool:
    width = 16
    for start in range(right + 2, min(right + 11, dark_red.shape[1] - width + 1)):
        # A dark rail above and below the fill also survives the white damage flash.
        columns = slice(start, start + width)
        if np.count_nonzero(fill[center_y - 1 : center_y + 2, columns]) < width * 2:
            continue
        rails = np.flatnonzero(np.count_nonzero(dark_red[center_y - 6 : center_y + 7, columns], axis=1) >= width - 2)
        if any(upper < 6 < lower and 4 <= lower - upper <= 9 for upper in rails for lower in rails):
            return True
    return False


def find_enemy_unit_markers(image: np.ndarray | None) -> tuple[EnemyUnitMarker, ...]:
    """Find red enemy level marks anywhere in the native 1v1 battlefield."""
    if not isinstance(image, np.ndarray) or image.shape != (*GAME_SCREEN_SIZE[::-1], 3) or image.dtype != np.uint8:
        return ()

    left, top, right, bottom = ENEMY_UNIT_SEARCH_REGION_LTRB
    hsv = cv2.cvtColor(image[top:bottom, left:right], cv2.COLOR_BGR2HSV)
    red = cv2.bitwise_or(
        cv2.inRange(hsv, np.array((0, 105, 62)), np.array((10, 255, 255))),
        cv2.inRange(hsv, np.array((160, 105, 62)), np.array((179, 255, 255))),
    )
    dark_red = cv2.bitwise_and(red, cv2.inRange(hsv, np.array((0, 0, 0)), np.array((179, 255, 150))))
    white = cv2.inRange(hsv, np.array((0, 0, 197)), np.array((179, 78, 255)))
    # Native recordings also show yellow level text in the same red badge.
    yellow = cv2.inRange(hsv, np.array((18, 145, 204)), np.array((34, 255, 255)))
    glyph_mask = cv2.bitwise_or(white, yellow)
    for x1, y1, x2, y2 in ENEMY_PRINCESS_UI_REGIONS_LTRB:
        glyph_mask[y1 - top : y2 - top, x1 - left : x2 - left] = 0

    bar_fill = cv2.bitwise_or(cv2.subtract(red, dark_red), white)
    return tuple(
        EnemyUnitMarker(
            (x1 + left, y1 + top, x2 + left, y2 + top),
            _has_unit_health_bar(bar_fill, dark_red, x2, (y1 + y2) // 2),
        )
        for x1, y1, x2, y2 in _level_boxes(_level_glyphs(glyph_mask, red, dark_red))
    )


def is_startup_loading_screen(image) -> bool:
    """Identify the loading splash by its filled bar section and crown shield."""
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    return (
        all(pixel_is_equal(image[y, x], color, tol=18) for x, y, color in STARTUP_LOADING_BAR_SAMPLES)
        and find_image(image, "startup_loading", tolerance=0.94, subcrop=STARTUP_LOADING_LOGO_REGION) is not None
    )


def is_game_login_screen(image) -> bool:
    """Require both provider controls on the native Chinese login screen."""
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    return (
        find_image(image, "login_wechat", tolerance=0.94, subcrop=WECHAT_LOGIN_REGION) is not None
        and find_image(image, "login_qq", tolerance=0.94, subcrop=QQ_LOGIN_REGION) is not None
    )


def is_qq_authorization_screen(image) -> bool:
    """Identify QQ authorization from its title and scan instructions."""
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    return (
        find_image(image, "qq_authorization_title", tolerance=0.94, subcrop=QQ_AUTHORIZATION_TITLE_REGION) is not None
        and find_image(image, "qq_authorization_hint", tolerance=0.94, subcrop=QQ_AUTHORIZATION_HINT_REGION) is not None
    )


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
    pixels = [iar[1102][306], iar[1106][410], iar[1118][368], iar[1120][346]]
    colors = [[255, 187, 104], [255, 187, 104], [255, 255, 255], [255, 255, 255]]
    pixel_match = True
    for i, p in enumerate(pixels):
        if not pixel_is_equal(p, colors[i], tol=20):
            pixel_match = False
            break
    if pixel_match:
        return (344, 1112)
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
    if coord is None or not _is_bright_green(image, coord, (106, 52)):
        return None
    return coord[0] + 34, coord[1] + 24


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
    return (left, top, right, bottom) if bottom - top >= 52 else None


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


def find_collect_all_button(image) -> tuple[int, int] | None:
    """Require the 道具发放 title and its visible 领取全部 button, regardless of items."""
    if not is_item_distribution_image(image):
        return None
    coord = find_image(
        image, "item_distribution_collect_all", tolerance=0.93, subcrop=ITEM_DISTRIBUTION_COLLECT_ALL_REGION
    )
    if coord is None or not is_bright_collect_all_button(image, coord):
        return None
    width, height = ITEM_DISTRIBUTION_COLLECT_ALL_SIZE
    return coord[0] + width // 2, coord[1] + height // 2


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
    return (coord[0] + 16, coord[1] + 20) if coord is not None else None


def find_free_shop_confirmation(image) -> tuple[int, int] | None:
    """Require the offer dialog AND its explicit, undimmed 免费 button."""
    if find_shop_offer_dialog_close(image) is None:
        return None
    coord = find_image(image, "shop_free_confirmation", tolerance=0.94, subcrop=SHOP_FREE_CONFIRM_REGION)
    if coord is None or not _is_bright_green(image, coord, (122, 58)):
        return None
    return coord[0] + 62, coord[1] + 28


def find_shop_recommendations(image) -> tuple[int, int] | None:
    if not _shop_image_is_valid(image):
        return None
    return find_image(image, "shop_recommendations", tolerance=0.92, subcrop=SHOP_RECOMMENDATIONS_REGION)
