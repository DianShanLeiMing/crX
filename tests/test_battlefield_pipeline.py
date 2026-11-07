"""Full screenshots and scene groups must survive annotation and inference unchanged."""

import json
from threading import Event

import numpy as np
import pytest

from crX.detection.battlefield import (
    INPUT_CONFIG_NAME,
    battlefield_box_to_screen,
    battlefield_image,
    input_config,
)
from crX.utils.battlefield_data import PreparationSession, prepare_annotation_images, recrop_session
from crX.utils.dataset_split import export_split, plan_split, scan_annotations


def test_training_export_and_inference_share_pixels_and_coordinates(tmp_path):
    frame = np.zeros((1280, 720, 3), np.uint8)
    frame[121, 13] = (12, 34, 56)
    source = PreparationSession.create(tmp_path / "captures", (20, 50, 200, 300))
    source.append(frame, source="test")
    before = {path: path.read_bytes() for path in source.directory.rglob("*") if path.is_file()}
    inbox = tmp_path / "annotated"
    result = prepare_annotation_images(source, inbox, stop=Event(), progress=lambda _: None)
    from PIL import Image

    exported = np.array(Image.open(result.directory / f"{source.metadata['battle_id']}__000000.png"))[:, :, ::-1]
    np.testing.assert_array_equal(exported, battlefield_image(frame))
    assert exported.shape == (1280, 720, 3)
    assert exported[121, 13].tolist() == [12, 34, 56]
    assert battlefield_box_to_screen((10, 10, 20, 20)) == (10, 10, 20, 20)
    assert battlefield_box_to_screen((0, 0, 720, 1280)) == (0, 0, 720, 1280)
    assert {path.name for path in inbox.iterdir()} == {"images"}
    assert not list((inbox / "labels").rglob("*.txt"))
    assert all(path.read_bytes() == content for path, content in before.items())
    recrop = recrop_session(source, tmp_path / "captures", (0, 120, 720, 1030), stop=Event(), progress=lambda _: None)
    repeated = prepare_annotation_images(recrop, inbox, stop=Event(), progress=lambda _: None)
    assert repeated.count == 0 and repeated.skipped == 1


@pytest.mark.parametrize("box", [(-1, 0, 10, 10), (0, 0, 721, 910), (10, 10, 0, 0), (float("nan"), 0, 1, 1)])
def test_invalid_prediction_coordinates_are_rejected(box):
    with pytest.raises(ValueError):
        battlefield_box_to_screen(box)


@pytest.mark.parametrize("labels_folder", [False, True])
def test_profile_survives_split_and_prevents_mixed_input_geometry(tmp_path, labels_folder):
    from PIL import Image

    inbox = tmp_path / "annotated"
    for index, color in enumerate((30, 130)):
        source = PreparationSession.create(tmp_path / f"capture-{index}")
        source.append(np.full((1280, 720, 3), color, np.uint8), source="test")
        result = prepare_annotation_images(source, inbox, stop=Event(), progress=lambda _: None)
        label_directory = inbox / "labels" if labels_folder else result.directory
        label_directory.mkdir(exist_ok=True)
        (label_directory / f"{source.metadata['battle_id']}__000000.txt").write_text("0 0.5 0.5 0.2 0.2\n")
    (inbox / "classes.txt").write_text("enemy_knight\n")
    scan = scan_annotations(inbox, inbox / "classes.txt")
    plan = plan_split(scan)
    output = export_split(plan, tmp_path / "datasets")
    assert json.loads((output / INPUT_CONFIG_NAME).read_text()) == input_config()
    assert len(plan.train) == len(plan.val) == 1
    Image.new("RGB", (720, 910)).save(scan.images[0].image)
    with pytest.raises(ValueError, match="尺寸.*不一致"):
        scan_annotations(inbox, inbox / "classes.txt")
    altered = input_config()
    altered["crop_ltrb"][1] = 120
    (inbox / INPUT_CONFIG_NAME).write_text(json.dumps(altered))
    with pytest.raises(ValueError, match="配置"):
        export_split(plan, tmp_path / "other-datasets")


def test_cancelled_export_leaves_no_partial_battle_and_legacy_is_read_only(tmp_path):
    source = PreparationSession.create(tmp_path / "captures")
    source.append(np.zeros((1280, 720, 3), np.uint8), source="test")
    stop = Event()
    stop.set()
    result = prepare_annotation_images(source, tmp_path / "annotated", stop=stop, progress=lambda _: None)
    assert result.count == 0 and result.directory.is_dir()
    assert list((tmp_path / "annotated" / "images").iterdir()) == []
    legacy = PreparationSession.create(tmp_path / "old", (0, 60, 360, 515), screen_size=(360, 640))
    legacy.append(np.zeros((640, 360, 3), np.uint8), source="test")
    with pytest.raises(ValueError, match="720×1280"):
        prepare_annotation_images(legacy, tmp_path / "legacy-annotated", stop=Event(), progress=lambda _: None)
