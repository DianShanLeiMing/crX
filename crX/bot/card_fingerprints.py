"""Read original reference fingerprints without executable data files."""

import hashlib
import json
import sys
from pathlib import Path


def load_builtin_fingerprints():
    directory = (
        Path(getattr(sys, "_MEIPASS", Path(sys.executable).parent))
        if getattr(sys, "frozen", False)
        else Path(__file__).parent
    )
    path = directory / "card_builtin_references.json"
    if path.is_symlink() or path.is_junction():
        raise ValueError("原始参考库不能使用链接")
    with path.open("rb") as stream:
        payload = stream.read(2 * 1024 * 1024 + 1)
    if len(payload) > 2 * 1024 * 1024:
        raise ValueError("原始参考库过大")
    document = json.loads(payload)
    colors = document["colors"]
    if document.get("version") != 1 or len(colors) != 16 or len(set(colors)) != 16:
        raise ValueError("原始参考库格式无效")
    base, variants = {}, {}
    entries = document["entries"]
    if not isinstance(entries, list) or len(entries) > 2048:
        raise ValueError("原始参考库数量无效")
    for entry in entries:
        value = entry["fingerprint"]
        if len(value) != 4 or any(
            len(row) != 16 or any(type(n) is not int or not 0 <= n <= 891 for n in row) for row in value
        ):
            raise ValueError("原始参考指纹无效")
        identifier = hashlib.sha256(json.dumps([entry["card_id"], value], separators=(",", ":")).encode()).hexdigest()[
            :16
        ]
        if entry["id"] != identifier:
            raise ValueError("原始参考编号无效")
        if entry["source"] == "upstream":
            base[entry["legacy_id"]] = [dict(zip(colors, row, strict=True)) for row in value]
        else:
            variants.setdefault(entry["card_id"], []).append(value)
    return {"base": base, "variants": variants}
