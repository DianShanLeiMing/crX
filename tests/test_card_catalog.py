"""Catalog counts effective references rather than source screenshots."""

from crX.bot import card_detection as detection
from crX.bot.card_data import CARD_NAMES_ZH
from crX.utils import card_catalog as catalog
from crX.utils import card_workflow as workflow
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
    assert reference.status == "待上传" and catalog.reference_crop(reference) is not None
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
    assert catalog.reference_crop(data["knight"][0]) is None
    assert catalog.preview_crop(data["giant"][0]) is None
    other_machine = catalog.build_catalog([])
    assert len(other_machine["knight"]) == 1 and catalog.reference_crop(other_machine["knight"][0]) is None
    assert other_machine["knight"][0].status == "正式参考"
    assert destination.read_bytes() == before
