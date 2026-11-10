"""Publish only selected formal fingerprints, preserving unrelated local edits."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections import Counter
from copy import deepcopy
from functools import lru_cache
from pathlib import Path

from crX.utils import card_workflow as workflow
from crX.utils import formal_library as formal
from crX.utils import reference_hits as hits
from crX.utils import reference_preferences as preferences
from crX.utils.card_review import atomic_json

LIBRARY_FILE = "crX/bot/card_reference_library.json"
PREFERENCES_FILE = "crX/bot/card_reference_preferences.json"
HITS_FILE = "crX/bot/card_reference_hits.json"
UPLOAD_FILES = {LIBRARY_FILE, PREFERENCES_FILE, HITS_FILE}
COMMIT_TITLE = "data(cards): 上传已审核的卡牌参考"
LEGACY_COMMIT_TITLE = "data(cards): upload approved references"
DETAIL_PREFIX = "data(cards): "
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


def _upstream_target(repo):
    branch = _text(repo, "branch", "--show-current")
    if not branch:
        raise ValueError("当前处于分离 HEAD，请先切换到有远端跟踪的分支")
    remote = _text(repo, "config", "--get", f"branch.{branch}.remote", check=False)
    target = _text(repo, "config", "--get", f"branch.{branch}.merge", check=False)
    if not remote or remote.startswith("-") or remote not in _text(repo, "remote").splitlines():
        raise ValueError("当前分支尚未配置远端跟踪，请先配置上游分支")
    if not target.startswith("refs/heads/"):
        raise ValueError("当前分支的远端目标无效")
    _git(repo, "check-ref-format", target)
    return remote, target


def _merge_card_data(base, local, remote, reset, card_for):
    merged = {}
    conflicts = set()
    for key in dict.fromkeys((*local, *remote)):
        original, current, incoming = base.get(key), local.get(key), remote.get(key)
        card = card_for(key, current or incoming)
        if card in reset or current == original:
            value = incoming
        elif incoming in (original, current):
            value = current
        else:
            conflicts.add(card)
            continue
        if value is not None:
            merged[key] = value
    if conflicts:
        names = "、".join(formal.CARD_NAMES_ZH[card] for card in sorted(conflicts))
        raise ValueError(f"本机与远端的卡牌数据有冲突：{names}。可在样本库选择相应卡牌重置为远端")
    return merged


def _baseline_ref(remote, target):
    scope = hashlib.sha256(f"{remote}\0{target}".encode()).hexdigest()
    return f"refs/crx/card-data/{scope}"


def update_from_remote(root, *, reset_cards=()):
    """Fetch and apply only card data, without changing the checked-out branch."""
    repo = _project_root()
    if not formal.source_checkout() or formal.formal_path().resolve() != repo / LIBRARY_FILE:
        if reset_cards:
            raise ValueError("重置为远端需要可写的源码 Git 项目")
        return "使用内置卡牌数据"
    reset = set(reset_cards)
    if not reset.issubset(formal.CARD_NAMES_ZH):
        raise ValueError("请选择有效卡牌")
    order_path = preferences.preferences_path()
    with (
        workflow._locked(root),
        tempfile.TemporaryDirectory(prefix="crx-card-sync-") as name,
        preferences._LOCK,
        formal.writer_lock(order_path.with_suffix(".json.lock")),
    ):
        remote, target = _upstream_target(repo)
        _git(repo, "fetch", "--quiet", "--no-tags", "--no-recurse-submodules", remote, target, timeout=20)
        revision = _text(repo, "rev-parse", "--verify", "FETCH_HEAD")
        baseline_ref = _baseline_ref(remote, target)
        common = _text(repo, "merge-base", "HEAD", revision)
        previous = _text(repo, "rev-parse", "--verify", baseline_ref, check=False)
        if previous and _git(repo, "merge-base", "--is-ancestor", previous, common, check=False).returncode:
            common = previous
        directory = Path(name)
        local_entries = formal._read_entries(formal.formal_path())
        remote_entries = _base_entries(repo, directory, revision)
        base_entries = _base_entries(repo, directory, common)
        entries = _merge_card_data(
            {entry["id"]: entry for entry in base_entries},
            {entry["id"]: entry for entry in local_entries},
            {entry["id"]: entry for entry in remote_entries},
            reset,
            lambda _key, entry: entry["card_id"],
        )
        local_order = preferences.read_document(order_path)
        remote_order = _base_document(repo, directory, PREFERENCES_FILE, preferences.read_document, revision)
        base_order = _base_document(repo, directory, PREFERENCES_FILE, preferences.read_document, common)
        order = _merge_card_data(
            base_order["cards"], local_order["cards"], remote_order["cards"], reset, lambda card, _entry: card
        )
        shared = _base_document(repo, directory, HITS_FILE, hits.read_shared, revision)
        reset_ids = {entry["id"] for entry in (*local_entries, *remote_entries) if entry["card_id"] in reset}
        if reset:
            from crX.utils.card_catalog import _catalog_references

            catalog = _catalog_references()
            for card in reset:
                reset_ids.update(ref.identifier for ref in catalog.get(card, ()))
                reset_ids.update(local_order["cards"].get(card, {}).get("numbers", ()))
                reset_ids.update(remote_order["cards"].get(card, {}).get("numbers", ()))
        result = list(entries.values())
        order_document = {"version": 1, "cards": order}
        for filename, document, reader in (
            ("validated-library.json", {"version": 1, "entries": result}, formal._read_entries),
            ("validated-order.json", order_document, preferences.read_document),
        ):
            path = directory / filename
            path.write_bytes(_payload(document))
            reader(path)
        if result != local_entries:
            workflow._commit(root, local_entries, result, [])
        if order_document != local_order:
            atomic_json(order_path, order_document)
        hits.sync_shared(root, shared, reset_ids)
        _git(repo, "update-ref", baseline_ref, revision)
    if reset:
        return f"已将 {len(reset)} 种卡牌重置为远端"
    return "卡牌参考、顺序和共享统计已同步"


def _tracking(repo, head, *, update_hits=False):
    remote, target = _upstream_target(repo)
    _git(repo, "fetch", "--quiet", "--no-tags", remote, target, timeout=60)
    upstream = _text(repo, "rev-parse", "--verify", "FETCH_HEAD")
    if _git(repo, "merge-base", "--is-ancestor", upstream, head, check=False).returncode:
        synced = _text(repo, "rev-parse", "--verify", _baseline_ref(remote, target), check=False)
        if synced == upstream and not _git(repo, "merge-base", "--is-ancestor", head, upstream, check=False).returncode:
            return remote, target, False, upstream
        raise ValueError("远端已有新提交，请重新启动任务同步卡牌数据后重试。若 Git 分支已分叉，请先处理分支冲突")
    ahead = _text(repo, "rev-list", f"{upstream}..{head}").splitlines()
    for commit in ahead:
        title = _text(repo, "show", "-s", "--format=%s", commit)
        paths = _text(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", commit).splitlines()
        detailed = title.startswith(tuple(DETAIL_PREFIX + verb for verb in ("新增", "更新", "清空")))
        if (
            title not in (COMMIT_TITLE, LEGACY_COMMIT_TITLE)
            and not detailed
            or not paths
            or not set(paths).issubset(UPLOAD_FILES)
        ):
            raise ValueError("当前分支有未推送的其他提交，请先处理这些提交后再上传参考")
        if HITS_FILE in paths and not update_hits:
            raise ValueError("上次命中数提交尚未推送，请回到样本库点击「上传修改」重试")
    return remote, target, bool(ahead), head


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


def _base_document(repo, directory, filename, reader, revision="HEAD"):
    path = directory / Path(filename).name
    if _text(repo, "ls-tree", revision, "--", filename):
        if int(_text(repo, "cat-file", "-s", f"{revision}:{filename}")) > hits.MAX_BYTES:
            raise ValueError("已提交的参考配置过大")
        path.write_bytes(_git(repo, "show", f"{revision}:{filename}").stdout)
    else:
        path.unlink(missing_ok=True)
    return reader(path)


def _payload(document):
    return (json.dumps(document, ensure_ascii=False, indent=2) + "\n").encode("utf-8")


@lru_cache(maxsize=8)
def _published_ids_at(repo, revision):
    # Git objects are immutable; the tracking revision is still checked on every
    # refresh so a fetch, branch switch or successful upload is visible at once.
    with tempfile.TemporaryDirectory(prefix="crx-card-published-") as name:
        return {entry["id"]: revision for entry in _base_entries(repo, Path(name), revision)}


@lru_cache(maxsize=8)
def _published_preferences_at(repo, revision):
    with tempfile.TemporaryDirectory(prefix="crx-card-baseline-") as name:
        return _base_document(repo, Path(name), PREFERENCES_FILE, preferences.read_document, revision)["cards"]


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
        return _published_ids_at(repo, revision).copy()
    except (OSError, ValueError):
        return {}


def published_preferences():
    """Read the last fetched upstream baseline without network I/O."""
    if not formal.source_checkout():
        return preferences.read_document(preferences.preferences_path())["cards"]
    repo = _project_root()
    if formal.formal_path().resolve() != repo / LIBRARY_FILE:
        return None
    try:
        tracking = _text(repo, "rev-parse", "--symbolic-full-name", "@{upstream}", check=False)
        if not tracking.startswith("refs/remotes/"):
            return None
        revision = _text(repo, "rev-parse", "--verify", tracking)
        return deepcopy(_published_preferences_at(repo, revision))
    except (OSError, ValueError):
        return None


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


def _describe_upload(repo, directory, revision, payloads):
    from crX.utils.card_catalog import _catalog_references

    details = []
    if LIBRARY_FILE in payloads:
        known = {entry["id"] for entry in _base_entries(repo, directory, revision)}
        added = Counter(
            entry["card_id"] for entry in json.loads(payloads[LIBRARY_FILE])["entries"] if entry["id"] not in known
        )
        for card, count in sorted(added.items()):
            suffix = f"（{count} 份）" if count > 1 else ""
            details.append(f"新增{formal.CARD_NAMES_ZH[card]}参考样本{suffix}")
    if PREFERENCES_FILE in payloads:
        old = _base_document(repo, directory, PREFERENCES_FILE, preferences.read_document, revision)["cards"]
        new = json.loads(payloads[PREFERENCES_FILE])["cards"]
        for card in sorted(old.keys() | new.keys()):
            if old.get(card) != new.get(card):
                details.append(f"更新{formal.CARD_NAMES_ZH[card]}参考顺序及编号配置")
    if HITS_FILE in payloads:
        old = _base_document(repo, directory, HITS_FILE, hits.read_shared, revision)
        new = json.loads(payloads[HITS_FILE])
        owners = {ref.identifier: card for card, refs in _catalog_references().items() for ref in refs}
        before, after = hits._displayed(old, {}), hits._displayed(new, {})
        old_resets, new_resets = old.get("resets", {}), new.get("resets", {})
        for key in sorted(before.keys() | after.keys() | new_resets.keys()):
            name = formal.CARD_NAMES_ZH.get(owners.get(key, ""), "未知卡牌")
            if key in new_resets and new_resets[key] != old_resets.get(key):
                details.append(f"清空{name}线上命中次数（参考 {key}）")
            elif before.get(key, 0) != after.get(key, 0):
                details.append(f"更新{name}命中次数（参考 {key}，{before.get(key, 0)} → {after.get(key, 0)}）")
    return details


def _commit_entries(repo, head, payloads, directory, details):
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
        for number, (filename, payload) in enumerate(payloads.items()):
            document = directory / f"upload-{number}.json"
            document.write_bytes(payload)
            blob = _text(repo, "hash-object", "-w", str(document))
            _git(repo, "update-index", "--add", "--cacheinfo", f"100644,{blob},{filename}", env=env)
        tree = _text(repo, "write-tree", env=env)
        try:
            title = DETAIL_PREFIX + details[0] if details else COMMIT_TITLE
            if len(details) > 1:
                title += f"等 {len(details)} 项变更"
            message = ["-m", title]
            if len(details) > 1:
                message.extend(("-m", "\n".join(details)))
            _git(repo, "commit", *message, env=env, timeout=60)
        finally:
            # Also reconcile the index if a post-commit hook timed out after success.
            committed = _text(repo, "rev-parse", "HEAD")
            if committed != head and _text(repo, "rev-parse", f"{committed}^{{tree}}") == tree:
                shutil.copyfile(temporary_index, lock)
                os.replace(lock, index)
                owns_lock = False
        paths = _text(repo, "diff-tree", "--no-commit-id", "--name-only", "-r", committed).splitlines()
        if set(paths) != set(payloads):
            raise ValueError("提交钩子修改了其他文件，本次未推送，请人工检查提交")
        if _text(repo, "rev-parse", f"{committed}^{{tree}}") != tree:
            raise ValueError("提交钩子改变了所选参考，本次未推送，请人工检查提交")
        return committed
    finally:
        if owns_lock:
            lock.unlink(missing_ok=True)


def _commit_on_upstream(repo, base, payloads, directory, details):
    checkout = directory / "checkout"
    _git(repo, "worktree", "add", "--detach", "--quiet", str(checkout), base, timeout=30)
    try:
        return _commit_entries(checkout, base, payloads, directory, details)
    finally:
        _git(repo, "worktree", "remove", "--force", str(checkout), timeout=30)


def upload_references(
    root,
    selected,
    *,
    cards=None,
    update_hits=False,
    metadata_only=False,
    selected_resets=(),
    progress=lambda _message: None,
):
    """Append selected approved references to HEAD, commit and push the tracked branch."""
    progress("正在准备上传数据……")
    if not formal.source_checkout():
        raise ValueError("上传需要可写的源码 Git 项目，当前安装版不支持")
    repo = _project_root()
    if formal.formal_path().resolve() != repo / LIBRARY_FILE:
        raise ValueError("正式库不属于当前源码项目，不能上传")
    if metadata_only and (selected or cards or not update_hits):
        raise ValueError("顺序与命中次数上传不能包含样本入库操作")
    if selected_resets and not metadata_only:
        raise ValueError("请从样本库上传所选清空记录")
    if cards is not None and (
        not cards or len(cards) > formal.MAX_BATCH or not set(cards).issubset(formal.CARD_NAMES_ZH)
    ):
        raise ValueError("请选择有效卡牌，一次最多上传64种")
    with workflow._locked(root):
        if metadata_only:
            from crX.utils.card_catalog import build_catalog

            # Freeze labels/order for newly included references before taking the
            # metadata snapshot, just as a catalog refresh does.
            build_catalog(workflow._scan(root), root=root)
        samples = workflow._fresh(root, selected) if selected else []
        if (
            not metadata_only
            and not samples
            and (not cards or len(cards) > formal.MAX_BATCH or not set(cards).issubset(formal.CARD_NAMES_ZH))
        ):
            raise ValueError("请选择需要上传的卡牌")
        entries = {entry["id"]: entry for entry in formal._read_entries(formal.formal_path())}
        chosen = {}
        chosen_cards = set(cards or ())
        for sample in samples:
            entry = entries.get(sample.review.get("formal_id"))
            if sample.status != "included" or entry is None or sample.card_id != entry["card_id"]:
                raise ValueError("请选择已入库且仍关联有效正式参考的条目")
            chosen[entry["id"]] = entry
            chosen_cards.add(entry["card_id"])
        if cards:
            chosen.update({key: entry for key, entry in entries.items() if entry["card_id"] in cards})
        head = _text(repo, "rev-parse", "--verify", "HEAD")
        progress("正在检查远端分支……")
        remote, target, pending_push, base = _tracking(repo, head, update_hits=update_hits)
        detached = head != base
        upstream = _text(repo, "rev-parse", "--verify", "FETCH_HEAD")
        uploaded_stats = None
        with tempfile.TemporaryDirectory(prefix="crx-card-upload-") as name:
            directory = Path(name)
            before = _base_entries(repo, directory, base)
            merged = {entry["id"]: entry for entry in before}
            merged.update(chosen)
            payloads = {}
            if list(merged.values()) != before:
                payload = _payload({"version": 1, "entries": list(merged.values())})
                path = directory / "validated.json"
                path.write_bytes(payload)
                formal._read_entries(path)
                payloads[LIBRARY_FILE] = payload
            current_preferences = preferences.read_document(preferences.preferences_path())
            old_preferences = _base_document(repo, directory, PREFERENCES_FILE, preferences.read_document, base)
            new_preferences = {"version": 1, "cards": dict(old_preferences["cards"])}
            preference_cards = current_preferences["cards"] if metadata_only else chosen_cards
            for card in () if selected_resets else preference_cards:
                if card in current_preferences["cards"]:
                    new_preferences["cards"][card] = current_preferences["cards"][card]
            if new_preferences != old_preferences:
                payloads[PREFERENCES_FILE] = _payload(new_preferences)
            if update_hits:
                old_stats = _base_document(repo, directory, HITS_FILE, hits.read_shared, base)
                uploaded_stats = hits.prepare_upload(root, old_stats, selected_resets=selected_resets)
                validation = directory / "validated-hits.json"
                validation.write_bytes(_payload(uploaded_stats))
                hits.read_shared(validation)
                if uploaded_stats != old_stats:
                    payloads[HITS_FILE] = _payload(uploaded_stats)
            if payloads:
                details = _describe_upload(repo, directory, base, payloads)
                _lint(repo, progress)
                progress("正在提交选中的参考配置……")
                if head == base:
                    head = _commit_entries(repo, head, payloads, directory, details)
                else:
                    head = _commit_on_upstream(repo, base, payloads, directory, details)
            elif not pending_push:
                progress("正在核对已同步的数据……")
                if uploaded_stats is not None:
                    hits.acknowledge_upload(root, uploaded_stats)
                if not metadata_only:
                    _archive_published(root, chosen, base)
                if selected_resets:
                    return "所选线上清空记录已同步"
                return "顺序与命中次数已同步，无需重复提交" if metadata_only else "所选正式参考已上传，无需重复提交"
            if pending_push:
                published_payloads = {}
                for filename in UPLOAD_FILES:
                    if _git(repo, "diff", "--quiet", upstream, head, "--", filename, check=False).returncode == 1:
                        published_payloads[filename] = _git(repo, "show", f"{head}:{filename}").stdout
                details = _describe_upload(repo, directory, upstream, published_payloads)
        progress("正在推送正式参考……")
        try:
            _git(repo, "push", remote, f"{head}:{target}", timeout=90)
        except ValueError as error:
            if detached:
                raise ValueError("推送未完成，本机数据已保留。请检查网络或远端更新后重试") from error
            raise ValueError("推送未完成，本地提交已保留；检查网络、登录权限或远端更新后可再次点击上传") from error
        progress("推送成功，正在更新本机上传记录……")
        try:
            if uploaded_stats is not None:
                hits.acknowledge_upload(root, uploaded_stats)
            if not metadata_only:
                _archive_published(root, chosen, head)
            if detached:
                _git(repo, "update-ref", _baseline_ref(remote, target), head)
        except (OSError, ValueError) as error:
            raise ValueError("远端已上传，本机归档或图片清理未完成；刷新后可重试，正式参考保留") from error
        if details:
            return "\n".join(details)
        if selected_resets:
            return "已清空所选样本的线上命中次数"
        if metadata_only:
            return "已上传全部样本的顺序与新增命中次数；本地清空结果未上传，来源图片保留"
        return f"已上传 {len(chosen)} 份正式参考及所选配置；其他机器更新代码后生效，来源图片已清理，备注保留"
