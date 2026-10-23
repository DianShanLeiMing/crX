"""Promotion requires per-reference evidence and an explicit, rechecked human approval."""

import json

import pytest

from crX.bot import card_detection
from crX.utils import card_library as local
from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json, library_card_summaries, save_review, scan_grouped_samples
from tests.test_card_library import fingerprint
from tests.test_card_review import add_group_sample


@pytest.fixture
def promotion_samples(tmp_path, monkeypatch):
    destination = tmp_path / "builtins.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(formal, "card_color_data", {})
    monkeypatch.setattr(formal, "CARD_FINGERPRINT_VARIANTS", {"giant": [fingerprint(15).tolist()]})
    monkeypatch.setattr(formal, "_runtime_cache", (None, 0.0, []))
    values = {}
    monkeypatch.setattr(local, "sample_fingerprint", lambda sample: values[sample.key])

    def add(name="sample-000", *, card="knight", channel=0, count=100, margin=100):
        sample = add_group_sample(tmp_path, "samples", name)
        values[sample.key] = fingerprint(channel)
        save_review(sample, "confirmed", card)
        local.update_library(tmp_path, [sample], "enable")
        entry = next(entry for entry in local.read_library(tmp_path) if sample.key in entry["sources"])
        local.merge_match_stats(
            tmp_path, {entry["id"]: {"matches": count, "distance_total": count * 40, "margin_total": count * margin}}
        )
        return sample, entry

    return add, destination, values


@pytest.mark.parametrize(
    ("count", "margin", "expected"), [(99, 200, "暂不建议"), (100, 99, "暂不建议"), (100, 100, "建议纳入")]
)
def test_suggestion_thresholds_are_per_reference(tmp_path, promotion_samples, count, margin, expected):
    add, _destination, _values = promotion_samples
    _, entry = add(count=count, margin=margin)
    result = formal.assess_references(tmp_path)[entry["id"]]
    assert result["status"] == expected
    assert result["average_distance"] == 40


def test_summary_counts_do_not_promote_unobserved_appearances(tmp_path, promotion_samples):
    add, destination, _values = promotion_samples
    first, entry = add(count=226, margin=200)
    second, other = add("sample-001", channel=4, count=0)
    samples, errors, _groups = scan_grouped_samples(tmp_path, refresh_library=True)
    assert not errors
    summary = library_card_summaries(samples)[0]
    assert summary.review["summary_matches"] == 226 and summary.review["summary_enabled"] == 2
    assert summary.review["formal_status"] == "建议纳入"
    assert "建议纳入 1 份" in summary.review["formal_detail"]
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    assert [item["id"] for item in candidates] == [entry["id"]]
    assert candidates[0]["preview"].height > 20
    assert not formal._read_entries(destination)  # Preparing the preview does not write anything.
    assert "1 份参考" in formal.confirm_promotion(tmp_path, candidates)
    assert len(formal._read_entries(destination)) == 1
    results = formal.assess_references(tmp_path)
    assert results[entry["id"]]["status"] == "已纳入"
    assert results[other["id"]]["status"] == "暂不建议"
    before = destination.read_bytes()
    assert "无需重复" in formal.confirm_promotion(tmp_path, candidates)
    assert destination.read_bytes() == before
    local.clear_filtered_samples(tmp_path, {first.key, second.key})
    assert destination.read_bytes() == before
    monkeypatch = pytest.MonkeyPatch()
    with monkeypatch.context() as patch:
        patch.setattr(card_detection, "runtime_entries", lambda: [])
        recognized = card_detection.recognize_card_fingerprint(
            [dict(zip(card_detection.COLORS_KEYS, row.tolist(), strict=True)) for row in fingerprint(0)]
        )
    assert recognized["card_id"] == "knight" and recognized["source"].startswith("builtin:")
    assert set(json.loads(before)["entries"][0]) == {"id", "card_id", "fingerprint"}


def test_rechecks_after_preview_and_blocks_changed_evidence(tmp_path, promotion_samples):
    add, destination, _values = promotion_samples
    source, entry = add()
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    local.update_library(tmp_path, [source], "revoke")
    with pytest.raises(ValueError):
        formal.confirm_promotion(tmp_path, candidates)
    assert not formal._read_entries(destination)
    assert formal.assess_references(tmp_path)[entry["id"]]["status"] == "暂不建议"


def test_human_label_conflicts_cannot_be_promoted(tmp_path, promotion_samples):
    add, destination, _values = promotion_samples
    _, entry = add()
    add("sample-001", card="cannon")
    assert "冲突" in formal.assess_references(tmp_path)[entry["id"]]["reason"]
    assert formal.prepare_promotion(tmp_path, {"knight"}) == []
    assert not formal._read_entries(destination)


def test_builtin_coverage_and_cross_card_similarity(tmp_path, promotion_samples, monkeypatch):
    add, _destination, _values = promotion_samples
    _, entry = add()
    monkeypatch.setattr(formal, "CARD_FINGERPRINT_VARIANTS", {"knight": [fingerprint().tolist()]})
    assert formal.assess_references(tmp_path)[entry["id"]]["status"] == "已纳入"
    monkeypatch.setattr(formal, "CARD_FINGERPRINT_VARIANTS", {"cannon": [fingerprint().tolist()]})
    result = formal.assess_references(tmp_path)[entry["id"]]
    assert result["status"] == "暂不建议" and "其他卡牌" in result["reason"]


def test_installed_app_exports_without_claiming_incorporation(tmp_path, promotion_samples, monkeypatch):
    add, destination, _values = promotion_samples
    _, entry = add()
    monkeypatch.setattr(formal, "source_checkout", lambda: False)
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    assert "已导出" in formal.confirm_promotion(tmp_path, candidates)
    exported = formal._read_entries(tmp_path / "formal-library-export.json")
    assert len(exported) == 1 and not formal._read_entries(destination)
    formal.confirm_promotion(tmp_path, candidates)
    assert formal._read_entries(tmp_path / "formal-library-export.json") == exported
    assert formal.assess_references(tmp_path)[entry["id"]]["status"] == "建议纳入"


@pytest.mark.parametrize("mutation", ["id", "fingerprint", "version", "size", "metadata"])
def test_invalid_formal_data_is_rejected_without_breaking_base_recognition(tmp_path, promotion_samples, mutation):
    _add, destination, _values = promotion_samples
    entry = {
        "id": formal._signature("knight", fingerprint().tolist()),
        "card_id": "knight",
        "fingerprint": fingerprint().tolist(),
    }
    document = {"version": 1, "entries": [entry]}
    if mutation == "id":
        entry["id"] = "wrong"
    elif mutation == "fingerprint":
        entry["fingerprint"][0][0] = -1
    elif mutation == "version":
        document["version"] = 2
    elif mutation == "metadata":
        entry["screenshot_path"] = "private-player-data.png"
    if mutation == "size":
        destination.write_bytes(b"0" * (formal.MAX_BYTES + 1))
    else:
        atomic_json(destination, document)
    with pytest.raises(ValueError):
        formal._read_entries(destination)
    assert formal.runtime_formal_entries() == []


def test_atomic_write_failure_and_concurrent_change_preserve_library(tmp_path, promotion_samples, monkeypatch):
    add, destination, _values = promotion_samples
    add()
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    before = destination.read_bytes()
    monkeypatch.setattr(formal, "atomic_json", lambda *_args: (_ for _ in ()).throw(OSError("disk full")))
    with pytest.raises(OSError, match="disk full"):
        formal.confirm_promotion(tmp_path, candidates)
    assert destination.read_bytes() == before
    assert not list(tmp_path.glob("*.lock"))
    with pytest.raises(ValueError, match="已变化"):
        formal._write_entries(destination, [], [{"other": "version"}])
    assert destination.read_bytes() == before


def test_missing_preview_and_changed_fingerprint_cannot_be_approved(tmp_path, promotion_samples):
    add, destination, values = promotion_samples
    sample, entry = add()
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    sample.file("card").unlink()
    with pytest.raises(ValueError, match="截图"):
        formal.prepare_promotion(tmp_path, {"knight"})
    values[sample.key] = fingerprint(4)
    with pytest.raises(ValueError):
        formal.confirm_promotion(tmp_path, candidates)
    assert not formal._read_entries(destination)
    assert formal.assess_references(tmp_path)[entry["id"]]["status"] == "暂不建议"


def test_existing_writer_lock_blocks_without_overwriting(tmp_path, promotion_samples):
    add, destination, _values = promotion_samples
    add()
    candidates = formal.prepare_promotion(tmp_path, {"knight"})
    before = destination.read_bytes()
    lock = destination.with_name(destination.name + ".lock")
    lock.write_text("another writer", encoding="utf-8")
    with pytest.raises(ValueError, match="另一个窗口"):
        formal.confirm_promotion(tmp_path, candidates)
    assert destination.read_bytes() == before and lock.read_text(encoding="utf-8") == "another writer"
