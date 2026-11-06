"""Explicitly synthesized 720p replays of historical 360p captures.

These exercise native-coordinate rules; they do not certify real 720p rendering.
Historical sample/annotation tests continue reading their original files.
"""

import cv2


def native_replay(image):
    assert image is not None
    if image.shape == (640, 360, 3):
        return cv2.resize(image, (720, 1280), interpolation=cv2.INTER_NEAREST)
    # Keep unsupported geometries intact for negative fixture sweeps.
    return image
