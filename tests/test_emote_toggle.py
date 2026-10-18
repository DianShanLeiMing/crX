"""Emote opt-in reaches both play modes; disabled sends no emote clicks."""

from contextlib import nullcontext

import pytest

from crX.__main__ import make_job_dictionary
from crX.bot import fight, states
from crX.bot.coords import EMOTE_BUTTON_COORD, EMOTE_ICON_COORDS
from crX.interface.enums import UIField
from crX.utils import caching
from crX.utils.logger import Logger


@pytest.mark.parametrize("value", [None, "true", 0, 1])
def test_emote_setting_requires_boolean(value):
    with pytest.raises(ValueError, match="勾选项"):
        make_job_dictionary({UIField.USE_EMOTES: value})


def test_old_settings_default_off_and_opt_in_is_persisted(tmp_path, monkeypatch):
    assert make_job_dictionary({})[UIField.USE_EMOTES] is False
    monkeypatch.setattr(caching, "top_level", str(tmp_path))
    cache = caching.FileCache("settings.json")
    cache.cache_data(make_job_dictionary({UIField.USE_EMOTES: True}))
    assert make_job_dictionary(cache.load_data())[UIField.USE_EMOTES] is True


@pytest.mark.parametrize("random_mode", [False, True])
@pytest.mark.parametrize("enabled", [False, True])
def test_worker_job_reaches_the_selected_fight_loop(monkeypatch, random_mode, enabled):
    received = []
    monkeypatch.setattr(fight, "card_sample_session", lambda *_: nullcontext())
    monkeypatch.setattr(fight, "wait_for_battle_start", lambda *_: True)
    monkeypatch.setattr(fight.time, "sleep", lambda *_: None)

    def run(*args, use_emotes=False):
        received.append(use_emotes)
        return True

    chosen = "_random_fight_loop" if random_mode else "_fight_loop"
    monkeypatch.setattr(fight, chosen, run)
    jobs = {UIField.USE_EMOTES: enabled, UIField.RANDOM_PLAYS_USER_TOGGLE: random_mode}
    context = states.RuntimeContext(selected_mode="Classic 1v1")
    assert states.state_tree(None, Logger(), "fight", jobs, context) == "end_fight"
    assert received == [enabled]


@pytest.mark.parametrize(
    "random_mode,mode", [(False, "Classic 1v1"), (True, "Classic 1v1"), (False, "Classic 2v2"), (True, "Classic 2v2")]
)
@pytest.mark.parametrize("enabled", [False, True])
def test_actual_play_only_sends_emote_when_enabled(monkeypatch, random_mode, mode, enabled):
    checks = iter((True, False))
    monkeypatch.setattr(fight, "check_for_in_battle_with_delay", lambda *_: next(checks))
    monkeypatch.setattr(fight, "check_if_in_battle", lambda *_: True)
    monkeypatch.setattr(fight, "check_if_battle_has_ended", lambda *_: True)
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda *_: None)
    monkeypatch.setattr(fight, "_maybe_start_fight_recording", lambda *_: None)
    monkeypatch.setattr(fight, "stop_fight_capture", lambda: None)
    monkeypatch.setattr(fight, "wait_for_elixir", lambda *args, **kwargs: True)
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *args, **kwargs: [0])
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *_: ("knight", (200, 350)))
    monkeypatch.setattr(fight, "active_card_samples", lambda: None)
    monkeypatch.setattr(fight.time, "sleep", lambda *_: None)
    monkeypatch.setattr(fight.random, "choice", lambda choices: choices[0])
    # Trigger the existing 10% emote branch; other random ranges use their minimum.
    monkeypatch.setattr(fight.random, "randint", lambda lower, upper: 1 if (lower, upper) == (0, 9) else lower)

    class Device:
        def __init__(self):
            self.clicks = []

        def click(self, x, y):
            self.clicks.append((x, y))

    device, logger = Device(), Logger()
    run = fight._random_fight_loop if random_mode else fight._fight_loop
    assert run(device, logger, False, mode, use_emotes=enabled)
    assert logger.stats["cards_played"] == 1
    assert len(device.clicks) == (4 if enabled else 2)
    if enabled:
        assert device.clicks[-2:] == [EMOTE_BUTTON_COORD, EMOTE_ICON_COORDS[0]]


def test_disabled_emotes_skip_the_random_trigger(monkeypatch):
    def forbidden(*args):
        pytest.fail("Disabled emotes must not draw or click")

    monkeypatch.setattr(fight.random, "randint", forbidden)
    monkeypatch.setattr(fight, "send_emote", forbidden)
    fight._maybe_send_emote(None, Logger(), False)
