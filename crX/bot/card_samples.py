"""Bounded, asynchronous diagnostics from the exact frame used for recognition."""

from __future__ import annotations

import html
import json
import logging
import queue
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import cv2
import numpy as np
from PIL import Image

from crX.bot.coords import GAME_SCREEN_SIZE, HAND_CARD_SIZE, HAND_CARD_TOP_LEFTS, HAND_DIAGNOSTIC_REGION
from crX.utils.platform import get_app_data_dir

LOG = logging.getLogger(__name__)
MAX_ROOT_BYTES = 128 * 1024 * 1024
MAX_SAMPLES_PER_FIGHT = 32
OBSERVATION_INTERVAL_S = 1.0
MAX_SIMILAR_SAMPLES = 1
MAX_SAMPLE_GROUPS = 512


@dataclass
class _SampleGroup:
    hash_value: int
    pixels: np.ndarray
    count: int


def get_card_samples_dir() -> Path:
    return Path(get_app_data_dir("crX")) / "card_samples"


def _perceptual_hash(image) -> int:
    gray = cv2.cvtColor(cv2.resize(image, (9, 8)), cv2.COLOR_BGR2GRAY)
    value = 0
    for bit in (gray[:, 1:] > gray[:, :-1]).flat:
        value = (value << 1) | int(bit)
    return value


class CardSampleCollector:
    """One fight, one bounded queue; callers never encode PNGs or wait on disk."""

    def __init__(
        self, mode: str, root: Path | None = None, *, max_samples=MAX_SAMPLES_PER_FIGHT, max_bytes=MAX_ROOT_BYTES
    ):
        self.root = root if root is not None else get_card_samples_dir()
        self.directory = self.root / (time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8])
        self.mode = mode
        self.max_samples = max_samples
        self.max_bytes = max_bytes
        self.started = time.monotonic()
        self.last_observation = float("-inf")
        self.empty_observations = 0
        self.stats = dict.fromkeys(("queued", "saved", "duplicates", "dropped", "errors"), 0)
        self._queue: queue.Queue = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._match_lock = threading.Lock()
        self._match_pending: dict[str, dict[str, int]] = {}
        self._hit_pending: dict[str, int] = {}
        self._hit_episodes: dict[int, str] = {}
        self._hand_episodes: dict[int, tuple[str, bool]] = {}
        self._assisted_in_fight: set[str] = set()
        self._next_match_flush = 0.0
        self._seen: list[tuple[str, int]] = []
        self._records: list[dict[str, Any]] = []
        self._groups: list[_SampleGroup] | None = None
        self._thread = threading.Thread(target=self._run, name="card-samples", daemon=True)
        self._thread.start()

    def observation_due(self) -> bool:
        now = time.monotonic()
        if self._stop.is_set() or now - self.last_observation < OBSERVATION_INTERVAL_S:
            return False
        self.last_observation = now
        return True

    def observe_library_match(self, recognition: dict, slot: int) -> None:
        """Count reference hits per card arrival; retain separate legacy rescue stats."""
        from crX.utils.card_library import empty_match_stats

        if type(slot) is not int or not 0 <= slot < 4 or self._stop.is_set():
            return
        card = recognition.get("card_id", "UNKNOWN")
        identifier = recognition.get("reference_id", "")
        candidates = recognition.get("candidates", [])
        if (
            card != "UNKNOWN"
            and self._hit_episodes.get(slot) != card
            and isinstance(identifier, str)
            and len(identifier) == 16
            and all(char in "0123456789abcdef" for char in identifier)
            and candidates
            and type(candidates[0].get("distance")) is int
            and 0 <= candidates[0]["distance"] <= recognition.get("match_threshold", 1000)
        ):
            with self._match_lock:
                if identifier in self._hit_pending or len(self._hit_pending) < 256:
                    self._hit_pending[identifier] = self._hit_pending.get(identifier, 0) + 1
                    self._hit_episodes[slot] = card
        elif card != "UNKNOWN" and self._hit_episodes.get(slot) != card:
            # A different accepted card ends the preceding appearance even if it
            # belongs to a legacy reference without a stable counter identifier.
            self._hit_episodes.pop(slot, None)
        # Unknown/temporarily unavailable frames do not establish a replacement card.
        if card != "UNKNOWN" and self._hand_episodes.get(slot, (None, False))[0] != card:
            self._hand_episodes[slot] = (card, False)
        source = recognition.get("source", "")
        if not source.startswith(("local_trial:", "local_active:")) or self._stop.is_set():
            return
        identifier = source.split(":", 1)[1]
        candidates = recognition.get("candidates", [])
        if len(identifier) != 16 or any(char not in "0123456789abcdef" for char in identifier) or not candidates:
            return
        distance = candidates[0].get("distance")
        margin = recognition.get("candidate_margin")
        if type(distance) is not int or type(margin) is not int or not 0 <= distance <= 10000:
            return
        with self._match_lock:
            if identifier not in self._match_pending and len(self._match_pending) >= 256:
                return
            record = self._match_pending.setdefault(identifier, empty_match_stats())
            if record["matches"] < 100000:
                record["matches"] += 1
                record["distance_total"] += distance
                record["margin_total"] += max(0, min(10000, margin))
            if (
                card != "UNKNOWN"
                and recognition.get("builtin_result", {}).get("card_id") == "UNKNOWN"
                and not self._hand_episodes[slot][1]
                and record["assists"] < 100000
            ):
                record["assists"] += 1
                record["assist_distance_total"] += distance
                record["assist_margin_total"] += max(0, min(10000, margin))
                record["assist_battles"] += int(identifier not in self._assisted_in_fight)
                self._assisted_in_fight.add(identifier)
                self._hand_episodes[slot] = (card, True)

    def _flush_match_stats(self):
        from crX.utils.card_library import merge_match_stats
        from crX.utils.reference_hits import record_hits, totals

        with self._match_lock:
            pending, self._match_pending = self._match_pending, {}
            hits, self._hit_pending = self._hit_pending, {}
        if hits:
            try:
                record_hits(self.root, hits)
            except (OSError, ValueError, TypeError):
                with self._match_lock:
                    for key, value in hits.items():
                        self._hit_pending[key] = self._hit_pending.get(key, 0) + value
                raise
        if pending:
            merge_match_stats(self.root, pending)
        totals(self.root)

    def offer(self, frame, slot: int | None, metadata: dict) -> bool:
        if self._stop.is_set() or frame is None or frame.shape != (*GAME_SCREEN_SIZE[::-1], 3):
            return False
        if slot is None:
            left, top, right, bottom = HAND_DIAGNOSTIC_REGION
        elif 0 <= slot < len(HAND_CARD_TOP_LEFTS):
            left, top = HAND_CARD_TOP_LEFTS[slot]
            width, height = HAND_CARD_SIZE
            right, bottom = left + width, top + height
        else:
            return False
        if slot is not None and metadata.get("available") is False:
            self.stats["dropped"] += 1
            return False
        crop = frame[top:bottom, left:right]
        reason = str(metadata["reason"])
        fingerprint = _perceptual_hash(crop)
        if any(
            reason == prior_reason and (fingerprint ^ prior_hash).bit_count() <= 4
            for prior_reason, prior_hash in self._seen
        ):
            self.stats["duplicates"] += 1
            return False
        if self.stats["queued"] >= self.max_samples or self._queue.full():
            self.stats["dropped"] += 1
            return False
        record = {
            **metadata,
            "slot": slot,
            "mode": self.mode,
            "elapsed_s": round(time.monotonic() - self.started, 3),
            "captured_at": time.time(),
            "frame_size": list(GAME_SCREEN_SIZE),
            "color_order": "BGR",
            "crop": [left, top, right, bottom],
            "label": None,
        }
        try:
            self._queue.put_nowait((frame.copy(), record))
        except queue.Full:
            self.stats["dropped"] += 1
            return False
        self._seen.append((reason, fingerprint))
        self.stats["queued"] += 1
        return True

    def close(self, timeout: float = 2.0) -> bool:
        self._stop.set()
        self._thread.join(timeout=timeout)
        return not self._thread.is_alive()

    def _run(self) -> None:
        used_bytes = None
        while not self._stop.is_set() or not self._queue.empty():
            if time.monotonic() >= self._next_match_flush:
                self._next_match_flush = time.monotonic() + 5
                try:
                    self._flush_match_stats()
                except (OSError, ValueError, TypeError):
                    LOG.warning("运行匹配统计未能保存，继续对战", exc_info=True)
            try:
                frame, record = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if used_bytes is None:
                    used_bytes = sum(
                        p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()
                    )
                used_bytes += self._save(frame, record, used_bytes)
            except (OSError, ValueError, cv2.error):
                self.stats["errors"] += 1
                if self.stats["errors"] == 1:
                    LOG.warning("卡牌诊断样本保存失败，继续执行对战", exc_info=True)
            finally:
                self._queue.task_done()
        try:
            self._flush_match_stats()
        except (OSError, ValueError, TypeError):
            LOG.warning("运行匹配统计未能保存，继续对战", exc_info=True)
        if self._records:
            try:
                self._write_report()
            except OSError:
                LOG.warning("卡牌样本清单保存失败，原始样本仍可检查", exc_info=True)

    def _save(self, frame, record: dict, used_bytes: int) -> int:
        if not self._keep_representative(frame, record):
            self.stats["duplicates"] += 1
            return 0
        name = f"sample-{len(self._records):03d}"
        left, top, right, bottom = record["crop"]
        hx1, hy1, hx2, hy2 = HAND_DIAGNOSTIC_REGION
        images = {"frame": frame, "card": frame[top:bottom, left:right], "hand": frame[hy1:hy2, hx1:hx2]}
        encoded = {}
        for kind, pixels in images.items():
            ok, data = cv2.imencode(".png", pixels)
            if not ok:
                raise ValueError("PNG encoding failed")
            encoded[f"{name}-{kind}.png"] = data.tobytes()
        record = {**record, "files": {kind: f"{name}-{kind}.png" for kind in images}}
        metadata = json.dumps(record, ensure_ascii=False) + "\n"
        size = sum(len(data) for data in encoded.values()) + len(metadata.encode("utf-8"))
        # Reserve room for the final report, and stop collecting without deleting old samples.
        if used_bytes + size + 32768 > self.max_bytes:
            self.stats["dropped"] += 1
            return 0
        self.directory.mkdir(parents=True, exist_ok=True)
        for filename, data in encoded.items():
            (self.directory / filename).write_bytes(data)
        with (self.directory / "samples.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(metadata)
        self._records.append(record)
        self.stats["saved"] += 1
        return size

    def _keep_representative(self, frame, record) -> bool:
        """Cross-fight filtering runs on the disk worker, never on the gameplay thread."""
        from crX.utils.card_review import (
            _group_features,
            image_group_features,
            scan_grouped_samples,
            similar_card_images,
        )

        if type(record.get("slot")) is not int or record.get("available") is not True:
            return True
        if record.get("reason") == "builtin_disagreement":
            # A confirmed source image must not swallow a new disagreement report.
            # Retain one report per reference / baseline card for human review.
            from crX.utils.card_review import scan_samples

            existing, errors = scan_samples(self.root)
            if errors:
                return False
            return not any(
                sample.record.get("reason") == "builtin_disagreement"
                and sample.record.get("source") == record.get("source")
                and sample.record.get("builtin_result", {}).get("card_id")
                == record.get("builtin_result", {}).get("card_id")
                for sample in existing
            )
        if self._groups is None:
            samples, _, groups = scan_grouped_samples(self.root)
            by_key = {sample.key: sample for sample in samples}
            self._groups = []
            for key, members in list(groups.items())[:MAX_SAMPLE_GROUPS]:
                features = _group_features(by_key[key])
                if features is not None:
                    self._groups.append(_SampleGroup(*features, len(members)))
        left, top, right, bottom = record["crop"]
        features = image_group_features(Image.fromarray(cv2.cvtColor(frame[top:bottom, left:right], cv2.COLOR_BGR2RGB)))
        if features is None:
            return False
        hash_value, pixels = features
        for group in self._groups:
            if similar_card_images(features, (group.hash_value, group.pixels)):
                group.count += 1
                return group.count <= MAX_SIMILAR_SAMPLES
        if len(self._groups) < MAX_SAMPLE_GROUPS:
            self._groups.append(_SampleGroup(hash_value, pixels, 1))
        return True

    def _write_report(self) -> None:
        rows = []
        for record in sorted(self._records, key=lambda r: (r["reason"], r.get("card_id", ""))):
            candidates = ", ".join(f'{c["card_id"]}: {c["distance"]}' for c in record.get("candidates", []))
            rows.append(
                f'<tr><td><a href="{record["files"]["frame"]}"><img src="{record["files"]["card"]}" alt="手牌样本"></a></td>'
                f'<td>{html.escape(record["reason"])}</td><td>{record["slot"]}</td>'
                f'<td>{record["elapsed_s"]}</td><td>{html.escape(candidates)}</td><td>待确认</td></tr>'
            )
        page = (
            '<!doctype html><html lang="zh-CN"><meta charset="utf-8"><title>卡牌识别样本</title>'
            "<style>body{font:16px sans-serif;margin:24px}td,th{padding:10px;border-bottom:1px solid #ccc}"
            "img{max-height:100px}table{border-collapse:collapse}</style><h1>战后卡牌样本</h1>"
            "<p>候选结果仅供核对，实际卡名尚未确认。点击图片查看同一帧原图。</p>"
            "<table><tr><th>样本</th><th>原因</th><th>位置（0–3）</th><th>秒</th><th>候选／距离</th><th>标注</th></tr>"
            + "".join(rows)
            + "</table></html>"
        )
        (self.directory / "index.html").write_text(page, encoding="utf-8")
        (self.directory / "summary.json").write_text(json.dumps(self.stats, indent=2), encoding="utf-8")


_active: CardSampleCollector | None = None


def start_card_samples(mode: str, root: Path | None = None) -> CardSampleCollector:
    global _active
    finish_card_samples()
    _active = CardSampleCollector(mode, root)
    return _active


def active_card_samples() -> CardSampleCollector | None:
    return _active


def finish_card_samples() -> None:
    global _active
    collector, _active = _active, None
    if collector is not None:
        collector.close()


@contextmanager
def card_sample_session(mode: str):
    try:
        collector = start_card_samples(mode)
    except (OSError, RuntimeError):
        LOG.warning("卡牌诊断采样未能启动，继续执行对战", exc_info=True)
        collector = None
    try:
        yield collector
    finally:
        if collector is not None:
            finish_card_samples()
