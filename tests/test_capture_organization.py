"""Flat organization, collision handling, frame deletion and empty-batch cleanup."""

import json
from threading import Event

import numpy as np
import pytest
from PIL import Image

from crX.utils import battlefield_data as data


def batch(root, colors=(20, 80)):
    session = data.PreparationSession.create(root, (0, 0, 720, 1280))
    for color in colors:
        session.append(np.full((1280, 720, 3), color, np.uint8), source="test")
    return session


def test_batch_names_are_readable_and_same_second_collisions_are_numbered(tmp_path, monkeypatch):
    monkeypatch.setattr(data.time, "strftime", lambda _: "2026-11-07_16-20-30")
    first = batch(tmp_path / "captures", ())
    second = batch(tmp_path / "captures", ())
    other_root = batch(tmp_path / "other", ())
    assert first.directory.name == "2026-11-07_16-20-30"
    assert second.directory.name == "2026-11-07_16-20-30_02"
    assert first.metadata["battle_id"] != other_root.metadata["battle_id"]


def test_new_and_legacy_batch_names_are_sorted_by_capture_time(tmp_path):
    old = tmp_path / "20261107-150000-abcdefgh"
    new = tmp_path / "2026-11-07_16-00-00"
    for directory in (old, new):
        directory.mkdir()
        (directory / "metadata.json").write_text(json.dumps({"schema": data.SCHEMA}))
    assert data.find_batches(tmp_path, stop=Event()).directories == (new, old)


def test_flat_export_keeps_name_conflicts_and_repeated_exports_are_idempotent(tmp_path):
    source = batch(tmp_path / "captures", (20,))
    output = tmp_path / "output"
    images = output / "images"
    images.mkdir(parents=True)
    conflict = images / f"{source.metadata['battle_id']}__000000.png"
    Image.new("RGB", (720, 1280), (10, 20, 30)).save(conflict)
    before = conflict.read_bytes()
    label = conflict.with_suffix(".txt")
    label.write_text("keep user labels")
    result = data.prepare_annotation_images(source, output, stop=Event(), progress=lambda _: None)
    assert result.count == 1 and result.skipped == 0
    assert conflict.read_bytes() == before and label.read_text() == "keep user labels"
    assert (images / f"{conflict.stem}_dup002.png").is_file()
    repeated = data.prepare_annotation_images(source, output, stop=Event(), progress=lambda _: None)
    assert repeated.count == 0 and repeated.skipped == 1
    assert {path.name for path in output.iterdir()} == {"images"}
    assert all(path.is_file() for path in images.iterdir())


def test_bulk_export_and_stop_can_resume_without_replacing_existing_images(tmp_path):
    first, second = batch(tmp_path / "captures"), batch(tmp_path / "nested", (130,))
    output = tmp_path / "output"
    stop = Event()
    partial = data.organize_annotation_images([first, second], output, stop=stop, progress=lambda _: stop.set())
    assert partial.count == 1
    preserved = {path: path.read_bytes() for path in partial.directory.iterdir()}
    stop.clear()
    completed = data.organize_annotation_images([first, second], output, stop=stop, progress=lambda _: None)
    assert completed.count == 2 and completed.skipped == 1
    assert len(list(completed.directory.glob("*.png"))) == 3
    assert all(path.read_bytes() == content for path, content in preserved.items())


def test_empty_legacy_batch_does_not_block_organizing_native_images(tmp_path):
    empty = data.PreparationSession.create(tmp_path / "old", (0, 60, 360, 515), screen_size=(360, 640))
    source = batch(tmp_path / "captures", (60,))
    result = data.organize_annotation_images(
        [empty, source], tmp_path / "output", stop=Event(), progress=lambda _: None
    )
    assert result.count == 1


def test_delete_updates_metadata_quarantines_files_and_never_reuses_frame_numbers(tmp_path):
    source = batch(tmp_path / "captures")
    first = source.directory / "raw" / "000000.png"
    before = first.read_bytes()
    removed = data.delete_frame(source, 0)
    assert removed.index == 0 and len(removed.session.metadata["frames"]) == 1
    assert not first.exists()
    assert next(source.directory.glob(".deleted/*/raw.png")).read_bytes() == before
    reopened = data.PreparationSession.open(source.directory)
    np.testing.assert_array_equal(reopened.frame(0), np.full((1280, 720, 3), 80, np.uint8))
    reopened.append(np.full((1280, 720, 3), 140, np.uint8), source="test")
    assert [entry["name"] for entry in reopened.metadata["frames"]] == ["000001.png", "000002.png"]
    last = data.delete_frame(reopened, 1)
    assert last.index == 0
    empty = data.delete_frame(last.session, 0)
    assert empty.session.metadata["frames"] == []
    assert empty.cleanup_error == "" and not source.directory.exists()
    assert source.directory.parent.is_dir()


def test_loading_removes_empty_batches_and_preserves_nonempty_and_unrelated_folders(tmp_path):
    empty = batch(tmp_path / "captures", ())
    nested = batch(tmp_path / "captures/nested", ())
    source = batch(tmp_path / "captures", (60,))
    unrelated = tmp_path / "captures/unrelated"
    unrelated.mkdir()
    preserved = (source.directory / "raw/000000.png").read_bytes()
    result = data.find_batches(tmp_path / "captures", stop=Event(), cleanup_empty=True)
    assert result.directories == (source.directory,) and result.removed == 2
    assert not empty.directory.exists() and not nested.directory.exists()
    assert unrelated.is_dir() and (source.directory / "raw/000000.png").read_bytes() == preserved
    another = batch(tmp_path / "other", ())
    direct = data.find_batches(another.directory, stop=Event(), cleanup_empty=True)
    assert direct.directories == () and direct.removed == 1 and not another.directory.exists()


def test_empty_metadata_with_unregistered_files_is_excluded_but_not_deleted(tmp_path):
    source = batch(tmp_path / "captures", ())
    unknown = source.directory / "raw/000000.png"
    unknown.write_bytes(b"unregistered data")
    result = data.find_batches(source.directory.parent, stop=Event(), cleanup_empty=True)
    assert result.directories == () and result.removed == 0
    assert result.warnings and "未登记" in result.warnings[0]
    assert unknown.read_bytes() == b"unregistered data"


def test_last_image_deletion_keeps_other_files_and_reports_cleanup_failure(tmp_path):
    source = batch(tmp_path / "captures", (50,))
    extra = source.directory / "notes.txt"
    extra.write_text("keep notes")
    deleted = data.delete_frame(source, 0)
    assert deleted.session.metadata["frames"] == [] and "其他文件" in deleted.cleanup_error
    assert extra.read_text() == "keep notes"
    assert list(source.directory.glob(".deleted/*/raw.png"))


def test_delete_rolls_back_file_move_when_metadata_write_fails(tmp_path, monkeypatch):
    source = batch(tmp_path / "captures", (40,))
    metadata_before = (source.directory / "metadata.json").read_bytes()
    original = (source.directory / "raw" / "000000.png").read_bytes()
    monkeypatch.setattr(data, "atomic_json", lambda *_: (_ for _ in ()).throw(OSError("disk failure")))
    with pytest.raises(OSError, match="disk failure"):
        data.delete_frame(source, 0)
    assert (source.directory / "metadata.json").read_bytes() == metadata_before
    assert (source.directory / "raw" / "000000.png").read_bytes() == original


def test_delete_rejects_path_traversal_without_touching_other_files(tmp_path):
    source = batch(tmp_path / "captures", (40,))
    outside = tmp_path / "keep.txt"
    outside.write_text("keep")
    source.metadata["frames"][0]["name"] = "../../keep.txt"
    with pytest.raises(ValueError, match="路径无效"):
        data.delete_frame(source, 0)
    assert outside.read_text() == "keep"
