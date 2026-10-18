"""Diagnostics never change play decisions, request extra frames or block on I/O."""

import json
import threading
import time
from pathlib import Path

import cv2
import numpy as np
import pytest

from crX.bot import card_detection as cd
from crX.bot import card_samples as samples
from crX.bot import fight
from crX.bot.card_data import CARD_FINGERPRINT_VARIANTS, CARD_GROUPS
from crX.utils.logger import Logger

FIXTURE = Path(__file__).parent / "fixtures/card_hand_bgr_regression.png"


def hand():
    image = cv2.imread(str(FIXTURE))
    assert image is not None
    return image


def unknown_fingerprint():
    return [{color: (891 if color == "Red" else 0) for color in cd.COLORS_KEYS} for _ in range(4)]


def test_real_candidate_distances_survive_unknown_rejection():
    recognition = cd.recognize_card_fingerprint(unknown_fingerprint())
    assert recognition["card_id"] == "UNKNOWN"
    assert recognition["candidates"][0]["distance"] > cd.CARD_MATCH_THRESHOLD
    assert len({c["card_id"] for c in recognition["candidates"]}) == 3


def test_added_fingerprints_use_pixel_counts_and_known_card_ids():
    known_cards = {card for cards in CARD_GROUPS.values() for card in cards}
    for card, variants in CARD_FINGERPRINT_VARIANTS.items():
        assert card in known_cards
        for variant in variants:
            values = np.array(variant)
            assert values.shape == (4, len(cd.COLORS_KEYS))
            assert np.all(values >= 0) and np.all(values.sum(axis=1) == 891)


def test_existing_hand_decisions_and_scores_are_consistent():
    frame = hand()
    for slot, expected in enumerate(("lumberjack", "ram_rider", "ice_wizard", "hero_bowler")):
        fingerprint = cd.extract_card_fingerprint(frame, slot)
        result = cd.recognize_card_fingerprint(fingerprint)
        assert result["card_id"] == expected
        assert cd.find_closest_card(fingerprint) == result["fingerprint_id"]
        raw = result["fingerprint_id"]
        _, distance = cd.calculate_offset(
            raw, cd.card_color_data[raw], np.array([list(c.values()) for c in fingerprint])
        )
        assert result["candidates"][0]["distance"] == int(distance)


@pytest.mark.parametrize("number", [1, 2])
def test_native_minion_giant_added_without_changing_other_cards(number):
    frame = cv2.imread(str(FIXTURE.parent / f"card-hand-native-zh-{number}.png"))
    expected = ("little_prince", "minion_giant", "cannon", "barb_barrel")
    for slot, name in enumerate(expected):
        fingerprint = cd.extract_card_fingerprint(frame, slot)
        result = cd.recognize_card_fingerprint(fingerprint)
        assert result["card_id"] == name
        if name == "minion_giant":
            collected = np.array([list(c.values()) for c in fingerprint])
            old_best = min(
                int(cd.calculate_offset(card, data, collected)[1]) for card, data in cd.card_color_data.items()
            )
            assert old_best > cd.CARD_MATCH_THRESHOLD
            assert result["candidates"][0]["source"] == "zh_native_1"
            assert result["candidates"][0]["distance"] < 300


def test_native_evo_cannon_recognized_on_another_frame():
    frame = cv2.imread(str(FIXTURE.parent / "card-evo-cannon-native-zh.png"))
    fingerprint = cd.extract_card_fingerprint(frame, 0)
    collected = np.array([list(c.values()) for c in fingerprint])
    old_best = min(int(cd.calculate_offset(card, data, collected)[1]) for card, data in cd.card_color_data.items())
    assert old_best > cd.CARD_MATCH_THRESHOLD
    result = cd.recognize_card_fingerprint(fingerprint)
    assert result["card_id"] == "evo_cannon"
    assert result["candidates"][0]["distance"] == 34
    assert cd.get_card_group(result["card_id"]) == "defense_building"


def test_verified_new_card_name_and_group():
    from crX.bot.card_data import get_card_display_name

    assert get_card_display_name("minion_giant") == "亡灵巨人"
    assert cd.get_card_group("minion_giant") == "bridge_line"
    assert "baby_dragon" not in CARD_FINGERPRINT_VARIANTS


def test_exact_frame_copied_before_background_write(tmp_path):
    frame = hand()
    original = frame.copy()
    collector = samples.CardSampleCollector("Classic 2v2", tmp_path)
    assert collector.offer(frame, 0, {"reason": "unknown", "candidates": []})
    frame[:] = 0
    assert collector.close()
    rows = [
        json.loads(line) for line in (collector.directory / "samples.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    record = rows[0]
    assert record["label"] is None and record["mode"] == "Classic 2v2"
    assert record["color_order"] == "BGR"
    saved = cv2.imread(str(collector.directory / record["files"]["frame"]))
    assert np.array_equal(saved, original)
    crop = cv2.imread(str(collector.directory / record["files"]["card"]))
    assert np.array_equal(crop, original[529:595, 115:169])
    assert (collector.directory / "index.html").is_file()


def test_duplicates_and_per_fight_limit(tmp_path):
    collector = samples.CardSampleCollector("Trophy Road", tmp_path, max_samples=1)
    frame = hand()
    assert collector.offer(frame, 0, {"reason": "unknown"})
    assert not collector.offer(frame, 0, {"reason": "unknown"})
    assert not collector.offer(frame, 1, {"reason": "unknown"})
    assert collector.close()
    assert collector.stats["saved"] == 1
    assert collector.stats["duplicates"] == 1 and collector.stats["dropped"] == 1


def test_disk_budget_keeps_old_files_and_skips_new_sample(tmp_path):
    old = tmp_path / "keep.txt"
    old.write_text("keep", encoding="utf-8")
    collector = samples.CardSampleCollector("Classic 1v1", tmp_path, max_bytes=10)
    assert collector.offer(hand(), 0, {"reason": "unknown"})
    assert collector.close()
    assert collector.stats["saved"] == 0 and collector.stats["dropped"] == 1
    assert old.read_text(encoding="utf-8") == "keep"
    assert not collector.directory.exists()


def test_disk_error_does_not_escape_to_the_battle(tmp_path, monkeypatch):
    collector = samples.CardSampleCollector("Classic 1v1", tmp_path)
    monkeypatch.setattr(collector, "_save", lambda *_: (_ for _ in ()).throw(OSError("disk failure")))
    assert collector.offer(hand(), 0, {"reason": "unknown"})
    assert collector.close()
    assert collector.stats["errors"] == 1


def test_queue_full_and_close_are_bounded(tmp_path, monkeypatch):
    collector = samples.CardSampleCollector("Classic 1v1", tmp_path)
    entered, release = threading.Event(), threading.Event()
    original_save = collector._save

    def slow_save(*args):
        entered.set()
        release.wait(3)
        return original_save(*args)

    monkeypatch.setattr(collector, "_save", slow_save)
    assert collector.offer(hand(), 0, {"reason": "first"})
    assert entered.wait(2)
    try:
        for index in range(8):
            assert collector.offer(hand(), 0, {"reason": f"queued-{index}"})
        started = time.monotonic()
        assert not collector.offer(hand(), 0, {"reason": "overflow"})
        assert not collector.close(timeout=0.01)
        assert time.monotonic() - started < 0.5
    finally:
        release.set()
        assert collector.close()


def test_real_observation_uses_one_frame_and_leaves_decision_unchanged(tmp_path, monkeypatch):
    frame = hand()
    # An affordable slot with a dark, invalid card image must be sampled as UNKNOWN.
    frame[529:582, 115:169] = 0
    available_before = []

    class Device:
        calls = 0

        def screenshot(self):
            self.calls += 1
            return frame

    device = Device()
    monkeypatch.setattr(samples, "_active", None)
    available_before = cd.check_which_cards_are_available(device)
    collector = samples.start_card_samples("Classic 1v1", tmp_path)
    try:
        calls = device.calls
        assert cd.check_which_cards_are_available(device) == available_before
        assert device.calls == calls + 1
    finally:
        samples.finish_card_samples()
    assert collector.stats["saved"] >= 1
    records = (collector.directory / "samples.jsonl").read_text(encoding="utf-8")
    assert '"reason": "unknown"' in records


def test_empty_hand_sampling_requires_repeated_high_elixir(tmp_path, monkeypatch):
    collector = samples.start_card_samples("Classic 2v2", tmp_path)
    monkeypatch.setattr(collector, "observation_due", lambda: True)
    monkeypatch.setattr(cd, "image_has_elixir", lambda *_: False)
    try:
        for _ in range(4):
            cd._observe_hand_diagnostics(hand(), [], [0] * 4)
        assert collector.stats["queued"] == 0
        monkeypatch.setattr(cd, "image_has_elixir", lambda *_: True)
        for _ in range(3):
            cd._observe_hand_diagnostics(hand(), [], [0] * 4)
        assert collector.stats["queued"] == 1
    finally:
        samples.finish_card_samples()


def test_selected_unknown_is_saved_even_when_periodic_observation_is_throttled(tmp_path, monkeypatch):
    frame = hand()
    frame[529:582, 115:169] = 0

    class Device:
        calls = 0

        def screenshot(self):
            self.calls += 1
            return frame

    collector = samples.start_card_samples("Classic 1v1", tmp_path)
    monkeypatch.setattr(collector, "observation_due", lambda: False)
    device = Device()
    try:
        assert 0 in cd.check_which_cards_are_available(device)
        assert collector.stats["queued"] == 0
        assert cd.identify_hand_cards(device, 0) == "UNKNOWN"
        assert device.calls == 1
    finally:
        samples.finish_card_samples()
    assert collector.stats["saved"] == 1
    record = json.loads((collector.directory / "samples.jsonl").read_text(encoding="utf-8"))
    assert record["trigger"] == "selected_card" and record["available"]


def test_random_play_also_checks_the_selected_card_without_changing_placement(tmp_path, monkeypatch):
    samples.start_card_samples("Classic 2v2", tmp_path)
    calls, clicks = [], []
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *_args, **_kwargs: [0])
    monkeypatch.setattr(fight, "identify_hand_cards", lambda *_args: calls.append("identified") or "UNKNOWN")
    monkeypatch.setattr(fight.time, "sleep", lambda *_: None)

    class Device:
        def click(self, *point):
            clicks.append(point)

    logger = Logger()
    try:
        assert fight.play_random_available_card(Device(), logger, False, 12)
        assert calls == ["identified"] and len(clicks) == 2
        assert logger.stats["cards_played"] == 1
    finally:
        samples.finish_card_samples()


@pytest.mark.parametrize("random_mode,mode", [(False, "Classic 1v1"), (True, "Classic 2v2")])
def test_sampling_lifecycle_for_both_play_modes_on_exception(tmp_path, monkeypatch, random_mode, mode):
    collectors = []
    original_start = samples.start_card_samples

    def start(chosen_mode):
        collector = original_start(chosen_mode, tmp_path)
        collectors.append(collector)
        return collector

    monkeypatch.setattr(samples, "start_card_samples", start)
    monkeypatch.setattr(fight, "wait_for_battle_start", lambda *_: True)

    def fail(*_):
        assert samples.active_card_samples() is collectors[0]
        collectors[0].offer(hand(), 0, {"reason": "unknown"})
        raise RuntimeError("battle failed")

    monkeypatch.setattr(fight, "_fight_loop", fail)
    monkeypatch.setattr(fight, "_random_fight_loop", fail)

    with pytest.raises(RuntimeError, match="battle failed"):
        fight.do_fight_state(None, Logger(), random_mode, mode)
    assert samples.active_card_samples() is None
    assert collectors[0].stats["saved"] == 1
    assert not collectors[0]._thread.is_alive()


def test_wrong_geometry_is_not_interpreted_or_saved(tmp_path):
    collector = samples.CardSampleCollector("Classic 1v1", tmp_path)
    try:
        assert not collector.offer(np.zeros((1600, 900, 3), dtype=np.uint8), 0, {"reason": "unknown"})
        with pytest.raises(ValueError, match="419"):
            cd.extract_card_fingerprint(np.zeros((1600, 900, 3), dtype=np.uint8), 0)
    finally:
        assert collector.close()
