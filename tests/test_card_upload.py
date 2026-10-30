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
from crX.utils.card_review import atomic_json
from tests.test_card_library import fingerprint
from tests.test_card_review import add_group_sample

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="Git is unavailable")
run_lint_checks = upload._lint
source_fingerprint = local.sample_fingerprint


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


def test_upload_only_selected_fingerprints_and_keep_unstaged_edits(upload_store):
    repo, remote, root, add, checks = upload_store
    first, other = add("first"), add("other", 4, "archers")
    working = (repo / upload.LIBRARY_FILE).read_bytes()
    (repo / "notes.txt").write_text("local change\n", encoding="utf-8")
    result = upload.upload_references(root, [first])
    assert "已上传 1 份" in result and checks == [True]
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
    assert "无需重复提交" in upload.upload_references(root, [first])
    assert git(repo, "rev-parse", "HEAD") == head and checks == [True]
    upload.upload_references(root, [other])
    assert len(json.loads(git(remote, "show", f"main:{upload.LIBRARY_FILE}"))["entries"]) == 2


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


def test_failed_push_retries_without_duplicate_commit(upload_store, monkeypatch):
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
    head = git(repo, "rev-parse", "HEAD")
    monkeypatch.setattr(upload, "_git", run)
    assert "已上传" in upload.upload_references(root, [sample])
    assert git(repo, "rev-parse", "HEAD") == head == git(remote, "rev-parse", "main")
    assert checks == [True]
    assert workflow.scan_review(root)[0][0].uploaded
    assert all(not sample.file(kind).exists() for kind in ("card", "hand", "frame"))


def test_uploaded_samples_archive_per_reference_and_keep_sources(upload_store):
    _repo, _remote, root, add, _checks = upload_store
    first, second = add("first"), add("second", 4)
    upload.upload_references(root, [first])
    current = {sample.key: sample for sample in workflow.scan_review(root)[0]}
    assert current[first.key].uploaded and not current[second.key].uploaded
    assert all(sample.status == "included" for sample in current.values())
    assert current[first.key].review["note"] == "private local note"
    assert source_fingerprint(current[first.key]).tolist() == next(
        entry["fingerprint"]
        for entry in formal._read_entries(formal.formal_path())
        if entry["id"] == first.review["formal_id"]
    )
    assert all(not first.file(kind).exists() for kind in ("card", "hand", "frame"))
    assert all(second.file(kind).is_file() for kind in ("card", "hand", "frame"))
    later = add("later", 8)
    assert not later.uploaded
    # Old records recover their archive status from the fetched remote, not HEAD alone.
    entries = formal._read_entries(formal.formal_path())
    workflow._commit(
        root, entries, entries, [(current[first.key], {"published_reference_id": "", "published_commit": ""})]
    )
    recovered = {sample.key: sample for sample in workflow.scan_review(root)[0]}
    assert recovered[first.key].uploaded and not recovered[later.key].uploaded


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


def test_history_cleanup_recovers_after_interruption_and_requires_valid_published_fingerprint(
    upload_store, monkeypatch
):
    _repo, _remote, root, add, _checks = upload_store
    sample, unpublished = add("first"), add("second", 4)
    entries = formal._read_entries(formal.formal_path())
    # Publication markers represent already successful historic uploads.
    workflow._commit(
        root,
        entries,
        entries,
        [(sample, {"published_reference_id": sample.review["formal_id"], "published_commit": "a" * 40})],
    )
    original_unlink = type(root).unlink

    def locked_image(path, *args, **kwargs):
        if path.name == "sample-000-hand.png" and path.parent == sample.directory:
            raise PermissionError("image is open")
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(type(root), "unlink", locked_image)
    with pytest.raises(PermissionError):
        workflow.scan_review(root)
    current = {item.key: item for item in workflow._scan(root)}
    assert not current[sample.key].review.get("images_cleaned")
    assert sample.file("frame").exists()
    assert all(unpublished.file(kind).exists() for kind in ("card", "hand", "frame"))
    monkeypatch.setattr(type(root), "unlink", original_unlink)
    current = {item.key: item for item in workflow.scan_review(root)[0]}
    assert current[sample.key].review["images_cleaned"]
    assert all(not sample.file(kind).exists() for kind in ("card", "hand", "frame"))
    assert formal._read_entries(formal.formal_path()) == entries
