"""Test: random selection among existing checked deck slots.

Begins on main, selects from existing checked decks, ends on main.

Precondition: MuMu VM running, signed in to Clash Royale, on the main menu.
"""

from __future__ import annotations

from crX.bot import states
from crX.bot.nav import wait_for_clash_main_menu
from crX.interface.enums import UIField

DECK_ORDER = [1, 2]


def run_test(emulator, logger) -> tuple[bool, str]:
    if not wait_for_clash_main_menu(emulator, logger):
        return (False, "Didn't begin on clash main")

    jobs = {UIField.RANDOM_DECKS_USER_TOGGLE: True, UIField.SELECTED_DECKS: DECK_ORDER}
    if states.state_tree(emulator, logger, "randomize_deck", jobs, states.RuntimeContext()) != "cycle_deck":
        return (False, f"Failed during random deck selection from {DECK_ORDER}")

    if not wait_for_clash_main_menu(emulator, logger):
        return (False, "Didn't end on clash main")

    return (True, "")
