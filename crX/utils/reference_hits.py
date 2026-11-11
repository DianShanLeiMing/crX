"""Explicitly shared, resettable per-machine hit counters (no machine identity data)."""

from __future__ import annotations

import hashlib
import json
import threading
import uuid
from contextlib import contextmanager
from typing import Any

from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json, sample_file
from crX.utils.reference_preferences import IDENTIFIER

_LOCK = threading.RLock()
_runtime_counts = {}
MAX_BYTES = 8 * 1024 * 1024
MAX_COUNT = 2**53 - 1


def shared_path():
    return formal.formal_path().with_name("card_reference_hits.json")


def empty_shared():
    return {"version": 1, "epoch": "initial", "nodes": {}}


def _read(path, default):
    if path.is_symlink() or path.is_junction():
        raise ValueError("命中统计不能使用链接")
    if not path.exists():
        return default
    with path.open("rb") as stream:
        payload = stream.read(MAX_BYTES + 1)
    if len(payload) > MAX_BYTES:
        raise ValueError("命中统计过大")
    value = json.loads(payload)
    if not isinstance(value, dict):
        raise ValueError("命中统计格式无效")
    return value


def _counts(value, maximum=MAX_COUNT):
    if (
        not isinstance(value, dict)
        or len(value) > formal.MAX_ENTRIES * 2
        or any(
            not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or type(n) is not int or not 0 <= n <= maximum
            for key, n in value.items()
        )
    ):
        raise ValueError("命中次数无效")
    return value


def _epoch(value):
    return isinstance(value, str) and (
        value == "initial" or len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    )


def _resets(value):
    if (
        not isinstance(value, dict)
        or len(value) > formal.MAX_ENTRIES * 2
        or any(
            not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or not _epoch(marker) or marker == "initial"
            for key, marker in value.items()
        )
    ):
        raise ValueError("命中清空记录无效")
    return value


def read_shared(path=None):
    value = _read(path or shared_path(), empty_shared())
    if (
        set(value) - {"resets"} != {"version", "epoch", "nodes"}
        or type(value.get("version")) is not int
        or value.get("version") != 1
        or not _epoch(value.get("epoch"))
        or not isinstance(value.get("nodes"), dict)
        or len(value["nodes"]) > 256
    ):
        raise ValueError("共享命中统计格式无效")
    for node, counts in value["nodes"].items():
        if not _epoch(node) or node == "initial":
            raise ValueError("统计节点无效")
        _counts(counts)
    _resets(value.get("resets", {}))
    return value


def _token(shared):
    return hashlib.sha256(json.dumps(shared, sort_keys=True).encode()).hexdigest()


@contextmanager
def _locked(root):
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or root.is_junction():
        raise ValueError("统计目录不能使用链接")
    with _LOCK, formal.writer_lock(sample_file(root, "reference-hits.lock")):
        yield sample_file(root, "reference-hits-local.json")


def _local(path, shared):
    local: dict[str, Any] = _read(path, {})
    if not local:
        local = {
            "version": 1,
            "node": uuid.uuid4().hex,
            "epoch": shared["epoch"],
            "counts": {},
            "cleared": False,
            "shared_token": _token(shared),
        }
    if (
        local.get("version") != 1
        or not _epoch(local.get("node"))
        or local["node"] == "initial"
        or not _epoch(local.get("epoch"))
        or type(local.get("cleared")) is not bool
    ):
        raise ValueError("本地命中统计格式无效")
    _counts(local.get("counts"))
    _counts(local.setdefault("last_used", {}))
    _counts(local.setdefault("completed_last_used", dict(local["last_used"])))
    # Migrate the former whole-library reset into local-only display offsets.
    if local["epoch"] != shared["epoch"]:
        pending = local["counts"] if local["cleared"] else {}
        local.update(epoch=shared["epoch"], counts=pending, hidden={}, baseline_hidden={})
    local["cleared"] = False
    for field in ("hidden", "baseline_hidden"):
        _counts(local.setdefault(field, {}), MAX_COUNT * 256)
    previous = _resets(local.get("resets", {}))
    current = shared.get("resets", {})
    pending = _resets(local.setdefault("pending_resets", {}))
    for key in previous.keys() | current.keys():
        if previous.get(key) != current.get(key):
            for field in ("counts", "hidden", "baseline_hidden"):
                local[field].pop(key, None)
        if current.get(key) == pending.get(key):
            pending.pop(key, None)
    local["resets"] = current
    counts = shared["nodes"].get(local["node"], {})
    local["counts"] = {
        key: max(local["counts"].get(key, 0), counts.get(key, 0)) for key in local["counts"].keys() | counts.keys()
    }
    local["shared_token"] = _token(shared)
    return local


def _merged(shared, local):
    if shared["epoch"] != local["epoch"]:
        raise ValueError("共享统计已重置，请先更新代码再上传命中数")
    nodes = dict(shared["nodes"])
    counts = nodes.get(local["node"], {})
    resets = shared.get("resets", {})
    nodes[local["node"]] = {
        key: max(counts.get(key, 0), local["counts"].get(key, 0) if resets.get(key) == local["resets"].get(key) else 0)
        for key in counts.keys() | local["counts"].keys()
    }
    return {**shared, "nodes": nodes}


def snapshot(root):
    """Return displayed totals and locally changed identifiers in one locked read."""
    global _runtime_counts
    with _locked(root) as path:
        shared = read_shared()
        local = _local(path, shared)
        atomic_json(path, local)
        merged = _merged(shared, local)
        published = shared["nodes"].get(local["node"], {})
        keys = local["counts"].keys() | local["pending_resets"].keys()
        changed = {
            key
            for key in keys
            if local["counts"].get(key, 0) != published.get(key, 0) or key in local["pending_resets"]
        }
    result = _displayed(merged, local["hidden"])
    _runtime_counts = result
    return result, changed


def _displayed(merged, hidden):
    result = {}
    for counts in merged["nodes"].values():
        for key, n in counts.items():
            result[key] = result.get(key, 0) + n
    for key, n in hidden.items():
        result[key] = max(0, result.get(key, 0) - n)
    return result


def totals(root):
    counts, _changed = snapshot(root)
    return counts, False


def recent_usage(root, *, completed_only=False):
    with _locked(root) as path:
        local = _read(path, {})
        usage = local.get("last_used", {})
        if completed_only:
            usage = local.get("completed_last_used", usage)
        return dict(_counts(usage))


def record_usage(root, timestamps, *, finished=True):
    """Persist usage during battle and publish completed usage for stable UI sorting."""
    _counts(timestamps)
    with _locked(root) as path:
        local = _local(path, read_shared())
        for key, timestamp in timestamps.items():
            local["last_used"][key] = max(local["last_used"].get(key, 0), timestamp)
            if finished:
                local["completed_last_used"][key] = max(local["completed_last_used"].get(key, 0), timestamp)
        _counts(local["last_used"])
        _counts(local["completed_last_used"])
        atomic_json(path, local)


def record_hits(root, pending):
    _counts(pending)
    with _locked(root) as path:
        local = _local(path, read_shared())
        for key, n in pending.items():
            local["counts"][key] = min(MAX_COUNT, local["counts"].get(key, 0) + n)
        _counts(local["counts"])
        atomic_json(path, local)


def clear_local(root, identifiers=None, *, allow_upload=False):
    """Clear displayed hits locally and optionally stage a per-reference shared reset."""
    with _locked(root) as path:
        shared = read_shared()
        local = _local(path, shared)
        chosen = set(local["counts"]) if identifiers is None else set(identifiers)
        _counts(dict.fromkeys(chosen, 0))
        published = shared["nodes"].get(local["node"], {})
        totals = _displayed(_merged(shared, local), local["hidden"])
        chosen = {key for key in chosen if totals.get(key, 0) > 0}
        if not chosen:
            return ""
        for key in chosen:
            local["counts"][key] = published.get(key, 0)
            local["hidden"][key] = published.get(key, 0)
            if allow_upload:
                local["hidden"][key] = sum(counts.get(key, 0) for counts in shared["nodes"].values())
                local["pending_resets"][key] = uuid.uuid4().hex
        _counts(local["hidden"], MAX_COUNT * 256)
        _resets(local["pending_resets"])
        atomic_json(path, local)
    if allow_upload:
        return f"已在本机清空 {len(chosen)} 个样本的次数。保留勾选并点击「上传顺序与次数」可清空对应线上次数"
    return f"已清空 {len(chosen)} 个样本的本机贡献次数；其他机器及线上统计保留，清空结果不会上传"


def sync_shared(root, shared, reset_identifiers=()):
    with _locked(root) as path:
        local = _local(path, shared)
        published = shared["nodes"].get(local["node"], {})
        for key in reset_identifiers:
            local["counts"][key] = published.get(key, 0)
            for field in ("hidden", "baseline_hidden", "pending_resets"):
                local[field].pop(key, None)
        if read_shared() != shared:
            atomic_json(shared_path(), shared)
        atomic_json(path, local)


def prepare_upload(root, committed_shared, *, selected_resets=(), selected_identifiers=None):
    with _locked(root) as path:
        local = _local(path, read_shared())
        committed_resets = committed_shared.get("resets", {})
        chosen = set(selected_identifiers) if selected_identifiers is not None else None
        reset_keys = selected_resets if chosen is None else chosen
        resets = {key: local["pending_resets"][key] for key in reset_keys if key in local["pending_resets"]}
        if any(marker != local["resets"].get(key) and key not in resets for key, marker in committed_resets.items()):
            raise ValueError("有尚未同步的线上清空提交，请勾选原卡牌重试或先更新本机共享统计")
        merged = _merged(committed_shared, local)
        if chosen is not None:
            published = committed_shared["nodes"].get(local["node"], {})
            own = merged["nodes"][local["node"]]
            merged["nodes"][local["node"]] = {
                key: own.get(key, 0) if key in chosen else published.get(key, 0)
                for key in published.keys() | (own.keys() & chosen)
            }
        if selected_resets or resets:
            if not resets:
                raise ValueError("所选卡牌没有待上传的清空记录，请先在本机清空次数")
            source = committed_shared if chosen is None else merged
            merged = {
                **source,
                "resets": {**committed_resets, **resets},
                "nodes": {
                    node: {key: count for key, count in counts.items() if key not in resets}
                    for node, counts in source["nodes"].items()
                },
            }
        hidden = dict(local["hidden"])
        if chosen is not None:
            hidden = dict(local["baseline_hidden"])
            hidden.update({key: value for key, value in local["hidden"].items() if key in chosen})
        local["upload_snapshot"] = {"token": _token(merged), "hidden": hidden}
        if resets:
            local["upload_snapshot"]["reset_counts"] = {key: local["counts"].get(key, 0) for key in resets}
        atomic_json(path, local)
        return merged


def acknowledge_upload(root, shared):
    with _locked(root) as path:
        local = _local(path, read_shared())
        snapshot = local.get("upload_snapshot", {})
        if isinstance(snapshot, dict) and snapshot.get("token") == _token(shared):
            local["baseline_hidden"] = dict(_counts(snapshot["hidden"], MAX_COUNT * 256))
            local.pop("upload_snapshot", None)
            reset_counts = _counts(snapshot.get("reset_counts", {}))
            for key, count in reset_counts.items():
                local["counts"][key] = max(0, local["counts"].get(key, 0) - count)
                local["hidden"].pop(key, None)
                local["baseline_hidden"].pop(key, None)
                local["resets"][key] = shared["resets"][key]
                if local["pending_resets"].get(key) == shared["resets"][key]:
                    local["pending_resets"].pop(key)
        # Keep increments and local clears made after the uploaded snapshot.
        local.update(epoch=shared["epoch"], cleared=False, shared_token=_token(shared))
        atomic_json(shared_path(), shared)
        atomic_json(path, local)
