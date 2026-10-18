"""Offline review workflows: immutable sources, persisted labels and draft exports."""

import json
from pathlib import Path

import cv2
import pytest

from crX.utils.card_review import export_candidate, load_previews, sample_file, save_review, scan_samples


def make_sample(root, *, slot=0, available=True):
    directory = root / "20261017-191852-test"
    directory.mkdir(parents=True, exist_ok=True)
    image = cv2.imread(str(Path(__file__).parent / "fixtures/card-evo-cannon-native-zh.png"))
    for kind in ("frame", "card", "hand"):
        cv2.imwrite(str(directory / f"sample-000-{kind}.png"), image)
    record = {
        "reason": "unknown",
        "mode": "1v1",
        "slot": slot,
        "available": available,
        "captured_at": 1792235992,
        "elapsed_s": 60,
        "label": None,
        "candidates": [{"card_id": "ice_golem", "distance": 2014}],
        "files": {kind: f"sample-000-{kind}.png" for kind in ("frame", "card", "hand")},
    }
    (directory / "samples.jsonl").write_text(json.dumps(record) + "\n", encoding="utf-8")
    return scan_samples(root)[0][0]


def test_scan_live_partial_json_and_existing_review(tmp_path):
    sample = make_sample(tmp_path)
    source = sample.directory / "samples.jsonl"
    with source.open("a", encoding="utf-8") as stream:
        stream.write('{"files":')
    (sample.directory / "review.json").write_text(
        json.dumps(
            {
                sample.name: {
                    "confirmed_card_id": "evo_cannon",
                    "included_in_reference_library": True,
                }
            }
        ),
        encoding="utf-8",
    )
    samples, errors = scan_samples(tmp_path)
    assert len(samples) == 1 and errors == 1
    assert samples[0].status == "included"
    assert samples[0].card_id == "evo_cannon"


def test_confirm_ignore_reset_preserve_source_and_other_reviews(tmp_path):
    sample = make_sample(tmp_path)
    original = (sample.directory / "samples.jsonl").read_bytes()
    save_review(sample, "confirmed", "evo_cannon", " 已核对卡牌信息页 ")
    sample = scan_samples(tmp_path)[0][0]
    assert sample.status == "confirmed" and sample.card_id == "evo_cannon"
    assert sample.review["note"] == "已核对卡牌信息页"
    save_review(sample, "ignored", note="切牌动画")
    sample = scan_samples(tmp_path)[0][0]
    assert sample.status == "ignored"
    save_review(sample, "pending")
    sample = scan_samples(tmp_path)[0][0]
    assert sample.status == "pending" and sample.card_id == ""
    assert (sample.directory / "samples.jsonl").read_bytes() == original
    assert not list(sample.directory.glob("*.tmp"))


def test_confirm_requires_known_id_and_corrupt_annotations_are_not_overwritten(tmp_path):
    sample = make_sample(tmp_path)
    with pytest.raises(ValueError, match="卡名"):
        save_review(sample, "confirmed", "imaginary_card")
    annotations = sample.directory / "annotations.json"
    annotations.write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        save_review(sample, "ignored")
    assert annotations.read_text(encoding="utf-8") == "broken"
    assert scan_samples(tmp_path)[1] == 1


def test_annotations_override_legacy_status(tmp_path):
    sample = make_sample(tmp_path)
    (sample.directory / "review.json").write_text(
        json.dumps(
            {
                sample.name: {
                    "confirmed_card_id": "evo_cannon",
                    "included_in_reference_library": True,
                }
            }
        ),
        encoding="utf-8",
    )
    save_review(sample, "pending")
    assert scan_samples(tmp_path)[0][0].status == "pending"


def test_paths_cannot_escape_session(tmp_path):
    sample = make_sample(tmp_path)
    for filename in ("../outside.png", "nested/card.png", "C:/outside.png"):
        with pytest.raises(ValueError):
            sample_file(sample.directory, filename)
    source = sample.directory / "samples.jsonl"
    record = sample.record | {"files": sample.record["files"] | {"frame": "../outside.png"}}
    source.write_text(json.dumps(record), encoding="utf-8")
    assert scan_samples(tmp_path) == ([], 1)


def test_export_reextracts_bgr_and_remains_unvalidated_draft(tmp_path):
    from crX.bot.card_detection import COLORS_KEYS, extract_card_fingerprint

    sample = make_sample(tmp_path)
    with pytest.raises(ValueError, match="先确认"):
        export_candidate(sample)
    save_review(sample, "confirmed", "evo_cannon")
    sample = scan_samples(tmp_path)[0][0]
    destination = export_candidate(sample)
    exported = json.loads(destination.read_text(encoding="utf-8"))
    assert exported["card_id"] == "evo_cannon"
    assert exported["validated"] is False
    assert exported["color_order"] == "BGR"
    expected = extract_card_fingerprint(cv2.imread(str(sample.file("frame"))), 0)
    assert exported["fingerprint"] == [[corner[color] for color in COLORS_KEYS] for corner in expected]
    assert scan_samples(tmp_path)[0][0].status == "confirmed"


@pytest.mark.parametrize(("slot", "available"), [(None, True), (True, True), (0, False)])
def test_diagnostic_or_unavailable_samples_cannot_become_references(tmp_path, slot, available):
    sample = make_sample(tmp_path, slot=slot, available=available)
    save_review(sample, "confirmed", "evo_cannon")
    with pytest.raises(ValueError, match="单张手牌"):
        export_candidate(scan_samples(tmp_path)[0][0])


def test_missing_images_and_invalid_frame_are_reported(tmp_path):
    sample = make_sample(tmp_path)
    sample.file("card").unlink()
    sample.file("frame").write_bytes(b"broken")
    previews = load_previews(sample)
    assert previews["card"] is None and previews["frame"] is None
    assert previews["hand"] is not None
    save_review(sample, "confirmed", "evo_cannon")
    with pytest.raises(ValueError, match="原始截图"):
        export_candidate(scan_samples(tmp_path)[0][0])


def test_atomic_write_failure_keeps_previous_review(tmp_path, monkeypatch):
    sample = make_sample(tmp_path)
    save_review(sample, "confirmed", "evo_cannon")

    def fail(*_):
        raise OSError("disk full")

    monkeypatch.setattr("crX.utils.card_review.os.replace", fail)
    with pytest.raises(OSError):
        save_review(sample, "ignored")
    assert scan_samples(tmp_path)[0][0].status == "confirmed"
    assert not list(sample.directory.glob("*.tmp"))
