import random
import time
from collections import Counter

import cv2
import numpy

from crX.bot.card_data import (
    CARD_FINGERPRINT_VARIANTS,
    CARD_GROUPS,
    PLAY_COORDS,
    card_color_data,
    get_card_display_name,
)
from crX.bot.card_samples import active_card_samples
from crX.bot.coords import (
    CARD_FINGERPRINT_SIZE,
    CHAMPION_ABILITY_DISMISS_COORD,
    GAME_SCREEN_SIZE,
    HAND_AVAILABLE_TOP_LEFTS,
    HAND_CARD_SIZE,
    HAND_CARD_TOP_LEFTS,
    LEGACY_HAND_CARD_TOP_LEFTS,
    LEGACY_SCREEN_SIZE,
)
from crX.bot.state_detect import image_has_elixir
from crX.utils import reference_hits
from crX.utils.card_library import runtime_entries
from crX.utils.formal_library import _signature as reference_signature
from crX.utils.formal_library import runtime_formal_entries
from crX.utils.reference_preferences import runtime_preferences

toplefts = HAND_CARD_TOP_LEFTS

TOTAL_WIDTH, TOTAL_HEIGHT = CARD_FINGERPRINT_SIZE
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
_REFERENCE_NAMES = tuple(card_color_data)
_REFERENCE_MATRIX = numpy.array(list(card_color_data.values()), dtype=numpy.int64)
_REFERENCE_SOURCES = ("upstream",) * len(_REFERENCE_NAMES)
for _card_id, _variants in CARD_FINGERPRINT_VARIANTS.items():
    for _number, _variant in enumerate(_variants, 1):
        _REFERENCE_NAMES += (_card_id,)
        _REFERENCE_SOURCES += (f"zh_native_{_number}",)
        _REFERENCE_MATRIX = numpy.concatenate((_REFERENCE_MATRIX, numpy.array([_variant], dtype=numpy.int64)))
DIAGNOSTIC_MIN_MARGIN = 100
_reference_ids_cache = (None, (), ())


def _reference_identifiers():
    global _reference_ids_cache
    matrix, names, identifiers = _reference_ids_cache
    if matrix is not _REFERENCE_MATRIX or names != _REFERENCE_NAMES:
        identifiers = tuple(
            reference_signature(CARD_DETECTION_ALIASES.get(name, name), value.tolist())
            for name, value in zip(_REFERENCE_NAMES, _REFERENCE_MATRIX, strict=True)
        )
        _reference_ids_cache = (_REFERENCE_MATRIX, _REFERENCE_NAMES, identifiers)
    return identifiers


def calculate_offset(card_name, card_data, collected_data_array):
    total_offset = 0
    for i, corner_data_array in enumerate(card_data):
        offset = numpy.sum(numpy.abs(collected_data_array[i] - corner_data_array))
        total_offset += offset
    return card_name, total_offset


def recognize_card_fingerprint(collected_data):
    """Expose real distances even beyond the acceptance threshold; keep play policy."""
    collected = numpy.array([[corner[color] for color in COLORS_KEYS] for corner in collected_data], dtype=numpy.int64)
    distances = numpy.abs(_REFERENCE_MATRIX - collected).sum(axis=(1, 2))
    names = _REFERENCE_NAMES
    sources = _REFERENCE_SOURCES
    identifiers = _reference_identifiers()
    formal = runtime_formal_entries()
    if formal:
        formal_distances = numpy.abs(numpy.array([entry["fingerprint"] for entry in formal]) - collected).sum(
            axis=(1, 2)
        )
        distances = numpy.concatenate((distances, formal_distances))
        names += tuple(entry["card_id"] for entry in formal)
        sources += tuple(f"builtin:{entry['id']}" for entry in formal)
        identifiers += tuple(entry["id"] for entry in formal)
    preferences = runtime_preferences()

    def tie_order(index):
        card = CARD_DETECTION_ALIASES.get(names[index], names[index])
        value = preferences.get(card, {})
        identifier = identifiers[index]
        return (
            int(distances[index]),
            0 if value.get("preferred") == identifier else 1,
            -reference_hits._runtime_counts.get(identifier, 0),
            value.get("numbers", {}).get(identifier, index + 1),
            index,
        )

    # Compare the very same frame with the complete shipped library, before locals.
    builtin_index = min(range(len(distances)), key=tie_order)
    builtin_distance = int(distances[builtin_index])
    builtin_card = CARD_DETECTION_ALIASES.get(names[builtin_index], names[builtin_index])
    builtin_result = {
        "card_id": builtin_card if builtin_distance <= CARD_MATCH_THRESHOLD else "UNKNOWN",
        "distance": builtin_distance,
        "source": sources[builtin_index],
    }
    local = runtime_entries()
    if local:
        local_distances = numpy.abs(numpy.array([entry["fingerprint"] for entry in local]) - collected).sum(axis=(1, 2))
        distances = numpy.concatenate((distances, local_distances))
        names += tuple(entry["card_id"] for entry in local)
        sources += tuple(f"local_{entry['status']}:{entry['id']}" for entry in local)
        identifiers += tuple(entry["id"] for entry in local)
    candidates = []
    canonical_seen = set()
    for index in sorted(range(len(distances)), key=tie_order):
        raw_id = names[index]
        canonical_id = CARD_DETECTION_ALIASES.get(raw_id, raw_id)
        if canonical_id in canonical_seen:
            continue
        canonical_seen.add(canonical_id)
        candidates.append(
            {
                "card_id": canonical_id,
                "fingerprint_id": raw_id,
                "source": sources[index],
                "reference_id": identifiers[index],
                "distance": int(distances[index]),
            }
        )
        if len(candidates) == 3:
            break
    best = candidates[0]
    accepted = int(best["distance"]) <= CARD_MATCH_THRESHOLD
    return {
        "card_id": best["card_id"] if accepted else "UNKNOWN",
        "fingerprint_id": best["fingerprint_id"] if accepted else "UNKNOWN",
        "source": best["source"] if accepted else "",
        "reference_id": best["reference_id"] if accepted else "",
        "candidates": candidates,
        "candidate_margin": int(candidates[1]["distance"]) - int(best["distance"]),
        "match_threshold": CARD_MATCH_THRESHOLD,
        "builtin_result": builtin_result,
    }


def find_closest_card(collected_data):
    return recognize_card_fingerprint(collected_data)["fingerprint_id"]


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
    return extract_card_fingerprint(battle_iar, chosen_card_index)


def extract_card_fingerprint(image, chosen_card_index):
    if image is None or image.shape not in ((*GAME_SCREEN_SIZE[::-1], 3), (*LEGACY_SCREEN_SIZE[::-1], 3)):
        raise ValueError("卡牌指纹需要 360×640 BGR 画面, 历史样本兼容 419×633")
    if not 0 <= chosen_card_index < len(toplefts):
        raise ValueError("手牌位置必须为 0–3")
    legacy = image.shape[:2] == LEGACY_SCREEN_SIZE[::-1]
    left, top = (LEGACY_HAND_CARD_TOP_LEFTS if legacy else toplefts)[chosen_card_index]
    width, height = CARD_FINGERPRINT_SIZE if legacy else HAND_CARD_SIZE
    card = image[top : top + height, left : left + width]
    # Keep the 4x16, 891-pixel-per-quadrant schema used by bundled and local libraries.
    # Only the extracted card artwork is normalized; screenshots and device taps stay native.
    card = cv2.resize(card, CARD_FINGERPRINT_SIZE, interpolation=cv2.INTER_NEAREST_EXACT)
    topleft = (0, 0)

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

    return [get_corner_pixels(*corner, card) for corner in corners]


purple_color = numpy.array([255, 43, 227])
card_toplefts = numpy.array(HAND_AVAILABLE_TOP_LEFTS)

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
    pixels = numpy.array([iar[467][278], iar[458][287], iar[467][289]])
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
    if battle_iar is None or battle_iar.shape != (*GAME_SCREEN_SIZE[::-1], 3):
        return card_exists_list

    if check_side:
        global play_side
        _, play_side = switch_side()

    purple_counts = _card_purple_counts(battle_iar)
    for i, count in enumerate(purple_counts):
        if count >= 26:
            card_exists_list.append(i)

    _observe_hand_diagnostics(battle_iar, card_exists_list, purple_counts)
    return card_exists_list


def _card_purple_counts(image):
    counts = []
    for coords in card_coords:
        x_coords, y_coords = coords
        iar_pixels = image[numpy.ix_(y_coords, x_coords)]
        purple_pixels = numpy.all(numpy.abs(iar_pixels - purple_color) <= 30, axis=-1)
        counts.append(int(numpy.sum(purple_pixels)))
    return counts


def _observe_hand_diagnostics(image, available, purple_counts):
    collector = active_card_samples()
    if collector is None or not collector.observation_due():
        return
    if available or not image_has_elixir(image, 5):
        collector.empty_observations = 0
    else:
        collector.empty_observations += 1
        if collector.empty_observations >= 3:
            collector.offer(image, None, {"reason": "no_available_cards_high_elixir", "purple_counts": purple_counts})
    for slot in available:
        fingerprint = extract_card_fingerprint(image, slot)
        recognition = recognize_card_fingerprint(fingerprint)
        _record_match_sample(
            image, slot, fingerprint, recognition, trigger="hand_observation", purple_counts=purple_counts
        )


def _record_match_sample(image, slot, fingerprint, recognition, *, trigger, purple_counts=None):
    collector = active_card_samples()
    if collector is None:
        return
    collector.observe_library_match(recognition, slot)
    if recognition["card_id"] == "UNKNOWN":
        reason = "unknown"
    elif recognition.get("source", "").startswith(("local_trial:", "local_active:")) and recognition.get(
        "builtin_result", {}
    ).get("card_id", "UNKNOWN") not in ("UNKNOWN", recognition["card_id"]):
        reason = "builtin_disagreement"
    elif recognition.get("source", "").startswith("local_trial:"):
        reason = "trial_match"
    elif recognition["candidate_margin"] < DIAGNOSTIC_MIN_MARGIN:
        reason = "low_margin"
    else:
        return
    if purple_counts is None:
        purple_counts = _card_purple_counts(image)
    collector.offer(
        image,
        slot,
        {
            **recognition,
            "reason": reason,
            "trigger": trigger,
            "fingerprint": fingerprint,
            "available": purple_counts[slot] >= 26,
            "purple_counts": purple_counts,
        },
    )


def trigger_hero_champion_ability(emulator, logger) -> None:
    emulator.click(*CHAMPION_ABILITY_DISMISS_COORD)
    logger.change_status("已触发英雄／冠军技能")


def identify_hand_cards(emulator, card_index):
    color_chosen_card = get_all_pixel_data(emulator, card_index)
    recognition = recognize_card_fingerprint(color_chosen_card)
    # A selected-card failure must not be lost to the periodic observation gate.
    _record_match_sample(battle_iar, card_index, color_chosen_card, recognition, trigger="selected_card")
    return recognition["card_id"]


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
                return (random.randint(52, 177), random.randint(446, 461))
            return (random.randint(180, 302), random.randint(446, 461))
        if elapsed_time < 80:  # Less than 2 minutes
            if side_preference == "left":
                return (random.randint(52, 177), random.randint(364, 461))
            return (random.randint(180, 302), random.randint(364, 461))
        # 2 minutes or more
        if side_preference == "left":
            return (random.randint(52, 177), random.randint(284, 461))
        return (random.randint(180, 302), random.randint(284, 461))

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


bridge_pixel = [[86, 202], [236, 202]]


def switch_side():
    assert battle_iar is not None, "battle_iar must be set before calling switch_side()"
    assert bridge_iar is not None, "bridge_iar must be set before calling switch_side()"
    bridge_color_offset = []
    for i, bridge in enumerate(bridge_pixel):
        all_coords = [(y, x) for x in range(bridge[0], bridge[0] + 34) for y in range(bridge[1], bridge[1] + 177)]
        pixel_coords = numpy.array(all_coords)
        iar_pixels = battle_iar[pixel_coords[:, 0], pixel_coords[:, 1]]
        bridge_iar_pixels = bridge_iar[pixel_coords[:, 0], pixel_coords[:, 1]]
        # Normalize the smaller sampled area so existing activity thresholds keep their meaning.
        offset = numpy.linalg.norm(iar_pixels - bridge_iar_pixels)
        bridge_color_offset.append(offset * numpy.sqrt((40 * 175) / (34 * 177)))

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
