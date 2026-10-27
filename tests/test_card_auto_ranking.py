"""Confirmation activates references; runtime predictions remain observations only."""

import json

from crX.bot import card_detection
from crX.bot.card_samples import CardSampleCollector
from crX.utils import card_library as library
from crX.utils.card_review import save_review, scan_grouped_samples, scan_samples
from tests import test_card_library as library_fixtures
from tests.test_card_review import add_group_sample

samples = library_fixtures.samples


def test_legacy_trial_and_covered_entries_migrate_without_independent_evidence(tmp_path, samples, monkeypatch):
    a = samples("source")
    b = samples("source", channel=4, name="sample-001")
    c = samples("source", channel=8, name="sample-002")
    library.update_library(tmp_path, [a, b, c], "enable")
    entries = library.read_library(tmp_path)
    for entry, status in zip(entries, ("trial", "covered", "disabled"), strict=True):
        entry.update(status=status, score=100, validation={}, auto_selected=False)
    library._write_library(tmp_path, entries)
    records, errors, _ = scan_grouped_samples(tmp_path, refresh_library=True)
    assert not errors and len(records) == 3
    entries = library.read_library(tmp_path)
    assert [e["status"] for e in entries] == ["active", "active", "disabled"]
    assert all("score" not in e and "validation" not in e for e in entries)
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    assert len(library.runtime_entries()) == 2


def test_runtime_counts_do_not_promote_disable_or_rank_appearances(tmp_path, samples):
    a = samples("source")
    b = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [a, b], "enable")
    identifier = library.read_library(tmp_path)[0]["id"]
    library.merge_match_stats(tmp_path, {identifier: {"matches": 100, "distance_total": 90000, "margin_total": 50}})
    library.sync_library(tmp_path)
    entries = library.read_library(tmp_path)
    assert all(e["status"] == "active" for e in entries)
    assert entries[0]["runtime"]["matches"] == 100 and entries[1]["runtime"]["matches"] == 0
    assert len(scan_samples(tmp_path)[0]) == 2


def test_pending_sample_cannot_be_enabled_by_a_prediction(tmp_path, monkeypatch):
    source = add_group_sample(tmp_path, "pending", "sample-000")
    library.update_library(tmp_path, [source], "enable")
    assert library.read_library(tmp_path) == []
    library.merge_match_stats(tmp_path, {"0" * 16: {"matches": 100, "distance_total": 0, "margin_total": 10000}})
    library.sync_library(tmp_path)
    assert library.read_library(tmp_path) == [] and scan_samples(tmp_path)[0][0].status == "pending"


def test_correcting_a_conflicting_label_restores_valid_appearance(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    wrong = samples("wrong", card="cannon")
    assert library.read_library(tmp_path)[0]["disabled_reason"] == "conflict"
    save_review(wrong, "confirmed", "knight")
    assert library.read_library(tmp_path)[0]["status"] == "active"


def test_multiple_appearances_and_versions_are_all_loaded(tmp_path, samples, monkeypatch):
    a = samples("source")
    b = samples("source", channel=4, name="sample-001")
    evo = samples("evo", card="evo_knight", channel=8)
    library.update_library(tmp_path, [a, b, evo], "enable")
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    entries = library.runtime_entries()
    assert len(entries) == 3
    for entry in entries:
        collected = [dict(zip(card_detection.COLORS_KEYS, row, strict=True)) for row in entry["fingerprint"]]
        recognition = card_detection.recognize_card_fingerprint(collected)
        assert recognition["source"] == f"local_active:{entry['id']}"
        assert recognition["card_id"] == entry["card_id"]
        ids = [candidate["card_id"] for candidate in recognition["candidates"]]
        assert len(ids) == len(set(ids))


def test_manual_revoke_survives_refresh_and_new_matching_labels(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    library.update_library(tmp_path, [source], "revoke")
    samples("new-confirmation")
    library.sync_library(tmp_path)
    assert library.read_library(tmp_path)[0]["status"] == "disabled"
    library.update_library(tmp_path, [source], "enable")
    assert library.read_library(tmp_path)[0]["status"] == "active"


def test_collector_flushes_telemetry_without_creating_labels_or_images(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    identifier = library.read_library(tmp_path)[0]["id"]
    collector = CardSampleCollector("Classic 1v1", tmp_path)
    try:
        for _ in range(3):
            collector.observe_library_match(
                {"source": f"local_active:{identifier}", "candidates": [{"distance": 30}], "candidate_margin": 150},
                0,
            )
    finally:
        assert collector.close()
    assert library.read_match_stats(tmp_path)[identifier] == {
        **library.empty_match_stats(),
        "matches": 3,
        "distance_total": 90,
        "margin_total": 450,
    }
    assert not collector.directory.exists()
    assert len(scan_samples(tmp_path)[0]) == 1


def test_malformed_runtime_stats_do_not_change_library(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    before = library.library_path(tmp_path).read_bytes()
    (tmp_path / "runtime-matches.json").write_text(json.dumps({"entries": {"broken": {}}}), encoding="utf-8")
    _, errors, _ = scan_grouped_samples(tmp_path, refresh_library=True)
    assert errors and library.library_path(tmp_path).read_bytes() == before


def test_selected_clear_removes_only_associated_references_and_stats(tmp_path, samples):
    a = samples("source")
    b = samples("other", card="cannon", channel=4)
    library.update_library(tmp_path, [a, b], "enable")
    entries = library.read_library(tmp_path)
    for entry in entries:
        library.merge_match_stats(tmp_path, {entry["id"]: {"matches": 1, "distance_total": 10, "margin_total": 10}})
    assert library.clear_filtered_samples(tmp_path, {a.key}) == 1
    assert len(library.read_library(tmp_path)) == 1 and library.read_library(tmp_path)[0]["card_id"] == "cannon"
    assert set(library.read_match_stats(tmp_path)) == {entries[1]["id"]}
    assert b.file("frame").exists()


def test_gameplay_sample_scan_never_writes_library(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    before = library.library_path(tmp_path).read_bytes()
    scan_grouped_samples(tmp_path)
    assert library.library_path(tmp_path).read_bytes() == before


def test_optional_protected_clear_keeps_all_enabled_appearances(tmp_path, samples):
    a = samples("source")
    b = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [a, b], "enable")
    before = library.library_path(tmp_path).read_bytes()
    assert library.clear_filtered_samples(tmp_path, {a.key, b.key}, preserve_selected_cards=True) == 0
    assert library.library_path(tmp_path).read_bytes() == before
    assert a.file("frame").exists() and b.file("frame").exists()


def test_legacy_migration_preserves_one_backup(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    legacy = {"version": 1, "entries": library.read_library(tmp_path)}
    legacy["entries"][0].update(status="trial", validation={}, score=0, auto_selected=False)
    library.library_path(tmp_path).write_text(json.dumps(legacy), encoding="utf-8")
    library.sync_library(tmp_path)
    backup = tmp_path / "fingerprints-before-multi.json"
    assert json.loads(backup.read_text(encoding="utf-8")) == legacy
    before = backup.read_bytes()
    library.update_library(tmp_path, [source], "revoke")
    library.sync_library(tmp_path)
    assert backup.read_bytes() == before
    assert library.read_library(tmp_path)[0]["status"] == "disabled"


def test_reenable_cannot_exceed_eight_appearances_or_corrupt_library(tmp_path, samples):
    rows = [samples("source", channel=i, name=f"sample-{i:03d}") for i in range(9)]
    library.update_library(tmp_path, rows[:8], "enable")
    library.update_library(tmp_path, rows[:1], "revoke")
    library.update_library(tmp_path, rows[8:], "enable")
    result = library.update_library(tmp_path, rows[:1], "enable")
    assert "达到单卡8份上限" in result
    entries = library.read_library(tmp_path)
    assert len(entries) == 9 and sum(e["status"] == "active" for e in entries) == 8
