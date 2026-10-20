"""Unknown frames must trigger bounded recovery rather than a completed fight."""

from contextlib import nullcontext

import pytest

from crX.bot import fight, states
from crX.interface.enums import UIField
from crX.utils.logger import Logger


@pytest.fixture
def unknown_battle(monkeypatch):
    monkeypatch.setattr(fight, "card_sample_session", lambda *_: nullcontext())
    monkeypatch.setattr(fight, "wait_for_battle_start", lambda *_: True)
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda *_: None)
    monkeypatch.setattr(fight, "_maybe_start_fight_recording", lambda *_: None)
    monkeypatch.setattr(fight, "check_for_in_battle_with_delay", lambda *_: False)
    monkeypatch.setattr(fight, "check_if_battle_has_ended", lambda *_: False)
    monkeypatch.setattr(fight.time, "sleep", lambda *_: None)


@pytest.mark.parametrize("random_mode", [False, True])
def test_unconfirmed_battle_does_not_advance_to_result_or_count_completion(unknown_battle, random_mode):
    logger, context = Logger(), states.RuntimeContext(selected_mode="Classic 1v1")
    assert (
        states.state_tree(None, logger, "fight", {UIField.RANDOM_PLAYS_USER_TOGGLE: random_mode}, context) == "restart"
    )
    assert context.completed_fights == context.completed_jobs == 0
    assert logger.stats["completed_fights"] == logger.stats["classic_1v1_fights"] == 0


def test_elixir_wait_does_not_report_unknown_frames_as_battle_end(unknown_battle, monkeypatch):
    monkeypatch.setattr(fight, "count_elixir", lambda *_: False)
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *_: [])
    monkeypatch.setattr(fight, "is_hero_champion_ability_visible", lambda *_: False)
    monkeypatch.setattr(fight, "switch_side", lambda: (0, "left"))
    logger = Logger()
    assert fight.wait_for_elixir(None, logger, 7) == "restart"
    assert "尚未确认结算" in logger.current_status
