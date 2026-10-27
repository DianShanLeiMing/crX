"""Reference-level edits retain unrelated appearances and recheck merged sources."""

import pytest

from crX.utils import card_library as local
from crX.utils import formal_library as formal
from crX.utils.card_reference_review import delete_reference, rename_reference
from crX.utils.card_review import library_reference_samples, save_review, scan_grouped_samples, scan_samples
from tests import test_card_library as fixtures
from tests.test_card_library import fingerprint

samples = fixtures.samples


@pytest.fixture
def references(tmp_path, samples, monkeypatch):
    monkeypatch.setattr(formal, "_bundled_references", lambda: [("giant", fingerprint(15).tolist())])
    first = samples("first")
    duplicate = samples("duplicate", shift=5)
    other = samples("other", channel=4)
    save_review(first, "confirmed", "knight", "保留第一份备注")
    save_review(duplicate, "confirmed", "knight", "保留第二份备注")
    local.update_library(tmp_path, [first, duplicate, other], "enable")
    entry = next(entry for entry in local.read_library(tmp_path) if first.key in entry["sources"])
    return first, duplicate, other, entry


def test_reference_rows_merge_sources_and_keep_individual_counts(tmp_path, references):
    first, duplicate, other, entry = references
    local.merge_match_stats(
        tmp_path,
        {
            entry["id"]: {
                "matches": 40,
                "distance_total": 400,
                "margin_total": 8000,
                "assists": 12,
                "assist_battles": 2,
                "assist_distance_total": 120,
                "assist_margin_total": 2400,
            }
        },
    )
    records, errors, _ = scan_grouped_samples(tmp_path, refresh_library=True)
    groups = library_reference_samples(records, "knight")
    assert not errors and len(groups) == 2
    assert {sample.key for sample in groups[entry["id"]]} == {first.key, duplicate.key}
    assert groups[entry["id"]][0].review["library_runtime"]["assists"] == 12
    assert (
        next(group for group in groups.values() if group[0].key == other.key)[0]
        .review["library_runtime"]
        .get("assists", 0)
        == 0
    )


def test_rename_reference_updates_all_merged_labels_preserves_notes_and_other_reference(tmp_path, references):
    first, duplicate, other, entry = references
    result = rename_reference(tmp_path, entry["id"], {first.key, duplicate.key}, "knight", "cannon")
    assert "已更正 2 张" in result
    records = {record.key: record for record in scan_samples(tmp_path)[0]}
    assert records[first.key].card_id == records[duplicate.key].card_id == "cannon"
    assert records[first.key].review["note"] == "保留第一份备注"
    assert records[duplicate.key].review["note"] == "保留第二份备注"
    assert records[other.key].card_id == "knight" and records[other.key].status == "included"
    assert all(sample.file("frame").exists() for sample in (first, duplicate, other))
    assert len([item for item in local.read_library(tmp_path) if item["status"] == "active"]) == 2


def test_delete_reference_removes_only_its_merged_sources(tmp_path, references):
    first, duplicate, other, entry = references
    assert delete_reference(tmp_path, entry["id"], {first.key, duplicate.key}, "knight") == 2
    assert not first.file("frame").exists() and not duplicate.file("frame").exists()
    assert other.file("frame").exists()
    assert [sample.key for sample in scan_samples(tmp_path)[0]] == [other.key]
    assert len(local.read_library(tmp_path)) == 1


@pytest.mark.parametrize("operation", [delete_reference, rename_reference])
def test_stale_or_partial_reference_snapshot_is_rejected_before_changes(tmp_path, references, operation):
    first, duplicate, other, entry = references
    before = {sample.key: sample.file("frame").read_bytes() for sample in (first, duplicate, other)}
    args = ("cannon",) if operation is rename_reference else ()
    with pytest.raises(ValueError, match="已变化"):
        operation(tmp_path, entry["id"], {first.key}, "knight", *args)
    assert all(sample.file("frame").read_bytes() == before[sample.key] for sample in (first, duplicate, other))
    assert all(sample.card_id == "knight" for sample in scan_samples(tmp_path)[0])
