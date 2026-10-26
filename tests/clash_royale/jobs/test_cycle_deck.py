"""Test: select_deck_state (deck cycling).

Begins on main, selects deck 1 of a 10-deck cycle, ends on main. Asserts the
returned selected_deck_number is sane (in range 1..deck_count).

Precondition: MuMu VM running, signed in to Clash Royale, on the main menu.
"""

from __future__ import annotations

from crX.bot.deck import select_deck_state
from crX.bot.nav import wait_for_clash_main_menu

DECK_ORDER = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]


def run_test(emulator, logger) -> tuple[bool, str]:
    if not wait_for_clash_main_menu(emulator, logger):
        return (False, "Didn't begin on clash main")

    success, selected_deck_number = select_deck_state(emulator, logger, DECK_ORDER)
    if not success or selected_deck_number is None:
        return (False, f"Failed during select_deck_state(deck_order={DECK_ORDER})")
    if selected_deck_number not in DECK_ORDER:
        return (
            False,
            f"Failed during select_deck_state: selected_deck_number={selected_deck_number} not in {DECK_ORDER}",
        )

    if not wait_for_clash_main_menu(emulator, logger):
        return (False, "Didn't end on clash main")

    return (True, "")
