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
    GAME_SCREEN_SIZE,
    MAIN_BATTLE_BUTTON_REGION,
    MAIN_BATTLE_TAB_REGION,
    MAIN_BURGER_BUTTON_REGION,
    MAIN_NATIVE_YELLOW_POINTS,
    POST_BATTLE_COIN_REWARD_REGION,
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


def check_if_in_battle(emulator):
    iar_bgr = emulator.screenshot()
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

    coords_1v1 = [(534, 42), (538, 66), (552, 45), (552, 66), (625, 99)]
    coords_2v2 = [(540, 46), (539, 69), (554, 45), (554, 65), (622, 98)]
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
        iar[599][148],
        iar[624][155],
        iar[614][163],
        iar[610][172],
        iar[603][180],
        iar[600][189],
        iar[607][198],
        iar[617][202],
        iar[630][211],
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
        iar[195][77],
        iar[198][88],
        iar[196][101],
        iar[196][124],
        iar[198][143],
        iar[198][167],
        iar[196][180],
        iar[196][201],
        iar[196][229],
        iar[198][255],
        iar[199][268],
        iar[202][281],
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
    [620, 119],
    [620, 135],
    [620, 158],
    [620, 182],
    [620, 210],
    [620, 232],
    [620, 257],
    [620, 284],
    [620, 309],
    [620, 334],
]
ELIXIR_COLOR = [240, 137, 244]


def count_elixir(emulator, elixir_count) -> bool:
    """Method to check for 4 elixir during a battle"""
    return image_has_elixir(emulator.screenshot(), elixir_count)


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
    line1 = check_line_for_color(emulator, x_1=40, y_1=136, x_2=94, y_2=156, color=(255, 51, 102))
    line2 = check_line_for_color(emulator, x_1=40, y_1=154, x_2=99, y_2=139, color=(255, 51, 102))
    line3 = check_line_for_color(emulator, x_1=40, y_1=146, x_2=95, y_2=149, color=(255, 51, 102))
    if line1 and line2 and line3:
        return False
    return True


def check_if_on_clash_main_menu(emulator) -> bool:
    """Checks if the user is on the clash main menu.
    Returns True if on main menu, False if not.
    """
    return _is_clash_main_menu_image(emulator.screenshot())


def _is_clash_main_menu_image(image) -> bool:
    if image is None or image.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return False
    # At 160 DPI, 9:16 moves fixed-size controls rather than stretching their artwork.
    # Require three independent native anchors plus undimmed yellow button pixels.
    if all(pixel_is_equal(image[y, x], [0, 187, 255], tol=25) for x, y in MAIN_NATIVE_YELLOW_POINTS) and all(
        find_image(image, folder, tolerance=0.95, subcrop=region) is not None
        for folder, region in (
            ("main_battle_button", MAIN_BATTLE_BUTTON_REGION),
            ("main_burger_button", MAIN_BURGER_BUTTON_REGION),
            ("main_battle_tab", MAIN_BATTLE_TAB_REGION),
        )
    ):
        return True
    pixels = [
        image[14][180],
        image[14][279],
        image[19][256],
        image[17][343],
        image[587][224],
        image[590][143],
        image[628][143],
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


def detect_post_battle_reward(image) -> Literal["lucky_chest", "event_reward"] | None:
    """Require a full reward backdrop AND its foreground; color alone is insufficient.

    HSV ranges describe the orange/purple lucky chest and blue framed event
    reward. Attached enlarged images are calibration examples, not templates.
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
            if 12 <= width <= 40 and 12 <= height <= 40 and 0.3 < area / (width * height) < 0.75
        ]
        aligned = 1 <= len(centers) <= 5 and max(y for _, y in centers) - min(y for _, y in centers) < 8
        background = orange if orange is not None else purple
        foreground = 1 - _region(background, POST_BATTLE_REWARD_OBJECT_REGION).mean()
        if aligned and foreground > 0.12:
            return "lucky_chest"
    if blue is not None:
        if (
            find_image(image, "post_battle_coin_reward", tolerance=0.93, subcrop=POST_BATTLE_COIN_REWARD_REGION)
            is not None
        ):
            return "event_reward"
        gold = (hue >= 5) & (hue <= 35) & vivid
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
    pixels = [image[103][41], image[98][54], image[129][61], image[117][50], image[110][43]]
    colors = [[255, 255, 255], [155, 63, 4], [237, 99, 0], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_1v1_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 1v1 game type."""
    image = emulator.screenshot()
    pixels = [image[102][20], image[108][79], image[102][42], image[110][44], image[117][44]]
    colors = [[195, 45, 44], [190, 44, 41], [255, 255, 255], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_2v2_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 2v2 game type."""
    image = emulator.screenshot()
    pixels = [
        image[100][19],
        image[103][73],
        image[100][286],
        image[104][336],
        image[102][43],
        image[109][46],
        image[116][43],
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
    pixels = [image[383][90], image[392][217], image[348][212], image[264][87], image[421][69]]
    colors = [[98, 95, 252], [255, 187, 104], [243, 238, 227], [243, 238, 227], [243, 238, 227]]
    return all_pixels_are_equal(pixels, colors, 25)


CLAN_VOYAGE_PIXELS: list[tuple[tuple[int, int], list[int]]] = [
    ((42, 610), [73, 87, 108]),
    ((150, 610), [78, 175, 255]),
    ((205, 612), [78, 175, 255]),
    ((26, 121), [20, 102, 223]),
    ((291, 122), [20, 102, 221]),
    ((206, 599), [102, 185, 251]),
    ((13, 615), [73, 87, 108]),
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
        iar[42][220],
        iar[41][236],
        iar[41][242],
        iar[42][252],
        iar[44][279],
        iar[32][205],
        iar[34][289],
        iar[51][213],
        iar[50][289],
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
    print(f"[DEBUG] Checking if {mode} is selected...")
    print(f"[DEBUG] Looking in folder: {look_folder}")
    coord = find_image(emulator.screenshot(), look_folder, tolerance=0.9, subcrop=SELECTED_BATTLE_MODE_REGION)
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
        iar[438][50],
        iar[117][51],
        iar[59][70],
        iar[65][154],
        iar[63][93],
        iar[68][125],
        iar[78][159],
        iar[78][72],
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
        iar[59][67],
        iar[60][80],
        iar[61][101],
        iar[61][113],
        iar[61][121],
        iar[64][136],
        iar[66][149],
        iar[65][160],
        iar[68][64],
        iar[68][71],
        iar[68][79],
        iar[68][88],
        iar[73][137],
        iar[73][148],
        iar[72][156],
        iar[73][164],
        iar[589][83],
        iar[593][86],
        iar[601][86],
        iar[614][84],
        iar[607][150],
        iar[603][150],
        iar[598][152],
        iar[596][154],
        iar[593][158],
        iar[591][164],
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
        [(518, 263), (521, 292), (521, 317), (529, 295), (529, 283), (529, 312)],
        [[255, 187, 105], [255, 187, 105], [255, 187, 105], [255, 255, 255], [255, 254, 254], [255, 175, 78]],
    ),
    "info location #2": (
        [(324, 265), (336, 264), (337, 267), (334, 313), (322, 317), (322, 319)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105]],
    ),
    "info location #3": (
        [(299, 266), (311, 264), (314, 320), (306, 315), (300, 294), (308, 284)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105], [255, 255, 255]],
    ),
    "upgrade location #1": (
        [(299, 267), (299, 284), (301, 321), (299, 308), (300, 315)],
        [[119, 235, 107], [98, 194, 89], [119, 235, 107], [250, 254, 250], [201, 226, 198]],
    ),
    "upgrade location #2": (
        [(516, 265), (519, 319), (519, 325), (519, 275), (524, 302), (519, 311)],
        [[119, 235, 107], [119, 235, 107], [119, 235, 107], [254, 255, 254], [255, 255, 255], [248, 253, 248]],
    ),
}


def check_if_on_battle_log_page(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = [
        iar[73][137],
        iar[72][161],
        iar[72][169],
        iar[73][198],
        iar[74][222],
        iar[65][314],
        iar[80][314],
        iar[71][314],
        iar[63][79],
        iar[78][272],
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
    pixels = [iar[596][14], iar[616][16], iar[14][182], iar[15][284], iar[607][110]]
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
        iar[394][153],
        iar[391][174],
        iar[395][207],
        iar[405][154],
        iar[412][204],
        iar[215][295],
        iar[209][299],
        iar[256][67],
        iar[398][272],
        iar[286][292],
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
    (307, 54, 152, 117, 254),
    (326, 58, 154, 119, 253),
    (340, 41, 162, 129, 255),
    (326, 30, 129, 161, 177),
    (18, 613, 74, 88, 110),
    (58, 613, 74, 88, 110),
    (97, 615, 74, 88, 109),
    (134, 611, 75, 89, 110),
    (160, 611, 104, 187, 255),
    (193, 614, 78, 175, 255),
    (222, 615, 74, 88, 109),
    (271, 620, 73, 87, 108),
    (315, 619, 73, 87, 109),
    (343, 619, 73, 87, 109),
    (174, 611, 255, 255, 255),
)
_WAR_PAGE_ALT2_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (204, 608, 75, 111, 146),
    (204, 616, 77, 115, 150),
    (274, 621, 79, 117, 152),
    (272, 612, 77, 113, 148),
    (277, 591, 73, 106, 139),
    (15, 48, 124, 79, 255),
    (15, 37, 141, 101, 255),
    (16, 24, 145, 107, 255),
    (17, 18, 147, 110, 255),
    (342, 54, 136, 96, 254),
    (344, 42, 144, 108, 255),
    (344, 33, 157, 124, 255),
)
_WAR_BOOT_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (20, 608, 75, 89, 111),
    (52, 606, 75, 89, 111),
    (106, 615, 74, 88, 109),
    (238, 615, 74, 88, 109),
    (286, 616, 74, 88, 109),
    (350, 620, 73, 87, 108),
    (155, 615, 78, 175, 255),
    (177, 613, 111, 134, 153),
    (199, 613, 78, 175, 255),
    (127, 394, 255, 190, 43),
    (186, 394, 255, 190, 43),
    (193, 375, 255, 196, 54),
)
_WAR_BOOT_PIXELS_ALT: tuple[tuple[int, int, int, int, int], ...] = (
    (127, 396, 255, 190, 43),
    (142, 397, 255, 190, 43),
    (153, 398, 255, 190, 43),
    (130, 428, 255, 190, 43),
    (135, 410, 224, 167, 38),
    (142, 416, 255, 255, 254),
    (153, 416, 255, 255, 255),
    (174, 421, 255, 255, 255),
    (113, 615, 74, 88, 109),
    (235, 613, 74, 88, 110),
    (257, 613, 74, 88, 110),
    (158, 607, 104, 187, 255),
)
_FINAL_RESULTS_PAGE_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (22, 150, 142, 82, 255),
    (50, 156, 142, 82, 255),
    (64, 156, 142, 82, 255),
    (85, 154, 142, 82, 255),
    (101, 154, 142, 82, 255),
    (117, 154, 142, 82, 255),
    (131, 154, 245, 240, 253),
    (147, 155, 199, 186, 225),
    (156, 156, 255, 255, 255),
    (166, 156, 142, 82, 255),
    (178, 156, 255, 255, 255),
    (198, 156, 245, 240, 253),
    (204, 156, 11, 7, 19),
    (216, 157, 155, 141, 184),
    (233, 161, 216, 205, 238),
    (241, 161, 142, 82, 255),
    (259, 159, 142, 82, 255),
    (282, 157, 142, 82, 255),
    (301, 155, 142, 82, 255),
    (325, 155, 142, 82, 255),
    (339, 155, 142, 82, 255),
    (21, 608, 75, 89, 111),
    (38, 605, 75, 89, 111),
    (63, 605, 75, 89, 111),
    (78, 605, 75, 89, 111),
    (101, 608, 75, 89, 111),
    (115, 611, 75, 89, 110),
    (134, 612, 74, 88, 110),
    (152, 613, 78, 175, 255),
    (174, 616, 255, 255, 255),
    (187, 618, 255, 255, 255),
    (210, 619, 78, 175, 255),
    (231, 619, 73, 87, 109),
    (251, 616, 74, 88, 109),
    (276, 613, 74, 88, 110),
    (298, 609, 75, 89, 110),
    (320, 606, 75, 89, 111),
    (332, 607, 75, 89, 111),
)
_BATTLE_DAYS_STARTED_POPUP_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (46, 313, 142, 82, 255),
    (64, 315, 142, 82, 255),
    (71, 315, 142, 82, 255),
    (95, 316, 2, 1, 2),
    (113, 316, 245, 241, 253),
    (130, 316, 3, 3, 4),
    (150, 318, 60, 35, 108),
    (171, 322, 255, 255, 255),
    (194, 322, 142, 82, 255),
    (210, 323, 255, 255, 255),
    (235, 323, 116, 67, 208),
    (253, 323, 4, 2, 7),
    (271, 323, 142, 82, 255),
    (290, 323, 142, 82, 255),
    (314, 321, 142, 82, 255),
    (328, 319, 142, 82, 255),
    (40, 611, 75, 89, 110),
    (52, 611, 75, 89, 110),
    (71, 608, 75, 89, 111),
    (90, 609, 75, 89, 110),
    (102, 611, 75, 89, 110),
    (120, 611, 75, 89, 110),
    (137, 612, 74, 88, 110),
    (159, 611, 104, 187, 255),
    (180, 611, 255, 255, 255),
    (193, 611, 104, 187, 255),
    (217, 612, 74, 88, 110),
    (237, 613, 74, 88, 110),
    (254, 614, 74, 88, 110),
    (276, 615, 74, 88, 109),
    (300, 614, 74, 88, 110),
    (320, 612, 74, 88, 110),
)
_WAR_DECK_INDICATOR_TOL = 15
_WAR_DECK_INDICATORS: dict[int, tuple[tuple[int, int, int, int, int], ...]] = {
    1: ((0, 94, 207, 58, 546), (0, 108, 211, 55, 508), (255, 255, 255, 67, 525), (0, 93, 207, 76, 546)),
    2: ((0, 108, 211, 131, 508), (255, 255, 255, 143, 524), (0, 109, 211, 155, 506), (0, 101, 209, 130, 526)),
    3: ((0, 109, 211, 207, 508), (255, 255, 255, 218, 524), (0, 94, 207, 229, 543), (0, 109, 211, 230, 507)),
    4: ((0, 109, 211, 283, 506), (255, 255, 255, 295, 526), (0, 94, 207, 302, 544), (0, 109, 211, 304, 507)),
}
CARD_MASTERY_COORD = (292, 445)
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
        >= 25
        for left, top, right, bottom in DECK_CARD_COST_REGIONS
    ):
        return True
    pixels = [
        iar[174][37],
        iar[174][112],
        iar[174][186],
        iar[174][259],
        iar[313][37],
        iar[314][112],
        iar[312][186],
        iar[312][259],
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
    pixel_coords = [(85, 167), (101, 168), (85, 316), (101, 316)]
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
