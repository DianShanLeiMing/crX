"""Regression: fingerprints and bot both use BGR emulator screenshots."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import Mock

import cv2
import numpy as np
from PIL import Image

from crX.bot import card_detection as cd
from tests.screen_fixtures import native_replay

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "card_hand_bgr_regression.png"

EXPECTED = {
    0: "lumberjack",
    1: "ram_rider",
    2: "ice_wizard",
    3: "hero_bowler",
}


class _BgrEmulator:
    def __init__(self, bgr: np.ndarray) -> None:
        self._bgr = bgr

    def screenshot(self) -> np.ndarray:
        return self._bgr


def _load_bgr() -> np.ndarray:
    if not FIXTURE.is_file():
        raise AssertionError(f"missing fixture: {FIXTURE}")
    rgb = np.array(Image.open(FIXTURE).convert("RGB"))
    return cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)


def test_bot_uses_raw_bgr_screenshot() -> None:
    bgr = np.zeros((4, 4, 3), dtype=np.uint8)
    bgr[0, 0] = [10, 20, 30]
    out = _BgrEmulator(bgr).screenshot()
    assert out[0, 0].tolist() == [10, 20, 30]


def test_bgr_path_identifies_hand() -> None:
    bgr = native_replay(_load_bgr())
    cd.check_which_cards_are_available(_BgrEmulator(bgr))
    for slot, expected in EXPECTED.items():
        corners = cd.get_all_pixel_data(None, slot)
        raw = cd.find_closest_card(corners)
        got = cd.CARD_DETECTION_ALIASES.get(raw, raw)
        assert got == expected, f"slot {slot}: expected {expected}, got {got} (raw {raw})"


def test_color_counts_match_euclidean_reference():
    bgr = native_replay(_load_bgr())
    left, top = cd.toplefts[0]
    corner = bgr[top : top + 66, left : left + 54]
    palette = np.array(list(cd.COLORS.values()), dtype=np.int64)
    distances = np.linalg.norm(palette - corner.reshape(-1, 1, 3), axis=2)
    expected = np.bincount(np.argmin(distances, axis=1), minlength=len(palette))
    assert cd.get_corner_pixels((0, 54), (0, 66), corner) == dict(zip(cd.COLORS, expected, strict=True))


def test_diagnostic_and_play_reuse_recognition_only_on_the_same_frame(monkeypatch):
    frame = native_replay(_load_bgr())
    collector = Mock()
    collector.observation_due.return_value = True
    monkeypatch.setattr(cd, "active_card_samples", lambda: collector)
    monkeypatch.setattr(cd, "_card_purple_counts", lambda _: [104, 0, 0, 0])
    extract = Mock(wraps=cd.extract_card_fingerprint)
    monkeypatch.setattr(cd, "extract_card_fingerprint", extract)
    for current in (frame, frame.copy()):
        assert cd.check_which_cards_are_available(None, frame=current) == [0]
        assert cd.identify_hand_cards(None, 0) == EXPECTED[0]
    # Card sampling only matches usable positions; selection reuses the cache.
    assert extract.call_count == 2


def test_golden_knight_live_fingerprint_does_not_match_prince():
    # Held-out native frame from the match where this portrait matched Prince.
    counts = (
        (0, 0, 0, 1, 0, 59, 1, 0, 0, 26, 470, 0, 1, 9, 322, 2),
        (0, 0, 0, 2, 3, 54, 0, 0, 0, 26, 499, 0, 0, 2, 304, 1),
        (0, 0, 0, 3, 0, 143, 5, 0, 24, 122, 120, 0, 9, 16, 446, 3),
        (0, 0, 0, 2, 0, 29, 3, 0, 43, 50, 528, 0, 6, 6, 219, 5),
    )
    fingerprint = [dict(zip(cd.COLORS_KEYS, corner, strict=True)) for corner in counts]
    assert cd.recognize_card_fingerprint(fingerprint)["card_id"] == "golden_knight"
