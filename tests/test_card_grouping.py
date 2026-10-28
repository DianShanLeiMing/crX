"""Review grouping tolerates shifts without widening capture deduplication."""

from itertools import permutations
from pathlib import Path

import cv2
import numpy as np
import pytest

from crX.utils.card_grouping import group_review_samples
from crX.utils.card_review import group_similar_samples
from tests.test_card_review import add_group_sample

FIXTURES = Path(__file__).parent / "fixtures"


@pytest.mark.parametrize("order", list(permutations(range(3))))
def test_reported_animation_crops_group_in_every_order_but_capture_stays_strict(tmp_path, order):
    samples = [
        add_group_sample(
            tmp_path,
            "shifted",
            f"sample-{index:03d}",
            pixels=cv2.imread(str(FIXTURES / f"card-group-shift-{index + 1:02d}.png")),
        )
        for index in range(3)
    ]
    ordered = [samples[index] for index in order]
    assert list(group_review_samples(ordered).values()) == [[sample.key for sample in ordered]]
    assert len(group_similar_samples(ordered)) == 3


def test_candidate_names_neither_merge_unrelated_art_nor_split_same_art(tmp_path):
    first = add_group_sample(
        tmp_path,
        "candidate",
        "sample-000",
        pixels=cv2.imread(str(FIXTURES / "card-group-shift-01.png")),
    )
    other = add_group_sample(tmp_path, "candidate", "sample-001")
    shifted = add_group_sample(
        tmp_path,
        "candidate",
        "sample-002",
        pixels=cv2.imread(str(FIXTURES / "card-group-shift-03.png")),
    )
    first.record["candidates"] = other.record["candidates"] = [{"card_id": "rascals", "distance": 1900}]
    shifted.record["candidates"] = [{"card_id": "knight", "distance": 2500}]
    groups = group_review_samples([first, other, shifted])
    assert list(groups.values()) == [[first.key, shifted.key], [other.key]]
    assert all(not sample.card_id for sample in (first, other, shifted))


@pytest.mark.parametrize("labels", [("cannon", "evo_cannon"), ("knight", "cannon")])
def test_confirmed_different_cards_and_versions_never_group(tmp_path, labels):
    samples = [add_group_sample(tmp_path, "labels", f"sample-{index:03d}") for index in range(2)]
    for sample, card_id in zip(samples, labels, strict=True):
        sample.review["confirmed_card_id"] = card_id
    assert len(group_review_samples(samples)) == 2


@pytest.mark.parametrize("card_id", ["archers", "knight", "evo_knight"])
def test_same_manual_card_and_version_groups_distinct_appearances(tmp_path, card_id):
    first = add_group_sample(tmp_path, "manual", "sample-000")
    second = add_group_sample(
        tmp_path,
        "manual",
        "sample-001",
        pixels=cv2.imread(str(FIXTURES / "card-group-shift-01.png")),
    )
    assert len(group_review_samples([first, second])) == 2
    for sample in (first, second):
        sample.review["confirmed_card_id"] = card_id
    assert list(group_review_samples([first, second]).values()) == [[first.key, second.key]]
    assert len(group_similar_samples([first, second])) == 2  # Capture remains appearance-based.


def test_manual_group_takes_priority_over_an_earlier_unlabelled_visual_match(tmp_path):
    unknown = add_group_sample(tmp_path, "priority", "sample-000")
    labelled = add_group_sample(
        tmp_path,
        "priority",
        "sample-001",
        pixels=cv2.imread(str(FIXTURES / "card-group-shift-01.png")),
    )
    other_appearance = add_group_sample(tmp_path, "priority", "sample-002")
    for sample in (labelled, other_appearance):
        sample.review["confirmed_card_id"] = "archers"
    groups = group_review_samples([unknown, labelled, other_appearance])
    assert list(groups.values()) == [[unknown.key], [labelled.key, other_appearance.key]]
    assert not unknown.card_id


def test_invalid_manual_name_cannot_force_different_art_into_same_group(tmp_path):
    first = add_group_sample(tmp_path, "invalid-label", "sample-000")
    second = add_group_sample(
        tmp_path,
        "invalid-label",
        "sample-001",
        pixels=cv2.imread(str(FIXTURES / "card-group-shift-01.png")),
    )
    for sample in (first, second):
        sample.review["confirmed_card_id"] = "not-a-card"
    assert len(group_review_samples([first, second])) == 2


def test_members_are_compared_with_representative_without_chaining(tmp_path):
    artwork = np.random.default_rng(42).integers(0, 256, size=(66, 54, 3), dtype=np.uint8)
    samples = [
        add_group_sample(tmp_path, "chain", f"sample-{index:03d}", pixels=np.roll(artwork, index * 7, axis=1))
        for index in range(3)
    ]
    assert len(group_review_samples(samples[1:])) == 1
    assert list(group_review_samples(samples).values()) == [[samples[0].key, samples[1].key], [samples[2].key]]


def test_flat_colors_are_not_enough_to_group(tmp_path):
    samples = [
        add_group_sample(
            tmp_path, "flat", f"sample-{index:03d}", pixels=np.full((66, 54, 3), 80 + index * 10, np.uint8)
        )
        for index in range(3)
    ]
    assert list(group_review_samples(samples).values()) == [[samples[0].key, samples[1].key], [samples[2].key]]


@pytest.mark.parametrize("kind", ["unavailable", "slot", "size", "missing", "corrupt", "oversized"])
def test_unsuitable_crops_are_not_grouped(tmp_path, kind):
    first, second = (add_group_sample(tmp_path, "invalid", f"sample-{index:03d}") for index in range(2))
    for sample in (first, second):
        sample.review["confirmed_card_id"] = "archers"
    if kind == "unavailable":
        second.record["available"] = False
    elif kind == "slot":
        second.record["slot"] = None
    elif kind == "size":
        cv2.imwrite(str(second.file("card")), np.zeros((50, 50, 3), np.uint8))
    elif kind == "missing":
        second.file("card").unlink()
    elif kind == "corrupt":
        second.file("card").write_bytes(b"not an image")
    else:
        second.file("card").write_bytes(b"0" * (1024 * 1024 + 1))
    assert len(group_review_samples([first, second])) == 2
