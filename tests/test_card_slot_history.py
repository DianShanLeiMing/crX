"""Recent slot history follows completed play operations, including relaxed choices."""

from collections import deque
from unittest.mock import Mock

import pytest

from crX.bot import fight


@pytest.fixture
def play_setup(monkeypatch):
    monkeypatch.setattr(fight, "last_three_cards", deque([0, 1, 2], maxlen=3))
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *args, **kwargs: [0, 1, 2])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: ("knight", (115, 332)))
    return Mock(), Mock(), fight.BattleStrategy()


def test_relaxed_choices_rotate_instead_of_repeating_the_oldest_slot(play_setup):
    emulator, logger, strategy = play_setup
    for slot, history in [(0, [1, 2, 0]), (1, [2, 0, 1]), (2, [0, 1, 2])]:
        assert fight.play_a_card(emulator, logger, False, strategy)
        assert list(fight.last_three_cards) == history
        assert emulator.click.call_args_list[-2].args == fight.HAND_CARDS_COORDS[slot]


@pytest.mark.parametrize("available, expected", [([0, 1, 2, 3], [1, 2, 3]), ([2], [0, 1, 2])])
def test_history_stays_bounded_and_a_lone_available_slot_remains_playable(play_setup, monkeypatch, available, expected):
    emulator, logger, strategy = play_setup
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *args, **kwargs: available)
    assert fight.play_a_card(emulator, logger, False, strategy)
    assert list(fight.last_three_cards) == expected


def test_missing_placement_does_not_change_history(play_setup, monkeypatch):
    emulator, logger, strategy = play_setup
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *args, **kwargs: [3])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *args: ("knight", None))
    assert not fight.play_a_card(emulator, logger, False, strategy)
    assert list(fight.last_three_cards) == [0, 1, 2]


def test_failed_placement_click_does_not_change_history(play_setup, monkeypatch):
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *args, **kwargs: [3])
    emulator, logger, strategy = play_setup
    emulator.click.side_effect = [None, RuntimeError("placement failed")]
    with pytest.raises(RuntimeError, match="placement failed"):
        fight.play_a_card(emulator, logger, False, strategy)
    assert list(fight.last_three_cards) == [0, 1, 2]
