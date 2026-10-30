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


def test_local_clear_does_not_change_shared_and_a_pull_can_replace_it(tmp_path, shared):
    a, b = tmp_path / "a", tmp_path / "b"
    hits.record_hits(a, {FIRST: 3})
    publish(a)
    before = shared.read_bytes()
    hits.clear_local(a)
    assert hits.totals(a) == ({}, True)
    assert shared.read_bytes() == before
    hits.record_hits(b, {FIRST: 2})
    publish(b)  # Simulates receiving a new shared file through pull.
    assert hits.totals(a) == ({FIRST: 5}, False)


def test_uploaded_reset_never_resurrects_old_offline_machine_counts(tmp_path, shared):
    a, b = tmp_path / "a", tmp_path / "b"
    hits.record_hits(a, {FIRST: 3})
    hits.record_hits(b, {FIRST: 8})
    publish(b)
    publish(a)
    old = hits.read_shared()
    hits.record_hits(b, {FIRST: 4})  # Unpublished old-epoch contributions.
    hits.clear_local(a)
    hits.record_hits(a, {SECOND: 2})
    reset = publish(a)
    assert reset["epoch"] != old["epoch"]
    assert hits.totals(b) == ({SECOND: 2}, False)
    hits.record_hits(b, {FIRST: 1})
    publish(b)
    assert hits.totals(a)[0] == {FIRST: 1, SECOND: 2}


def test_failed_push_snapshot_can_retry_the_same_reset(tmp_path, shared):
    root = tmp_path / "a"
    hits.record_hits(root, {FIRST: 4})
    publish(root)
    hits.clear_local(root)
    first = hits.prepare_upload(root, hits.read_shared())
    retry = hits.prepare_upload(root, first)  # Commit exists; push had failed.
    assert retry == first
    assert hits.totals(root) == ({}, True)
    hits.acknowledge_upload(root, retry)
    assert hits.totals(root) == ({}, False)


def test_another_local_clear_replaces_an_unpushed_reset_snapshot(tmp_path, shared):
    root = tmp_path / "a"
    hits.record_hits(root, {FIRST: 3})
    publish(root)
    hits.clear_local(root)
    hits.record_hits(root, {FIRST: 1})
    unpushed = hits.prepare_upload(root, hits.read_shared())
    hits.clear_local(root)
    latest = hits.prepare_upload(root, unpushed)
    hits.acknowledge_upload(root, latest)
    assert latest["epoch"] != unpushed["epoch"]
    assert hits.totals(root) == ({}, False)


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
