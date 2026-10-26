"""Checked deck order, random/cycle precedence and bounded slot-only navigation."""

from unittest.mock import Mock

import pytest

from crX.__main__ import make_job_dictionary
from crX.bot import deck, states
from crX.bot.coords import DECK_TAB_CLICK_OFFSET
from crX.interface.config import DEFAULTS
from crX.interface.enums import UIField
from crX.utils.logger import Logger


def test_cycle_wins_when_both_options_are_enabled(monkeypatch):
    jobs = make_job_dictionary(
        {
            UIField.RANDOM_DECKS_USER_TOGGLE: True,
            UIField.CYCLE_DECKS_USER_TOGGLE: True,
            UIField.SELECTED_DECKS: [4, 2, 7],
        }
    )
    calls = []
    monkeypatch.setattr(states.random, "shuffle", lambda _: pytest.fail("Cycle must not randomize"))

    def choose(emulator, logger, order):
        calls.append(order[0])
        return True, order[0]

    monkeypatch.setattr(states, "select_deck_state", choose)
    context, logger = states.RuntimeContext(selected_mode="Classic 1v1"), Logger()
    for _ in range(4):
        assert states.state_tree(None, logger, "randomize_deck", jobs, context) == "cycle_deck"
        assert states.state_tree(None, logger, "cycle_deck", jobs, context) == "start_fight"
    assert calls == [4, 2, 7, 4]
    assert logger.stats["card_cycles"] == 4
    assert logger.stats["card_randomizations"] == 0


def test_random_uses_only_checked_existing_decks_without_changing_saved_order(monkeypatch):
    jobs = make_job_dictionary({UIField.RANDOM_DECKS_USER_TOGGLE: True, UIField.SELECTED_DECKS: [5, 6, 7]})
    monkeypatch.setattr(states.random, "shuffle", lambda order: order.reverse())
    selector = Mock(return_value=(True, 7))
    monkeypatch.setattr(states, "select_deck_state", selector)
    logger, context = Logger(), states.RuntimeContext(selected_mode="Classic 1v1")
    assert states.state_tree(None, logger, "randomize_deck", jobs, context) == "cycle_deck"
    selector.assert_called_once_with(None, logger, [7, 6, 5])
    assert states.state_tree(None, logger, "cycle_deck", jobs, context) == "start_fight"
    assert selector.call_count == 1
    assert jobs[UIField.SELECTED_DECKS] == [5, 6, 7]
    assert logger.stats["card_randomizations"] == 1
    assert logger.stats["card_cycles"] == 0


def test_cycle_continues_after_the_actual_selected_slot_and_keeps_position_on_failure(monkeypatch):
    jobs = make_job_dictionary({UIField.CYCLE_DECKS_USER_TOGGLE: True, UIField.SELECTED_DECKS: [4, 2, 7]})
    selector = Mock(side_effect=[(True, 2), (False, None), (True, 7)])
    monkeypatch.setattr(states, "select_deck_state", selector)
    logger, context = Logger(), states.RuntimeContext(selected_mode="Classic 1v1")
    assert states.state_tree(None, logger, "cycle_deck", jobs, context) == "start_fight"
    assert states.state_tree(None, logger, "cycle_deck", jobs, context) == "restart"
    assert states.state_tree(None, logger, "cycle_deck", jobs, context) == "start_fight"
    assert [call.args[2] for call in selector.call_args_list] == [[4, 2, 7], [7, 4, 2], [7, 4, 2]]
    assert context.deck_indices["Classic 1v1"] == 0
    assert logger.stats["card_cycles"] == 2


def test_both_options_off_does_not_select_a_deck(monkeypatch):
    selector = Mock()
    monkeypatch.setattr(states, "select_deck_state", selector)
    jobs = make_job_dictionary({UIField.SELECTED_DECKS: []})
    context, logger = states.RuntimeContext(), Logger()
    states.state_tree(None, logger, "randomize_deck", jobs, context)
    states.state_tree(None, logger, "cycle_deck", jobs, context)
    selector.assert_not_called()


@pytest.mark.parametrize(
    ("settings", "expected"),
    [
        ({UIField.CYCLE_DECKS_USER_TOGGLE: True, "max_deck_selection": "4"}, [1, 2, 3, 4]),
        ({UIField.RANDOM_DECKS_USER_TOGGLE: True, "deck_number_selection": "2"}, [2]),
        ({"max_deck_selection": 4, "deck_number_selection": 2, UIField.SELECTED_DECKS: [7, 5]}, [7, 5]),
    ],
)
def test_old_json_settings_migrate_and_new_order_takes_priority(settings, expected):
    jobs = make_job_dictionary(settings)
    assert jobs[UIField.SELECTED_DECKS] == expected
    assert "max_deck_selection" not in jobs
    assert "deck_number_selection" not in jobs


def test_default_deck_lists_are_independent():
    first, second = make_job_dictionary({}), make_job_dictionary({})
    first[UIField.SELECTED_DECKS].append(10)
    assert second[UIField.SELECTED_DECKS] == DEFAULTS[UIField.SELECTED_DECKS] == [1, 2]


@pytest.fixture
def deck_screen(monkeypatch):
    """Fake paged screenshots; only the fake's current page exposes tab targets."""
    emulator = Mock()
    emulator.page = 1
    emulator.screenshot.side_effect = lambda: emulator.page
    monkeypatch.setattr(deck, "is_single_deck_layout_by_pixel", lambda _: False)
    monkeypatch.setattr(deck.time, "sleep", lambda _: None)

    def find(image, folder, **kwargs):
        if folder == "deck_tabs/switch_deck":
            return (300, 100)
        number = int(folder.rsplit("_", 1)[1])
        if (1 if number <= 5 else 2) == image:
            return (number * 10, 100)
        return None

    def switch(emulator, logger):
        emulator.page = 3 - emulator.page
        return True

    monkeypatch.setattr(deck, "find_image", find)
    monkeypatch.setattr(deck, "switch_deck_page", switch)
    return emulator


@pytest.mark.parametrize(("initial_page", "number"), [(1, 7), (2, 2), (2, 10)])
def test_selects_requested_slot_across_pages(deck_screen, monkeypatch, initial_page, number):
    deck_screen.page = initial_page
    monkeypatch.setattr(deck, "is_deck_full", lambda _: True)
    assert deck.find_and_click_deck(deck_screen, Logger(), [number]) == (True, number)
    deck_screen.click.assert_called_once_with(number * 10 + DECK_TAB_CLICK_OFFSET[0], 100 + DECK_TAB_CLICK_OFFSET[1])


def test_incomplete_checked_decks_are_skipped_without_filling_or_using_unchecked_slots(deck_screen, monkeypatch):
    fullness = iter([False, False, True])
    monkeypatch.setattr(deck, "is_deck_full", lambda _: next(fullness))
    assert deck.find_and_click_deck(deck_screen, Logger(), [7, 2, 9]) == (True, 9)
    assert [call.args for call in deck_screen.click.call_args_list] == [
        (number * 10 + DECK_TAB_CLICK_OFFSET[0], 100 + DECK_TAB_CLICK_OFFSET[1]) for number in [7, 2, 9]
    ]


def test_missing_checked_slots_fail_after_a_bounded_search(deck_screen, monkeypatch):
    monkeypatch.setattr(deck, "find_image", lambda *args, **kwargs: None)
    assert deck.find_and_click_deck(deck_screen, Logger(), [5, 6, 7]) == (False, None)
    deck_screen.click.assert_not_called()
    assert deck_screen.screenshot.call_count == 3


@pytest.mark.parametrize(
    ("order", "full", "expected"),
    [([5, 6, 7], True, (False, None)), ([1], False, (False, None)), ([1], True, (True, 1))],
)
def test_single_layout_never_substitutes_an_unchecked_deck(monkeypatch, order, full, expected):
    monkeypatch.setattr(deck, "is_single_deck_layout_by_pixel", lambda _: True)
    monkeypatch.setattr(deck, "is_deck_full", lambda _: full)
    emulator = Mock()
    assert deck.find_and_click_deck(emulator, Logger(), order) == expected
    emulator.click.assert_not_called()


@pytest.mark.parametrize(("selected", "returned"), [(False, True), (True, False)])
def test_deck_selection_requires_a_selected_slot_and_return_to_main(monkeypatch, selected, returned):
    monkeypatch.setattr(deck, "_navigate_to_deck_selection", lambda *args: True)
    monkeypatch.setattr(deck, "find_and_click_deck", lambda *args: (selected, 7 if selected else None))
    monkeypatch.setattr(deck, "return_to_clash_main_from_card_page", lambda *args: returned)
    assert deck.select_deck(Mock(), Logger(), [7]) == (False, None)
