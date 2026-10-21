"""Local candidates use independent human labels and can be individually revoked."""

import json

import numpy as np
import pytest

from crX.bot import card_detection
from crX.utils import card_library as library
from crX.utils.card_review import save_review, scan_samples
from tests.test_card_review import add_group_sample


def fingerprint(channel=0, shift=0):
    value = np.zeros((4, 16), dtype=np.int64)
    value[:, channel] = 891 - shift
    value[:, (channel + 1) % 16] = shift
    return value


@pytest.fixture
def samples(tmp_path, monkeypatch):
    values = {}
    monkeypatch.setattr(library, "sample_fingerprint", lambda sample: values[sample.key])
    monkeypatch.setattr(library, "base_match", lambda _: ("UNKNOWN", 10000))

    def add(session, card="knight", channel=0, shift=0, name="sample-000"):
        sample = add_group_sample(tmp_path, session, name)
        values[sample.key] = fingerprint(channel, shift)
        save_review(sample, "confirmed", card)
        return next(item for item in scan_samples(tmp_path)[0] if item.key == sample.key)

    return add


def test_confirmation_enables_multiple_appearances_and_individual_rollback(tmp_path, samples):
    first = samples("source")
    second = samples("source", shift=200, name="sample-001")
    library.update_library(tmp_path, [first, second], "enable")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["active", "active"]
    assert {sample.status for sample in scan_samples(tmp_path)[0]} == {"included"}
    library.update_library(tmp_path, [first], "revoke")
    library.sync_library(tmp_path)
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["disabled", "active"]
    library.update_library(tmp_path, [first], "enable")
    assert all(entry["status"] == "active" for entry in library.read_library(tmp_path))


def test_new_conflicting_human_label_blocks_only_matching_appearance(tmp_path, samples):
    first = samples("source")
    other = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [first, other], "enable")
    samples("feedback", card="cannon")
    entries = library.read_library(tmp_path)
    assert [e["status"] for e in entries] == ["disabled", "active"]
    assert entries[0]["conflicts"] == ["cannon"]
    library.sync_library(tmp_path)  # Conflicts must not disappear on the next scan.
    assert library.read_library(tmp_path)[0]["status"] == "disabled"


def test_relabel_or_ignore_training_source_disables_candidate(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "enable")
    save_review(source, "ignored")
    assert library.read_library(tmp_path)[0]["status"] == "disabled"
    assert scan_samples(tmp_path)[0][0].status == "ignored"


def test_near_duplicates_merge_without_requiring_other_fights(tmp_path, samples):
    first = samples("source")
    duplicate = samples("independent", shift=5)
    library.update_library(tmp_path, [first, duplicate], "enable")
    entries = library.read_library(tmp_path)
    assert len(entries) == 1 and len(entries[0]["sources"]) == 2
    assert entries[0]["status"] == "active"
    save_review(first, "ignored")
    assert library.read_library(tmp_path)[0]["status"] == "active"  # Another confirmed source remains.


def test_distinct_appearances_are_retained_without_extra_coverage(tmp_path, samples):
    first = samples("source")
    second = samples("source", shift=50, name="sample-001")
    library.update_library(tmp_path, [first, second], "enable")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["active", "active"]


def test_original_library_coverage_does_not_block_manual_reference(tmp_path, samples, monkeypatch):
    first = samples("source")
    monkeypatch.setattr(library, "base_match", lambda _: ("knight", 20))
    library.update_library(tmp_path, [first], "enable")
    assert library.read_library(tmp_path)[0]["status"] == "active"
    assert scan_samples(tmp_path)[0][0].status == "included"


def test_eight_appearance_limit_leaves_extra_samples_confirmed(tmp_path, samples):
    selected = [samples("source", channel=index, name=f"sample-{index:03d}") for index in range(9)]
    result = library.update_library(tmp_path, selected, "enable")
    assert len(library.read_library(tmp_path)) == 8
    assert "达到单卡8份上限" in result
    assert sum(sample.status == "confirmed" for sample in scan_samples(tmp_path)[0]) == 1


def test_runtime_immediately_reads_all_confirmed_appearances_and_reloads_after_revoke(tmp_path, samples, monkeypatch):
    first = samples("source")
    second = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [first, second], "enable")
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    assert len(library.runtime_entries()) == 2
    for channel in (0, 4):
        collected = [dict(zip(card_detection.COLORS_KEYS, row, strict=True)) for row in fingerprint(channel).tolist()]
        result = card_detection.recognize_card_fingerprint(collected)
        assert result["card_id"] == "knight" and result["source"].startswith("local_active:")
    library.update_library(tmp_path, [first], "revoke")
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    assert len(library.runtime_entries()) == 1


def test_invalid_library_falls_back_to_base_and_cannot_be_overwritten(tmp_path, monkeypatch):
    library.library_path(tmp_path).write_text(json.dumps({"entries": [{"status": "trial"}]}), encoding="utf-8")
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    assert library.runtime_entries() == []
    with pytest.raises(ValueError):
        library.update_library(tmp_path, [], "trial")


def test_native_bgr_fingerprint_extraction(tmp_path):
    from crX.utils.card_library import sample_fingerprint

    sample = add_group_sample(tmp_path, "native", "sample-000", slot=1)
    value = sample_fingerprint(sample)
    assert value.shape == (4, 16)
    assert np.all(value.sum(axis=1) == 891)


def test_capture_limits_similar_samples_across_fights(tmp_path):
    import cv2

    from crX.bot.card_samples import CardSampleCollector
    from crX.bot.coords import HAND_CARD_SIZE, HAND_CARD_TOP_LEFTS

    sample = add_group_sample(tmp_path, "old", "sample-000", slot=0)
    frame = cv2.imread(str(sample.file("frame")))
    left, top = HAND_CARD_TOP_LEFTS[0]
    collector = CardSampleCollector("1v1", tmp_path)
    width, height = HAND_CARD_SIZE
    record = {"slot": 0, "available": True, "crop": [left, top, left + width, top + height]}
    try:
        assert collector._keep_representative(frame, record)
        assert collector._keep_representative(frame, record)
        assert not collector._keep_representative(frame, record)
    finally:
        collector.close()


def test_clear_samples_retains_formal_library_after_future_reviews(tmp_path, samples):
    first = samples("source")
    samples("positive")
    samples("negative", card="cannon", channel=3)
    library.update_library(tmp_path, [first], "trial")
    library.update_library(tmp_path, [first], "validate")
    trial = samples("trial-source", channel=5)
    library.update_library(tmp_path, [trial], "trial")
    unrelated = tmp_path / "unrelated"
    unrelated.mkdir()
    (unrelated / "keep.txt").write_text("keep", encoding="utf-8")
    assert library.clear_samples(tmp_path) == 4
    assert not scan_samples(tmp_path)[0]
    entries = library.read_library(tmp_path)
    assert entries[0]["status"] == "active" and entries[0]["sources_cleared"]
    assert entries[1]["status"] == "active" and entries[1]["sources_cleared"]
    assert (unrelated / "keep.txt").read_text(encoding="utf-8") == "keep"
    samples("future", card="cannon", channel=3)
    assert library.read_library(tmp_path)[0]["status"] == "active"
    assert library.clear_samples(tmp_path) == 1
    assert library.clear_samples(tmp_path) == 0


def test_clear_samples_refuses_nested_external_link(tmp_path, samples):
    sample = samples("source")
    outside = tmp_path.parent / (tmp_path.name + "-outside.txt")
    outside.write_text("keep", encoding="utf-8")
    try:
        (sample.directory / "link.txt").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation requires Windows developer mode or elevation")
    with pytest.raises(ValueError, match="链接"):
        library.clear_samples(tmp_path)
    assert outside.read_text(encoding="utf-8") == "keep"
    assert sample.file("frame").exists()
