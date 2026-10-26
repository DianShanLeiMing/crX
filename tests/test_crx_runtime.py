"""Reduced pipeline, isolated configuration and bounded recovery."""

from pathlib import Path

import pytest

from crX.__main__ import make_job_dictionary
from crX.bot import states, worker
from crX.interface.config import DEFAULTS
from crX.interface.enums import UIField, has_start_ready_job
from crX.utils import caching, platform
from crX.utils.logger import Logger


def test_shop_only_is_throttled_and_skips_battles(monkeypatch):
    calls = []
    monkeypatch.setattr(states, "shop_daily_state", lambda *a: calls.append("shop") or True)
    jobs = make_job_dictionary({UIField.CLASSIC_1V1_USER_TOGGLE: False, UIField.SHOP_DAILY_OFFER_USER_TOGGLE: True})
    context, logger = states.RuntimeContext(), Logger()
    assert has_start_ready_job(jobs)
    assert states.state_tree(None, logger, "shop_daily", jobs, context) == "select_battle_mode"
    assert states.state_tree(None, logger, "select_battle_mode", jobs, context) == "idle"
    states.state_tree(None, logger, "shop_daily", jobs, context)
    assert calls == ["shop"]
    assert context.last_shop_at is not None
    context.last_shop_at -= states.SHOP_INTERVAL_S + 1
    states.state_tree(None, logger, "shop_daily", jobs, context)
    assert calls == ["shop", "shop"]


def test_modes_cycle_with_independent_deck_positions(monkeypatch):
    monkeypatch.setattr(states, "check_if_battle_mode_is_selected", lambda *a: True)
    decks = []
    monkeypatch.setattr(
        states, "select_deck_state", lambda emu, logger, order: (decks.append(order[0]) or True, order[0])
    )
    jobs = make_job_dictionary(
        {
            UIField.CLASSIC_2V2_USER_TOGGLE: True,
            UIField.CYCLE_DECKS_USER_TOGGLE: True,
            UIField.SELECTED_DECKS: [4, 2, 7],
        }
    )
    context, logger = states.RuntimeContext(), Logger()
    modes = []
    for _ in range(8):
        states.state_tree(None, logger, "select_battle_mode", jobs, context)
        modes.append(context.selected_mode)
        states.state_tree(None, logger, "cycle_deck", jobs, context)
    assert modes == ["Classic 1v1", "Classic 2v2"] * 4
    assert decks == [4, 4, 2, 2, 7, 7, 4, 4]


@pytest.mark.parametrize(
    "values",
    [
        {UIField.SELECTED_DECKS: [0]},
        {UIField.SELECTED_DECKS: [11]},
        {UIField.SELECTED_DECKS: "bad"},
        {UIField.SELECTED_DECKS: [True]},
        {UIField.SELECTED_DECKS: [1.5]},
        {UIField.SELECTED_DECKS: [2, 2]},
        {UIField.SELECTED_DECKS: [[1]]},
        {UIField.SELECTED_DECKS: list(range(1, 12))},
        {UIField.RANDOM_DECKS_USER_TOGGLE: True, UIField.SELECTED_DECKS: []},
        {UIField.CYCLE_DECKS_USER_TOGGLE: True, UIField.SELECTED_DECKS: []},
        {UIField.MUMU_INSTANCE: "all"},
        {UIField.MUMU_INSTANCE: "0,1"},
        {UIField.ADB_SERIAL: "host:99999"},
        {UIField.ADB_SERIAL: "host:1234 & echo injected"},
        {UIField.CLASSIC_1V1_USER_TOGGLE: "false"},
    ],
)
def test_invalid_settings_are_rejected(values):
    with pytest.raises((ValueError, TypeError)):
        make_job_dictionary(values)


def test_removed_settings_cannot_enter_worker_configuration():
    jobs = make_job_dictionary({"war_user_toggle": True, "discord_rpc_toggle": True})
    assert set(jobs) == set(DEFAULTS)
    assert "war_user_toggle" not in jobs


def test_recovery_is_not_reset_by_skipping_states(monkeypatch, tmp_path):
    events = []

    def tree(emu, logger, state, jobs, context):
        events.append(state)
        return "shop_daily" if state == "restart" else "restart"

    monkeypatch.setattr(worker, "state_tree", tree)
    monkeypatch.setattr(worker, "finish_fight_recording", lambda *a: None)
    monkeypatch.setattr("crX.utils.failure_samples.get_failure_samples_dir", lambda: tmp_path / "failures")

    class Signal:
        def is_set(self):
            return False

        def wait(self, timeout):
            pass

    process = worker.WorkerProcess.__new__(worker.WorkerProcess)
    process.shutdown_event = Signal()
    logger = Logger()
    process._run_bot_loop(None, {}, logger)
    assert len(events) == 9
    assert "连续 5 次恢复失败" in logger.current_status


def test_all_dynamic_deck_templates_exist():
    root = Path(__file__).parents[1] / "crX/detection/reference_images/deck_tabs"
    for number in range(1, 11):
        assert list((root / f"deck_{number}").glob("*.png"))


def test_json_settings_merge_and_do_not_load_legacy_pickle(tmp_path, monkeypatch):
    monkeypatch.setattr(caching, "top_level", str(tmp_path))
    (tmp_path / "user_settings.dat").write_bytes(b"untrusted legacy data")
    cache = caching.FileCache("settings.json")
    cache.cache_data({"theme": "darkly"})
    cache.cache_data({"instance": "1"})
    assert cache.load_data() == {"theme": "darkly", "instance": "1"}
    assert list(tmp_path.glob("*.tmp")) == []
    cache.path.write_text('["not settings"]', encoding="utf-8")
    assert cache.load_data() == {}
    assert (tmp_path / "user_settings.dat").exists()


def test_clear_preserves_unrelated_archive_files(tmp_path):
    zips = tmp_path / "zips"
    zips.mkdir()
    (zips / "personal.zip").write_bytes(b"keep")
    (zips / "packs-a__b.zip").write_bytes(b"generated")
    platform.clear_recordings(custom_path=str(tmp_path))
    assert (zips / "personal.zip").read_bytes() == b"keep"
    assert not (zips / "packs-a__b.zip").exists()


def test_clear_skips_linked_packs(tmp_path, monkeypatch):
    pack = tmp_path / "20261014-120000-abc123"
    pack.mkdir()
    (pack / "keep.txt").write_bytes(b"keep")
    monkeypatch.setattr(platform, "_is_link", lambda p: p == pack)
    assert platform.clear_recordings(custom_path=str(tmp_path)) == (0, 0)
    assert (pack / "keep.txt").exists()
