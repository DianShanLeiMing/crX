"""Reference priority changes adoption without changing the best-matching card."""

import numpy as np

from crX.bot import card_detection as detection
from crX.utils import formal_library as formal
from tests.test_card_library import fingerprint


def test_first_choice_is_adopted_but_card_distances_remain_the_best_match(monkeypatch):
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

    result = recognize(base)
    assert result["reference_id"] == preferred_id
    assert result["reference_distance"] == 20
    assert result["candidates"][0]["reference_id"] == first_id
    assert result["candidates"][0]["distance"] == 0
    midpoint = base.copy()
    midpoint[0][0] -= 5
    midpoint[0][1] += 5
    assert recognize(midpoint)["reference_id"] == preferred_id
    assert recognize(midpoint)["card_id"] == "knight"


def test_manual_order_precedes_hit_counts_and_skips_references_over_threshold(monkeypatch):
    base = fingerprint()
    later = base.copy()
    later[0][0] -= 10
    later[0][1] += 10
    ids = [formal._signature("knight", value.tolist()) for value in (fingerprint(15), base, later)]
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("knight", "knight", "knight", "giant", "zap"))
    monkeypatch.setattr(
        detection, "_REFERENCE_MATRIX", np.array([fingerprint(15), base, later, fingerprint(14), fingerprint(13)])
    )
    monkeypatch.setattr(detection, "_REFERENCE_SOURCES", ("upstream", "one", "two", "upstream", "upstream"))
    monkeypatch.setattr(detection, "runtime_entries", lambda: [])
    monkeypatch.setattr(detection, "runtime_formal_entries", lambda: [])
    value = {"preferred": ids[0], "numbers": dict(zip(ids, range(3), strict=True)), "order": [ids[0], ids[2], ids[1]]}
    monkeypatch.setattr(detection, "runtime_preferences", lambda: {"knight": value})
    monkeypatch.setattr(detection.reference_hits, "_runtime_counts", {ids[1]: 1000})
    observed = [dict(zip(detection.COLORS_KEYS, row.tolist(), strict=True)) for row in base]
    result = detection.recognize_card_fingerprint(observed)
    assert result["card_id"] == "knight" and result["reference_id"] == ids[2] and result["source"] == "two"
    assert result["reference_distance"] == 20 and result["candidates"][0]["distance"] == 0
    value["order"] = [ids[0], ids[1], ids[2]]
    assert detection.recognize_card_fingerprint(observed)["reference_id"] == ids[1]
