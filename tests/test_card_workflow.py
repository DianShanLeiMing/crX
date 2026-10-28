"""One-step approval, provenance, corrections and interrupted-write recovery."""

import subprocess
import sys

import pytest

from crX.bot import card_detection as detection
from crX.utils import card_library as local
from crX.utils import card_workflow as workflow
from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json, save_review, scan_samples
from tests.test_card_library import fingerprint
from tests.test_card_review import add_group_sample


@pytest.fixture
def review_store(tmp_path, monkeypatch):
    destination = tmp_path / "formal.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("giant",))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", fingerprint(15)[None, ...])
    values = {}
    monkeypatch.setattr(local, "sample_fingerprint", lambda sample: values[sample.key])

    def add(name, channel=0, shift=0):
        sample = add_group_sample(tmp_path, name, "sample-000")
        values[sample.key] = fingerprint(channel, shift)
        return sample

    return add, destination


def records(root):
    samples, errors, _ = workflow.scan_review(root, refresh_library=True)
    assert not errors
    return {sample.key: sample for sample in samples}


def test_approval_adds_without_runtime_evidence_and_batch_duplicates_are_covered(tmp_path, review_store):
    add, destination = review_store
    first, duplicate, appearance = add("first"), add("duplicate", shift=5), add("appearance", channel=4)
    result = workflow.confirm_samples(tmp_path, [first, duplicate, appearance], "knight", "人工核对")
    assert "已入库 2 张" in result and "已有覆盖 1 张" in result
    current = records(tmp_path)
    assert current[first.key].status == current[appearance.key].status == "included"
    assert current[duplicate.key].status == "covered"
    assert all(sample.review["note"] == "人工核对" for sample in current.values())
    assert len(formal._read_entries(destination)) == 2
    assert current[first.key].review["formal_id"] == current[duplicate.key].review["formal_id"]
    assert records(tmp_path)[first.key].status == "included"  # Never reclassify its own reference as covered.
    assert not local.read_match_stats(tmp_path)
    assert all(set(entry) == {"id", "card_id", "fingerprint"} for entry in formal._read_entries(destination))


def test_formal_and_manual_label_conflicts_keep_human_choice_pending(tmp_path, review_store):
    add, destination = review_store
    formal_conflict = add("formal-conflict", channel=15)
    result = workflow.confirm_samples(tmp_path, [formal_conflict], "knight", "核对版本")
    current = records(tmp_path)[formal_conflict.key]
    assert "需复核" in result and "巨人" in current.review["work_reason"]
    assert current.status == "pending" and current.card_id == "knight"
    assert current.review["note"] == "核对版本"
    assert not formal._read_entries(destination)
    historical = add("historical")
    save_review(historical, "confirmed", "cannon")
    candidate = add("candidate", shift=5)
    assert "需复核" in workflow.confirm_samples(tmp_path, [candidate], "knight")
    assert records(tmp_path)[candidate.key].status == "pending"


def test_failed_image_does_not_block_other_selected_samples(tmp_path, review_store):
    add, destination = review_store
    bad, good = add("bad"), add("good", channel=4)
    bad.file("card").write_bytes(b"broken")
    result = workflow.confirm_samples(tmp_path, [bad, good], "knight")
    assert "检查失败 1 张" in result and "已入库 1 张" in result
    assert records(tmp_path)[bad.key].status == "pending"
    assert len(formal._read_entries(destination)) == 1


def test_failed_formal_write_keeps_choice_pending_and_retry_is_idempotent(tmp_path, review_store, monkeypatch):
    add, destination = review_store
    source = add("source")
    original_write = formal._write_entries
    monkeypatch.setattr(formal, "_write_entries", lambda *_: (_ for _ in ()).throw(PermissionError("read only")))
    with pytest.raises(PermissionError):
        workflow.confirm_samples(tmp_path, [source], "knight", "保留备注")
    current = records(tmp_path)[source.key]
    assert current.status == "pending" and current.card_id == "knight" and current.review["note"] == "保留备注"
    assert "read only" in current.review["work_reason"]
    assert not formal._read_entries(destination)
    assert not (tmp_path / "card-review-transaction.json").exists()
    monkeypatch.setattr(formal, "_write_entries", original_write)
    workflow.confirm_samples(tmp_path, [current], "knight", "保留备注")
    with pytest.raises(ValueError, match="变化|待确认"):
        workflow.confirm_samples(tmp_path, [current], "knight")
    assert len(formal._read_entries(destination)) == 1


def test_interrupted_annotation_write_recovers_owned_result_without_duplicate(tmp_path, review_store, monkeypatch):
    add, destination = review_store
    source = add("source")
    write = workflow.atomic_json

    def fail_after_formal(path, value):
        if path.name == "annotations.json" and formal._read_entries(destination):
            raise OSError("annotation disk failure")
        write(path, value)

    monkeypatch.setattr(workflow, "atomic_json", fail_after_formal)
    with pytest.raises(OSError, match="annotation disk failure"):
        workflow.confirm_samples(tmp_path, [source], "knight")
    assert (tmp_path / "card-review-transaction.json").is_file()
    assert len(formal._read_entries(destination)) == 1
    monkeypatch.setattr(workflow, "atomic_json", write)
    current = records(tmp_path)[source.key]
    assert current.status == "included" and current.review["owns_reference"]
    assert not (tmp_path / "card-review-transaction.json").exists()
    assert len(formal._read_entries(destination)) == 1


def test_legacy_trials_return_pending_with_notes_and_do_not_promote(tmp_path, review_store):
    add, destination = review_store
    source = add("legacy")
    save_review(source, "confirmed", "knight", "原备注")
    local.update_library(tmp_path, [source], "enable")
    current = records(tmp_path)[source.key]
    assert current.status == "pending" and current.card_id == "knight" and current.review["note"] == "原备注"
    assert not formal._read_entries(destination)
    assert local.read_library(tmp_path)[0]["status"] == "disabled"


def test_legacy_formal_and_ignored_records_are_classified_separately(tmp_path, review_store):
    add, destination = review_store
    included, covered, ignored = add("included"), add("covered", shift=5), add("ignored")
    for sample in (included, covered):
        save_review(sample, "confirmed", "knight")
    save_review(ignored, "ignored", note="切牌动画")
    value = fingerprint().tolist()
    entry = {"id": formal._signature("knight", value), "card_id": "knight", "fingerprint": value}
    atomic_json(destination, {"version": 1, "entries": [entry]})
    current = records(tmp_path)
    assert [current[sample.key].status for sample in (included, covered, ignored)] == ["included", "covered", "ignored"]
    assert current[ignored.key].review["work_reason"] == "切牌动画"


def test_correction_updates_shared_sources_and_remove_retains_images(tmp_path, review_store):
    add, destination = review_store
    first, duplicate, unrelated = add("first"), add("duplicate", shift=5), add("unrelated", channel=4)
    workflow.confirm_samples(tmp_path, [first], "knight", "备注一")
    workflow.confirm_samples(tmp_path, [duplicate], "knight", "备注二")
    workflow.confirm_samples(tmp_path, [unrelated], "archers")
    current = records(tmp_path)
    workflow.edit_formal_reference(tmp_path, [current[first.key]], new_card_id="cannon")
    current = records(tmp_path)
    assert current[first.key].card_id == current[duplicate.key].card_id == "cannon"
    assert current[first.key].review["note"] == "备注一" and current[duplicate.key].review["note"] == "备注二"
    assert {entry["card_id"] for entry in formal._read_entries(destination)} == {"cannon", "archers"}
    owner = next(
        sample for sample in current.values() if sample.card_id == "cannon" and sample.review["owns_reference"]
    )
    workflow.edit_formal_reference(tmp_path, [owner])
    current = records(tmp_path)
    assert current[first.key].status == current[duplicate.key].status == "pending"
    assert current[unrelated.key].status == "included"
    assert all(sample.file("frame").exists() for sample in current.values())
    assert [entry["card_id"] for entry in formal._read_entries(destination)] == ["archers"]


def test_conflicting_correction_preserves_existing_formal_and_annotations(tmp_path, review_store):
    add, destination = review_store
    source = add("source")
    workflow.confirm_samples(tmp_path, [source], "knight")
    current = records(tmp_path)[source.key]
    # The requested label conflicts with a different human label on an independent source.
    report = add("report")
    save_review(report, "confirmed", "archers")
    before = destination.read_bytes()
    with pytest.raises(ValueError, match="复核"):
        workflow.edit_formal_reference(tmp_path, [current], new_card_id="cannon")
    assert destination.read_bytes() == before
    assert next(sample for sample in scan_samples(tmp_path)[0] if sample.key == source.key).card_id == "knight"


def test_ignore_restore_and_sample_deletion_do_not_change_formal_library(tmp_path, review_store):
    add, destination = review_store
    source, ignored = add("source"), add("ignored")
    workflow.confirm_samples(tmp_path, [source], "knight")
    workflow.change_result(tmp_path, [ignored], "ignored", "切牌动画")
    current = records(tmp_path)
    assert current[ignored.key].status == "ignored"
    workflow.change_result(tmp_path, [current[ignored.key]], "pending")
    current = records(tmp_path)
    assert current[ignored.key].status == "pending" and current[ignored.key].review["note"] == "切牌动画"
    before = destination.read_bytes()
    assert workflow.delete_samples(tmp_path, [current[source.key]]) == 1
    assert destination.read_bytes() == before and not source.file("frame").exists()


def test_read_only_installation_never_claims_success(tmp_path, review_store, monkeypatch):
    add, destination = review_store
    source = add("source")
    monkeypatch.setattr(formal, "source_checkout", lambda: False)
    with pytest.raises(ValueError, match="安装版"):
        workflow.confirm_samples(tmp_path, [source], "knight")
    assert records(tmp_path)[source.key].status == "pending"
    assert not formal._read_entries(destination)


def test_os_lock_is_released_after_abrupt_writer_exit(tmp_path):
    path = tmp_path / "writer.lock"
    code = "from pathlib import Path; import os, sys; from crX.utils.formal_library import writer_lock; lock = writer_lock(Path(sys.argv[1])); lock.__enter__(); os._exit(0)"
    subprocess.run([sys.executable, "-c", code, str(path)], check=True, timeout=10)
    with formal.writer_lock(path):
        pass


def test_pending_conflict_shows_real_manual_counterpart(tmp_path, review_store):
    add, _ = review_store
    reference = add("reference")
    save_review(reference, "confirmed", "cannon")
    source = add("source", shift=5)
    workflow.confirm_samples(tmp_path, [source], "knight")
    current = records(tmp_path)[source.key]
    comparison = workflow.reference_details(tmp_path, current)
    assert "加农炮" in comparison["text"] and reference.key in comparison["text"]
    assert comparison["image"] is not None


def test_concurrent_formal_change_does_not_leave_a_blocking_transaction(tmp_path, review_store):
    add, destination = review_store
    source = add("source")
    value = fingerprint(4).tolist()
    entry = {"id": formal._signature("archers", value), "card_id": "archers", "fingerprint": value}
    atomic_json(destination, {"version": 1, "entries": [entry]})
    with workflow._locked(tmp_path), pytest.raises(ValueError, match="变化"):
        workflow._commit(tmp_path, [], [], [(source, workflow._result("pending", "knight", "待确认"))])
    assert not (tmp_path / "card-review-transaction.json").exists()
    assert records(tmp_path)[source.key].status == "pending"


def test_reference_source_cannot_remove_a_program_base_reference(tmp_path, review_store):
    add, destination = review_store
    source = add("source", channel=15)
    workflow.confirm_samples(tmp_path, [source], "giant")
    current = records(tmp_path)[source.key]
    assert current.status == "covered" and not current.review["owns_reference"]
    with pytest.raises(ValueError, match="本流程"):
        workflow.edit_formal_reference(tmp_path, [current])
    assert not formal._read_entries(destination)
