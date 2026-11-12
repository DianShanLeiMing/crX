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
