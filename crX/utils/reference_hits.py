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


def _counts(value):
    if (
        not isinstance(value, dict)
        or len(value) > formal.MAX_ENTRIES * 2
        or any(
            not isinstance(key, str) or not IDENTIFIER.fullmatch(key) or type(n) is not int or not 0 <= n <= MAX_COUNT
            for key, n in value.items()
        )
    ):
        raise ValueError("命中次数无效")
    return value


def _epoch(value):
    return isinstance(value, str) and (
        value == "initial" or len(value) == 32 and all(c in "0123456789abcdef" for c in value)
    )


def read_shared(path=None):
    value = _read(path or shared_path(), empty_shared())
    if (
        set(value) != {"version", "epoch", "nodes"}
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
    # A pulled shared update may replace a local-only clear. A shared reset discards
    # every stale component, including unpublished counts from the former epoch.
    if (local["cleared"] and local.get("shared_token") != _token(shared)) or (
        not local["cleared"] and local["epoch"] != shared["epoch"]
    ):
        local.update(epoch=shared["epoch"], counts={}, cleared=False)
    if not local["cleared"]:
        counts = shared["nodes"].get(local["node"], {})
        local["counts"] = {
            key: max(local["counts"].get(key, 0), counts.get(key, 0)) for key in local["counts"].keys() | counts.keys()
        }
    local["shared_token"] = _token(shared)
    return local


def _merged(shared, local):
    if local["cleared"]:
        nodes = {} if shared["epoch"] != local["epoch"] else dict(shared["nodes"])
    elif shared["epoch"] != local["epoch"]:
        # An old machine must update before publishing after somebody else's reset.
        raise ValueError("共享统计已重置，请先更新代码再上传命中数")
    else:
        nodes = dict(shared["nodes"])
    counts = nodes.get(local["node"], {})
    nodes[local["node"]] = {
        key: max(counts.get(key, 0), local["counts"].get(key, 0)) for key in counts.keys() | local["counts"].keys()
    }
    return {"version": 1, "epoch": local["epoch"], "nodes": nodes}


def totals(root):
    global _runtime_counts
    with _locked(root) as path:
        shared = read_shared()
        local = _local(path, shared)
        atomic_json(path, local)
        merged = _merged(shared, local)
    result = {}
    for counts in merged["nodes"].values():
        for key, n in counts.items():
            result[key] = result.get(key, 0) + n
    _runtime_counts = result
    return result, local["cleared"]


def record_hits(root, pending):
    _counts(pending)
    with _locked(root) as path:
        local = _local(path, read_shared())
        for key, n in pending.items():
            local["counts"][key] = min(MAX_COUNT, local["counts"].get(key, 0) + n)
        _counts(local["counts"])
        atomic_json(path, local)


def clear_local(root):
    with _locked(root) as path:
        shared = read_shared()
        local = _local(path, shared)
        local.update(epoch=uuid.uuid4().hex, counts={}, cleared=True)
        atomic_json(path, local)
    return "本地命中数已清空；未选择更新命中数时不会上传，拉取远端统计后可被覆盖"


def prepare_upload(root, committed_shared):
    with _locked(root) as path:
        local = _local(path, read_shared())
        atomic_json(path, local)
        return _merged(committed_shared, local)


def acknowledge_upload(root, shared):
    with _locked(root) as path:
        local = _local(path, read_shared())
        if local["cleared"] and local["epoch"] != shared["epoch"]:
            raise ValueError("上传期间本地命中数再次清空；远端已更新，本地清空结果保留，请刷新")
        # Preserve any new local increments made since the upload snapshot.
        local.update(epoch=shared["epoch"], cleared=False, shared_token=_token(shared))
        atomic_json(shared_path(), shared)
        atomic_json(path, local)
