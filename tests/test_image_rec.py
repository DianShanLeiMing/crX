"""Template matching respects the BGR capture and reference format."""

import cv2
import numpy as np

from crX.detection.image_rec import compare_images


def test_color_template_matches_its_bgr_luminance():
    rng = np.random.default_rng(42)
    template: np.ndarray = rng.integers(0, 256, (12, 10, 3), dtype=np.uint8)
    image = rng.integers(0, 256, (30, 30, 3), dtype=np.uint8)
    # A grayscale screen rendering retains the reference's BGR luminance.
    luminance = cv2.cvtColor(template, cv2.COLOR_BGR2GRAY)
    image[5:17, 7:17] = luminance[:, :, None]

    assert compare_images(image, template, threshold=0.99) == [5, 7]


def test_reference_cache_reuses_templates_and_reloads_replaced_files(tmp_path, monkeypatch):
    from crX.detection import image_rec

    folder = tmp_path / "reference_images" / "buttons"
    folder.mkdir(parents=True)
    rng = np.random.default_rng(7)
    frame: np.ndarray = rng.integers(0, 256, (40, 40, 3), dtype=np.uint8)
    path = folder / "button.png"
    path.write_bytes(cv2.imencode(".png", frame[5:17, 7:17])[1].tobytes())
    monkeypatch.setattr(image_rec, "dirname", lambda _: str(tmp_path))
    original = image_rec.open_from_path
    reads = []

    def load(filename):
        reads.append(filename)
        return original(filename)

    monkeypatch.setattr(image_rec, "open_from_path", load)
    for _ in range(2):
        assert image_rec.find_references(frame, "buttons", 0.99) == ([[5, 7]], ["button.png"])
    assert len(reads) == 1
    # A different crop also changes file size, without depending on timestamp precision.
    path.write_bytes(cv2.imencode(".png", frame[20:34, 18:30])[1].tobytes())
    assert image_rec.find_references(frame, "buttons", 0.99) == ([[20, 18]], ["button.png"])
    assert len(reads) == 2
