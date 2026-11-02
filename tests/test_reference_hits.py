"""Shared counters merge idempotently and local resets remain opt-in."""

import json

import pytest

from crX.bot.card_samples import CardSampleCollector
from crX.utils import reference_hits as hits
from crX.utils.card_review import atomic_json

FIRST, SECOND = "1" * 16, "2" * 16


@pytest.fixture
def shared(tmp_path, monkeypatch):
    path = tmp_path / "shared.json"
    monkeypatch.setattr(hits, "shared_path", lambda: path)
    atomic_json(path, hits.empty_shared())
    return path


def publish(root):
    document = hits.prepare_upload(root, hits.read_shared())
    hits.acknowledge_upload(root, document)
    return document


def test_two_machines_add_only_their_new_counts_and_retries_are_idempotent(tmp_path, shared):
    a, b = tmp_path / "a", tmp_path / "b"
    hits.record_hits(a, {FIRST: 3})
    hits.record_hits(b, {FIRST: 5, SECOND: 2})
    publish(a)
    publish(b)
    assert hits.totals(a)[0] == hits.totals(b)[0] == {FIRST: 8, SECOND: 2}
    before = shared.read_bytes()
    publish(a)
    publish(b)
    assert shared.read_bytes() == before
    hits.record_hits(a, {FIRST: 1})
    publish(a)
    assert hits.totals(b)[0] == {FIRST: 9, SECOND: 2}


def test_selected_clear_keeps_other_machines_and_never_resets_online(tmp_path, shared):
    a, b = tmp_path / "a", tmp_path / "b"
    hits.record_hits(a, {FIRST: 3, SECOND: 4})
    publish(a)
    hits.record_hits(b, {FIRST: 5})
    publish(b)
    before = shared.read_bytes()
    hits.record_hits(a, {FIRST: 10, SECOND: 2})
    hits.clear_local(a, [FIRST])
    assert hits.totals(a)[0] == {FIRST: 5, SECOND: 6}
    assert hits.snapshot(a)[1] == {FIRST, SECOND}
    assert shared.read_bytes() == before
    publish(a)
    assert hits.totals(a)[0] == {FIRST: 5, SECOND: 6}
    assert hits.totals(b)[0] == {FIRST: 8, SECOND: 6}
    assert hits.snapshot(a)[1] == set()
    hits.record_hits(a, {FIRST: 2})
    publish(a)
    assert hits.totals(a)[0][FIRST] == 7
    assert hits.totals(b)[0][FIRST] == 10
    # Remote updates and restarts must not resurrect this machine's cleared hits.
    hits.record_hits(b, {FIRST: 1})
    publish(b)
    assert hits.totals(a)[0][FIRST] == 8


def test_clear_before_first_upload_discards_only_selected_unpublished_hits(tmp_path, shared):
    hits.record_hits(tmp_path, {FIRST: 3, SECOND: 4})
    hits.clear_local(tmp_path, [FIRST])
    publish(tmp_path)
    assert hits.totals(tmp_path)[0] == {FIRST: 0, SECOND: 4}
    assert sum(node.get(FIRST, 0) for node in hits.read_shared()["nodes"].values()) == 0


def test_failed_push_retries_without_uploading_local_clear(tmp_path, shared):
    hits.record_hits(tmp_path, {FIRST: 4})
    publish(tmp_path)
    before = hits.read_shared()
    hits.clear_local(tmp_path, [FIRST])
    first = hits.prepare_upload(tmp_path, before)
    retry = hits.prepare_upload(tmp_path, first)
    assert retry == first == before
    hits.acknowledge_upload(tmp_path, retry)
    assert hits.totals(tmp_path)[0] == {FIRST: 0}
    assert hits.snapshot(tmp_path)[1] == set()


def test_increments_and_clear_during_upload_remain_changed(tmp_path, shared):
    hits.record_hits(tmp_path, {FIRST: 3, SECOND: 2})
    publish(tmp_path)
    hits.record_hits(tmp_path, {SECOND: 1})
    uploading = hits.prepare_upload(tmp_path, hits.read_shared())
    hits.clear_local(tmp_path, [FIRST])
    hits.record_hits(tmp_path, {SECOND: 1})
    hits.acknowledge_upload(tmp_path, uploading)
    assert hits.snapshot(tmp_path) == ({FIRST: 0, SECOND: 4}, {FIRST, SECOND})
    publish(tmp_path)
    assert hits.snapshot(tmp_path) == ({FIRST: 0, SECOND: 4}, set())


def test_one_hit_per_continuous_card_slot_and_new_battle(tmp_path, shared):
    def result(card, reference):
        return {"card_id": card, "reference_id": reference, "source": "upstream", "candidates": [{"distance": 20}]}

    collector = CardSampleCollector("1v1", tmp_path)
    try:
        for _ in range(10):
            collector.observe_library_match(result("knight", FIRST), 0)
        collector.observe_library_match({"card_id": "UNKNOWN"}, 0)
        collector.observe_library_match(result("knight", SECOND), 0)
        collector.observe_library_match(result("knight", SECOND), 1)
        collector.observe_library_match(result("giant", SECOND), 0)
        collector.observe_library_match(result("knight", FIRST), 0)
    finally:
        assert collector.close()
    assert hits.totals(tmp_path)[0] == {FIRST: 2, SECOND: 2}
    second = CardSampleCollector("1v1", tmp_path)
    second.observe_library_match(result("knight", FIRST), 0)
    assert second.close()
    assert hits.totals(tmp_path)[0] == {FIRST: 3, SECOND: 2}
    assert not collector.directory.exists()


@pytest.mark.parametrize("value", [-1, True, "2", hits.MAX_COUNT + 1])
def test_invalid_counts_are_rejected(tmp_path, shared, value):
    document = {"version": 1, "epoch": "initial", "nodes": {"a" * 32: {FIRST: value}}}
    shared.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ValueError, match="命中次数无效"):
        hits.totals(tmp_path)


def test_legacy_local_reset_cannot_reset_online_statistics(tmp_path, shared):
    hits.record_hits(tmp_path, {FIRST: 4})
    publish(tmp_path)
    before = hits.read_shared()
    path = tmp_path / "reference-hits-local.json"
    local = json.loads(path.read_text(encoding="utf-8"))
    local.update(epoch="b" * 32, counts={SECOND: 2}, cleared=True)
    atomic_json(path, local)
    uploaded = publish(tmp_path)
    assert uploaded["epoch"] == before["epoch"]
    assert hits.totals(tmp_path)[0] == {FIRST: 4, SECOND: 2}
