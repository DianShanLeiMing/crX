"""Same-frame baseline comparisons and independent rescue evidence for promotion."""

import json

import numpy as np
import pytest

from crX.bot import card_detection as detection
from crX.bot import card_samples
from crX.utils import card_library as local
from crX.utils.card_review import atomic_json, scan_samples
from tests.test_card_library import fingerprint
from tests.test_card_samples import hand


@pytest.mark.parametrize("baseline", ["UNKNOWN", "knight", "cannon", "spirit_empress"])
def test_baseline_uses_same_fingerprint_and_canonical_card_names(monkeypatch, baseline):
    base = fingerprint(0)
    observed = base.copy()
    observed[0][0] -= 100
    observed[0][1] += 100
    first = fingerprint(15) if baseline == "UNKNOWN" else observed
    raw = "spirit_empress_air" if baseline == "spirit_empress" else baseline
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", np.array([first, fingerprint(14), fingerprint(13)]))
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", (raw, "giant", "zap"))
    monkeypatch.setattr(detection, "_REFERENCE_SOURCES", ("upstream",) * 3)
    monkeypatch.setattr(detection, "runtime_formal_entries", lambda: [])
    monkeypatch.setattr(
        detection,
        "runtime_entries",
        lambda: [
            {"card_id": "knight", "fingerprint": base.tolist(), "status": "active", "id": "0" * 16},
        ],
    )
    result = detection.recognize_card_fingerprint(
        [dict(zip(detection.COLORS_KEYS, row, strict=True)) for row in base.tolist()]
    )
    assert result["card_id"] == "knight" and result["source"].startswith("local_active:")
    assert result["builtin_result"]["card_id"] == baseline
    assert (result["builtin_result"]["distance"] > 1000) == (baseline == "UNKNOWN")


def test_shipped_json_is_part_of_baseline_not_a_local_rescue(monkeypatch):
    base = fingerprint(0).tolist()
    monkeypatch.setattr(
        detection,
        "runtime_formal_entries",
        lambda: [
            {"card_id": "knight", "fingerprint": base, "id": "1" * 16},
        ],
    )
    monkeypatch.setattr(
        detection,
        "runtime_entries",
        lambda: [
            {"card_id": "knight", "fingerprint": base, "status": "active", "id": "0" * 16},
        ],
    )
    result = detection.recognize_card_fingerprint([dict(zip(detection.COLORS_KEYS, row, strict=True)) for row in base])
    assert result["builtin_result"]["card_id"] == "knight"
    assert result["source"] == result["builtin_result"]["source"] == "builtin:" + "1" * 16


def recognition(identifier, *, baseline="UNKNOWN", card="knight"):
    return {
        "card_id": card,
        "source": f"local_active:{identifier}",
        "candidates": [{"card_id": card, "distance": 30}],
        "candidate_margin": 150,
        "builtin_result": {"card_id": baseline, "distance": 1806},
    }


@pytest.fixture
def reference(tmp_path, monkeypatch):
    identifier = "0" * 16
    monkeypatch.setattr(
        local,
        "read_library",
        lambda _root: [
            {
                "id": identifier,
                "card_id": "knight",
                "status": "active",
                "sources": [],
                "fingerprint": fingerprint().tolist(),
            }
        ],
    )
    return identifier


def test_continuous_card_counts_once_across_periodic_selected_and_disk_flush(tmp_path, reference):
    collector = card_samples.CardSampleCollector("1v1", tmp_path)
    result = recognition(reference)
    try:
        for _ in range(10):
            collector.observe_library_match(result, 0)
        collector._flush_match_stats()
        # Temporary unknowns and a different winning appearance of the same card
        # must not manufacture a new card arrival.
        collector.observe_library_match({"card_id": "UNKNOWN", "source": ""}, 0)
        for _ in range(10):
            collector.observe_library_match(result, 0)
        collector.observe_library_match({"card_id": "knight", "source": "upstream"}, 0)
        collector.observe_library_match(result, 0)
        collector.observe_library_match({"card_id": "cannon", "source": "upstream"}, 0)
        collector.observe_library_match(result, 0)
        collector.observe_library_match(result, 1)
    finally:
        assert collector.close()
    stats = local.read_match_stats(tmp_path)[reference]
    assert stats["assists"] == 3 and stats["assist_battles"] == 1
    assert stats["assist_distance_total"] == 90 and stats["assist_margin_total"] == 450
    assert not collector.directory.exists()
    second = card_samples.CardSampleCollector("2v2", tmp_path)
    second.observe_library_match(result, 0)
    assert second.close()
    stats = local.read_match_stats(tmp_path)[reference]
    assert stats["assists"] == 4 and stats["assist_battles"] == 2


@pytest.mark.parametrize("baseline", ["knight", "cannon", None])
def test_redundant_disagreeing_and_legacy_observations_never_become_rescues(tmp_path, reference, baseline):
    collector = card_samples.CardSampleCollector("1v1", tmp_path)
    result = recognition(reference, baseline=baseline)
    if baseline is None:
        result.pop("builtin_result")
    collector.observe_library_match(result, 0)
    assert collector.close()
    stats = local.read_match_stats(tmp_path)[reference]
    assert stats["matches"] == 1 and stats["assists"] == stats["assist_battles"] == 0


def test_old_counts_are_not_reinterpreted_when_first_new_rescue_arrives(tmp_path, reference):
    atomic_json(
        tmp_path / "runtime-matches.json",
        {
            "entries": {
                reference: {"matches": 10000, "distance_total": 500000, "margin_total": 2000000},
            }
        },
    )
    collector = card_samples.CardSampleCollector("1v1", tmp_path)
    collector.observe_library_match(recognition(reference), 0)
    assert collector.close()
    stats = local.read_match_stats(tmp_path)[reference]
    assert stats["matches"] == 10001 and stats["assists"] == stats["assist_battles"] == 1
    assert stats["assist_margin_total"] == 150


@pytest.mark.parametrize("mutation", ["partial", "bool", "negative", "too_many_battles", "oversize"])
def test_invalid_rescue_stats_are_rejected(tmp_path, mutation):
    stats = local.empty_match_stats()
    if mutation == "partial":
        stats.pop("assist_battles")
    elif mutation == "bool":
        stats["assists"] = True
    elif mutation == "negative":
        stats["assist_margin_total"] = -1
    elif mutation == "too_many_battles":
        stats["assist_battles"] = 1
    else:
        stats["assists"] = local.MAX_MATCH_OBSERVATIONS + 1
    atomic_json(tmp_path / "runtime-matches.json", {"entries": {"0" * 16: stats}})
    with pytest.raises(ValueError):
        local.read_match_stats(tmp_path)


def test_disagreement_report_survives_old_image_dedup_and_is_not_repeated(tmp_path, reference, monkeypatch):
    frame = hand()
    first = card_samples.CardSampleCollector("1v1", tmp_path)
    first.offer(frame, 0, {"reason": "unknown", "available": True})
    assert first.close() and first.stats["saved"] == 1
    for index in range(2):
        collector = card_samples.CardSampleCollector("1v1", tmp_path)
        monkeypatch.setattr(card_samples, "_active", collector)
        result = recognition(reference, baseline="cannon")
        try:
            for _ in range(2):
                detection._record_match_sample(frame, 0, [], result, trigger="selected_card", purple_counts=[30] * 4)
        finally:
            assert collector.close()
        assert collector.stats["saved"] == (1 if index == 0 else 0)
    reports, errors = scan_samples(tmp_path)
    assert not errors and len(reports) == 2
    report = next(sample for sample in reports if sample.record["reason"] == "builtin_disagreement")
    assert report.status == "pending" and report.record["builtin_result"]["card_id"] == "cannon"
    assert json.loads((report.directory / "samples.jsonl").read_text(encoding="utf-8"))["card_id"] == "knight"
    assert local.read_match_stats(tmp_path)[reference]["assists"] == 0
