"""Automatic selection uses independent human labels; runtime predictions are telemetry only."""

import json

from crX.bot.card_samples import CardSampleCollector
from crX.utils import card_library as library
from crX.utils.card_review import scan_grouped_samples, scan_samples
from tests import test_card_library as library_fixtures

samples = library_fixtures.samples


def test_scanning_scores_and_selects_one_best_candidate_with_independent_labels(tmp_path, samples):
    first = samples("source")
    second = samples("source", shift=100, name="sample-001")
    library.update_library(tmp_path, [first, second], "trial")
    samples("positive-a", shift=10)
    samples("positive-b", shift=20)
    samples("negative", card="cannon", channel=3)
    records, errors, _ = scan_grouped_samples(tmp_path, rank_library=True)
    assert not errors
    entries = library.read_library(tmp_path)
    selected = [entry for entry in entries if entry.get("auto_selected")]
    assert len(selected) == 1 and selected[0]["status"] == "active"
    assert selected[0]["sources"] == [first.key]
    assert selected[0]["score"] > entries[1]["score"]
    shown = next(sample for sample in records if sample.key == first.key)
    assert shown.review["library_selected"] and shown.review["library_score"] == selected[0]["score"]


def test_runtime_score_never_promotes_without_independent_validation(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    entry = library.read_library(tmp_path)[0]
    library.merge_match_stats(tmp_path, {entry["id"]: {"matches": 100, "distance_total": 0, "margin_total": 50000}})
    library.refresh_ranked_library(tmp_path)
    entry = library.read_library(tmp_path)[0]
    assert entry["runtime"]["matches"] == 100 and entry["score"] == 20
    assert entry["status"] == "trial" and not entry["auto_selected"]
    assert len(scan_samples(tmp_path)[0]) == 1  # Runtime observations do not create human labels.


def test_runtime_rejects_high_score_unverified_and_unselected_entries(tmp_path, samples, monkeypatch):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    entry = library.read_library(tmp_path)[0]
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    for status, selected, validation in (
        ("trial", False, {}),
        ("active", True, {}),
        ("active", True, {"correct": 1, "extra": 1, "other_cards": 1, "wrong": 1}),
        ("active", False, {"correct": 1, "extra": 1, "other_cards": 1, "wrong": 0}),
    ):
        library._write_library(
            tmp_path, [{**entry, "status": status, "auto_selected": selected, "score": 100, "validation": validation}]
        )
        monkeypatch.setattr(library, "_cache", (None, 0, []))
        assert library.runtime_entries() == []


def test_highest_score_must_be_qualified_and_wrong_match_switches_to_backup(tmp_path, samples, monkeypatch):
    first = samples("source")
    second = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [first, second], "trial")
    samples("positive-a")
    samples("positive-b", channel=4)
    samples("negative", card="cannon", channel=8)
    library.refresh_ranked_library(tmp_path)
    old = next(entry for entry in library.read_library(tmp_path) if entry.get("auto_selected"))
    samples("wrong", card="cannon", channel=0 if old["sources"] == [first.key] else 4)
    library.refresh_ranked_library(tmp_path)
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    runtime = library.runtime_entries()
    assert len(runtime) == 1 and runtime[0]["id"] != old["id"]
    assert runtime[0]["auto_selected"] and runtime[0]["validation"]["wrong"] == 0


def test_runtime_deduplicates_same_card_without_merging_versions(tmp_path, samples, monkeypatch):
    first = samples("source")
    second = samples("source", channel=4, name="sample-001")
    evo = samples("evo", card="evo_knight", channel=8)
    library.update_library(tmp_path, [first, second, evo], "trial")
    entries = library.read_library(tmp_path)
    for entry, score in zip(entries, (70, 90, 80), strict=True):
        entry.update(
            status="active",
            auto_selected=True,
            score=score,
            validation={"correct": 1, "extra": 1, "other_cards": 1, "wrong": 0},
        )
    library._write_library(tmp_path, entries)
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    runtime = library.runtime_entries()
    assert len(runtime) == 2 and {entry["card_id"] for entry in runtime} == {"knight", "evo_knight"}
    assert next(entry for entry in runtime if entry["card_id"] == "knight")["score"] == 90


def test_runtime_uses_only_selected_candidate_after_auto_ranking(tmp_path, samples, monkeypatch):
    first = samples("source")
    second = samples("source", shift=100, name="sample-001")
    library.update_library(tmp_path, [first, second], "trial")
    samples("positive", shift=10)
    samples("negative", card="cannon", channel=3)
    library.refresh_ranked_library(tmp_path)
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    runtime = library.runtime_entries()
    assert len(runtime) == 1 and runtime[0]["sources"] == [first.key]


def test_runtime_quality_changes_score_but_wrong_label_disables_candidate(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    entry = library.read_library(tmp_path)[0]
    library.merge_match_stats(tmp_path, {entry["id"]: {"matches": 2, "distance_total": 100, "margin_total": 200}})
    samples("positive", shift=5)
    samples("negative", card="cannon", channel=3)
    library.refresh_ranked_library(tmp_path)
    assert library.read_library(tmp_path)[0]["auto_selected"]
    samples("wrong", card="cannon")
    library.refresh_ranked_library(tmp_path)
    entry = library.read_library(tmp_path)[0]
    assert entry["status"] == "disabled" and not entry["auto_selected"]


def test_collector_flushes_bounded_telemetry_without_source_images(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    identifier = library.read_library(tmp_path)[0]["id"]
    collector = CardSampleCollector("Classic 1v1", tmp_path)
    try:
        for _ in range(3):
            collector.observe_library_match(
                {"source": f"local_trial:{identifier}", "candidates": [{"distance": 30}], "candidate_margin": 150}
            )
    finally:
        assert collector.close()
    record = library.read_match_stats(tmp_path)[identifier]
    assert record == {"matches": 3, "distance_total": 90, "margin_total": 450}
    assert not collector.directory.exists()


def test_malformed_runtime_stats_are_reported_without_changing_library(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    before = library.library_path(tmp_path).read_bytes()
    (tmp_path / "runtime-matches.json").write_text(json.dumps({"entries": {"broken": {}}}), encoding="utf-8")
    _, errors, _ = scan_grouped_samples(tmp_path, rank_library=True)
    assert errors and library.library_path(tmp_path).read_bytes() == before


def test_clear_filters_also_remove_candidate_runtime_stats(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    identifier = library.read_library(tmp_path)[0]["id"]
    library.merge_match_stats(tmp_path, {identifier: {"matches": 1, "distance_total": 10, "margin_total": 10}})
    library.clear_filtered_samples(tmp_path, {source.key})
    assert not library.read_match_stats(tmp_path)


def test_gameplay_sample_scan_never_writes_ranked_library(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    samples("positive")
    samples("negative", card="cannon", channel=3)
    before = library.library_path(tmp_path).read_bytes()
    scan_grouped_samples(tmp_path)
    assert library.library_path(tmp_path).read_bytes() == before
    scan_grouped_samples(tmp_path, rank_library=True)
    assert library.read_library(tmp_path)[0]["auto_selected"]


def test_trial_stage_clear_preserves_selected_source_and_library(tmp_path, samples):
    chosen = samples("source")
    trial = samples("source", shift=100, name="sample-001")
    library.update_library(tmp_path, [chosen, trial], "trial")
    samples("positive", shift=10)
    samples("negative", card="cannon", channel=3)
    library.refresh_ranked_library(tmp_path)
    selected = next(entry for entry in library.read_library(tmp_path) if entry.get("auto_selected"))
    assert library.clear_filtered_samples(tmp_path, {chosen.key, trial.key}, statuses={"trial", "covered"}) == 1
    remaining = {sample.key: sample for sample in scan_samples(tmp_path)[0]}
    assert remaining[chosen.key].status == "included" and trial.key not in remaining
    assert chosen.file("frame").exists()
    assert library.read_library(tmp_path) == [selected]


def test_summary_clear_rechecks_selection_and_preserves_all_working_card_candidates(tmp_path, samples):
    chosen = samples("source")
    backup = samples("source", shift=100, name="sample-001")
    library.update_library(tmp_path, [chosen, backup], "trial")
    # Simulate selection changing after the UI prepared its clearing confirmation.
    keys = {chosen.key, backup.key}
    samples("positive", shift=10)
    samples("negative", card="cannon", channel=3)
    library.refresh_ranked_library(tmp_path)
    before = library.library_path(tmp_path).read_bytes()
    assert (
        library.clear_filtered_samples(tmp_path, keys, statuses={"trial", "covered"}, preserve_selected_cards=True) == 0
    )
    assert chosen.file("frame").exists() and backup.file("frame").exists()
    assert library.library_path(tmp_path).read_bytes() == before
