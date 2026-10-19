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


def test_trial_promotion_and_individual_rollback(tmp_path, samples):
    first = samples("source")
    second = samples("source", shift=200, name="sample-001")
    library.update_library(tmp_path, [first, second], "trial")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["trial", "trial"]
    assert {sample.status for sample in scan_samples(tmp_path)[0]} == {"trial"}
    library.update_library(tmp_path, [first], "validate")
    assert library.read_library(tmp_path)[0]["status"] == "trial"  # training frame is not validation
    samples("positive")
    samples("other-positive", shift=200)
    samples("negative", card="cannon", channel=3)
    message = library.update_library(tmp_path, [first, second], "validate")
    assert "验证通过并保留 2" in message
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["active", "active"]
    library.update_library(tmp_path, [first], "revoke")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["disabled", "active"]
    states = {sample.key: sample.status for sample in scan_samples(tmp_path)[0]}
    assert states[first.key] == "confirmed" and states[second.key] == "included"


def test_new_wrong_card_label_automatically_disables_only_matching_candidate(tmp_path, samples):
    first = samples("source")
    other = samples("source", channel=4, name="sample-001")
    library.update_library(tmp_path, [first, other], "trial")
    samples("feedback", card="cannon")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["disabled", "trial"]
    assert library.read_library(tmp_path)[0]["validation"]["wrong"] == 1


def test_relabel_or_ignore_training_source_disables_candidate(tmp_path, samples):
    source = samples("source")
    library.update_library(tmp_path, [source], "trial")
    save_review(source, "ignored")
    assert library.read_library(tmp_path)[0]["status"] == "disabled"
    assert scan_samples(tmp_path)[0][0].status == "ignored"


def test_near_duplicates_merge_and_keep_other_session_as_validation(tmp_path, samples):
    first = samples("source")
    duplicate = samples("independent", shift=5)
    samples("negative", card="cannon", channel=4)
    library.update_library(tmp_path, [first, duplicate], "trial")
    entries = library.read_library(tmp_path)
    assert len(entries) == 1 and len(entries[0]["sources"]) == 2
    library.update_library(tmp_path, [first], "validate")
    assert library.read_library(tmp_path)[0]["status"] == "active"


def test_candidates_without_extra_coverage_are_not_all_retained(tmp_path, samples):
    first = samples("source")
    second = samples("source", shift=50, name="sample-001")
    samples("positive", shift=10)
    samples("negative", card="cannon", channel=3)
    library.update_library(tmp_path, [first, second], "trial")
    library.update_library(tmp_path, [first, second], "validate")
    assert [entry["status"] for entry in library.read_library(tmp_path)] == ["active", "covered"]


def test_base_library_covered_sample_needs_no_trial(tmp_path, samples, monkeypatch):
    first = samples("source")
    monkeypatch.setattr(library, "base_match", lambda _: ("knight", 20))
    library.update_library(tmp_path, [first], "trial")
    assert library.read_library(tmp_path)[0]["status"] == "covered"
    assert scan_samples(tmp_path)[0][0].status == "covered"


def test_trial_limit_leaves_extra_samples_confirmed(tmp_path, samples):
    selected = [samples("source", channel=index, name=f"sample-{index:03d}") for index in range(7)]
    result = library.update_library(tmp_path, selected, "trial")
    assert len(library.read_library(tmp_path)) == 5
    assert "达到单卡上限 2" in result
    assert sum(sample.status == "confirmed" for sample in scan_samples(tmp_path)[0]) == 2


def test_runtime_reads_local_library_and_reload_after_revoke(tmp_path, samples, monkeypatch):
    first = samples("source")
    library.update_library(tmp_path, [first], "trial")
    monkeypatch.setattr("crX.bot.card_samples.get_card_samples_dir", lambda: tmp_path)
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    collected = [dict(zip(card_detection.COLORS_KEYS, row, strict=True)) for row in fingerprint().tolist()]
    result = card_detection.recognize_card_fingerprint(collected)
    assert result["card_id"] == "knight" and result["source"].startswith("local_trial:")
    library.update_library(tmp_path, [first], "revoke")
    monkeypatch.setattr(library, "_cache", (None, 0, []))
    assert not card_detection.recognize_card_fingerprint(collected)["source"].startswith("local_")


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
    from crX.bot.coords import HAND_CARD_TOP_LEFTS

    sample = add_group_sample(tmp_path, "old", "sample-000", slot=0)
    frame = cv2.imread(str(sample.file("frame")))
    left, top = HAND_CARD_TOP_LEFTS[0]
    collector = CardSampleCollector("1v1", tmp_path)
    record = {"slot": 0, "available": True, "crop": [left, top, left + 54, top + 66]}
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
    assert entries[1]["status"] == "disabled"
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
