"""Catalog counts effective references rather than source screenshots."""

import pytest

from crX.bot import card_detection as detection
from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils import card_catalog as catalog
from crX.utils import card_workflow as workflow
from crX.utils import reference_hits as hits
from crX.utils import reference_preferences as preferences
from tests.test_card_library import fingerprint
from tests.test_card_workflow import records

pytest_plugins = ("tests.test_card_workflow",)


def test_catalog_includes_zero_sample_cards_and_deduplicates_formal_sources(tmp_path, review_store, monkeypatch):
    add, _destination = review_store
    first, duplicate, extra = add("first"), add("duplicate", shift=5), add("extra", channel=4)
    workflow.confirm_samples(tmp_path, [first, duplicate, extra], "knight", "保留备注")
    current = records(tmp_path)
    data = catalog.build_catalog(list(current.values()))
    assert set(CARD_NAMES_ZH).issubset(data)
    assert len(data["knight"]) == 2 and len(data["giant"]) == 1 and data["archers"] == []
    reference = next(ref for ref in data["knight"] if ref.identifier == current[first.key].review["formal_id"])
    assert {sample.key for sample in reference.sources} == {first.key, duplicate.key}
    assert reference.status == "待上传" and reference.name == "参考1"
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("knight", "knight"))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", fingerprint()[None, ...].repeat(2, axis=0))
    deduplicated = catalog.build_catalog(list(current.values()))
    assert len(deduplicated["knight"]) == 2
    assert deduplicated["knight"][0].builtin and deduplicated["knight"][0].uploaded


def test_missing_images_keep_references_and_never_invent_a_thumbnail(tmp_path, review_store):
    add, destination = review_store
    sample = add("missing")
    workflow.confirm_samples(tmp_path, [sample], "knight")
    before = destination.read_bytes()
    current = records(tmp_path)
    sample.file("card").unlink()
    data = catalog.build_catalog(list(current.values()))
    assert len(data["knight"]) == 1
    assert data["knight"][0].name == "参考1"
    assert data["giant"][0].name == "首选（内置参考）"
    other_machine = catalog.build_catalog([])
    assert len(other_machine["knight"]) == 1 and other_machine["knight"][0].identifier == data["knight"][0].identifier
    assert other_machine["knight"][0].status == "正式参考"
    assert destination.read_bytes() == before


def test_first_choice_swap_preserves_source_and_counts(tmp_path, review_store, monkeypatch):
    add, _destination = review_store
    first, second = add("first"), add("second", channel=4)
    workflow.confirm_samples(tmp_path, [first, second], "knight")
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("knight", "knight"))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", fingerprint(15)[None, ...].repeat(2, axis=0))
    data = catalog.build_catalog(list(records(tmp_path).values()), root=tmp_path)
    # Duplicate original references collapse, leaving one original first choice.
    original = next(ref for ref in data["knight"] if ref.builtin)
    candidate = next(ref for ref in data["knight"] if not ref.builtin)
    assert original.name == "首选（内置参考）" and candidate.name == "参考1"
    hits.record_hits(tmp_path, {original.identifier: 3, candidate.identifier: 8})
    catalog.set_preferred_reference(tmp_path, "knight", candidate.identifier)
    refs = catalog.build_catalog(list(records(tmp_path).values()), root=tmp_path)["knight"]
    assert refs[0].identifier == candidate.identifier and refs[0].name == "首选" and refs[0].hits == 8
    old = next(ref for ref in refs if ref.identifier == original.identifier)
    assert old.name == "参考1（内置参考）" and old.hits == 3
    assert sum(ref.preferred for ref in refs) == 1


def test_no_original_reference_requires_manual_first_choice_and_stable_numbers(tmp_path, review_store):
    add, _destination = review_store
    first, second = add("first"), add("second", channel=4)
    workflow.confirm_samples(tmp_path, [first, second], "knight")
    refs = catalog.build_catalog(list(records(tmp_path).values()), root=tmp_path)["knight"]
    assert [ref.name for ref in refs] == ["参考1", "参考2"]
    hits.record_hits(tmp_path, {refs[1].identifier: 10})
    reordered = catalog.build_catalog(list(records(tmp_path).values()), root=tmp_path)["knight"]
    assert [ref.name for ref in reordered] == ["参考2", "参考1"]
    catalog.set_preferred_reference(tmp_path, "knight", refs[1].identifier)
    preferred = catalog.build_catalog(list(records(tmp_path).values()), root=tmp_path)["knight"]
    assert preferred[0].name == "首选" and preferred[1].name == "参考1"
    value = preferences.read_document(preferences.preferences_path())["cards"]["knight"]
    assert value["preferred"] == refs[1].identifier and len(value["numbers"]) == 2


def test_manual_order_survives_counts_refresh_and_first_choice_exchange(tmp_path, review_store):
    add, _destination = review_store
    first, second, third = add("first"), add("second", channel=4), add("third", channel=8)
    workflow.confirm_samples(tmp_path, [first, second, third], "knight")
    samples = list(records(tmp_path).values())
    refs = catalog.build_catalog(samples, root=tmp_path)["knight"]
    preferred, one, two = (ref.identifier for ref in refs)
    catalog.set_preferred_reference(tmp_path, "knight", preferred)
    original = (preferred, one, two)
    reordered = (preferred, two, one)
    catalog.reorder_references(tmp_path, "knight", reordered, original)
    hits.record_hits(tmp_path, {one: 50, two: 1})
    actual = catalog.build_catalog(samples, root=tmp_path)["knight"]
    assert tuple(ref.identifier for ref in actual) == reordered
    assert [ref.name for ref in actual] == ["首选", "参考3", "参考2"]
    with pytest.raises(ValueError, match="顺序已经变化"):
        catalog.reorder_references(tmp_path, "knight", original, original)
    with pytest.raises(ValueError, match="首选固定"):
        catalog.reorder_references(tmp_path, "knight", (two, preferred, one), reordered)
    catalog.set_preferred_reference(tmp_path, "knight", one)
    actual = catalog.build_catalog(samples, root=tmp_path)["knight"]
    assert [ref.identifier for ref in actual] == [one, two, preferred]
    assert [ref.hits for ref in actual] == [50, 1, 0]
