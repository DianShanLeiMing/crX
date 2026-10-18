"""Pure state-detection helpers.

Every function here follows the same shape: take a screenshot, run a pixel or
image comparison, return a bool. No clicks, no waits, no orchestration — those
live in nav.py or the per-state modules that import from here.

This module is a leaf: the only crX.bot module it imports from is
`coords` (itself a leaf — pure data, no imports). No nav, fight, deck, etc.
"""

import numpy

from crX.bot.coords import (
    BATTLE_RESULT_BUTTON_REGION,
    BATTLE_REWARD_ANIMATION_REGION,
    BATTLE_REWARD_CROWN_REGION,
    BATTLE_REWARDS_REGION,
    GAME_SCREEN_SIZE,
)
from crX.detection.image_rec import all_pixels_are_equal, check_line_for_color, find_image, pixel_is_equal


def check_if_in_battle(emulator):
    iar_bgr = emulator.screenshot()
    if iar_bgr is None:
        return False
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

    coords_1v1 = [(528, 49), (532, 77), (546, 52), (546, 77), (618, 115)]
    coords_2v2 = [(534, 53), (533, 80), (548, 52), (548, 76), (615, 114)]
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
    iar = emulator.screenshot()
    pixels = [
        iar[592][172],
        iar[617][180],
        iar[607][190],
        iar[603][200],
        iar[596][210],
        iar[593][220],
        iar[600][230],
        iar[610][235],
        iar[623][246],
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
    iar = emulator.screenshot()
    pixels = [
        iar[193][90],
        iar[196][103],
        iar[194][117],
        iar[194][144],
        iar[196][166],
        iar[196][194],
        iar[194][210],
        iar[194][234],
        iar[194][267],
        iar[196][297],
        iar[197][312],
        iar[200][327],
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
    [613, 149],
    [613, 165],
    [613, 188],
    [613, 212],
    [613, 240],
    [613, 262],
    [613, 287],
    [613, 314],
    [613, 339],
    [613, 364],
]
ELIXIR_COLOR = [240, 137, 244]


def count_elixir(emulator, elixir_count) -> bool:
    """Method to check for 4 elixir during a battle"""
    iar = emulator.screenshot()
    if pixel_is_equal(
        iar[ELIXIR_COORDS[elixir_count - 1][0], ELIXIR_COORDS[elixir_count - 1][1]], ELIXIR_COLOR, tol=65
    ):
        return True
    return False


def check_pixels_for_win_in_battle_log(emulator) -> bool:
    """Method to check pixels that appear in the battle
    log to determine if the previous game was a win
    """
    line1 = check_line_for_color(emulator, x_1=47, y_1=135, x_2=109, y_2=154, color=(255, 51, 102))
    line2 = check_line_for_color(emulator, x_1=46, y_1=152, x_2=115, y_2=137, color=(255, 51, 102))
    line3 = check_line_for_color(emulator, x_1=47, y_1=144, x_2=110, y_2=147, color=(255, 51, 102))
    if line1 and line2 and line3:
        return False
    return True


def check_if_on_clash_main_menu(emulator) -> bool:
    """Checks if the user is on the clash main menu.
    Returns True if on main menu, False if not.
    """
    return _is_clash_main_menu_image(emulator.screenshot())


def _is_clash_main_menu_image(image) -> bool:
    if image is None or image.shape[:2] != GAME_SCREEN_SIZE[::-1]:
        return False
    pixels = [
        image[14][209],
        image[14][325],
        image[19][298],
        image[17][399],
        image[581][261],
        image[584][166],
        image[621][166],
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
    return False


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
    pixels = [image[102][48], image[97][63], image[128][71], image[116][58], image[109][50]]
    colors = [[255, 255, 255], [155, 63, 4], [237, 99, 0], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_1v1_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 1v1 game type."""
    image = emulator.screenshot()
    pixels = [image[101][23], image[107][92], image[101][49], image[109][51], image[116][51]]
    colors = [[195, 45, 44], [190, 44, 41], [255, 255, 255], [255, 255, 255], [255, 255, 255]]
    return all_pixels_are_equal(pixels, colors, 25)


def check_if_on_classic_2v2_deck_page(emulator) -> bool:
    """True if the card/deck page is showing the Classic 2v2 game type."""
    image = emulator.screenshot()
    pixels = [
        image[99][22],
        image[102][85],
        image[99][333],
        image[103][391],
        image[101][50],
        image[108][54],
        image[115][50],
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
    pixels = [image[379][105], image[388][252], image[344][247], image[261][101], image[416][80]]
    colors = [[98, 95, 252], [255, 187, 104], [243, 238, 227], [243, 238, 227], [243, 238, 227]]
    return all_pixels_are_equal(pixels, colors, 25)


CLAN_VOYAGE_PIXELS: list[tuple[tuple[int, int], list[int]]] = [
    ((49, 603), [73, 87, 108]),
    ((175, 603), [78, 175, 255]),
    ((239, 605), [78, 175, 255]),
    ((30, 120), [20, 102, 223]),
    ((339, 121), [20, 102, 221]),
    ((240, 592), [102, 185, 251]),
    ((15, 608), [73, 87, 108]),
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
        iar[42][256],
        iar[41][275],
        iar[41][282],
        iar[42][293],
        iar[44][325],
        iar[32][239],
        iar[34][336],
        iar[50][248],
        iar[49][336],
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
    print("[DEBUG] Subcrop: (270, 455, 350, 533)")
    coord = find_image(emulator.screenshot(), look_folder, tolerance=0.9, subcrop=(270, 455, 350, 533))
    print(f"[DEBUG] Found at: {coord}")
    return coord is not None


def check_if_on_card_page(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = [
        iar[433][58],
        iar[116][59],
        iar[58][82],
        iar[64][179],
        iar[62][108],
        iar[67][146],
        iar[77][185],
        iar[77][84],
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
        iar[58][78],
        iar[59][93],
        iar[60][118],
        iar[60][132],
        iar[60][141],
        iar[63][158],
        iar[65][174],
        iar[64][186],
        iar[67][75],
        iar[67][83],
        iar[67][92],
        iar[67][102],
        iar[72][160],
        iar[72][172],
        iar[71][181],
        iar[72][191],
        iar[583][97],
        iar[587][100],
        iar[594][100],
        iar[607][98],
        iar[600][175],
        iar[596][175],
        iar[591][177],
        iar[589][179],
        iar[587][184],
        iar[585][191],
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
        [(512, 306), (515, 340), (515, 369), (523, 343), (523, 329), (523, 363)],
        [[255, 187, 105], [255, 187, 105], [255, 187, 105], [255, 255, 255], [255, 254, 254], [255, 175, 78]],
    ),
    "info location #2": (
        [(320, 308), (332, 307), (333, 311), (330, 364), (318, 369), (318, 371)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105]],
    ),
    "info location #3": (
        [(296, 310), (308, 307), (311, 372), (303, 367), (297, 342), (305, 330)],
        [[255, 187, 105], [255, 175, 78], [255, 175, 78], [255, 187, 105], [255, 187, 105], [255, 255, 255]],
    ),
    "upgrade location #1": (
        [(296, 311), (296, 330), (298, 374), (296, 358), (297, 367)],
        [[119, 235, 107], [98, 194, 89], [119, 235, 107], [250, 254, 250], [201, 226, 198]],
    ),
    "upgrade location #2": (
        [(510, 308), (513, 371), (513, 378), (513, 320), (518, 351), (513, 362)],
        [[119, 235, 107], [119, 235, 107], [119, 235, 107], [254, 255, 254], [255, 255, 255], [248, 253, 248]],
    ),
}


def check_if_on_battle_log_page(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = [
        iar[72][160],
        iar[71][187],
        iar[71][197],
        iar[72][231],
        iar[73][258],
        iar[64][366],
        iar[79][365],
        iar[70][365],
        iar[62][92],
        iar[77][316],
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
    pixels = [iar[589][16], iar[609][19], iar[14][212], iar[15][331], iar[600][128]]
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
        iar[390][178],
        iar[387][203],
        iar[391][241],
        iar[401][179],
        iar[407][238],
        iar[213][343],
        iar[207][348],
        iar[253][78],
        iar[394][316],
        iar[283][340],
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
    (357, 53, 152, 117, 254),
    (379, 57, 154, 119, 253),
    (396, 41, 162, 129, 255),
    (380, 30, 129, 161, 177),
    (21, 606, 74, 88, 110),
    (68, 606, 74, 88, 110),
    (113, 608, 74, 88, 109),
    (156, 604, 75, 89, 110),
    (186, 604, 104, 187, 255),
    (225, 607, 78, 175, 255),
    (258, 608, 74, 88, 109),
    (315, 613, 73, 87, 108),
    (367, 612, 73, 87, 109),
    (399, 612, 73, 87, 109),
    (202, 604, 255, 255, 255),
)
_WAR_PAGE_ALT2_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (238, 601, 75, 111, 146),
    (238, 609, 77, 115, 150),
    (319, 614, 79, 117, 152),
    (316, 605, 77, 113, 148),
    (322, 585, 73, 106, 139),
    (18, 47, 124, 79, 255),
    (18, 37, 141, 101, 255),
    (19, 24, 145, 107, 255),
    (20, 18, 147, 110, 255),
    (398, 53, 136, 96, 254),
    (400, 42, 144, 108, 255),
    (400, 33, 157, 124, 255),
)
_WAR_BOOT_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (23, 601, 75, 89, 111),
    (60, 599, 75, 89, 111),
    (123, 608, 74, 88, 109),
    (277, 608, 74, 88, 109),
    (333, 609, 74, 88, 109),
    (407, 613, 73, 87, 108),
    (180, 608, 78, 175, 255),
    (206, 606, 111, 134, 153),
    (232, 606, 78, 175, 255),
    (148, 390, 255, 190, 43),
    (217, 390, 255, 190, 43),
    (225, 371, 255, 196, 54),
)
_WAR_BOOT_PIXELS_ALT: tuple[tuple[int, int, int, int, int], ...] = (
    (148, 392, 255, 190, 43),
    (165, 393, 255, 190, 43),
    (178, 394, 255, 190, 43),
    (151, 423, 255, 190, 43),
    (157, 406, 224, 167, 38),
    (165, 411, 255, 255, 254),
    (178, 411, 255, 255, 255),
    (203, 416, 255, 255, 255),
    (132, 608, 74, 88, 109),
    (274, 606, 74, 88, 110),
    (299, 606, 74, 88, 110),
    (184, 600, 104, 187, 255),
)
_FINAL_RESULTS_PAGE_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (26, 148, 142, 82, 255),
    (58, 154, 142, 82, 255),
    (74, 154, 142, 82, 255),
    (99, 152, 142, 82, 255),
    (118, 152, 142, 82, 255),
    (136, 152, 142, 82, 255),
    (152, 152, 245, 240, 253),
    (171, 153, 199, 186, 225),
    (182, 154, 255, 255, 255),
    (193, 154, 142, 82, 255),
    (207, 154, 255, 255, 255),
    (230, 154, 245, 240, 253),
    (238, 154, 11, 7, 19),
    (251, 155, 155, 141, 184),
    (271, 159, 216, 205, 238),
    (281, 159, 142, 82, 255),
    (301, 157, 142, 82, 255),
    (328, 155, 142, 82, 255),
    (350, 153, 142, 82, 255),
    (378, 153, 142, 82, 255),
    (394, 153, 142, 82, 255),
    (25, 601, 75, 89, 111),
    (44, 598, 75, 89, 111),
    (73, 598, 75, 89, 111),
    (91, 598, 75, 89, 111),
    (117, 601, 75, 89, 111),
    (134, 604, 75, 89, 110),
    (156, 605, 74, 88, 110),
    (177, 606, 78, 175, 255),
    (202, 609, 255, 255, 255),
    (218, 611, 255, 255, 255),
    (244, 612, 78, 175, 255),
    (269, 612, 73, 87, 109),
    (292, 609, 74, 88, 109),
    (321, 606, 74, 88, 110),
    (347, 602, 75, 89, 110),
    (372, 599, 75, 89, 111),
    (386, 600, 75, 89, 111),
)
_BATTLE_DAYS_STARTED_POPUP_PIXELS: tuple[tuple[int, int, int, int, int], ...] = (
    (53, 310, 142, 82, 255),
    (74, 312, 142, 82, 255),
    (83, 312, 142, 82, 255),
    (110, 313, 2, 1, 2),
    (132, 313, 245, 241, 253),
    (151, 313, 3, 3, 4),
    (175, 315, 60, 35, 108),
    (199, 318, 255, 255, 255),
    (226, 318, 142, 82, 255),
    (244, 319, 255, 255, 255),
    (273, 319, 116, 67, 208),
    (295, 319, 4, 2, 7),
    (315, 319, 142, 82, 255),
    (338, 319, 142, 82, 255),
    (365, 317, 142, 82, 255),
    (382, 316, 142, 82, 255),
    (46, 604, 75, 89, 110),
    (61, 604, 75, 89, 110),
    (83, 601, 75, 89, 111),
    (105, 602, 75, 89, 110),
    (119, 604, 75, 89, 110),
    (140, 604, 75, 89, 110),
    (159, 605, 74, 88, 110),
    (185, 604, 104, 187, 255),
    (209, 604, 255, 255, 255),
    (225, 604, 104, 187, 255),
    (253, 605, 74, 88, 110),
    (276, 606, 74, 88, 110),
    (296, 607, 74, 88, 110),
    (321, 608, 74, 88, 109),
    (349, 607, 74, 88, 110),
    (373, 605, 74, 88, 110),
)
_WAR_DECK_INDICATOR_TOL = 15
_WAR_DECK_INDICATORS: dict[int, tuple[tuple[int, int, int, int, int], ...]] = {
    1: ((0, 94, 207, 68, 540), (0, 108, 211, 64, 502), (255, 255, 255, 78, 519), (0, 93, 207, 89, 540)),
    2: ((0, 108, 211, 153, 502), (255, 255, 255, 167, 518), (0, 109, 211, 180, 500), (0, 101, 209, 151, 520)),
    3: ((0, 109, 211, 241, 502), (255, 255, 255, 254, 518), (0, 94, 207, 267, 537), (0, 109, 211, 268, 501)),
    4: ((0, 109, 211, 329, 500), (255, 255, 255, 343, 520), (0, 94, 207, 352, 538), (0, 109, 211, 354, 501)),
}
CARD_MASTERY_COORD = (340, 440)
CARD_MASTERY_BGR = (95, 214, 251)
CARD_MASTERY_PIXEL_TOLERANCE = 15


def is_deck_full(emulator) -> bool:
    iar = numpy.asarray(emulator.screenshot())
    pixels = [
        iar[172][43],
        iar[172][130],
        iar[172][216],
        iar[172][302],
        iar[310][43],
        iar[311][130],
        iar[309][216],
        iar[309][302],
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
    pixel_coords = [(99, 165), (117, 166), (99, 313), (117, 313)]
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
