"""Reference priority changes adoption without changing the best-matching card."""

import numpy as np

from crX.bot import card_detection as detection
from crX.utils import formal_library as formal
from tests.test_card_library import fingerprint


def test_first_choice_only_breaks_equal_distances(monkeypatch):
    base = fingerprint()
    other = base.copy()
    other[0][0] -= 10
    other[0][1] += 10
    first_id = formal._signature("knight", base.tolist())
    preferred_id = formal._signature("knight", other.tolist())
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("knight", "knight", "giant", "zap"))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", np.array([base, other, fingerprint(14), fingerprint(13)]))
    monkeypatch.setattr(detection, "_REFERENCE_SOURCES", ("upstream", "zh_native_1", "upstream", "upstream"))
    monkeypatch.setattr(detection, "runtime_entries", lambda: [])
    monkeypatch.setattr(detection, "runtime_formal_entries", lambda: [])
    monkeypatch.setattr(
        detection,
        "runtime_preferences",
        lambda: {"knight": {"preferred": preferred_id, "numbers": {preferred_id: 0, first_id: 1}}},
    )

    def recognize(value):
        return detection.recognize_card_fingerprint(
            [dict(zip(detection.COLORS_KEYS, row.tolist(), strict=True)) for row in value]
        )

    assert recognize(base)["reference_id"] == first_id
    midpoint = base.copy()
    midpoint[0][0] -= 5
    midpoint[0][1] += 5
    assert recognize(midpoint)["reference_id"] == preferred_id
    assert recognize(midpoint)["card_id"] == "knight"
