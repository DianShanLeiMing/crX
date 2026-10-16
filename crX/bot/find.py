"""Pure screen-detection helpers.

Imports only from crX.detection.*, crX.utils.*, and crX.bot.coords.
Never imports from other crX.bot.* modules, with one exception:
detect_upgradable_cards reuses pixel_indicates_upgradable from state_detect (a leaf
predicate module that does not import from find).
"""

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
    iar = emulator.screenshot()
    pixels = [iar[545][178], iar[547][239], iar[553][214], iar[554][201]]
    colors = [[255, 187, 104], [255, 187, 104], [255, 255, 255], [255, 255, 255]]
    pixel_match = True
    for i, p in enumerate(pixels):
        if not pixel_is_equal(p, colors[i], tol=20):
            pixel_match = False
            break
    if pixel_match:
        return (200, 550)
    return find_image(iar, "post_battle_button", tolerance=0.9)


def locate_free_shop_offer_icon(emulator) -> tuple[int, int] | None:
    """Find the free daily shop offer icon anywhere on the current screen.

    Returns (x, y) of the match, or None if not found.
    """
    image = emulator.screenshot()
    return find_image(image, "daily_free_shop_offer_icon", tolerance=0.9)
