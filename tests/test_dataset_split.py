"""YOLO annotation validation and reproducible, leak-free battle-grouped export."""

import json
from threading import Event

import pytest
from PIL import Image

from crX.utils import dataset_split as data


def make_annotations(root, groups=(3, 3, 2), names="enemy_knight\nfriendly_knight\n"):
    root.mkdir()
    (root / "classes.txt").write_text(names, encoding="utf-8")
    number = 0
    for group_index, count in enumerate(groups):
        group = f"battle-{group_index:03d}"
        (root / "images" / group).mkdir(parents=True)
        (root / "labels" / group).mkdir(parents=True)
        for index in range(count):
            Image.new("RGB", (36, 64), (number % 256, number // 256, 100)).save(
                root / "images" / group / f"{index:06d}.png"
            )
            (root / "labels" / group / f"{index:06d}.txt").write_text(
                f"{index % 2} 0.5 0.5 0.2 0.3\n", encoding="utf-8"
            )
            number += 1
    return root


def scan(root):
    return data.scan_annotations(root, root / "classes.txt")


def test_entire_battles_stay_together_with_reproducible_closest_ratio(tmp_path):
    source = make_annotations(tmp_path / "source", groups=(8, 2, 1))
    loaded = scan(source)
    first = data.plan_split(loaded, 20, 42)
    second = data.plan_split(loaded, 20, 42)
    assert first == second
    assert len(first.val) == 2
    assert {item.group for item in first.train}.isdisjoint(item.group for item in first.val)
    assert len(first.train) + len(first.val) == 11
    assert {item.relative for item in first.train + first.val} == {item.relative for item in loaded.images}


def test_export_preserves_pairs_and_originals_and_writes_yolo26_metadata(tmp_path):
    source = make_annotations(tmp_path / "source", names="敌方: 骑士\n友方骑士\n")
    before = {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}
    plan = data.plan_split(scan(source))
    destination = tmp_path / "datasets"
    output = data.export_split(plan, destination)
    assert output.parent == destination
    assert not list(destination.glob(".preparing-*"))
    assert len(list(output.glob("images/*/*.png"))) == 8
    assert len(list(output.glob("labels/*/*.txt"))) == 8
    for image in output.glob("images/*/*.png"):
        label = output / "labels" / image.parent.name / image.with_suffix(".txt").name
        assert label.is_file()
    manifest = json.loads((output / "split.json").read_text(encoding="utf-8"))
    assert manifest["recommended_model"] == "yolo26n.pt"
    assert manifest["seed"] == 42
    by_group = {}
    for record in manifest["images"]:
        by_group.setdefault(record["group"], set()).add(record["split"])
    assert all(len(splits) == 1 for splits in by_group.values())
    yaml = (output / "data.yaml").read_text(encoding="utf-8")
    assert json.loads(yaml.splitlines()[0].partition(":")[2]) == output.as_posix()
    assert '0: "敌方: 骑士"' in yaml
    assert "train: images/train\nval: images/val\n" in yaml
    assert before == {path.relative_to(source): path.read_bytes() for path in source.rglob("*") if path.is_file()}


def test_same_pixels_in_different_groups_connect_groups_and_deduplicate(tmp_path):
    source = make_annotations(tmp_path / "source")
    first = source / "images/battle-000/000000.png"
    duplicate = source / "images/battle-001/000000.png"
    # Re-encode identical pixels, testing visual rather than compressed-byte identity.
    with Image.open(first) as image:
        image.save(duplicate, compress_level=0)
    assert first.read_bytes() != duplicate.read_bytes()
    plan = data.plan_split(scan(source))
    assert plan.scan.duplicates == 1
    assert len(plan.scan.images) == 7
    group_splits = {}
    for split, items in (("train", plan.train), ("val", plan.val)):
        for item in items:
            group_splits.setdefault(item.relative.split("/")[0], set()).add(split)
    assert group_splits["battle-000"] == group_splits["battle-001"]


def test_conflicting_labels_for_duplicate_pixels_are_rejected(tmp_path):
    source = make_annotations(tmp_path / "source")
    (source / "images/battle-001/000001.png").write_bytes((source / "images/battle-000/000000.png").read_bytes())
    with pytest.raises(ValueError, match="标注不一致"):
        scan(source)


@pytest.mark.parametrize(
    "label",
    [
        "2 0.5 0.5 0.2 0.3",
        "0 nan 0.5 0.2 0.3",
        "0 0.5 0.5 0 0.3",
        "0 0.99 0.5 0.2 0.3",
        "0 0.5 0.5 0.2",
        "-1 0.5 0.5 0.2 0.3",
    ],
)
def test_invalid_class_numbers_and_boxes_are_rejected(tmp_path, label):
    source = make_annotations(tmp_path / "source")
    (source / "labels/battle-000/000000.txt").write_text(label, encoding="utf-8")
    with pytest.raises(ValueError):
        scan(source)


def test_missing_labels_are_not_silently_treated_as_background(tmp_path):
    source = make_annotations(tmp_path / "source")
    label = source / "labels/battle-000/000000.txt"
    label.unlink()
    with pytest.raises(ValueError, match="缺少标签"):
        scan(source)
    label.write_text("", encoding="utf-8")
    assert not scan(source).images[0].classes


def test_orphan_label_is_reported(tmp_path):
    source = make_annotations(tmp_path / "source")
    (source / "labels/battle-000/orphan.txt").write_text("", encoding="utf-8")
    with pytest.raises(ValueError, match="没有对应图片"):
        scan(source)


def test_flat_images_cannot_be_split_without_battle_identity(tmp_path):
    source = make_annotations(tmp_path / "source")
    (source / "images/battle-000/000000.png").rename(source / "images/flat.png")
    with pytest.raises(ValueError, match="平铺|场次标识"):
        scan(source)


def test_single_battle_cannot_be_split(tmp_path):
    source = make_annotations(tmp_path / "source", groups=(10,))
    with pytest.raises(ValueError, match="至少需要两场"):
        data.plan_split(scan(source))


def test_cancelled_export_removes_only_its_staging_directory(tmp_path):
    source = make_annotations(tmp_path / "source")
    plan = data.plan_split(scan(source))
    destination = tmp_path / "output"
    destination.mkdir()
    keep = destination / "existing.txt"
    keep.write_text("keep", encoding="utf-8")
    stop = Event()
    with pytest.raises(data.DatasetCancelledError):
        data.export_split(plan, destination, stop=stop, progress=lambda _text: stop.set())
    assert list(destination.iterdir()) == [keep]
    assert scan(source).images == plan.scan.images


def test_changed_labels_after_loading_are_rejected_and_no_dataset_is_published(tmp_path):
    source = make_annotations(tmp_path / "source")
    plan = data.plan_split(scan(source))
    plan.train[0].label.write_text("", encoding="utf-8")
    destination = tmp_path / "output"
    with pytest.raises(ValueError, match="发生变化"):
        data.export_split(plan, destination)
    assert not list(destination.iterdir())


def test_changed_class_table_requires_reload(tmp_path):
    source = make_annotations(tmp_path / "source")
    plan = data.plan_split(scan(source))
    (source / "classes.txt").write_text("friendly_knight\nenemy_knight\n", encoding="utf-8")
    with pytest.raises(ValueError, match="类别表.*发生变化"):
        data.export_split(plan, tmp_path / "output")


def test_output_inside_source_is_rejected(tmp_path):
    source = make_annotations(tmp_path / "source")
    with pytest.raises(ValueError, match="输出目录不能"):
        data.export_split(data.plan_split(scan(source)), source / "output")
    assert not (source / "output").exists()


def test_inbox_creation_keeps_existing_exports(tmp_path):
    inbox = tmp_path / "annotated"
    data.ensure_annotation_inbox(inbox)
    classes = inbox / "classes.txt"
    classes.write_text("enemy_knight\n", encoding="utf-8")
    data.ensure_annotation_inbox(inbox)
    assert classes.read_text(encoding="utf-8") == "enemy_knight\n"
    assert (inbox / "导入说明.txt").is_file()


@pytest.mark.parametrize("percent,seed", [(0, 42), (100, 42), (20, -1), (20, 2**32)])
def test_invalid_split_options(tmp_path, percent, seed):
    source = make_annotations(tmp_path / "source")
    with pytest.raises(ValueError):
        data.plan_split(scan(source), percent, seed)


def test_duplicate_stems_with_different_extensions_are_ambiguous(tmp_path):
    source = make_annotations(tmp_path / "source")
    with Image.open(source / "images/battle-000/000000.png") as image:
        image.save(source / "images/battle-000/000000.jpg")
    with pytest.raises(ValueError, match="歧义"):
        scan(source)


def test_symlink_outside_source_is_rejected(tmp_path):
    source = make_annotations(tmp_path / "source")
    outside = tmp_path / "outside.png"
    outside.write_bytes((source / "images/battle-000/000000.png").read_bytes())
    link = source / "images/battle-000/linked.png"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("Creating symlinks requires OS permission")
    with pytest.raises(ValueError, match="链接"):
        scan(source)
