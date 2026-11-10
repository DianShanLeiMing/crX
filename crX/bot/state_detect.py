"""Pure state-detection helpers.

Every function here follows the same shape: take a screenshot, run a pixel or
image comparison, return a bool. No clicks, no waits, no orchestration — those
live in nav.py or the per-state modules that import from here.

This module is a leaf: the only crX.bot module it imports from is
`coords` (itself a leaf — pure data, no imports). No nav, fight, deck, etc.
"""

from typing import Literal

import cv2
import numpy

from crX.bot.coords import (
    BATTLE_HAND_BORDER_SAMPLES,
    BATTLE_RESULT_BUTTON_REGION,
    BATTLE_REWARD_ANIMATION_REGION,
    BATTLE_REWARD_CROWN_REGION,
    BATTLE_REWARDS_REGION,
    CARD_PAGE_BRIGHT_POINT,
    CARD_PAGE_HEADING_REGION,
    CARD_PAGE_MENU_REGION,
    DECK_CARD_COST_REGIONS,
    ENEMY_PRINCESS_BAR_ANCHORS,
    ENEMY_PRINCESS_BAR_BORDER_Y_RANGE,
    ENEMY_PRINCESS_BAR_WIDTH,
    ENEMY_PRINCESS_RUINS_REGIONS_LTRB,
    GAME_SCREEN_SIZE,
    ITEM_DISTRIBUTION_COLLECT_ALL_SIZE,
    ITEM_DISTRIBUTION_INTRO_TITLE_REGION,
    ITEM_DISTRIBUTION_TITLE_REGION,
    MAIN_BATTLE_BUTTON_REGION,
    MAIN_BATTLE_TAB_REGION,
    MAIN_BURGER_BUTTON_REGION,
    MAIN_LOADING_SPINNER_REGION,
    MAIN_NATIVE_YELLOW_POINTS,
    POPUP_CLOSE_BUTTON_SIZE,
    POST_BATTLE_COIN_REWARD_REGION,
    POST_BATTLE_PUZZLE_CROWN_REGION,
    POST_BATTLE_PUZZLE_FRAME_REGION,
    POST_BATTLE_PUZZLE_OPEN_REGION,
    POST_BATTLE_PUZZLE_TITLE_REGION,
    POST_BATTLE_PUZZLE_UNLOCKED_REGION,
    POST_BATTLE_RAINBOW_CHEST_REGION,
    POST_BATTLE_REWARD_EDGE_REGIONS,
    POST_BATTLE_REWARD_FLOOR_REGION,
    POST_BATTLE_REWARD_OBJECT_REGION,
    POST_BATTLE_REWARD_STARS_REGION,
    POST_BATTLE_REWARD_TOP_REGION,
    SELECTED_BATTLE_MODE_REGION,
    SHOP_GIFT_BRIGHT_POINT,
    SHOP_NATIVE_GIFT_REGION,
    SHOP_NATIVE_TAB_REGION,
    SHOP_RECOMMENDATIONS_REGION,
)
from crX.detection.image_rec import all_pixels_are_equal, check_line_for_color, find_image, pixel_is_equal


def is_item_distribution_image(image) -> bool:
    """Identify the centered intro or settled page; neither implies a claim button."""
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    return any(
        find_image(image, "item_distribution_title", tolerance=0.93, subcrop=region) is not None
        for region in (ITEM_DISTRIBUTION_TITLE_REGION, ITEM_DISTRIBUTION_INTRO_TITLE_REGION)
    )


def is_bright_collect_all_button(image, coord: tuple[int, int]) -> bool:
    """Require visible yellow fill; grayscale matching also accepts dimmed buttons."""
    x, y = coord
    width, height = ITEM_DISTRIBUTION_COLLECT_ALL_SIZE
    pixels = image[y : y + height, x : x + width].astype(numpy.int16)
    blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return bool(numpy.mean((red > 180) & (green > 120) & (blue < 130) & (red > green + 20)) > 0.25)


def is_bright_red_close_button(image, coord: tuple[int, int]) -> bool:
    """Reject dimmed or differently colored matches of the white X template."""
    x, y = coord
    width, height = POPUP_CLOSE_BUTTON_SIZE
    pixels = image[y : y + height, x : x + width].astype(numpy.int16)
    blue, green, red = pixels[..., 0], pixels[..., 1], pixels[..., 2]
    return bool(numpy.mean((red > 150) & (red > green + 45) & (red > blue + 35)) > 0.25)


def check_if_in_battle(emulator):
    return image_is_in_battle(emulator.screenshot())


def image_is_in_battle(iar_bgr) -> bool:
    if iar_bgr is None:
        return False
    if iar_bgr.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    if all(pixel_is_equal(iar_bgr[y, x], color, tol=18) for x, y, color in BATTLE_HAND_BORDER_SAMPLES):
        return True
    iar = iar_bgr[..., ::-1]

    def get_pixel(y: int, x: int) -> list[int] | None:
        if y >= iar.shape[0] or x >= iar.shape[1]:
            return None
        return iar[y][x].tolist()

    def is_bright(pixel: list[int] | None, threshold: int = 180) -> bool:
        if pixel is None:
            return False
        return all(channel >= threshold for channel in pixel) or (
            150 <= pixel[0] <= 195 and 40 <= pixel[1] <= 60 and (40 <= pixel[2] <= 60)
        )

    def is_filled_crown(pixel: list[int] | None) -> bool:
        """Check if pixel is a gold/yellow filled crown or UI accent."""
        if pixel is None:
            return False
        r, g, b = pixel
        return r >= 170 and g >= 130 and (b <= 140)

    def is_scoreboard_purple(pixel: list[int] | None) -> bool:
        if pixel is None:
            return False
        r, g, b = pixel
        return r >= 200 and b >= 200 and (g <= 140)

    def check_mode(coords: list[tuple[int, int]]) -> bool:
        pixels = [get_pixel(y, x) for y, x in coords]
        bright_required = max(1, len(coords) - 2)
        bright_count = sum(1 for pixel in pixels[:-1] if is_bright(pixel) or is_filled_crown(pixel))
        return bright_count >= bright_required and is_scoreboard_purple(pixels[-1])

    coords_1v1 = [(1068, 84), (1076, 132), (1104, 90), (1104, 132), (1250, 198)]
    coords_2v2 = [(1080, 92), (1078, 138), (1108, 90), (1108, 130), (1244, 196)]
    if check_mode(coords_1v1):
        return True
    if check_mode(coords_2v2):
        return True
    return False


def check_if_battle_has_ended(emulator) -> bool:
    """Best-effort confirmation that a battle ended (avoid false positives mid-fight)."""
    if check_if_on_clash_main_menu(emulator):
        return True
    if check_for_trophy_reward_menu(emulator):
        return True
    if check_for_post_battle_button(emulator):
        return True
    return False


def check_for_post_battle_button(emulator) -> bool:
    """Check for the Chinese 确定 button on a battle result screen."""
    image = emulator.screenshot()
    if image is None:
        return False
    return find_image(image, "post_battle_button", tolerance=0.9, subcrop=BATTLE_RESULT_BUTTON_REGION) is not None


def check_for_trophy_reward_menu(emulator) -> bool:
    return is_trophy_reward_image(emulator.screenshot())


def is_trophy_reward_image(iar) -> bool:
    if iar is None or iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    pixels = [
        iar[1198][296],
        iar[1248][310],
        iar[1228][326],
        iar[1220][344],
        iar[1206][360],
        iar[1200][378],
        iar[1214][396],
        iar[1234][404],
        iar[1260][422],
    ]
    colors = [
        [255, 184, 68],
        [255, 175, 78],
        [255, 175, 78],
        [248, 239, 227],
        [255, 187, 104],
        [255, 176, 79],
        [255, 187, 104],
        [255, 175, 78],
        [253, 135, 39],
    ]
    for i, pixel in enumerate(pixels):
        if not pixel_is_equal(pixel, colors[i], tol=25):
            return False
    return True


def check_for_reward_choice_screen(emulator) -> bool:
    """Detect the post-battle 'Choose your reward' screen by its banner text."""
    return is_reward_choice_image(emulator.screenshot())


def is_reward_choice_image(iar) -> bool:
    if iar is None or iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    pixels = [
        iar[390][154],
        iar[396][176],
        iar[392][202],
        iar[392][248],
        iar[396][286],
        iar[396][334],
        iar[392][360],
        iar[392][402],
        iar[392][458],
        iar[396][510],
        iar[398][536],
        iar[404][562],
    ]
    colors = [
        [255, 73, 173],
        [202, 57, 136],
        [239, 128, 224],
        [198, 98, 176],
        [184, 89, 162],
        [238, 128, 222],
        [254, 146, 248],
        [156, 44, 105],
        [171, 83, 151],
        [255, 153, 255],
        [255, 153, 255],
        [255, 153, 255],
    ]
    for i, pixel in enumerate(pixels):
        if not pixel_is_equal(pixel, colors[i], tol=30):
            return False
    return True


ELIXIR_COORDS = [
    [1240, 238],
    [1240, 270],
    [1240, 316],
    [1240, 364],
    [1240, 420],
    [1240, 464],
    [1240, 514],
    [1240, 568],
    [1240, 618],
    [1240, 668],
]
ELIXIR_COLOR = [240, 137, 244]


def count_elixir(emulator, elixir_count, observe_frame=None) -> bool:
    """Method to check for 4 elixir during a battle"""
    image = emulator.screenshot()
    if observe_frame is not None:
        observe_frame(image)
    return image_has_elixir(image, elixir_count)


def _read_princess_bar(image, x, y) -> tuple[int | None, bool]:
    """Return the contiguous fill width and whether the anchored bar track was seen."""
    width = ENEMY_PRINCESS_BAR_WIDTH
    top, bottom = ENEMY_PRINCESS_BAR_BORDER_Y_RANGE
    area_top = top - 5
    area = image[area_top : bottom + 1, x - 3 : x + width + 3].astype(numpy.int16)
    border = numpy.max(numpy.abs(area - (35, 0, 117)), axis=2) <= 18
    red = numpy.max(numpy.abs(area - (94, 38, 226)), axis=2) <= 24
    empty = numpy.max(numpy.abs(area - (73, 50, 93)), axis=2) <= 20
    track_seen = False
    rows = numpy.flatnonzero(numpy.count_nonzero(border[5:], axis=1) >= width - 3) + 5
    for row in sorted(rows, key=lambda row: abs(area_top + row - y)):
        for start in (3, 2, 4, 1, 5, 0, 6):
            columns = slice(start, start + width)
            if numpy.count_nonzero(border[row, columns]) < width - 3:
                continue
            track_seen = True
            # Read the solid interior below the highlight and above the bottom border.
            filled = numpy.count_nonzero(red[row - 5 : row - 1, columns], axis=0) >= 3
            unfilled = numpy.count_nonzero(empty[row - 5 : row - 1, columns], axis=0) >= 3
            indexes = numpy.flatnonzero(filled)
            if not len(indexes) or indexes[0] > 1:
                continue
            end = int(indexes[-1]) + 1
            if numpy.count_nonzero(~filled[:end]) <= 2 and numpy.count_nonzero(~unfilled[end:]) <= 3:
                return end, True
    return None, track_seen


def read_enemy_princess_bars(image) -> tuple[tuple[int | None, bool], ...]:
    """Measure the two enemy tracks on a native 720×1280 BGR frame."""
    if (
        not isinstance(image, numpy.ndarray)
        or image.shape != (*GAME_SCREEN_SIZE[::-1], 3)
        or image.dtype != numpy.uint8
    ):
        return ((None, False), (None, False))
    return tuple(_read_princess_bar(image, x, y) for x, y in ENEMY_PRINCESS_BAR_ANCHORS)


def image_has_enemy_princess_ruins(image: numpy.ndarray, side: int) -> bool:
    return (
        find_image(
            image,
            "princess_tower_ruins",
            tolerance=0.84,
            subcrop=ENEMY_PRINCESS_RUINS_REGIONS_LTRB[side],
        )
        is not None
    )


def image_has_elixir(image, elixir_count: int) -> bool:
    """Read elixir from an already captured frame, without another device call."""
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3) or not 1 <= elixir_count <= len(ELIXIR_COORDS):
        return False
    y, x = ELIXIR_COORDS[elixir_count - 1]
    return pixel_is_equal(image[y, x], ELIXIR_COLOR, tol=65)


def check_pixels_for_win_in_battle_log(emulator) -> bool:
    """Method to check pixels that appear in the battle
    log to determine if the previous game was a win
    """
    line1 = check_line_for_color(emulator, x_1=80, y_1=272, x_2=188, y_2=312, color=(255, 51, 102))
    line2 = check_line_for_color(emulator, x_1=80, y_1=308, x_2=198, y_2=278, color=(255, 51, 102))
    line3 = check_line_for_color(emulator, x_1=80, y_1=292, x_2=190, y_2=298, color=(255, 51, 102))
    if line1 and line2 and line3:
        return False
    return True


def check_if_on_clash_main_menu(emulator) -> bool:
    """Checks if the user is on the clash main menu.
    Returns True if on main menu, False if not.
    """
    return _is_clash_main_menu_image(emulator.screenshot())


def _is_clash_main_menu_image(image) -> bool:
    return _is_clash_main_menu_layout(image) and not _has_main_loading_spinner(image)


def is_clash_main_loading_image(image) -> bool:
    """Visible native main controls do not imply that resources finished loading."""
    return _is_clash_main_menu_layout(image) and _has_main_loading_spinner(image)


def _has_main_loading_spinner(image) -> bool:
    left, top, right, bottom = MAIN_LOADING_SPINNER_REGION
    blue, green, red = image[top:bottom, left:right].astype(numpy.int16).transpose(2, 0, 1)
    bright = (blue > 210) & (green > 200) & (red > 130) & (numpy.abs(blue - green) < 70)
    y, x = numpy.ogrid[: bottom - top, : right - left]
    radius_squared = (x - 40) ** 2 + (y - 40) ** 2
    ring = (radius_squared >= 24**2) & (radius_squared < 38**2)
    center = radius_squared < 18**2
    outside = (radius_squared >= 38**2) & (radius_squared < 45**2)
    # A rotating white/cyan arc, hollow center and clean outer edge. This check
    # only applies after three undimmed main-menu anchors have been verified.
    return bool(bright[ring].mean() > 0.35 and bright[center].mean() < 0.1 and bright[outside].mean() < 0.1)


def _is_clash_main_menu_layout(image) -> bool:
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    # At 320 DPI, 9:16 moves fixed-size controls rather than stretching their artwork.
    # Require three independent native anchors plus undimmed yellow button pixels.
    if all(pixel_is_equal(image[y, x], [0, 187, 255], tol=25) for x, y in MAIN_NATIVE_YELLOW_POINTS):
        # A native frame must pass every anchor; historical pixel fallbacks must
        # not accept a partially obscured native menu after a text-template match.
        return all(
            find_image(image, folder, tolerance=0.95, subcrop=region) is not None
            for folder, region in (
                ("main_battle_button", MAIN_BATTLE_BUTTON_REGION),
                ("main_burger_button", MAIN_BURGER_BUTTON_REGION),
                ("main_battle_tab", MAIN_BATTLE_TAB_REGION),
            )
        )
    pixels = [
        image[28][360],
        image[28][558],
        image[38][512],
        image[34][686],
        image[1174][448],
        image[1180][286],
        image[1256][286],
    ]
    colors_1 = [
        [255, 255, 255],
        [255, 255, 255],
        [53, 199, 233],
        [25, 198, 65],
        [138, 105, 71],
        [139, 105, 72],
        [155, 120, 82],
    ]
    colors_2 = [
        [255, 255, 255],
        [255, 255, 255],
        [53, 200, 233],
        [24, 199, 65],
        [138, 105, 71],
        [139, 105, 72],
        [155, 120, 81],
    ]
    colors_3 = [
        [57, 151, 206],
        [21, 148, 42],
        [36, 17, 1],
        [231, 190, 123],
        [140, 105, 74],
        [140, 105, 74],
        [156, 121, 82],
    ]
    for colors in [colors_1, colors_2, colors_3]:
        if all_pixels_are_equal(pixels, colors, 25):
            return True
    # Currency digits and their glints change the upper pixels. Require three
    # independent fixed UI anchors rather than relaxing every color tolerance.
    return (
        all_pixels_are_equal(pixels[-3:], colors_1[-3:], 25)
        and find_image(image, "main_battle_button", tolerance=0.95, subcrop=MAIN_BATTLE_BUTTON_REGION) is not None
        and find_image(image, "main_burger_button", tolerance=0.95, subcrop=MAIN_BURGER_BUTTON_REGION) is not None
    )


def is_clash_main_menu_image(image) -> bool:
    """Inspect the same frame as reward detection, without another screenshot."""
    return _is_clash_main_menu_image(image)


def _region(image, region):
    left, top, right, bottom = region
    return image[top:bottom, left:right]


def is_generic_post_battle_reward(image) -> bool:
    """Recognize a reward presentation without identifying its text or contents.

    Require known backdrop colors across all four outer regions, little outer
    UI detail, and a substantial illuminated object distinct from the backdrop.
    Navigation supplies the flow scope, temporal confirmation and click budget.
    """
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    hue, saturation, value = cv2.cvtColor(image, cv2.COLOR_BGR2HSV).transpose(2, 0, 1)
    warm = ((hue <= 40) | (hue >= 170)) & (saturation > 60) & (value > 60)
    cool = (hue >= 85) & (hue <= 170) & (saturation > 50) & (value > 45)
    teal = (hue >= 40) & (hue < 95) & (saturation > 50) & (value > 70)
    pastel = (saturation < 160) & (value > 140) & (value < 240)
    backdrop = warm | cool | teal | pastel
    edges = cv2.Canny(cv2.cvtColor(image, cv2.COLOR_BGR2GRAY), 70, 140) > 0
    # Resource counters, side controls and bottom navigation have much more
    # edge detail than reward backdrops. The floor may contain a tiled pattern.
    for region, max_detail in (
        (POST_BATTLE_REWARD_TOP_REGION, 0.02),
        *((region, 0.02) for region in POST_BATTLE_REWARD_EDGE_REGIONS),
        (POST_BATTLE_REWARD_FLOOR_REGION, 0.08),
    ):
        if _region(backdrop, region).mean() < 0.94 or _region(edges, region).mean() > max_detail:
            return False

    # Estimate the gradient behind the object from the left/right margins on
    # each row. Rays, titles and tiny glints alone must not count as an object.
    left, top, right, bottom = POST_BATTLE_REWARD_OBJECT_REGION
    left_region, right_region = POST_BATTLE_REWARD_EDGE_REGIONS
    left_colors = image[top:bottom, left_region[0] : left_region[2]].mean(axis=1)
    right_colors = image[top:bottom, right_region[0] : right_region[2]].mean(axis=1)
    left_center = (left_region[0] + left_region[2]) / 2
    right_center = (right_region[0] + right_region[2]) / 2
    weights = (numpy.arange(left, right) - left_center) / (right_center - left_center)
    background = (
        left_colors[:, None, :] * (1 - weights[None, :, None]) + right_colors[:, None, :] * weights[None, :, None]
    )
    contrast = numpy.abs(_region(image, POST_BATTLE_REWARD_OBJECT_REGION).astype(float) - background).max(axis=2)
    foreground = ((contrast > 45) & (_region(value, POST_BATTLE_REWARD_OBJECT_REGION) > 160)).astype(numpy.uint8)
    foreground = cv2.morphologyEx(foreground, cv2.MORPH_CLOSE, numpy.ones((5, 5), dtype=numpy.uint8))
    _, _, components, _ = cv2.connectedComponentsWithStats(foreground)
    return any(
        width >= 50 and height >= 70 and 2400 <= area < foreground.size * 0.7
        for _, _, width, height, area in components[1:]
    )


def detect_post_battle_reward(image) -> Literal["lucky_chest", "event_reward"] | None:
    """Require a full reward backdrop AND its foreground; color alone is insufficient.

    HSV ranges describe chest and event backdrops. Puzzle gradients additionally
    require native artwork/text anchors. Enlarged attachments are not templates.
    """
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return None
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue, saturation, value = hsv.transpose(2, 0, 1)
    vivid = (saturation > 70) & (value > 40)

    def backdrop(lower, upper):
        mask = (hue >= lower) & (hue <= upper) & vivid
        uniform_top = _region(mask, POST_BATTLE_REWARD_TOP_REGION).mean() > 0.94
        uniform_sides = all(_region(mask, region).mean() > 0.94 for region in POST_BATTLE_REWARD_EDGE_REGIONS)
        return mask if uniform_top and uniform_sides else None

    # Silver-framed puzzle packs use a dark blue/purple gradient. Both the
    # central crown panel and the explicit open prompt must be present.
    dark_puzzle = (hue >= 90) & (hue <= 150) & (saturation > 80) & (value > 50) & (value < 160)
    dark_puzzle_backdrop = (
        _region(dark_puzzle, POST_BATTLE_REWARD_TOP_REGION).mean() > 0.94
        and _region(dark_puzzle, POST_BATTLE_REWARD_FLOOR_REGION).mean() > 0.94
    )
    if (
        dark_puzzle_backdrop
        and find_image(image, "post_battle_puzzle_crown", tolerance=0.93, subcrop=POST_BATTLE_PUZZLE_CROWN_REGION)
        is not None
        and find_image(image, "post_battle_puzzle_open", tolerance=0.93, subcrop=POST_BATTLE_PUZZLE_OPEN_REGION)
        is not None
    ):
        return "event_reward"

    # Revealed puzzle pieces have a pastel or dark gradient rather than solid blue
    # backdrop. Require both fixed labels and the wooden frame, not the piece
    # artwork or progress count, which change independently of the screen type.
    pastel = (saturation < 150) & (value > 150) & (value < 240)
    dark_blue = (hue >= 90) & (hue <= 120) & (saturation > 120) & (value > 40) & (value < 120)
    gold = (hue >= 5) & (hue <= 35) & vivid
    pastel_puzzle_backdrop = (
        _region(pastel, POST_BATTLE_REWARD_TOP_REGION).mean() > 0.94
        and _region(dark_blue, POST_BATTLE_REWARD_FLOOR_REGION).mean() > 0.94
    )
    if (
        (pastel_puzzle_backdrop or dark_puzzle_backdrop)
        and _region(gold, POST_BATTLE_PUZZLE_FRAME_REGION).mean() > 0.06
        and find_image(image, "post_battle_puzzle_title", tolerance=0.93, subcrop=POST_BATTLE_PUZZLE_TITLE_REGION)
        is not None
        and find_image(image, "post_battle_puzzle_unlocked", tolerance=0.93, subcrop=POST_BATTLE_PUZZLE_UNLOCKED_REGION)
        is not None
    ):
        return "event_reward"

    orange, purple, blue = backdrop(8, 35), backdrop(125, 165), backdrop(90, 120)
    # The four-star chest uses a rainbow gradient instead of a uniform backdrop.
    # Require both the warm top / teal floor and the actual chest artwork.
    # A compact native question-panel reference tolerates the chest's vertical
    # bobbing and larger artwork without relaxing the backdrop or match threshold.
    top = _region(image, POST_BATTLE_REWARD_TOP_REGION).mean(axis=(0, 1))
    floor = _region(image, POST_BATTLE_REWARD_FLOOR_REGION).mean(axis=(0, 1))
    rainbow = top[2] > top[0] + 50 and top[2] > top[1] + 50 and floor[0] > floor[2] + 50 and floor[1] > floor[2] + 50
    if (
        rainbow
        and find_image(image, "post_battle_rainbow_chest", tolerance=0.93, subcrop=POST_BATTLE_RAINBOW_CHEST_REGION)
        is not None
    ):
        return "lucky_chest"
    if orange is not None or purple is not None:
        pixels = _region(image, POST_BATTLE_REWARD_STARS_REGION).astype(numpy.int16)
        b, g, r = pixels.transpose(2, 0, 1)
        stars = ((r > 215) & (g > 205) & (b < 225) & (r > b + 25)).astype(numpy.uint8)
        _, _, components, _ = cv2.connectedComponentsWithStats(stars)
        centers = [
            (x + width / 2, y + height / 2)
            for x, y, width, height, area in components[1:]
            if 24 <= width <= 80 and 24 <= height <= 80 and 0.3 < area / (width * height) < 0.75
        ]
        aligned = 1 <= len(centers) <= 5 and max(y for _, y in centers) - min(y for _, y in centers) < 16
        background = orange if orange is not None else purple
        foreground = 1 - _region(background, POST_BATTLE_REWARD_OBJECT_REGION).mean()
        if aligned and foreground > 0.12:
            return "lucky_chest"
    if blue is not None:
        # Native bag artwork is an amount-independent anchor; full legacy
        # references also contain the title/count and have different geometry.
        # Correlation ignores brightness, so also require visible gold coins.
        if (
            _region(gold & (value > 180), POST_BATTLE_COIN_REWARD_REGION).mean() > 0.02
            and find_image(image, "post_battle_coin_reward", tolerance=0.93, subcrop=POST_BATTLE_COIN_REWARD_REGION)
            is not None
        ):
            return "event_reward"
        # Both closed mystery frames and revealed pieces have visible wooden/gold borders.
        has_frame = _region(gold, POST_BATTLE_REWARD_OBJECT_REGION).mean() > 0.06
        foreground = 1 - _region(blue, POST_BATTLE_REWARD_OBJECT_REGION).mean()
        if has_frame and foreground > 0.12:
            return "event_reward"
    return None


def check_if_battle_rewards_finished(emulator) -> bool:
    """Recognize the empty brown crown bar only on a verified main-menu frame."""
    image = emulator.screenshot()
    if not _is_clash_main_menu_image(image):
        return False
    # Ignore the bobbing crown while matching the empty bar, then require its glyph separately.
    background = image.copy()
    x1, y1, x2, y2 = BATTLE_REWARD_ANIMATION_REGION
    background[y1:y2, x1:x2] = 0
    if find_image(background, "battle_rewards_finished", tolerance=0.96, subcrop=BATTLE_REWARDS_REGION) is None:
        return False
    return find_image(image, "battle_reward_crown", tolerance=0.9, subcrop=BATTLE_REWARD_CROWN_REGION) is not None


def check_if_on_trophy_road_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Trophy Road game type."""
    image = emulator.screenshot()
    pixels = [image[206][82], image[196][108], image[258][122], image[234][100], image[220][86]]
    colors = [[255, 255, 255], [155, 63, 4], [237, 99, 0], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_1v1_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 1v1 game type."""
    image = emulator.screenshot()
    pixels = [image[204][40], image[216][158], image[204][84], image[220][88], image[234][88]]
    colors = [[195, 45, 44], [190, 44, 41], [255, 255, 255], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_2v2_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 2v2 game type."""
    image = emulator.screenshot()
    pixels = [
        image[200][38],
        image[206][146],
        image[200][572],
        image[208][672],
        image[204][86],
        image[218][92],
        image[232][86],
    ]
    colors = [
        [197, 45, 45],
        [194, 45, 43],
        [197, 45, 45],
        [194, 45, 43],
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
    ]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_confirm_randomize_deck_page(emulator) -> bool:
    """True if the "replace deck?" confirm dialog is up after randomizing a full deck."""
    image = emulator.screenshot()
    pixels = [image[766][180], image[784][434], image[696][424], image[528][174], image[842][138]]
    colors = [[98, 95, 252], [255, 187, 104], [243, 238, 227], [243, 238, 227], [243, 238, 227]]
    return all_pixels_are_equal(pixels, colors, 25)


CLAN_VOYAGE_PIXELS: list[tuple[tuple[int, int], list[int]]] = [
    ((84, 1220), [73, 87, 108]),
    ((300, 1220), [78, 175, 255]),
    ((410, 1224), [78, 175, 255]),
    ((52, 242), [20, 102, 223]),
    ((582, 244), [20, 102, 221]),
    ((412, 1198), [102, 185, 251]),
    ((26, 1230), [73, 87, 108]),
]


def check_if_on_clan_voyage(emulator) -> bool:
    """Checks if the user is on the clan voyage screen.

    Returns True if the expected clan-voyage UI pixels are present, else False.
    """
    iar_bgr = emulator.screenshot()
    if iar_bgr is None:
        return False
    iar = iar_bgr[..., ::-1]
    pixels = [iar[y][x].tolist() for (x, y), _ in CLAN_VOYAGE_PIXELS]
    colors = [expected for _, expected in CLAN_VOYAGE_PIXELS]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_clash_main_burger_button_options_menu(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = [
        iar[84][440],
        iar[82][472],
        iar[82][484],
        iar[84][504],
        iar[88][558],
        iar[64][410],
        iar[68][578],
        iar[102][426],
        iar[100][578],
    ]
    colors = [
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 254],
        [255, 255, 255],
        [255, 187, 105],
        [255, 187, 105],
        [255, 175, 78],
        [255, 175, 78],
    ]
    for i, color in enumerate(colors):
        if not pixel_is_equal(pixels[i], color, tol=25):
            return False
    return True


def check_if_battle_mode_is_selected(emulator, mode: str):
    """Checks if the given battle mode is selected on the clash main menu.

    Classic 1v1 includes a tight shield/swords reference: the surrounding
    button fill and notification badge can change without changing the mode.

    Args:
        emulator: The emulator controller.
        mode: The battle mode to check for.

    Returns:
        True if the mode is selected, False otherwise.
    """
    expected_mode_types = ["Classic 1v1", "Classic 2v2", "Trophy Road"]
    if mode not in expected_mode_types:
        print(f'[!] Fatal error: Mode "{mode}" is not a valid mode type. Expected one of {expected_mode_types}.')
        return None
    mode2folder = {
        "Classic 1v1": "selected_1v1_on_main",
        "Classic 2v2": "selected_2v2_on_main",
        "Trophy Road": "selected_trophy_road_on_main",
    }
    look_folder = mode2folder[mode]
    image = emulator.screenshot()
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    print(f"[DEBUG] Checking if {mode} is selected...")
    print(f"[DEBUG] Looking in folder: {look_folder}")
    coord = find_image(image, look_folder, tolerance=0.9, subcrop=SELECTED_BATTLE_MODE_REGION)
    print(f"[DEBUG] Found at: {coord}")
    return coord is not None


def check_if_on_card_page(emulator) -> bool:
    iar = emulator.screenshot()
    if iar is None or iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    x, y = CARD_PAGE_BRIGHT_POINT
    if (
        pixel_is_equal(iar[y, x], [255, 255, 255], tol=25)
        and find_image(iar, "card_page_heading", tolerance=0.95, subcrop=CARD_PAGE_HEADING_REGION) is not None
        and find_image(iar, "card_page_menu", tolerance=0.95, subcrop=CARD_PAGE_MENU_REGION) is not None
    ):
        return True
    pixels = [
        iar[876][100],
        iar[234][102],
        iar[118][140],
        iar[130][308],
        iar[126][186],
        iar[136][250],
        iar[156][318],
        iar[156][144],
    ]
    colors1 = [
        [222, 0, 235],
        [255, 255, 255],
        [203, 137, 44],
        [195, 126, 34],
        [255, 255, 255],
        [255, 255, 255],
        [177, 103, 15],
        [178, 104, 15],
    ]
    colors2 = [
        [220, 0, 234],
        [255, 255, 255],
        [209, 68, 41],
        [202, 64, 41],
        [255, 255, 255],
        [255, 255, 255],
        [185, 52, 41],
        [185, 52, 41],
    ]
    colors3 = [
        [222, 0, 235],
        [222, 85, 0],
        [203, 137, 44],
        [195, 126, 34],
        [255, 255, 255],
        [255, 255, 255],
        [177, 103, 15],
        [178, 104, 15],
    ]
    if all_pixels_are_equal(pixels, colors1, tol=25):
        return True
    if all_pixels_are_equal(pixels, colors2, tol=25):
        return True
    if all_pixels_are_equal(pixels, colors3, tol=25):
        return True
    pixels4 = [
        iar[118][134],
        iar[120][160],
        iar[122][202],
        iar[122][226],
        iar[122][242],
        iar[128][272],
        iar[132][298],
        iar[130][320],
        iar[136][128],
        iar[136][142],
        iar[136][158],
        iar[136][176],
        iar[146][274],
        iar[146][296],
        iar[144][312],
        iar[146][328],
        iar[1178][166],
        iar[1186][172],
        iar[1202][172],
        iar[1228][168],
        iar[1214][300],
        iar[1206][300],
        iar[1196][304],
        iar[1192][308],
        iar[1186][316],
        iar[1182][328],
    ]
    colors4 = [
        [27, 112, 237],
        [29, 110, 234],
        [18, 53, 110],
        [28, 108, 230],
        [26, 94, 198],
        [91, 112, 131],
        [59, 146, 255],
        [62, 156, 255],
        [31, 90, 203],
        [31, 89, 203],
        [32, 90, 204],
        [32, 90, 203],
        [255, 255, 255],
        [53, 111, 255],
        [68, 143, 255],
        [60, 123, 255],
        [138, 105, 71],
        [139, 106, 73],
        [142, 109, 73],
        [148, 115, 77],
        [146, 111, 75],
        [143, 110, 74],
        [142, 107, 73],
        [140, 107, 73],
        [139, 106, 73],
        [139, 106, 73],
    ]
    if all_pixels_are_equal(pixels4, colors4, tol=25):
        return True
    return False


PRINCESS_FINGERPRINT_TOL = 25
PRINCESS_FINGERPRINTS: dict[str, tuple[list[tuple[int, int]], list[list[int]]]] = {
    "info location #1": (
        [(1036, 526), (1042, 584), (1042, 634), (1058, 590), (1058, 566), (1058, 624)],
        [[255, 187, 105], [255, 187, 105], [255, 187, 105], [255, 255, 255], [255, 254, 254], [255, 175, 78]],
    ),
    "info location #2": (
        [(648, 530), (672, 528), (674, 534), (668, 626), (644, 634), (644, 638)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105]],
    ),
    "info location #3": (
        [(598, 532), (622, 528), (628, 640), (612, 630), (600, 588), (616, 568)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105], [255, 255, 255]],
    ),
    "upgrade location #1": (
        [(598, 534), (598, 568), (602, 642), (598, 616), (600, 630)],
        [[119, 235, 107], [98, 194, 89], [119, 235, 107], [250, 254, 250], [201, 226, 198]],
    ),
    "upgrade location #2": (
        [(1032, 530), (1038, 638), (1038, 650), (1038, 550), (1048, 604), (1038, 622)],
        [[119, 235, 107], [119, 235, 107], [119, 235, 107], [254, 255, 254], [255, 255, 255], [248, 253, 248]],
    ),
}


def check_if_on_battle_log_page(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = [
        iar[146][274],
        iar[144][322],
        iar[144][338],
        iar[146][396],
        iar[148][444],
        iar[130][628],
        iar[160][628],
        iar[142][628],
        iar[126][158],
        iar[156][544],
    ]
    colors = [
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
        [255, 255, 255],
        [147, 135, 254],
        [38, 38, 240],
        [255, 255, 255],
        [138, 122, 115],
        [124, 106, 99],
    ]
    for i, p in enumerate(pixels):
        if not pixel_is_equal(p, colors[i], tol=25):
            return False
    return True


def check_if_on_shop(emulator) -> bool:
    iar = emulator.screenshot()
    if iar is None or iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    x, y = SHOP_GIFT_BRIGHT_POINT
    if pixel_is_equal(iar[y, x], [3, 122, 209], tol=25) and all(
        find_image(iar, folder, tolerance=0.95, subcrop=region) is not None
        for folder, region in (
            ("shop_recommendations", SHOP_RECOMMENDATIONS_REGION),
            ("shop_gift_tab", SHOP_NATIVE_GIFT_REGION),
            ("shop_bottom_tab", SHOP_NATIVE_TAB_REGION),
        )
    ):
        return True
    pixels = [iar[1192][28], iar[1232][32], iar[28][364], iar[30][568], iar[1214][220]]
    colors = [[140, 107, 73], [151, 116, 77], [57, 160, 214], [22, 186, 59], [255, 225, 137]]
    for i, p in enumerate(pixels):
        if not pixel_is_equal(p, colors[i], tol=25):
            return False
    return True


def check_for_free_offer_confirmation_condition_1(emulator) -> bool:
    """True if the free-offer confirmation popup is open (e.g. "Get Gems? FREE!").

    Fingerprints the green FREE button, the red close X, and the popup's light
    border. emulator.screenshot() is BGR, so colors are stored [B, G, R] (the
    captured values were r,g,b).
    """
    iar = emulator.screenshot()
    pixels = [
        iar[788][306],
        iar[782][348],
        iar[790][414],
        iar[810][308],
        iar[824][408],
        iar[430][590],
        iar[418][598],
        iar[512][134],
        iar[796][544],
        iar[572][584],
    ]
    colors = [
        [119, 235, 107],
        [119, 235, 107],
        [119, 235, 107],
        [73, 228, 58],
        [73, 228, 58],
        [49, 53, 254],
        [249, 249, 249],
        [243, 238, 227],
        [243, 238, 227],
        [243, 238, 227],
    ]
    return all_pixels_are_equal(pixels, colors, 25)


_WAR_PAGE_ALT_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (614, 108, 152, 117, 254),
    (652, 116, 154, 119, 253),
    (680, 82, 162, 129, 255),
    (652, 60, 129, 161, 177),
    (36, 1226, 74, 88, 110),
    (116, 1226, 74, 88, 110),
    (194, 1230, 74, 88, 109),
    (268, 1222, 75, 89, 110),
    (320, 1222, 104, 187, 255),
    (386, 1228, 78, 175, 255),
    (444, 1230, 74, 88, 109),
    (542, 1240, 73, 87, 108),
    (630, 1238, 73, 87, 109),
    (686, 1238, 73, 87, 109),
    (348, 1222, 255, 255, 255),
)
_WAR_PAGE_ALT2_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (408, 1216, 75, 111, 146),
    (408, 1232, 77, 115, 150),
    (548, 1242, 79, 117, 152),
    (544, 1224, 77, 113, 148),
    (554, 1182, 73, 106, 139),
    (30, 96, 124, 79, 255),
    (30, 74, 141, 101, 255),
    (32, 48, 145, 107, 255),
    (34, 36, 147, 110, 255),
    (684, 108, 136, 96, 254),
    (688, 84, 144, 108, 255),
    (688, 66, 157, 124, 255),
)
_WAR_BOOT_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (40, 1216, 75, 89, 111),
    (104, 1212, 75, 89, 111),
    (212, 1230, 74, 88, 109),
    (476, 1230, 74, 88, 109),
    (572, 1232, 74, 88, 109),
    (700, 1240, 73, 87, 108),
    (310, 1230, 78, 175, 255),
    (354, 1226, 111, 134, 153),
    (398, 1226, 78, 175, 255),
    (254, 788, 255, 190, 43),
    (372, 788, 255, 190, 43),
    (386, 750, 255, 196, 54),
)
_WAR_BOOT_PIXELS_ALT: tuple[tuple[int, int, int, int, int], ...] = (
    (254, 792, 255, 190, 43),
    (284, 794, 255, 190, 43),
    (306, 796, 255, 190, 43),
    (260, 856, 255, 190, 43),
    (270, 820, 224, 167, 38),
    (284, 832, 255, 255, 254),
    (306, 832, 255, 255, 255),
    (348, 842, 255, 255, 255),
    (226, 1230, 74, 88, 109),
    (470, 1226, 74, 88, 110),
    (514, 1226, 74, 88, 110),
    (316, 1214, 104, 187, 255),
)
_FINAL_RESULTS_PAGE_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (44, 300, 142, 82, 255),
    (100, 312, 142, 82, 255),
    (128, 312, 142, 82, 255),
    (170, 308, 142, 82, 255),
    (202, 308, 142, 82, 255),
    (234, 308, 142, 82, 255),
    (262, 308, 245, 240, 253),
    (294, 310, 199, 186, 225),
    (312, 312, 255, 255, 255),
    (332, 312, 142, 82, 255),
    (356, 312, 255, 255, 255),
    (396, 312, 245, 240, 253),
    (408, 312, 11, 7, 19),
    (432, 314, 155, 141, 184),
    (466, 322, 216, 205, 238),
    (482, 322, 142, 82, 255),
    (518, 318, 142, 82, 255),
    (564, 314, 142, 82, 255),
    (602, 310, 142, 82, 255),
    (650, 310, 142, 82, 255),
    (678, 310, 142, 82, 255),
    (42, 1216, 75, 89, 111),
    (76, 1210, 75, 89, 111),
    (126, 1210, 75, 89, 111),
    (156, 1210, 75, 89, 111),
    (202, 1216, 75, 89, 111),
    (230, 1222, 75, 89, 110),
    (268, 1224, 74, 88, 110),
    (304, 1226, 78, 175, 255),
    (348, 1232, 255, 255, 255),
    (374, 1236, 255, 255, 255),
    (420, 1238, 78, 175, 255),
    (462, 1238, 73, 87, 109),
    (502, 1232, 74, 88, 109),
    (552, 1226, 74, 88, 110),
    (596, 1218, 75, 89, 110),
    (640, 1212, 75, 89, 111),
    (664, 1214, 75, 89, 111),
)
_BATTLE_DAYS_STARTED_POPUP_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (92, 626, 142, 82, 255),
    (128, 630, 142, 82, 255),
    (142, 630, 142, 82, 255),
    (190, 632, 2, 1, 2),
    (226, 632, 245, 241, 253),
    (260, 632, 3, 3, 4),
    (300, 636, 60, 35, 108),
    (342, 644, 255, 255, 255),
    (388, 644, 142, 82, 255),
    (420, 646, 255, 255, 255),
    (470, 646, 116, 67, 208),
    (506, 646, 4, 2, 7),
    (542, 646, 142, 82, 255),
    (580, 646, 142, 82, 255),
    (628, 642, 142, 82, 255),
    (656, 638, 142, 82, 255),
    (80, 1222, 75, 89, 110),
    (104, 1222, 75, 89, 110),
    (142, 1216, 75, 89, 111),
    (180, 1218, 75, 89, 110),
    (204, 1222, 75, 89, 110),
    (240, 1222, 75, 89, 110),
    (274, 1224, 74, 88, 110),
    (318, 1222, 104, 187, 255),
    (360, 1222, 255, 255, 255),
    (386, 1222, 104, 187, 255),
    (434, 1224, 74, 88, 110),
    (474, 1226, 74, 88, 110),
    (508, 1228, 74, 88, 110),
    (552, 1230, 74, 88, 109),
    (600, 1228, 74, 88, 110),
    (640, 1224, 74, 88, 110),
)
_WAR_DECK_INDICATOR_TOL = 15
_WAR_DECK_INDICATORS: dict[int, tuple[tuple[int, int, int, int, int], ...]] = {
    1: ((0, 94, 207, 116, 1092), (0, 108, 211, 110, 1016), (255, 255, 255, 134, 1050), (0, 93, 207, 152, 1092)),
    2: ((0, 108, 211, 262, 1016), (255, 255, 255, 286, 1048), (0, 109, 211, 310, 1012), (0, 101, 209, 260, 1052)),
    3: ((0, 109, 211, 414, 1016), (255, 255, 255, 436, 1048), (0, 94, 207, 458, 1086), (0, 109, 211, 460, 1014)),
    4: ((0, 109, 211, 566, 1012), (255, 255, 255, 590, 1052), (0, 94, 207, 604, 1088), (0, 109, 211, 608, 1014)),
}
CARD_MASTERY_COORD = (584, 890)
CARD_MASTERY_BGR = (95, 214, 251)
CARD_MASTERY_PIXEL_TOLERANCE = 15


def is_deck_full(emulator) -> bool:
    iar = numpy.asarray(emulator.screenshot())
    if iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    if all(
        numpy.count_nonzero(
            (iar[top:bottom, left:right, 0] > 120)
            & (iar[top:bottom, left:right, 2] > 120)
            & (iar[top:bottom, left:right, 1] < 120)
        )
        >= 100
        for left, top, right, bottom in DECK_CARD_COST_REGIONS
    ):
        return True
    pixels = [
        iar[348][74],
        iar[348][224],
        iar[348][372],
        iar[348][518],
        iar[626][74],
        iar[628][224],
        iar[624][372],
        iar[624][518],
    ]
    valid_colors = [
        [175, 5, 179],
        [178, 5, 182],
        [178, 5, 183],
        [188, 2, 194],
        [185, 4, 191],
        [197, 2, 209],
        [193, 2, 203],
    ]
    return all(any(pixel_is_equal(valid_color, p, tol=40) for valid_color in valid_colors) for p in pixels)


def is_single_deck_layout_by_pixel(emulator) -> bool:
    iar = numpy.asarray(emulator.screenshot())
    pixel_coords = [(170, 334), (202, 336), (170, 632), (202, 632)]
    expected_colors = [[141, 29, 0], [147, 34, 0], [145, 31, 4], [149, 34, 3]]
    return all(
        (
            pixel_is_equal(expected, actual, tol=45)
            for (x, y), expected in zip(pixel_coords, expected_colors)
            for actual in [iar[y][x]]
        )
    )


def pixel_indicates_upgradable(bgr):
    b, g, r = bgr
    return g >= 240 and b <= 120 and (r <= 40)
