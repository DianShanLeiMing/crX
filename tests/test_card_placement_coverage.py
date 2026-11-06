"""Every named deployable card resolves to exactly one usable lane profile."""

from collections import Counter

from crX.bot.card_data import CARD_GROUPS, CARD_NAMES_ZH, PLAY_COORDS
from crX.bot.card_detection import CARD_DETECTION_ALIASES, calculate_play_coords, get_card_group
from crX.bot.coords import GAME_SCREEN_SIZE


def test_named_hand_cards_have_unique_valid_placement_profiles():
    tower_troops = {"tower_princess", "cannoneer", "dagger_duchess", "royal_chef"}
    hand_cards = {CARD_DETECTION_ALIASES.get(card, card) for card in CARD_NAMES_ZH} - tower_troops
    assignments = Counter(card for cards in CARD_GROUPS.values() for card in cards)
    assert set(assignments) == hand_cards
    assert all(count == 1 for count in assignments.values())

    width, height = GAME_SCREEN_SIZE
    for card in hand_cards:
        profile = PLAY_COORDS[get_card_group(card)]
        for side in ("left", "right"):
            points = profile[side]
            assert points
            assert all(0 <= x < width and 0 <= y < height for x, y in points)
            for elapsed in (0, 90, 201):
                assert calculate_play_coords(get_card_group(card), side, elapsed) in points
