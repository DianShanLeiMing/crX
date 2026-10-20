"""Filtered clearing must preserve other rows in the same session and other cards."""

import json

import pytest

from crX.utils import card_library as library
from crX.utils.card_review import atomic_json, export_candidate, save_review, scan_samples
from tests.test_card_review import add_group_sample


def test_clear_only_selected_records_images_annotations_and_library(tmp_path):
    first = add_group_sample(tmp_path, "session-a", "sample-000")
    other = add_group_sample(tmp_path, "session-a", "sample-001")
    elsewhere = add_group_sample(tmp_path, "session-b", "sample-000")
    for sample in (first, other, elsewhere):
        save_review(sample, "confirmed", "evo_cannon")
    first = next(sample for sample in scan_samples(tmp_path)[0] if sample.key == first.key)
    exported = export_candidate(first)
    library.update_library(tmp_path, [first], "trial")
    kept = {
        sample.file(kind): sample.file(kind).read_bytes()
        for sample in (other, elsewhere)
        for kind in ("card", "hand", "frame")
    }
    (first.directory / "keep.txt").write_text("unrelated", encoding="utf-8")
    assert library.clear_filtered_samples(tmp_path, {first.key}) == 1
    remaining, errors = scan_samples(tmp_path)
    assert errors == 0 and {sample.key for sample in remaining} == {other.key, elsewhere.key}
    assert all(sample.card_id == "evo_cannon" for sample in remaining)
    assert not first.file("frame").exists() and not exported.exists()
    assert not library.read_library(tmp_path)
    assert all(path.read_bytes() == content for path, content in kept.items())
    assert (first.directory / "keep.txt").read_text(encoding="utf-8") == "unrelated"
    assert library.clear_filtered_samples(tmp_path, {first.key}) == 0


def test_clear_refuses_invalid_manifest_before_deleting(tmp_path):
    sample = add_group_sample(tmp_path, "session", "sample-000")
    with (sample.directory / "samples.jsonl").open("a", encoding="utf-8") as stream:
        stream.write("broken\n")
    with pytest.raises(ValueError, match="记录存在错误"):
        library.clear_filtered_samples(tmp_path, {sample.key})
    assert sample.file("frame").exists()


def test_stage_clear_rechecks_current_status_before_deleting(tmp_path):
    pending = add_group_sample(tmp_path, "session", "sample-000")
    now_confirmed = add_group_sample(tmp_path, "session", "sample-001")
    save_review(now_confirmed, "confirmed", "evo_cannon")
    assert library.clear_filtered_samples(tmp_path, {pending.key, now_confirmed.key}, statuses={"pending"}) == 1
    remaining, errors = scan_samples(tmp_path)
    assert not errors and [sample.key for sample in remaining] == [now_confirmed.key]
    assert now_confirmed.file("frame").exists()
    assert library.clear_filtered_samples(tmp_path, {now_confirmed.key}, statuses={"confirmed"}) == 1


def test_clear_refuses_aliased_image_filenames(tmp_path):
    first = add_group_sample(tmp_path, "session", "sample-000")
    other = add_group_sample(tmp_path, "session", "sample-001")
    path = first.directory / "samples.jsonl"
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    rows[0]["files"]["frame"] = other.record["files"]["frame"]
    path.write_text("".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
    with pytest.raises(ValueError, match="命名异常"):
        library.clear_filtered_samples(tmp_path, {first.key})
    assert other.file("frame").exists()


def test_clear_refuses_export_link_and_keeps_external_file(tmp_path):
    sample = add_group_sample(tmp_path, "session", "sample-000")
    outside = tmp_path.parent / (tmp_path.name + "-outside.json")
    atomic_json(outside, {"keep": True})
    try:
        (sample.directory / "sample-000-candidate.json").symlink_to(outside)
    except OSError:
        pytest.skip("Symlink creation requires Windows developer mode or elevation")
    with pytest.raises(ValueError, match="路径无效|链接"):
        library.clear_filtered_samples(tmp_path, {sample.key})
    assert outside.exists() and sample.file("frame").exists()
