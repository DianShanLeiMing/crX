"""Offline review workflows: immutable sources, persisted labels and draft exports."""

import json
from pathlib import Path

import cv2
import pytest

from crX.utils.card_review import (
    export_candidate,
    group_similar_samples,
    load_previews,
    sample_file,
    save_review,
    save_reviews,
    scan_grouped_samples,
    scan_samples,
)


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


def add_group_sample(root, session, name, *, slot=0, pixels=None, available=True):
    """Use native card art; the optional pixels argument simulates another capture."""
    directory = root / session
    directory.mkdir(parents=True, exist_ok=True)
    frame = cv2.imread(str(Path(__file__).parent / "fixtures/card-evo-cannon-native-zh.png"))
    if pixels is None:
        pixels = frame[529:595, 115:169]
    for kind, image in (("card", pixels), ("frame", frame), ("hand", frame[529:595])):
        cv2.imwrite(str(directory / f"{name}-{kind}.png"), image)
    record = {
        "reason": "unknown",
        "mode": "1v1",
        "slot": slot,
        "available": available,
        "candidates": [{"card_id": "ice_golem", "distance": 2014}],
        "files": {kind: f"{name}-{kind}.png" for kind in ("frame", "card", "hand")},
    }
    with (directory / "samples.jsonl").open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record) + "\n")
    return next(sample for sample in scan_samples(root)[0] if sample.directory == directory and sample.name == name)


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


def test_visual_groups_cross_sessions_slots_and_candidates(tmp_path):
    import numpy as np

    first = add_group_sample(tmp_path, "session-a", "sample-000")
    image = cv2.imread(str(first.file("card")))
    second = add_group_sample(
        tmp_path, "session-b", "sample-000", slot=3, pixels=np.clip(image.astype(int) + 3, 0, 255).astype("uint8")
    )
    second.record["candidates"] = [{"card_id": "tesla", "distance": 2200}]
    different = add_group_sample(tmp_path, "session-b", "sample-001", pixels=image[:, ::-1])
    groups = group_similar_samples([first, second, different])
    assert groups[first.key] == [first.key, second.key]
    assert groups[different.key] == [different.key]
    assert not first.card_id and not second.card_id
    samples, errors, scanned_groups = scan_grouped_samples(tmp_path)
    assert len(samples) == 3 and errors == 0 and sorted(map(len, scanned_groups.values())) == [1, 2]


@pytest.mark.parametrize("kind", ["diagnostic", "unavailable", "missing", "corrupt", "wrong_size"])
def test_unsuitable_crops_stay_separate(tmp_path, kind):
    first = add_group_sample(tmp_path, "session", "sample-000")
    second = add_group_sample(tmp_path, "session", "sample-001")
    if kind == "diagnostic":
        second.record["slot"] = None
    elif kind == "unavailable":
        second.record["available"] = False
    elif kind == "missing":
        second.file("card").unlink()
    elif kind == "corrupt":
        second.file("card").write_bytes(b"broken")
    else:
        cv2.imwrite(str(second.file("card")), cv2.imread(str(first.file("frame"))))
    assert list(group_similar_samples([first, second]).values()) == [[first.key], [second.key]]


def test_groups_do_not_combine_conflicting_labels_or_chain_neighbours(tmp_path):
    import numpy as np

    samples = [add_group_sample(tmp_path, "session", f"sample-{index:03d}") for index in range(3)]
    samples[1].review = {"confirmed_card_id": "cannon"}
    samples[2].review = {"confirmed_card_id": "evo_cannon"}
    groups = group_similar_samples(samples)
    assert list(groups.values()) == [[samples[0].key, samples[1].key], [samples[2].key]]
    for index, sample in enumerate(samples):
        sample.review = {}
        # All hashes are equal, but the third image is too far from the first.
        cv2.imwrite(str(sample.file("card")), np.full((66, 54, 3), 80 + 10 * index, dtype=np.uint8))
    assert list(group_similar_samples(samples).values()) == [[samples[0].key, samples[1].key], [samples[2].key]]


def test_bulk_save_preserves_sources_unselected_reviews_and_can_reset(tmp_path):
    first = add_group_sample(tmp_path, "session-a", "sample-000")
    other = add_group_sample(tmp_path, "session-a", "sample-001")
    second = add_group_sample(tmp_path, "session-b", "sample-000")
    save_review(other, "confirmed", "knight", "keep")
    sources = {p: p.read_bytes() for p in tmp_path.rglob("*") if p.is_file() and p.name != "annotations.json"}
    assert save_reviews([first, second, first], "confirmed", "evo_cannon", " batch ") == 2
    selected = {first.key, second.key}
    samples = scan_samples(tmp_path)[0]
    assert all(
        s.status == "confirmed" and s.card_id == "evo_cannon" and s.review["note"] == "batch"
        for s in samples
        if s.key in selected
    )
    assert next(s for s in samples if s.key == other.key).card_id == "knight"
    assert save_reviews([first, second], "ignored") == 2
    assert all(s.status == "ignored" for s in scan_samples(tmp_path)[0] if s.key in selected)
    assert save_reviews([first, second], "pending") == 2
    assert all(s.status == "pending" for s in scan_samples(tmp_path)[0] if s.key in selected)
    assert all(p.read_bytes() == content for p, content in sources.items())


def test_bulk_save_preflights_all_sessions_before_writing(tmp_path):
    first = add_group_sample(tmp_path, "session-a", "sample-000")
    second = add_group_sample(tmp_path, "session-b", "sample-000")
    with pytest.raises(ValueError, match="卡名"):
        save_reviews([first, second], "confirmed", "imaginary_card")
    (second.directory / "annotations.json").write_text("broken", encoding="utf-8")
    with pytest.raises(ValueError):
        save_reviews([first, second], "ignored")
    assert not (first.directory / "annotations.json").exists()
    assert (second.directory / "annotations.json").read_text() == "broken"


def test_bulk_write_failure_reports_partial_success_and_keeps_failed_session(tmp_path, monkeypatch):
    first = add_group_sample(tmp_path, "session-a", "sample-000")
    second = add_group_sample(tmp_path, "session-b", "sample-000")
    from crX.utils import card_review

    original = card_review.os.replace

    def fail_second(source, destination):
        if Path(destination).parent == second.directory:
            raise OSError("disk full")
        original(source, destination)

    monkeypatch.setattr(card_review.os, "replace", fail_second)
    with pytest.raises(OSError, match="已保存 1/2"):
        save_reviews([first, second], "confirmed", "evo_cannon")
    states = {sample.key: sample.status for sample in scan_samples(tmp_path)[0]}
    assert states == {first.key: "confirmed", second.key: "pending"}
    assert not list(tmp_path.rglob("*.tmp"))
