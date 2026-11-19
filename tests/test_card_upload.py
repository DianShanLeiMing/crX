"""Exercise selective publication against a temporary local Git remote."""

import json
import shutil
import subprocess

import pytest

from crX.bot import card_detection as detection
from crX.utils import card_library as local
from crX.utils import card_upload as upload
from crX.utils import card_workflow as workflow
from crX.utils import formal_library as formal
from crX.utils import reference_hits as hits
from crX.utils.card_catalog import build_catalog, reorder_references, set_preferred_reference
from crX.utils.card_review import atomic_json, export_candidate
from tests.test_card_library import fingerprint
from tests.test_card_review import add_group_sample

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable")
run_lint_checks = upload._lint


def git(repo, *args):
    return upload._git(repo, *args).stdout.decode("utf-8").strip()


@pytest.fixture
def upload_store(tmp_path, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    remote = tmp_path / "remote.git"
    git(repo, "init", "--bare", str(remote))
    git(repo, "init", "-b", "main")
    git(repo, "config", "user.name", "Card test")
    git(repo, "config", "user.email", "cards@example.invalid")
    git(repo, "config", "commit.gpgsign", "false")
    destination = repo / upload.LIBRARY_FILE
    destination.parent.mkdir(parents=True)
    atomic_json(destination, {"version": 1, "entries": []})
    (repo / "notes.txt").write_text("base\n", encoding="utf-8")
    git(repo, "add", ".")
    git(repo, "commit", "-m", "Initial")
    git(repo, "remote", "add", "origin", str(remote))
    git(repo, "push", "-u", "origin", "main")
    monkeypatch.setattr(upload, "_project_root", lambda: repo)
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("giant",))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", fingerprint(15)[None, ...])
    values = {}
    monkeypatch.setattr(local, "sample_fingerprint", lambda sample: values[sample.key])
    checks = []
    monkeypatch.setattr(upload, "_lint", lambda _repo, _progress: checks.append(True))
    root = tmp_path / "samples"

    def add(name, channel=0, card_id="knight"):
        sample = add_group_sample(root, name, "sample-000")
        values[sample.key] = fingerprint(channel)
        workflow.confirm_samples(root, [sample], card_id, "private local note")
        samples, errors, _ = workflow.scan_review(root)
        assert not errors
        return next(item for item in samples if item.key == sample.key)

    return repo, remote, root, add, checks


def test_cancelled_sync_does_not_apply_remote_data(tmp_path, monkeypatch):
    from threading import Event
    from unittest.mock import Mock

    cancelled = Event()
    destination = tmp_path / upload.LIBRARY_FILE
    destination.parent.mkdir(parents=True)
    atomic_json(destination, {"version": 1, "entries": []})
    original = destination.read_bytes()
    monkeypatch.setattr(upload, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(upload.preferences, "preferences_path", lambda: tmp_path / "preferences.json")
    monkeypatch.setattr(upload, "_upstream_target", lambda _repo: ("origin", "refs/heads/main"))
    fetch = Mock(side_effect=lambda *a, **kw: cancelled.set())
    monkeypatch.setattr(upload, "_git", fetch)
    commit = Mock()
    monkeypatch.setattr(workflow, "_commit", commit)

    assert upload.update_from_remote(tmp_path / "samples", cancel_event=cancelled) == "卡牌同步已取消"
    assert fetch.call_args.args[1] == "fetch"
    commit.assert_not_called()
    assert destination.read_bytes() == original
    fetch.reset_mock()
    assert upload.update_from_remote(tmp_path / "samples", cancel_event=cancelled) == "卡牌同步已取消"
    fetch.assert_not_called()


def test_card_data_sync_keeps_code_and_local_hits_until_selected_reset(upload_store, tmp_path):
    repo, remote, root, add, _checks = upload_store
    sample = add("first")
    identifier = sample.review["formal_id"]
    upload.upload_references(root, [sample])
    hits.record_hits(root, {identifier: 10})
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    hits.record_hits(root, {identifier: 90})
    peer = tmp_path / "peer"
    git(tmp_path, "clone", "--branch", "main", str(remote), str(peer))
    git(peer, "config", "user.name", "Card test")
    git(peer, "config", "user.email", "cards@example.invalid")
    git(peer, "config", "commit.gpgsign", "false")
    (peer / "notes.txt").write_text("upstream\n", encoding="utf-8")
    shared = hits.read_shared(peer / upload.HITS_FILE)
    shared["nodes"]["f" * 32] = {identifier: 5}
    atomic_json(peer / upload.HITS_FILE, shared)
    git(peer, "commit", "-am", "Upstream update")
    git(peer, "push")
    notes = repo / "notes.txt"
    notes.write_text("local\n", encoding="utf-8")
    previous = git(repo, "rev-parse", "HEAD")

    upload.update_from_remote(root)
    assert hits.totals(root)[0][identifier] == 105
    assert git(repo, "rev-parse", "HEAD") == previous
    assert notes.read_text(encoding="utf-8") == "local\n"
    upload.update_from_remote(root, reset_cards=["knight"])
    assert hits.snapshot(root) == ({identifier: 15}, set())

    hits.record_hits(root, {identifier: 2})
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    assert hits.totals(root)[0][identifier] == 17
    assert git(repo, "rev-parse", "HEAD") == previous
    assert git(remote, "show", "main:notes.txt") == "upstream"


def test_upload_only_selected_fingerprints_and_keep_unstaged_edits(upload_store):
    repo, remote, root, add, checks = upload_store
    first, other = add("first"), add("other", 4, "archers")
    working = (repo / upload.LIBRARY_FILE).read_bytes()
    (repo / "notes.txt").write_text("local change\n", encoding="utf-8")
    progress = []
    result = upload.upload_references(root, [first], progress=progress.append)
    assert result.splitlines()[0] == f"新增{formal.CARD_NAMES_ZH['knight']}参考样本" and checks == [True]
    assert git(remote, "log", "-1", "--format=%s", "main").startswith(f"data(cards): {result.splitlines()[0]}")
    assert progress[-2:] == ["正在推送正式参考……", "推送成功，正在清理本机来源样本……"]
    document = json.loads(git(remote, "show", f"main:{upload.LIBRARY_FILE}"))
    assert [entry["id"] for entry in document["entries"]] == [first.review["formal_id"]]
    assert other.review["formal_id"] not in {entry["id"] for entry in document["entries"]}
    assert set(document["entries"][0]) == {"id", "card_id", "fingerprint"}
    assert git(remote, "show", "main:notes.txt") == "base"
    assert (repo / upload.LIBRARY_FILE).read_bytes() == working
    assert (repo / "notes.txt").read_text(encoding="utf-8") == "local change\n"
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert git(repo, "diff", "--name-only").splitlines() == [upload.LIBRARY_FILE, "notes.txt"]
    head = git(repo, "rev-parse", "HEAD")
    assert "无需重复提交" in upload.upload_references(root, [], cards={"knight"})
    assert git(repo, "rev-parse", "HEAD") == head and checks == [True]
    upload.upload_references(root, [other])
    assert len(json.loads(git(remote, "show", f"main:{upload.LIBRARY_FILE}"))["entries"]) == 2


def test_published_cache_tracks_upstream_revision_and_returns_independent_data(upload_store, monkeypatch):
    from unittest.mock import Mock

    repo, _remote, _root, _add, _checks = upload_store
    entries = Mock(wraps=upload._base_entries)
    document = Mock(wraps=upload._base_document)
    monkeypatch.setattr(upload, "_base_entries", entries)
    monkeypatch.setattr(upload, "_base_document", document)
    upload.published_reference_ids()["mutated"] = "local"
    upload.published_preferences()["mutated"] = {}
    assert "mutated" not in upload.published_reference_ids()
    assert "mutated" not in upload.published_preferences()
    assert entries.call_count == document.call_count == 1
    git(repo, "commit", "--allow-empty", "-m", "New upstream revision")
    git(repo, "push")
    upload.published_reference_ids()
    upload.published_preferences()
    assert entries.call_count == document.call_count == 2


def test_staged_changes_are_preserved_and_block_new_commit(upload_store):
    repo, _remote, root, add, _checks = upload_store
    sample = add("first")
    (repo / "notes.txt").write_text("already staged\n", encoding="utf-8")
    git(repo, "add", "notes.txt")
    before = git(repo, "rev-parse", "HEAD")
    staged = git(repo, "diff", "--cached")
    with pytest.raises(ValueError, match="暂存区已有"):
        upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == before
    assert git(repo, "diff", "--cached") == staged
    assert not (repo / ".git" / "index.lock").exists()


def test_upload_selected_clear_pushes_only_selected_statistics(upload_store):
    _repo, remote, root, add, _checks = upload_store
    first, other = add("first"), add("other", 4, "archers")
    selected, untouched = (sample.review["formal_id"] for sample in (first, other))
    upload.upload_references(root, [first, other])
    hits.record_hits(root, {selected: 4, untouched: 8})
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    previous = git(remote, "show", f"main:{upload.HITS_FILE}")
    hits.clear_local(root, [selected], allow_upload=True)
    assert git(remote, "show", f"main:{upload.HITS_FILE}") == previous

    result = upload.upload_references(root, [], metadata_only=True, update_hits=True, selected_resets=[selected])
    assert result == f"清空{formal.CARD_NAMES_ZH['knight']}线上命中次数（参考 {selected}）"
    shared = json.loads(git(remote, "show", f"main:{upload.HITS_FILE}"))
    assert set(shared["resets"]) == {selected}
    assert list(shared["nodes"].values()) == [{untouched: 8}]
    assert git(remote, "diff-tree", "--no-commit-id", "--name-only", "-r", "main") == upload.HITS_FILE
    assert git(remote, "log", "-1", "--format=%s", "main") == f"data(cards): {result}"


@pytest.mark.parametrize("legacy_title", [False, True])
def test_failed_push_retries_without_duplicate_commit(upload_store, monkeypatch, legacy_title):
    repo, remote, root, add, checks = upload_store
    sample = add("first")
    run = upload._git

    def fail_push(repo, *args, **kwargs):
        if args[0] == "push":
            raise ValueError("offline")
        return run(repo, *args, **kwargs)

    monkeypatch.setattr(upload, "_git", fail_push)
    with pytest.raises(ValueError, match="本地提交已保留"):
        upload.upload_references(root, [sample])
    assert all(sample.file(kind).exists() for kind in ("card", "hand", "frame"))
    assert not workflow.scan_review(root)[0][0].uploaded
    if legacy_title:
        git(repo, "commit", "--amend", "-m", "data(cards): upload approved references")
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(upload, "_git", run)
    assert f"新增{formal.CARD_NAMES_ZH['knight']}参考样本" in upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == head == git(remote, "rev-parse", "main")
    assert checks == [True]
    assert not workflow.scan_review(root)[0]
    assert all(not sample.file(kind).exists() for kind in ("card", "hand", "frame"))


def test_uploaded_samples_are_removed_per_reference(upload_store):
    _repo, _remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    pending = add_group_sample(root, "first", "sample-001")
    exported = export_candidate(first)
    (first.directory / "index.html").write_text("old report", encoding="utf-8")
    atomic_json(first.directory / "summary.json", {"saved": 2})
    formal_entries = formal._read_entries(formal.formal_path())
    identifier = first.review["formal_id"]
    hits.record_hits(root, {identifier: 7})

    upload.upload_references(root, [first])
    current = {sample.key: sample for sample in workflow.scan_review(root)[0]}
    assert set(current) == {second.key, pending.key}
    assert current[second.key].status == "included" and not current[second.key].uploaded
    assert current[pending.key].status == "pending"
    assert not exported.exists()
    assert {path.name for path in first.directory.iterdir()} == {
        "samples.jsonl",
        "annotations.json",
        *(pending.file(kind).name for kind in ("card", "hand", "frame")),
    }
    assert set(json.loads((first.directory / "annotations.json").read_text(encoding="utf-8"))) == {pending.name}
    assert all(second.file(kind).is_file() for kind in ("card", "hand", "frame"))
    assert formal._read_entries(formal.formal_path()) == formal_entries
    assert hits.totals(root)[0][identifier] == 7


def test_refresh_recovers_publication_without_a_local_marker(upload_store, monkeypatch):
    _repo, _remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    # A successful push may be interrupted before local publication is recorded.
    with monkeypatch.context() as patch:
        patch.setattr(upload, "_archive_published", lambda *_args: None)
        upload.upload_references(root, [first])
    assert not next(sample for sample in workflow._scan(root) if sample.key == first.key).uploaded
    current = workflow.scan_review(root)[0]
    assert [sample.key for sample in current] == [second.key]
    assert not first.directory.exists() and not current[0].uploaded


def test_upload_settings_and_hits_only_when_explicitly_selected(upload_store):
    repo, remote, root, add, _checks = upload_store
    sample = add("first")
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    set_preferred_reference(root, "knight", refs[0].identifier)
    hits.record_hits(root, {refs[0].identifier: 4})
    upload.upload_references(root, [sample])
    prefs = json.loads(git(remote, "show", f"main:{upload.PREFERENCES_FILE}"))
    assert set(prefs["cards"]) == {"knight"}
    assert prefs["cards"]["knight"]["preferred"] == refs[0].identifier
    assert not git(remote, "ls-tree", "main", "--", upload.HITS_FILE)
    # Catalog publication works after source images have been archived/removed.
    upload.upload_references(root, [], cards={"knight"}, update_hits=True)
    counts = json.loads(git(remote, "show", f"main:{upload.HITS_FILE}"))
    assert sum(node.get(refs[0].identifier, 0) for node in counts["nodes"].values()) == 4
    head = git(repo, "rev-parse", "HEAD")
    upload.upload_references(root, [], cards={"knight"}, update_hits=True)
    assert git(repo, "rev-parse", "HEAD") == head
    hits.clear_local(root)
    upload.upload_references(root, [], cards={"knight"})
    assert json.loads(git(remote, "show", f"main:{upload.HITS_FILE}")) == counts
    upload.upload_references(root, [], cards={"knight"}, update_hits=True)
    reset = json.loads(git(remote, "show", f"main:{upload.HITS_FILE}"))
    assert reset == counts
    assert hits.totals(root)[0][refs[0].identifier] == 0


def test_manual_reference_order_is_published_with_the_selected_card(upload_store):
    _repo, remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    original = tuple(ref.identifier for ref in refs)
    reorder_references(root, "knight", original[::-1], original)
    upload.upload_references(root, [first, second])
    document = json.loads(git(remote, "show", f"main:{upload.PREFERENCES_FILE}"))
    assert document["cards"]["knight"]["order"] == list(original[::-1])


@pytest.mark.parametrize("interrupted_file", ["sample-000-hand.png", "samples.jsonl"])
def test_history_cleanup_recovers_after_interruption(upload_store, monkeypatch, interrupted_file):
    _repo, _remote, root, add, _checks = upload_store
    sample, unpublished = add("first"), add("second", 4)
    entries = formal._read_entries(formal.formal_path())
    # Publication markers represent already successful historic uploads.
    workflow._commit(
        root,
        entries,
        entries,
        [
            (
                sample,
                {
                    "published_reference_id": sample.review["formal_id"],
                    "published_commit": "a" * 40,
                    "images_cleaned": True,
                },
            )
        ],
    )
    original_unlink = type(root).unlink

    def locked_file(path, *args, **kwargs):
        if path.name == interrupted_file and path.parent == sample.directory:
            raise PermissionError("sample file is open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "unlink", locked_file)
    with pytest.raises(PermissionError):
        workflow.scan_review(root)
    current = {item.key: item for item in workflow._scan(root)}
    assert sample.key in current
    assert all(unpublished.file(kind).exists() for kind in ("card", "hand", "frame"))
    monkeypatch.setattr(type(root), "unlink", original_unlink)
    current = {item.key: item for item in workflow.scan_review(root)[0]}
    assert set(current) == {unpublished.key}
    assert not sample.directory.exists()
    assert not (root / "sample-deletion.json").exists()
    assert formal._read_entries(formal.formal_path()) == entries


def test_unpublished_code_commit_is_never_pushed(upload_store):
    repo, remote, root, add, checks = upload_store
    sample = add("first")
    before = git(remote, "rev-parse", "main")
    (repo / "notes.txt").write_text("unpublished code\n", encoding="utf-8")
    git(repo, "add", "notes.txt")
    git(repo, "commit", "-m", "Unrelated code")
    with pytest.raises(ValueError, match="未推送的其他提交"):
        upload.upload_references(root, [sample])
    assert git(remote, "rev-parse", "main") == before and not checks


def test_remote_advance_stops_without_merging(upload_store, tmp_path):
    repo, remote, root, add, checks = upload_store
    sample = add("first")
    before = git(repo, "rev-parse", "HEAD")
    peer = tmp_path / "peer"
    git(repo, "clone", "-b", "main", str(remote), str(peer))
    git(peer, "config", "user.name", "Peer")
    git(peer, "config", "user.email", "peer@example.invalid")
    git(peer, "config", "commit.gpgsign", "false")
    (peer / "notes.txt").write_text("remote advance\n", encoding="utf-8")
    git(peer, "add", "notes.txt")
    git(peer, "commit", "-m", "Remote advance")
    git(peer, "push", "origin", "main")
    with pytest.raises(ValueError, match="远端已有新提交"):
        upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == before and not checks


def test_lint_failure_never_creates_or_pushes_commit(upload_store, monkeypatch):
    repo, remote, root, add, _checks = upload_store
    sample = add("first")
    before = git(repo, "rev-parse", "HEAD")

    def fail_lint(*_args):
        raise ValueError("ruff 未通过")

    monkeypatch.setattr(upload, "_lint", fail_lint)
    with pytest.raises(ValueError, match="ruff 未通过"):
        upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == git(remote, "rev-parse", "main") == before


def test_lint_runs_uv_without_shell_with_a_timeout(upload_store, monkeypatch):
    repo, _remote, _root, _add, _checks = upload_store
    calls = []
    monkeypatch.setattr(shutil, "which", lambda _name: "uv")

    def run(args, **kwargs):
        calls.append((args, kwargs))
        return subprocess.CompletedProcess(args, 0)

    monkeypatch.setattr(subprocess, "run", run)
    run_lint_checks(repo, lambda _message: None)
    assert len(calls) == 3
    assert all(args[:3] == ["uv", "run", "--no-sync"] for args, _ in calls)
    assert all(kwargs["shell"] is False and kwargs["timeout"] == 120 for _, kwargs in calls)


def test_git_commit_hook_is_respected_and_index_is_preserved(upload_store):
    repo, remote, root, add, _checks = upload_store
    sample = add("first")
    before = git(repo, "rev-parse", "HEAD")
    hook = repo / ".git" / "hooks" / "pre-commit"
    hook.write_text("#!/bin/sh\nexit 1\n", encoding="utf-8", newline="\n")
    hook.chmod(0o755)
    with pytest.raises(ValueError, match="commit 失败"):
        upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == git(remote, "rev-parse", "main") == before
    assert git(repo, "diff", "--cached", "--name-only") == ""
    assert not (repo / ".git" / "index.lock").exists()


def test_scoped_metadata_upload_keeps_unselected_order_counts_and_pending_clears(upload_store, monkeypatch):
    _repo, remote, root, add, _checks = upload_store
    samples = [add("first"), add("second", 4), add("other", 8, "archers"), add("other-second", 12, "archers")]
    upload.upload_references(root, samples)
    catalog = build_catalog(workflow.scan_review(root)[0], root=root)
    knight = tuple(ref.identifier for ref in catalog["knight"])
    archers = tuple(ref.identifier for ref in catalog["archers"])
    hits.record_hits(root, {knight[0]: 5, archers[0]: 7})
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    original_order = json.loads(git(remote, "show", f"main:{upload.PREFERENCES_FILE}"))
    for card, order in (("knight", knight), ("archers", archers)):
        reorder_references(root, card, order[::-1], order)
    hits.clear_local(root, [knight[0], archers[0]], allow_upload=True)
    hits.record_hits(root, {knight[1]: 3, archers[1]: 9})

    run = upload._git

    def fail_push(repo, *args, **options):
        if args[0] == "push":
            raise ValueError("offline")
        return run(repo, *args, **options)

    with monkeypatch.context() as patch:
        patch.setattr(upload, "_git", fail_push)
        with pytest.raises(ValueError, match="推送未完成"):
            upload.upload_references(root, [], metadata_only=True, update_hits=True, metadata_cards=["knight"])
        with pytest.raises(ValueError, match="未勾选"):
            upload.upload_references(root, [], metadata_only=True, update_hits=True, metadata_cards=["archers"])
    upload.upload_references(root, [], metadata_only=True, update_hits=True, metadata_cards=["knight"])
    order = json.loads(git(remote, "show", f"main:{upload.PREFERENCES_FILE}"))
    assert order["cards"]["knight"]["order"] == list(knight[::-1])
    assert order["cards"]["archers"] == original_order["cards"]["archers"]
    shared = json.loads(git(remote, "show", f"main:{upload.HITS_FILE}"))
    totals = hits._displayed(shared, {})
    assert totals.get(knight[0], 0) == 0 and totals[knight[1]] == 3
    assert totals[archers[0]] == 7 and totals.get(archers[1], 0) == 0
    assert hits.snapshot(root)[1] == set(archers)
    with pytest.raises(ValueError, match="请先勾选"):
        upload.upload_references(root, [], metadata_only=True, update_hits=True, metadata_cards=[])


def test_catalog_metadata_upload_marks_changes_and_keeps_new_samples_local(upload_store):
    repo, remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    upload.upload_references(root, [first, second])
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    original = tuple(ref.identifier for ref in refs)
    # Establish first choice, then compare position changes against upstream.
    reorder_references(root, "knight", original, original)
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    assert not any(ref.order_changed or ref.hits_changed for ref in refs)
    reorder_references(root, "knight", original[::-1], original)
    hits.record_hits(root, {original[0]: 5})
    changed = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    assert sum(ref.order_changed for ref in changed) == 2
    assert sum(ref.hits_changed for ref in changed) == 1
    assert changed[0].preferred and changed[0].markers == " new"
    new = add("not-published", 8)
    library = git(remote, "show", f"main:{upload.LIBRARY_FILE}")
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    assert git(remote, "show", f"main:{upload.LIBRARY_FILE}") == library
    assert new.file("card").exists()
    assert not next(sample for sample in workflow.scan_review(root)[0] if sample.key == new.key).uploaded
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    assert not any(ref.order_changed or ref.hits_changed for ref in refs)
    before = git(repo, "rev-parse", "HEAD")
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    assert git(repo, "rev-parse", "HEAD") == before


def test_failed_metadata_push_preserves_change_markers(upload_store, monkeypatch):
    _repo, _remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    upload.upload_references(root, [first, second])
    refs = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    order = tuple(ref.identifier for ref in refs)
    reorder_references(root, "knight", order[::-1], order)
    hits.record_hits(root, {order[0]: 3})
    run = upload._git

    def fail_push(repo, *args, **kwargs):
        if args[0] == "push":
            raise ValueError("offline")
        return run(repo, *args, **kwargs)

    monkeypatch.setattr(upload, "_git", fail_push)
    with pytest.raises(ValueError, match="本地提交已保留"):
        upload.upload_references(root, [], metadata_only=True, update_hits=True)
    changed = build_catalog(workflow.scan_review(root)[0], root=root)["knight"]
    assert any(ref.order_changed for ref in changed) and any(ref.hits_changed for ref in changed)
    monkeypatch.setattr(upload, "_git", run)
    upload.upload_references(root, [], metadata_only=True, update_hits=True)
    assert not any(ref.order_changed or ref.hits_changed for ref in build_catalog([], root=root)["knight"])
