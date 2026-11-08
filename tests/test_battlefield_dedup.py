"""Global duplicate groups, small-object preservation and snapshot-safe curated export."""

from shutil import copyfile
from threading import Event

import numpy as np
import pytest
from PIL import Image

from crX.utils import battlefield_data as data
from crX.utils import battlefield_filter_cache as feature_cache
from crX.utils.battlefield_dedup import analyze_batches, export_selection
from crX.utils.dataset_split import export_split, plan_split, scan_annotations


@pytest.fixture(autouse=True)
def isolated_filter_cache(tmp_path, monkeypatch):
    monkeypatch.setattr(feature_cache, "cache_path", lambda _root: tmp_path / "cache.sqlite3")


def capture(root, frames):
    session = data.PreparationSession.create(root, (0, 0, 720, 1280))
    for frame in frames:
        session.append(frame, source="test")
    return session


def sample_frames():
    original = np.full((1280, 720, 3), 40, np.uint8)
    hud = original.copy()
    hud[0:30, 0:30] = 220
    troop = original.copy()
    troop[300:302, 200:202] = 255
    return original, hud, troop


def test_global_exact_ignores_encoding_and_near_groups_keep_small_objects(tmp_path):
    frames = sample_frames()
    first = capture(tmp_path / "captures", frames)
    second = capture(tmp_path / "captures", [frames[0]])
    path = second.directory / "raw/000000.png"
    with Image.open(path) as image:
        image.save(path, compress_level=1)
    before = {path: path.read_bytes() for path in (tmp_path / "captures").rglob("*") if path.is_file()}
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    assert len(plan.images) == 4 and plan.exact_duplicates == 1
    assert len(plan.default_keep) == 2 and len(plan.unique_keep) == 3
    group = next(group for group in plan.groups if group.kind == "near")
    assert len(group.members) == 3 and len(group.default_keep) == 2
    troop = next(index for index, item in enumerate(plan.images) if item.path == first.directory / "raw/000002.png")
    assert troop in plan.default_keep and troop not in group.members
    assert all(path.read_bytes() == content for path, content in before.items())
    selected = set(plan.default_keep) - set(group.members) | {group.representative}
    result = export_selection(plan, selected, tmp_path / "selected", stop=Event(), progress=lambda _: None)
    assert result.count == 2 and result.excluded == 2
    assert {path.name for path in (tmp_path / "selected").iterdir()} == {"images"}
    assert all(Image.open(path).size == (720, 1280) for path in result.directory.glob("*.png"))


def test_exact_groups_link_scene_origins_after_flat_export(tmp_path):
    original, _hud, troop = sample_frames()
    capture(tmp_path / "captures", [original, troop])
    capture(tmp_path / "captures", [original])
    capture(tmp_path / "captures", [np.full_like(original, 140)])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    output = tmp_path / "output"
    result = export_selection(plan, set(plan.default_keep), output, stop=Event(), progress=lambda _: None)
    for image in result.directory.glob("*.png"):
        image.with_suffix(".txt").write_text("0 0.5 0.5 0.1 0.1\n")
    (output / "classes.txt").write_text("enemy_knight\n")
    scan = scan_annotations(output, output / "classes.txt")
    assert scan.source_groups == 2
    split = plan_split(scan)
    assert {item.group for item in split.train}.isdisjoint(item.group for item in split.val)
    assert (export_split(split, tmp_path / "datasets") / "data.yaml").is_file()


@pytest.mark.parametrize("change", ["image", "metadata", "new_batch"])
def test_changes_after_analysis_require_rescan_before_writing(tmp_path, change):
    source = capture(tmp_path / "captures", [sample_frames()[0]])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    if change == "image":
        Image.new("RGB", (720, 1280), (120, 20, 30)).save(source.directory / "raw/000000.png")
    elif change == "metadata":
        source.append(np.zeros((1280, 720, 3), np.uint8), source="test")
    else:
        capture(tmp_path / "captures", [sample_frames()[2]])
    with pytest.raises(ValueError, match="变化"):
        export_selection(plan, set(plan.default_keep), tmp_path / "output", stop=Event(), progress=lambda _: None)
    assert not (tmp_path / "output").exists()


def test_unselected_existing_output_is_not_silently_kept_or_deleted(tmp_path):
    capture(tmp_path / "captures", [sample_frames()[0]])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    images = tmp_path / "output/images"
    images.mkdir(parents=True)
    existing = images / "other__000000.png"
    Image.new("RGB", (720, 1280)).save(existing)
    before = existing.read_bytes()
    with pytest.raises(ValueError, match="输出目录"):
        export_selection(plan, set(plan.default_keep), images.parent, stop=Event(), progress=lambda _: None)
    assert existing.read_bytes() == before and len(list(images.iterdir())) == 1


def test_repeat_curated_export_keeps_reencoded_image_and_existing_label(tmp_path):
    capture(tmp_path / "captures", [sample_frames()[0]])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    output = tmp_path / "output"
    result = export_selection(plan, set(plan.default_keep), output, stop=Event(), progress=lambda _: None)
    path = next(result.directory.glob("*.png"))
    with Image.open(path) as image:
        image.save(path, compress_level=1)
    before = path.read_bytes()
    label = path.with_suffix(".txt")
    label.write_text("0 0.5 0.5 0.1 0.1\n")
    result = export_selection(plan, set(plan.default_keep), output, stop=Event(), progress=lambda _: None)
    assert result.count == 0 and result.skipped == 1
    assert path.read_bytes() == before and label.read_text() == "0 0.5 0.5 0.1 0.1\n"
    assert len(list(result.directory.glob("*.png"))) == 1


def test_separate_exports_have_unique_names_but_keep_same_scene_groups(tmp_path):
    original, _hud, troop = sample_frames()
    capture(tmp_path / "captures", [original, troop])
    capture(tmp_path / "captures", [np.full_like(original, 140)])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    first = export_selection(plan, set(plan.default_keep), tmp_path / "first", stop=Event(), progress=lambda _: None)
    second = export_selection(plan, set(plan.default_keep), tmp_path / "second", stop=Event(), progress=lambda _: None)
    first_names = {path.name for path in first.directory.glob("*.png")}
    second_names = {path.name for path in second.directory.glob("*.png")}
    assert len(first_names) == len(second_names) == 3 and first_names.isdisjoint(second_names)
    assert all("_export" in name for name in first_names | second_names)
    merged = tmp_path / "merged/images"
    merged.mkdir(parents=True)
    for directory in (first.directory, second.directory):
        for path in directory.glob("*.png"):
            copyfile(path, merged / path.name)
            (merged / path.name).with_suffix(".txt").write_text("0 0.5 0.5 0.1 0.1\n")
    classes = merged.parent / "classes.txt"
    classes.write_text("enemy_knight\n")
    scan = scan_annotations(merged.parent, classes)
    assert scan.source_groups == 2 and scan.duplicates == 3 and len(scan.images) == 3
    split = plan_split(scan)
    assert {item.group for item in split.train}.isdisjoint(item.group for item in split.val)


def test_interrupted_export_resumes_without_renaming_existing_images(tmp_path):
    original, _hud, troop = sample_frames()
    capture(tmp_path / "captures", [original, troop, np.full_like(original, 140)])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    stop = Event()
    output = tmp_path / "output"
    partial = export_selection(plan, set(plan.default_keep), output, stop=stop, progress=lambda _: stop.set())
    assert partial.count == 1
    preserved = {path: path.read_bytes() for path in partial.directory.glob("*.png")}
    stop.clear()
    resumed = export_selection(plan, set(plan.default_keep), output, stop=stop, progress=lambda _: None)
    assert resumed.count == 2 and resumed.skipped == 1
    assert all(path.read_bytes() == before for path, before in preserved.items())
    repeated = export_selection(plan, set(plan.default_keep), output, stop=stop, progress=lambda _: None)
    assert repeated.count == 0 and repeated.skipped == 3
    assert len(list(repeated.directory.glob("*.png"))) == 3


def test_legacy_export_names_and_existing_annotations_are_preserved(tmp_path):
    source = capture(tmp_path / "captures", [sample_frames()[0]])
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    images = tmp_path / "output/images"
    images.mkdir(parents=True)
    image = images / f"{source.metadata['battle_id']}__000000.png"
    copyfile(source.directory / "raw/000000.png", image)
    label = image.with_suffix(".txt")
    label.write_text("0 0.5 0.5 0.1 0.1\n")
    before = image.read_bytes(), label.read_bytes()
    result = export_selection(plan, set(plan.default_keep), images.parent, stop=Event(), progress=lambda _: None)
    assert result.count == 0 and result.skipped == 1
    assert (image.read_bytes(), label.read_bytes()) == before
    assert len(list(images.glob("*.png"))) == 1


def test_cancelled_analysis_never_modifies_sources(tmp_path):
    source = capture(tmp_path / "captures", sample_frames())
    before = (source.directory / "metadata.json").read_bytes()
    stop = Event()
    with pytest.raises(ValueError, match="已停止"):
        analyze_batches(tmp_path / "captures", stop=stop, progress=lambda _: stop.set())
    assert (source.directory / "metadata.json").read_bytes() == before
    assert len(list((source.directory / "raw").glob("*.png"))) == 3


def test_large_feature_index_does_not_merge_distinct_tiny_troops(tmp_path):
    frames = []
    for index in range(65):
        image = np.full((1280, 720, 3), 40, np.uint8)
        top, left = 300 + index // 10 * 8, 150 + index % 10 * 8
        image[top : top + 2, left : left + 2] = 255
        frames.append(image)
    capture(tmp_path / "captures", frames)
    plan = analyze_batches(tmp_path / "captures", stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 65 and plan.exact_duplicates == 0
    assert plan.groups == ()


def moving_frames(count):
    frames = []
    for index in range(count):
        image = np.full((1280, 720, 3), 80, np.uint8)
        image[300:330, 200 + index : 230 + index] = 180
        frames.append(image)
    return frames


def timestamp_frames(session, times):
    for entry, timestamp in zip(session.metadata["frames"], times, strict=True):
        entry["captured_at"] = timestamp
    session.save()


def test_continuous_scene_keeps_content_representatives_without_time_sampling(tmp_path):
    source = capture(tmp_path / "captures", moving_frames(10))
    timestamp_frames(source, range(100, 110))
    before = {path: path.read_bytes() for path in source.directory.rglob("*") if path.is_file()}
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.groups) == 1 and plan.groups[0].kind == "scene"
    assert len(plan.default_keep) == 1
    assert plan.exact_duplicates == 0 and len(plan.unique_keep) == 10
    assert all(path.read_bytes() == content for path, content in before.items())
    result = export_selection(plan, set(plan.default_keep), tmp_path / "output", stop=Event(), progress=lambda _: None)
    assert result.count == 1 and result.excluded == 9


def test_similar_scenes_in_different_battles_are_not_sampled_together(tmp_path):
    first = capture(tmp_path / "captures", moving_frames(1))
    second = capture(tmp_path / "captures", moving_frames(2)[1:])
    timestamp_frames(first, [100])
    timestamp_frames(second, [101])
    plan = analyze_batches(first.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 2 and plan.groups == ()
    assert len(set(plan.battle_groups)) == 2


@pytest.mark.parametrize("times", [[100, 110], [101, 100], [100, float("nan")]])
def test_content_selection_does_not_depend_on_timestamps(tmp_path, times):
    source = capture(tmp_path / "captures", moving_frames(2))
    timestamp_frames(source, times)
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 1 and len(plan.groups) == 1


def test_short_lived_visual_event_is_retained(tmp_path):
    frames = moving_frames(3)
    frames[1][500:525, 300:325] = 230
    # Same scene before and after, with a tiny non-significant animation difference.
    frames[2] = frames[0].copy()
    frames[2][200, 200] += 1
    source = capture(tmp_path / "captures", frames)
    timestamp_frames(source, [100, 100.3, 100.6])
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    transient = next(index for index, sample in enumerate(plan.images) if sample.frame_index == 1)
    assert transient in plan.default_keep


def test_one_and_three_second_sampling_make_same_content_selection(tmp_path):
    source = capture(tmp_path / "captures", moving_frames(30))
    timestamp_frames(source, range(100, 130))
    first = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    timestamp_frames(source, range(100, 190, 3))
    second = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert first.default_keep == second.default_keep and len(first.default_keep) < 4


def test_cache_reuses_unchanged_images_and_comparisons(tmp_path, monkeypatch):
    source = capture(tmp_path / "captures", moving_frames(10))
    first = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    read_frame = data.PreparationSession.frame
    reads = []

    def observe(session, index):
        reads.append(session.directory / session.metadata["frames"][index]["name"])
        return read_frame(session, index)

    monkeypatch.setattr(data.PreparationSession, "frame", observe)
    second = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert first.default_keep == second.default_keep and not reads
    capture(source.directory.parent, moving_frames(1))
    third = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(reads) == 1 and third.exact_duplicates == 1


def test_cache_invalidates_changed_image_and_handles_corruption(tmp_path):
    source = capture(tmp_path / "captures", moving_frames(2))
    first = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    changed = source.directory / "raw/000000.png"
    Image.new("RGB", (720, 1280), (240, 0, 0)).save(changed)
    second = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert first.images[0].digest != second.images[0].digest
    assert len(second.default_keep) == 2
    (tmp_path / "cache.sqlite3").write_bytes(b"broken cache")
    recovered = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert second.default_keep == recovered.default_keep


def test_cross_battle_visual_duplicates_are_filtered_but_new_objects_are_kept(tmp_path):
    image = np.full((1280, 720, 3), 80, np.uint8)
    first = capture(tmp_path / "captures", [image])
    capture(tmp_path / "captures", [image + 1])
    spawned = image.copy()
    spawned[400:420, 300:320] = 220
    capture(tmp_path / "captures", [spawned])
    plan = analyze_batches(first.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 2 and plan.exact_duplicates == 0
    assert any(group.kind == "scene" and len(group.members) == 2 for group in plan.groups)
    assert any(
        plan.images[index].path.parent.parent == first.directory for group in plan.groups for index in group.members
    )


def test_tower_health_region_changes_are_preserved(tmp_path):
    image = np.full((1280, 720, 3), 80, np.uint8)
    changed = image.copy()
    changed[135:140, 155:165] = 240
    source = capture(tmp_path / "captures", [image, changed])
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 2


def test_optional_cache_failure_does_not_block_filtering(tmp_path, monkeypatch):
    source = capture(tmp_path / "captures", moving_frames(3))
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("keep")
    monkeypatch.setattr(feature_cache, "cache_path", lambda _root: blocker / "cache.sqlite3")
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 1 and blocker.read_text() == "keep"


def test_clear_representative_is_selected_over_blurred_copy(tmp_path):
    import cv2

    image = moving_frames(1)[0]
    source = capture(tmp_path / "captures", [cv2.GaussianBlur(image, (9, 9), 2), image])
    plan = analyze_batches(source.directory.parent, stop=Event(), progress=lambda _: None)
    assert len(plan.default_keep) == 1
    assert plan.images[plan.default_keep[0]].frame_index == 1
