"""Publish only selected formal fingerprints, preserving unrelated local edits."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

from crX.utils import card_workflow as workflow
from crX.utils import formal_library as formal

LIBRARY_FILE = "crX/bot/card_reference_library.json"
COMMIT_TITLE = "data(cards): upload approved references"
MAX_INDEX_BYTES = 32 * 1024 * 1024


def _project_root():
    return Path(__file__).resolve().parents[2]


def _environment():
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env.update(GIT_TERMINAL_PROMPT="0", GCM_INTERACTIVE="Never")
    return env


def _git(repo, *args, env=None, timeout=20, check=True):
    executable = shutil.which("git")
    if executable is None:
        raise ValueError("未找到 Git，请先安装 Git 并配置仓库身份和登录凭据")
    try:
        result = subprocess.run(
            [executable, *args],
            cwd=repo,
            env=env if env is not None else _environment(),
            shell=False,
            capture_output=True,
            timeout=timeout,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise ValueError(f"Git {args[0]} 未完成，请检查网络或本地 Git 状态后重试") from error
    if check and result.returncode:
        # Network errors can echo credential-bearing remote URLs. Do not expose them.
        raise ValueError(f"Git {args[0]} 失败，请检查登录权限、仓库状态或提交钩子；本地数据保留")
    return result


def _text(repo, *args, **kwargs):
    return _git(repo, *args, **kwargs).stdout.decode("utf-8").strip()


def _lint(repo, progress):
    executable = shutil.which("uv")
    if executable is None:
        raise ValueError("未找到 uv，无法执行提交前检查")
    for command in (("ruff", "check"), ("ruff", "format", "--check"), ("ty", "check")):
        progress(f"上传前检查：{' '.join(command)}……")
        try:
            result = subprocess.run(
                [executable, "run", "--no-sync", *command],
                cwd=repo,
                env=_environment(),
                shell=False,
                capture_output=True,
                timeout=120,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                check=False,
            )
        except (OSError, subprocess.TimeoutExpired) as error:
            raise ValueError("提交前检查未完成，尚未创建提交，请检查项目环境") from error
        if result.returncode:
            raise ValueError(f"{' '.join(command)} 未通过，尚未创建提交，请先修复项目检查")


def _tracking(repo, head):
    branch = _text(repo, "branch", "--show-current")
    if not branch:
        raise ValueError("当前处于分离 HEAD，请先切换到有远端跟踪的分支")
    remote = _text(repo, "config", "--get", f"branch.{branch}.remote", check=False)
    target = _text(repo, "config", "--get", f"branch.{branch}.merge", check=False)
    if not remote or remote.startswith("-") or remote not in _text(repo, "remote").splitlines():
        raise ValueError("当前分支尚未配置远端跟踪，请先配置后上传")
    if not target.startswith("refs/heads/"):
        raise ValueError("当前分支的远端目标无效")
    _git(repo, "check-ref-format", target)
    _git(repo, "fetch", "--quiet", "--no-tags", remote, target, timeout=60)
    upstream = _text(repo, "rev-parse", "--verify", "FETCH_HEAD")
    if _git(repo, "merge-base", "--is-ancestor", upstream, head, check=False).returncode:
        raise ValueError("远端已有新提交，请先更新代码并处理冲突后重试；未自动合并")
    ahead = _text(repo, "rev-list", f"{upstream}..{head}").splitlines()
    for commit in ahead:
        title = _text(repo, "show", "-s", "--format=%s", commit)
        paths = _text(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        if title != COMMIT_TITLE or paths != [LIBRARY_FILE]:
            raise ValueError("当前分支有未推送的其他提交，请先处理这些提交后再上传参考")
    return remote, target, bool(ahead)


def _base_entries(repo, directory, revision="HEAD"):
    if not _text(repo, "ls-tree", revision, "--", LIBRARY_FILE):
        return []
    if int(_text(repo, "cat-file", "-s", f"{revision}:{LIBRARY_FILE}")) > formal.MAX_BYTES:
        raise ValueError("已提交的正式参考库过大")
    payload = _git(repo, "show", f"{revision}:{LIBRARY_FILE}").stdout
    if len(payload) > formal.MAX_BYTES:
        raise ValueError("已提交的正式参考库过大")
    path = directory / "base.json"
    path.write_bytes(payload)
    return formal._read_entries(path)


def published_reference_ids():
    """Use the locally fetched tracking revision; never fetch during UI refresh."""
    repo = _project_root()
    if not formal.source_checkout() or formal.formal_path().resolve() != repo / LIBRARY_FILE:
        return {}
    try:
        tracking = _text(repo, "rev-parse", "--symbolic-full-name", "@{upstream}", check=False)
        if not tracking.startswith("refs/remotes/"):
            return {}
        revision = _text(repo, "rev-parse", "--verify", tracking)
        with tempfile.TemporaryDirectory(prefix="crx-card-published-") as name:
            return {entry["id"]: revision for entry in _base_entries(repo, Path(name), revision)}
    except (OSError, ValueError):
        return {}


def _archive_published(root, identifiers, revision):
    entries = formal._read_entries(formal.formal_path())
    updates = [
        (sample, {"published_reference_id": sample.review["formal_id"], "published_commit": revision})
        for sample in workflow._scan(root)
        if sample.status == "included" and sample.review.get("formal_id") in identifiers and not sample.uploaded
    ]
    if updates:
        workflow._commit(root, entries, entries, updates)
    workflow._cleanup_uploaded_images(root, workflow._scan(root), entries)


def _commit_entries(repo, head, payload, directory):
    git_dir = Path(_text(repo, "rev-parse", "--absolute-git-dir")).resolve()
    index = git_dir / "index"
    lock = git_dir / "index.lock"
    if index.is_symlink() or not index.is_file() or index.stat().st_size > MAX_INDEX_BYTES:
        raise ValueError("Git 暂存区无效或过大，请人工检查")
    try:
        descriptor = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError as error:
        raise ValueError("Git 正在执行其他操作，请稍后重试") from error
    os.close(descriptor)
    temporary_index = directory / "index"
    env = {**_environment(), "GIT_INDEX_FILE": str(temporary_index)}
    owns_lock = True
    try:
        if _text(repo, "rev-parse", "HEAD") != head:
            raise ValueError("当前分支已变化，请刷新后重试")
        if _git(repo, "diff", "--cached", "--quiet", check=False).returncode:
            raise ValueError("暂存区已有其他改动，请先处理后上传；未改动原暂存区")
        shutil.copyfile(index, temporary_index)
        document = directory / "upload.json"
        document.write_bytes(payload)
        blob = _text(repo, "hash-object", "-w", str(document))
        _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{LIBRARY_FILE}", env=env)
        tree = _text(repo, "write-tree", env=env)
        try:
            _git(repo, "commit", "-m", COMMIT_TITLE, env=env, timeout=60)
        finally:
            # Also reconcile the index if a post-commit hook timed out after success.
            committed = _text(repo, "rev-parse", "HEAD")
            if committed != head and _text(repo, "rev-parse", f"{committed}^{{tree}}") == tree:
                shutil.copyfile(temporary_index, lock)
                os.replace(lock, index)
                owns_lock = False
        paths = _text(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", committed).splitlines()
        if paths != [LIBRARY_FILE]:
            raise ValueError("提交钩子修改了其他文件，本次未推送，请人工检查提交")
        if _text(repo, "rev-parse", f"{committed}^{{tree}}") != tree:
            raise ValueError("提交钩子改变了所选参考，本次未推送，请人工检查提交")
        return committed
    finally:
        if owns_lock:
            lock.unlink(missing_ok=True)


def upload_references(root, selected, *, progress=lambda _message: None):
    """Append selected approved references to HEAD, commit and push the tracked branch."""
    if not formal.source_checkout():
        raise ValueError("上传需要可写的源码 Git 项目，当前安装版不支持")
    repo = _project_root()
    if formal.formal_path().resolve() != repo / LIBRARY_FILE:
        raise ValueError("正式库不属于当前源码项目，不能上传")
    with workflow._locked(root):
        samples = workflow._fresh(root, selected)
        entries = {entry["id"]: entry for entry in formal._read_entries(formal.formal_path())}
        chosen = {}
        for sample in samples:
            entry = entries.get(sample.review.get("formal_id"))
            if sample.status != "included" or entry is None or sample.card_id != entry["card_id"]:
                raise ValueError("请选择已入库且仍关联有效正式参考的条目")
            chosen[entry["id"]] = entry
        head = _text(repo, "rev-parse", "--verify", "HEAD")
        progress("正在检查远端分支……")
        remote, target, pending_push = _tracking(repo, head)
        with tempfile.TemporaryDirectory(prefix="crx-card-upload-") as name:
            directory = Path(name)
            before = _base_entries(repo, directory)
            merged = {entry["id"]: entry for entry in before}
            merged.update(chosen)
            if list(merged.values()) != before:
                payload = (
                    json.dumps({"version": 1, "entries": list(merged.values())}, ensure_ascii=False, indent=2) + "\n"
                ).encode("utf-8")
                path = directory / "validated.json"
                path.write_bytes(payload)
                formal._read_entries(path)
                _lint(repo, progress)
                progress("正在提交选中的正式参考……")
                head = _commit_entries(repo, head, payload, directory)
            elif not pending_push:
                _archive_published(root, chosen, head)
                return "所选正式参考已上传，无需重复提交"
        progress("正在推送正式参考……")
        try:
            _git(repo, "push", remote, f"{head}:{target}", timeout=90)
        except ValueError as error:
            raise ValueError("推送未完成，本地提交已保留；检查网络、登录权限或远端更新后可再次点击上传") from error
        try:
            _archive_published(root, chosen, head)
        except (OSError, ValueError) as error:
            raise ValueError("远端已上传，本机归档未完成；刷新或再次上传可恢复，参考和原图保留") from error
        return f"已上传 {len(chosen)} 份正式参考；其他机器更新代码后生效，来源图片已清理，备注保留"
