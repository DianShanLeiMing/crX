"""Bounded recovery diagnostics; immutable evidence and separate human notes."""

from __future__ import annotations

import json
import logging
import queue
import threading
import time
import uuid
from pathlib import Path

import cv2
import numpy as np
from PIL import Image

from crX.utils.card_review import atomic_json, read_json, sample_file
from crX.utils.platform import get_log_dir

LOG = logging.getLogger(__name__)
STATUS_LABELS = {"pending": "待处理", "handled": "已处理", "ignored": "已忽略"}
MAX_FAILURE_SAMPLES = 200
MAX_FAILURE_BYTES = 128 * 1024**2
CAPTURE_TIMEOUT_S = 2.0


def get_failure_samples_dir() -> Path:
    return Path(get_log_dir("crX")) / "failures"


def _valid_image(image) -> bool:
    return (
        isinstance(image, np.ndarray)
        and image.dtype == np.uint8
        and image.ndim == 3
        and image.shape[2] == 3
        and 0 < image.shape[0] <= 4096
        and 0 < image.shape[1] <= 4096
    )


def remember_failure_frame(logger, image, **details):
    """Cache the exact frame that caused failure, never include pixels in UI stats."""
    logger.failure_frame = {
        "image": image.copy() if _valid_image(image) else None,
        "captured_at": time.time(),
        "details": details,
    }


def _capture(emulator, timeout):
    results = queue.Queue(maxsize=1)

    def run():
        try:
            results.put((emulator.screenshot(), None))
        except Exception as error:
            results.put((None, str(error)))

    threading.Thread(target=run, name="failure-screenshot", daemon=True).start()
    try:
        image, error = results.get(timeout=timeout)
    except queue.Empty:
        return None, f"截图超过 {timeout:g} 秒，已跳过；不会采用重启后的迟到截图"
    if image is not None and not _valid_image(image):
        return None, "截图格式或尺寸异常"
    return image, error or (None if image is not None else "未取得截图")


class FailureSampleRecorder:
    def __init__(self, root=None, *, max_samples=MAX_FAILURE_SAMPLES, max_bytes=MAX_FAILURE_BYTES):
        self.root = root if root is not None else get_failure_samples_dir()
        self.max_samples, self.max_bytes = max_samples, max_bytes
        self._queue = queue.Queue(maxsize=8)
        self._stop = threading.Event()
        self._warned = False
        self._thread = threading.Thread(target=self._run, name="failure-samples", daemon=True)
        self._thread.start()

    def offer(self, emulator, logger, *, state, attempt, session_log_path=None, context=None, exception=None):
        """Called before recovery, including the final failure that stops the task."""
        if self._stop.is_set() or self._queue.full():
            self._warn("异常流程队列已满或已关闭，继续恢复")
            return False
        cached = getattr(logger, "failure_frame", None)
        logger.failure_frame = None
        if cached is not None:
            image = cached["image"]
            captured_at = cached["captured_at"]
            details = cached["details"]
            capture_error = None if image is not None else "失败帧为空或格式异常"
            source = "failure_frame"
        else:
            image, capture_error = _capture(emulator, CAPTURE_TIMEOUT_S)
            captured_at, details, source = time.time(), {}, "before_recovery"
        record = {
            "id": time.strftime("%Y%m%d-%H%M%S-") + uuid.uuid4().hex[:8],
            "captured_at": captured_at,
            "state": state,
            "recovery_attempt": attempt,
            "reason": details.get("reason")
            or (exception.strip().splitlines()[-1] if exception else str(logger.current_status)),
            "status_at_failure": str(logger.current_status),
            "details": details,
            "context": context or {},
            "exception": exception,
            "session_log_path": str(session_log_path) if session_log_path else None,
            "capture_source": source,
            "capture_error": capture_error,
            "color_order": "BGR",
            "image_file": None,
        }
        try:
            self._queue.put_nowait((image, record))
        except queue.Full:
            self._warn("异常流程队列已满，继续恢复")
            return False
        logger.log(f"已提交恢复前异常流程：{self.root / record['id']}")
        return True

    def close(self, timeout=2.0):
        self._stop.set()
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            self._warn("异常流程后台尚未写完，不阻塞任务退出")

    def _warn(self, message):
        if not self._warned:
            LOG.warning(message)
            self._warned = True

    def _run(self):
        used_bytes, count = None, 0
        while not self._stop.is_set() or not self._queue.empty():
            try:
                image, record = self._queue.get(timeout=0.1)
            except queue.Empty:
                continue
            try:
                if used_bytes is None:
                    entries = list(self.root.glob("*/failure.json"))
                    count = len(entries)
                    used_bytes = sum(
                        p.stat().st_size for p in self.root.rglob("*") if p.is_file() and not p.is_symlink()
                    )
                encoded = None
                if image is not None:
                    ok, data = cv2.imencode(".png", image)
                    if ok:
                        encoded = data.tobytes()
                        record["image_file"] = "frame.png"
                        record["image_size"] = [image.shape[1], image.shape[0]]
                    else:
                        record["capture_error"] = "PNG 编码失败，仅保存诊断信息"
                size = len(encoded or b"") + len(json.dumps(record, ensure_ascii=False).encode("utf-8")) + 8192
                if count >= self.max_samples or used_bytes + size > self.max_bytes:
                    self._warn("异常流程达到容量上限，跳过新样本；已有样本不删除")
                    continue
                directory = self.root / record["id"]
                directory.mkdir(parents=True, exist_ok=False)
                if encoded is not None:
                    (directory / "frame.png").write_bytes(encoded)
                atomic_json(directory / "failure.json", record)
                count += 1
                used_bytes += size
                LOG.info("恢复前异常流程已保存：%s", directory)
            except Exception:
                self._warn("异常流程保存失败，继续恢复；请检查日志目录的磁盘空间和权限")
                LOG.debug("Failure sample write failed", exc_info=True)
            finally:
                self._queue.task_done()


def scan_failures(root):
    records, errors = [], 0
    if not root.exists():
        return records, errors
    for directory in sorted(root.iterdir(), reverse=True):
        if not directory.is_dir() or directory.is_symlink():
            continue
        try:
            data = read_json(sample_file(directory, "failure.json"))
            if not data:
                continue
            if not isinstance(data.get("reason"), str) or not isinstance(data.get("state"), str):
                raise ValueError("诊断格式无效")
            review = read_json(sample_file(directory, "review.json"))
            if review.get("status", "pending") not in STATUS_LABELS:
                raise ValueError("标记状态无效")
            if not isinstance(review.get("note", ""), str):
                raise ValueError("备注格式无效")
            records.append({"key": directory.name, "directory": directory, "data": data, "review": review})
        except (OSError, ValueError, TypeError):
            errors += 1
    return records, errors


def load_failure_preview(record):
    filename = record["data"].get("image_file")
    if not filename:
        return None
    try:
        with Image.open(sample_file(record["directory"], filename)) as image:
            if image.width > 4096 or image.height > 4096:
                return None
            return image.convert("RGB")
    except (OSError, ValueError, Image.DecompressionBombError):
        return None


def save_failure_review(record, status, note):
    if status not in STATUS_LABELS:
        raise ValueError("标记状态无效")
    atomic_json(
        sample_file(record["directory"], "review.json"),
        {"status": status, "note": note.strip()[:4000], "reviewed_at": time.time()},
    )


def delete_failure_samples(root: Path, keys: set[str]) -> int:
    """Delete an explicit selection, preflighting every target without recursion."""
    if not keys or not root.exists():
        return 0
    if len(keys) > MAX_FAILURE_SAMPLES:
        raise ValueError(f"一次最多删除 {MAX_FAILURE_SAMPLES} 条异常流程")
    if root.is_symlink() or root.is_junction():
        raise ValueError("异常流程根目录不能是链接")
    base = root.resolve(strict=True)
    plans = []
    allowed = {"frame.png", "failure.json", "review.json"}

    def check_directory(directory):
        if directory.is_symlink() or directory.is_junction() or directory.resolve().parent != base:
            raise ValueError("异常流程目录超出允许范围或包含链接")

    for key in sorted(keys):
        if not key or Path(key).name != key or key in {".", ".."}:
            raise ValueError("异常流程编号无效")
        directory = root / key
        check_directory(directory)
        if not directory.exists():
            continue
        files = list(directory.iterdir())
        if any(path.name not in allowed for path in files):
            raise ValueError("异常流程中存在额外文件，请先检查目录，已取消删除")
        for path in files:
            if path.is_symlink() or path.is_junction() or not path.is_file():
                raise ValueError("异常流程包含链接或异常文件，已取消删除")
            sample_file(directory, path.name)
        data = read_json(sample_file(directory, "failure.json"))
        if not isinstance(data.get("reason"), str) or not isinstance(data.get("state"), str):
            raise ValueError("异常流程诊断信息无效，已取消删除")
        plans.append((directory, files))
    for directory, files in plans:
        for path in files:
            check_directory(directory)
            if path.is_symlink() or path.is_junction() or path.resolve().parent != directory.resolve():
                raise ValueError("异常流程文件已变化，已停止删除")
            path.unlink(missing_ok=True)
        check_directory(directory)
        directory.rmdir()
    return len(plans)
