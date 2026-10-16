import random
import time
from collections import Counter

import numpy

from crX.bot.card_data import CARD_GROUPS, PLAY_COORDS, card_color_data, get_card_display_name
from crX.bot.coords import CHAMPION_ABILITY_DISMISS_COORD

toplefts = [
    (115, 529),
    (182, 529),
    (249, 529),
    (316, 529),
]

TOTAL_WIDTH = 54
TOTAL_HEIGHT = 66
HALF_WIDTH = int(TOTAL_WIDTH / 2)
HALF_HEIGHT = int(TOTAL_HEIGHT / 2)

# color stuff
CARD_MATCH_THRESHOLD = 1000

# Spirit Empress hand art switches between ground/air by elixir; match either variant.
CARD_DETECTION_ALIASES: dict[str, str] = {
    "spirit_empress_air": "spirit_empress",
    "spirit_empress_ground": "spirit_empress",
}

COLORS = {
    "Red": [255, 0, 0],
    "Orange": [255, 165, 0],
    "Yellow": [255, 255, 0],
    "Green": [0, 128, 0],
    "Blue": [0, 0, 255],
    "Indigo": [75, 0, 130],
    "Violet": [148, 0, 211],
    "Cyan": [0, 255, 255],
    "Magenta": [255, 0, 255],
    "Pink": [255, 192, 203],
    "Turquoise": [64, 224, 208],
    "Lime": [0, 255, 0],
    "Purple": [128, 0, 128],
    "Brown": [165, 42, 42],
    "Teal": [0, 128, 128],
    "Maroon": [128, 0, 0],
}

COLORS_ARRAY = numpy.array(list(COLORS.values()))
COLORS_KEYS = list(COLORS.keys())

# Pre-compute numpy arrays for each card's corner data
# Build a fresh dict with the transformed values
_card_color_data_transformed: dict[str, list[numpy.ndarray[tuple[int, ...], numpy.dtype[numpy.int64]]]] = {}
for card_name, card_data in card_color_data.items():
    if isinstance(card_data[0], dict):
        _card_color_data_transformed[card_name] = [numpy.array(list(corner.values())) for corner in card_data]
    else:
        # Already transformed
        _card_color_data_transformed[card_name] = card_data
card_color_data: dict[str, list[numpy.ndarray[tuple[int, ...], numpy.dtype[numpy.int64]]]] = (
    _card_color_data_transformed
)


def calculate_offset(card_name, card_data, collected_data_array):
    total_offset = 0
    for i, corner_data_array in enumerate(card_data):
        offset = numpy.sum(numpy.abs(collected_data_array[i] - corner_data_array))
        total_offset += offset
    return card_name, total_offset


def find_closest_card(collected_data):
    best_card = None
    best_offset = CARD_MATCH_THRESHOLD + 1
    # Debug offsets disabled in production
    debug_offsets = None

    collected_data_array = numpy.array(
        [list(corner.values()) for corner in collected_data],
    )

    for card_name, card_data in card_color_data.items():
        card_name, total_offset = calculate_offset(  # noqa: PLW2901
            card_name,
            card_data,
            collected_data_array,
        )
        if total_offset < best_offset:
            best_offset = total_offset
            best_card = card_name
        if debug_offsets is not None:
            debug_offsets.append((card_name, total_offset))

    # Debugging output removed from production.

    if best_offset > CARD_MATCH_THRESHOLD:
        return "UNKNOWN"

    return best_card


# identification methods
def make_pixel_dict_from_color_list(color_list):
    # Initialize the pixel_dict with zeros
    pixel_dict = dict.fromkeys(COLORS.keys(), 0)

    # Count the occurrences of each color in color_list
    color_counts = Counter(color_list)

    # Update the pixel_dict with the counts
    pixel_dict.update(color_counts)

    return pixel_dict


def color_from_pixel(pixels):
    # Calculate the Euclidean distance between the pixels and each color
    distances = numpy.linalg.norm(COLORS_ARRAY - pixels[:, None], axis=2)

    # Find the color with the minimum distance for each pixel
    closest_colors = [COLORS_KEYS[i] for i in numpy.argmin(distances, axis=1)]

    return closest_colors


def get_corner_pixels(x_range, y_range, iar):
    # Get all pixels in the range
    pixels = iar[y_range[0] : y_range[1], x_range[0] : x_range[1]].reshape(-1, 3)

    # Get the color for each pixel
    colors = color_from_pixel(pixels)

    return make_pixel_dict_from_color_list(colors)


def get_all_pixel_data(emulator, chosen_card_index):
    topleft = toplefts[chosen_card_index]

    corners = [
        ((topleft[0], topleft[0] + HALF_WIDTH), (topleft[1], topleft[1] + HALF_HEIGHT)),
        (
            (topleft[0] + HALF_WIDTH, topleft[0] + TOTAL_WIDTH),
            (topleft[1], topleft[1] + HALF_HEIGHT),
        ),
        (
            (topleft[0], topleft[0] + HALF_WIDTH),
            (topleft[1] + HALF_HEIGHT, topleft[1] + TOTAL_HEIGHT),
        ),
        (
            (topleft[0] + HALF_WIDTH, topleft[0] + TOTAL_WIDTH),
            (topleft[1] + HALF_HEIGHT, topleft[1] + TOTAL_HEIGHT),
        ),
    ]

    color_list = [get_corner_pixels(*corner, battle_iar) for corner in corners]

    # print(f"card_name: {color_list},")
    return color_list


purple_color = numpy.array([255, 43, 227])
card_toplefts = numpy.array(
    [
        [133, 582],
        [199, 583],
        [266, 583],
        [334, 582],
    ],
)

# Pre-calculate x_coords and y_coords for each card
card_coords = [
    (
        numpy.arange(topleft[0], topleft[0] + 20),
        numpy.arange(topleft[1], topleft[1] + 20),
    )
    for topleft in card_toplefts
]

play_side = "left"
battle_iar: numpy.ndarray[tuple[int, ...], numpy.dtype[numpy.uint8]] | None = None


def is_hero_champion_ability_visible(emulator) -> bool:
    iar = emulator.screenshot()
    pixels = numpy.array([iar[462][324], iar[453][334], iar[462][336]])
    colors = numpy.array(
        [
            [215, 28, 223],
            [240, 39, 254],
            [239, 40, 251],
        ],
    )

    for p in pixels:
        if numpy.any(numpy.all(numpy.abs(colors - p) <= 30, axis=1)):
            return True

    return False


def check_which_cards_are_available(emulator, check_side=False):
    global battle_iar
    battle_iar = emulator.screenshot()
    card_exists_list = []

    if check_side:
        global play_side
        _, play_side = switch_side()

    for i, coords in enumerate(card_coords):
        x_coords, y_coords = coords
        iar_pixels = battle_iar[numpy.ix_(y_coords, x_coords)]
        purple_pixels = numpy.all(numpy.abs(iar_pixels - purple_color) <= 30, axis=-1)
        count = numpy.sum(purple_pixels)
        if count >= 26:
            card_exists_list.append(i)

    return card_exists_list


def trigger_hero_champion_ability(emulator, logger) -> None:
    emulator.click(*CHAMPION_ABILITY_DISMISS_COORD)
    logger.change_status("已触发英雄／冠军技能")


def identify_hand_cards(emulator, card_index):
    color_chosen_card = get_all_pixel_data(emulator, card_index)
    card_id = find_closest_card(color_chosen_card)
    return CARD_DETECTION_ALIASES.get(card_id, card_id)


# Create the reverse lookup dictionary
CARD_TO_GROUP = {card: group for group, cards in CARD_GROUPS.items() for card in cards}


def get_card_group(card_id) -> str:
    # Use the reverse lookup dictionary for O(1) lookups
    return CARD_TO_GROUP.get(card_id, "No group")


def get_play_coords_for_card(emulator, logger, card_index, elapsed_time: float = 0):
    # get the ID of this card(ram_rider, zap, etc)
    id_cards_start_time = time.time()
    identity = identify_hand_cards(emulator, card_index)
    time_taken = str(time.time() - id_cards_start_time)[:3]
    logger.change_status(f"识别到卡牌：{get_card_display_name(identity)}（耗时 {time_taken} 秒）")

    # get the grouping of this card (hog, turret, spell, etc)
    group = get_card_group(identity)

    # get the play coords of this grouping
    coords = calculate_play_coords(group, play_side, elapsed_time)

    return identity, coords


def calculate_play_coords(card_grouping: str, side_preference: str, elapsed_time: float = 0):
    # if there is a dedicated coordinate for this card
    if card_grouping == "No group":
        if elapsed_time < 12:  # Less than 5 seconds
            if side_preference == "left":
                return (random.randint(60, 206), random.randint(441, 456))
            return (random.randint(210, 351), random.randint(441, 456))
        if elapsed_time < 80:  # Less than 2 minutes
            if side_preference == "left":
                return (random.randint(60, 206), random.randint(360, 456))
            return (random.randint(210, 351), random.randint(360, 456))
        # 2 minutes or more
        if side_preference == "left":
            return (random.randint(60, 206), random.randint(281, 456))
        return (random.randint(210, 351), random.randint(281, 456))

    if PLAY_COORDS.get(card_grouping):
        group_datum = PLAY_COORDS[card_grouping]
        if side_preference == "left" and "left" in group_datum:
            return random.choice(group_datum["left"])
        if side_preference == "right" and "right" in group_datum:
            return random.choice(group_datum["right"])
        if "coords" in group_datum:
            return random.choice(group_datum["coords"])


bridge_iar: numpy.ndarray[tuple[int, ...], numpy.dtype[numpy.uint8]] | None = None


def create_default_bridge_iar(emulator):
    global bridge_iar
    bridge_iar = emulator.screenshot()


bridge_pixel = [[100, 200], [275, 200]]


def switch_side():
    assert battle_iar is not None, "battle_iar must be set before calling switch_side()"
    assert bridge_iar is not None, "bridge_iar must be set before calling switch_side()"
    bridge_color_offset = []
    for i, bridge in enumerate(bridge_pixel):
        all_coords = [(y, x) for x in range(bridge[0], bridge[0] + 40) for y in range(bridge[1], bridge[1] + 175)]
        pixel_coords = numpy.array(all_coords)
        iar_pixels = battle_iar[pixel_coords[:, 0], pixel_coords[:, 1]]
        bridge_iar_pixels = bridge_iar[pixel_coords[:, 0], pixel_coords[:, 1]]
        bridge_color_offset.append(numpy.linalg.norm(iar_pixels - bridge_iar_pixels))

    if bridge_color_offset[0] > bridge_color_offset[1]:
        return bridge_color_offset[0], "left"
    return bridge_color_offset[1], "right"


if __name__ == "__main__":
    all_data = get_all_pixel_data(12, 0)
    for data in all_data:
        id = find_closest_card(data)
        if id == "UNKNOWN":
            print(data)
        print(id)
