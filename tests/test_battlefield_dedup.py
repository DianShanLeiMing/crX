"""Global duplicate groups, small-object preservation and snapshot-safe curated export."""

from shutil import copyfile
from threading import Event

import numpy as np
import pytest
from PIL import Image

from crX.utils import battlefield_data as data
from crX.utils.battlefield_dedup import analyze_batches, export_selection
from crX.utils.dataset_split import export_split, plan_split, scan_annotations


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
    assert len(plan.default_keep) == 3
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
    capture(tmp_path / "captures", sample_frames())
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
