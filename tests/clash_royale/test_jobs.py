"""The Clash Royale job suite as one ordered, parametrized test.

Each job/nav module exposes `run_test(emulator, logger) -> (bool, str)`; the
`SUITE` list below is the order (pytest preserves it). Run with `-x` — entries
double as setup for later ones (select_battle_mode leaves a mode the fight tests
need).
"""

from __future__ import annotations

import pytest

from .contract.test_screenshot import run_test as screenshot_contract
from .jobs.test_1v1_fight import run_test as fight_1v1
from .jobs.test_2v2_fight import run_test as fight_2v2
from .jobs.test_cycle_deck import run_test as cycle_deck
from .jobs.test_randomize_deck import run_test as randomize_deck
from .jobs.test_select_battle_mode import run_test as select_battle_mode
from .jobs.test_setup import run_app_installed
from .navigation.test_navigate_main_pages import run_test as nav_main_pages

SUITE = [
    ("app_installed", run_app_installed),
    ("screenshot_contract", screenshot_contract),
    ("nav_main_pages", nav_main_pages),
    ("select_battle_mode", select_battle_mode),
    ("randomize_deck", randomize_deck),
    ("cycle_deck", cycle_deck),
    ("1v1_fight", fight_1v1),
    ("2v2_fight", fight_2v2),
]


@pytest.mark.emulator
@pytest.mark.parametrize("run_test", [fn for _, fn in SUITE], ids=[name for name, _ in SUITE])
def test_clash_job(run_test, emulator, logger):
    ok, msg = run_test(emulator, logger)
    assert ok, msg
