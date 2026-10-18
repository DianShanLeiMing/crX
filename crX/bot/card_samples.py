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
from pathlib import Path
from typing import Any

import cv2

from crX.bot.coords import GAME_SCREEN_SIZE, HAND_CARD_SIZE, HAND_CARD_TOP_LEFTS, HAND_DIAGNOSTIC_REGION
from crX.utils.platform import get_app_data_dir

LOG = logging.getLogger(__name__)
MAX_ROOT_BYTES = 128 * 1024 * 1024
MAX_SAMPLES_PER_FIGHT = 32
OBSERVATION_INTERVAL_S = 1.0


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
        self._seen: list[tuple[str, int]] = []
        self._records: list[dict[str, Any]] = []
        self._thread = threading.Thread(target=self._run, name="card-samples", daemon=True)
        self._thread.start()

    def observation_due(self) -> bool:
        now = time.monotonic()
        if self._stop.is_set() or now - self.last_observation < OBSERVATION_INTERVAL_S:
            return False
        self.last_observation = now
        return True

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
        if self._records:
            try:
                self._write_report()
            except OSError:
                LOG.warning("卡牌样本清单保存失败，原始样本仍可检查", exc_info=True)

    def _save(self, frame, record: dict, used_bytes: int) -> int:
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
