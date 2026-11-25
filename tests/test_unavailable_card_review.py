"""Unavailable artwork cannot enter or remain in the card-identity approval queue."""

import json
from pathlib import Path

import cv2
import pytest

from crX.bot import card_samples
from crX.bot.card_detection import _recognize_hand_slot
from crX.bot.coords import HAND_CARD_SIZE, HAND_CARD_TOP_LEFTS
from crX.utils import card_workflow as workflow
from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json, scan_samples, unavailable_sample_reason

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.fixture
def store(tmp_path, monkeypatch):
    destination = tmp_path / "formal.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr("crX.utils.card_upload.published_reference_ids", lambda: {})
    return tmp_path / "samples", destination


def sample_at(root, name, *, gray=True, available=True, reason="unknown", slot=0):
    directory = root / name
    directory.mkdir(parents=True)
    image = cv2.imread(
        str(FIXTURES / ("hand-gray-native-20261124.png" if gray else "hand-four-slots-native-20261124.png"))
    )
    assert image is not None
    left, top = HAND_CARD_TOP_LEFTS[slot or 0]
    width, height = HAND_CARD_SIZE
    for kind, pixels in (("frame", image), ("hand", image), ("card", image[top : top + height, left : left + width])):
        assert cv2.imwrite(str(directory / f"sample-000-{kind}.png"), pixels)
    record = {
        "reason": reason,
        "mode": "Classic 1v1",
        "slot": slot,
        "available": available,
        "files": {kind: f"sample-000-{kind}.png" for kind in ("frame", "hand", "card")},
    }
    (directory / "samples.jsonl").write_text(json.dumps(record), encoding="utf-8")
    return next(sample for sample in scan_samples(root)[0] if sample.directory == directory)


@pytest.mark.parametrize("annotated", [False, True])
def test_historical_gray_with_incorrect_available_flag_is_ignored_without_deleting_evidence(store, annotated):
    root, destination = store
    gray = sample_at(root, "gray")
    color = sample_at(root, "color", gray=False)
    if annotated:
        atomic_json(gray.directory / "annotations.json", {gray.name: {"work_result": "pending", "note": "保留备注"}})
    original = {path: path.read_bytes() for path in root.glob("*/*") if path.name != "annotations.json"}
    before = destination.read_bytes()
    samples, errors, _ = workflow.scan_review(root)
    assert not errors
    current = {sample.key: sample for sample in samples}
    assert current[gray.key].status == "ignored" and "不可使用" in current[gray.key].review["work_reason"]
    assert current[color.key].status == "pending"
    if annotated:
        assert current[gray.key].review["note"] == "保留备注"
    assert destination.read_bytes() == before
    assert all(path.read_bytes() == data for path, data in original.items())
    annotations = (gray.directory / "annotations.json").read_bytes()
    workflow.scan_review(root)
    assert (gray.directory / "annotations.json").read_bytes() == annotations


@pytest.mark.parametrize(
    "reason,slot", [("unavailable_hand", None), ("no_available_cards_high_elixir", None), ("unknown", 0)]
)
def test_declared_unavailable_diagnostics_are_not_pending_even_if_artwork_is_color(store, reason, slot):
    root, _ = store
    source = sample_at(root, "source", gray=False, available=False, reason=reason, slot=slot)
    samples, errors, _ = workflow.scan_review(root, cleanup_processed=False)
    assert not errors and len(samples) == 1
    assert samples[0].status == "ignored" and "无需确认" in samples[0].review["work_reason"]
    assert source.file("frame").exists()


def test_direct_confirmation_cannot_promote_gray_art_without_a_prior_refresh(store):
    root, destination = store
    source = sample_at(root, "gray")
    before = destination.read_bytes()
    assert "不可使用" in workflow.confirm_samples(root, [source], "golem", "人工备注")
    current = scan_samples(root)[0][0]
    assert current.status == "ignored" and current.review["note"] == "人工备注"
    assert destination.read_bytes() == before and source.file("frame").exists()


def test_human_approved_reference_is_not_reclassified_or_removed(store):
    root, destination = store
    source = sample_at(root, "approved")
    image = cv2.imread(str(source.file("frame")))
    fingerprint, _ = _recognize_hand_slot(image, 0)
    values = [list(corner.values()) for corner in fingerprint]
    entry = {"id": formal._signature("golem", values), "card_id": "golem", "fingerprint": values}
    atomic_json(destination, {"version": 1, "entries": [entry]})
    atomic_json(
        source.directory / "annotations.json",
        {
            source.name: {
                "work_result": "included",
                "confirmed_card_id": "golem",
                "formal_id": entry["id"],
                "owns_reference": True,
                "coverage_policy": workflow.COVERAGE_POLICY,
            }
        },
    )
    before = destination.read_bytes()
    samples, errors, _ = workflow.scan_review(root, reassess_history=False, cleanup_processed=False)
    assert not errors and samples[0].status == "included"
    assert destination.read_bytes() == before


def test_usable_unknown_single_card_still_reaches_review_and_frame_cache_rechecks_edits(tmp_path):
    source = sample_at(tmp_path, "gray")
    assert "灰态" in unavailable_sample_reason(source)
    frame = cv2.imread(str(FIXTURES / "hand-four-slots-native-20261124.png"))
    assert cv2.imwrite(str(source.file("frame")), frame)
    assert unavailable_sample_reason(source) == ""
    collector = card_samples.CardSampleCollector("Classic 1v1", tmp_path / "new")
    try:
        assert collector.offer(frame, 0, {"reason": "unknown", "available": True})
    finally:
        assert collector.close()
    records, errors = scan_samples(collector.root)
    assert not errors and len(records) == 1 and records[0].status == "pending"
