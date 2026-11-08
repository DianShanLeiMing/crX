"""Optional local feature/comparison cache. Capture folders remain immutable."""

from __future__ import annotations

import hashlib
import math
import re
import sqlite3
from pathlib import Path

from crX.utils.platform import get_app_data_dir

CACHE_VERSION = "content-v1"


def cache_path(root):
    key = hashlib.sha256(str(root).encode("utf-8")).hexdigest()[:24]
    return Path(get_app_data_dir("crX")) / "filter-cache" / f"{CACHE_VERSION}-{key}.sqlite3"


class FeatureCache:
    def __init__(self, root):
        self.connection = None
        try:
            path = cache_path(root)
            path.parent.mkdir(parents=True, exist_ok=True)
            connection = sqlite3.connect(path, timeout=1)
            self.connection = connection
            connection.execute(
                "CREATE TABLE IF NOT EXISTS images (path TEXT PRIMARY KEY, stamp TEXT, digest TEXT, view TEXT, signature BLOB, quality REAL)"
            )
            connection.execute(
                "CREATE TABLE IF NOT EXISTS pairs (key TEXT PRIMARY KEY, same_scene INTEGER, cross_scene INTEGER)"
            )
        except (OSError, sqlite3.Error):
            self.close()

    def _execute(self, query, parameters=()):
        if self.connection is None:
            return None
        try:
            return self.connection.execute(query, parameters)
        except sqlite3.Error:
            self.close()
            return None

    def image(self, path, stamp):
        result = self._execute(
            "SELECT digest, view, signature, quality FROM images WHERE path=? AND stamp=?", (str(path), str(stamp))
        )
        row = self._fetch(result)
        if row is None:
            return None
        digest, view, signature, quality = row
        if (
            not all(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{64}", value) for value in (digest, view))
            or not isinstance(signature, bytes)
            or len(signature) != 32
            or not isinstance(quality, int | float)
            or not math.isfinite(quality)
        ):
            return None
        return row

    def save_image(self, path, stamp, feature):
        self._execute("INSERT OR REPLACE INTO images VALUES (?, ?, ?, ?, ?, ?)", (str(path), str(stamp), *feature))

    def pair(self, key):
        result = self._execute("SELECT same_scene, cross_scene FROM pairs WHERE key=?", (key,))
        row = self._fetch(result)
        return tuple(map(bool, row)) if row is not None and all(value in (0, 1) for value in row) else None

    def _fetch(self, result):
        try:
            return result.fetchone() if result is not None else None
        except sqlite3.Error:
            self.close()
            return None

    def save_pair(self, key, result):
        self._execute("INSERT OR REPLACE INTO pairs VALUES (?, ?, ?)", (key, *map(int, result)))

    def close(self):
        if self.connection is not None:
            connection, self.connection = self.connection, None
            try:
                connection.commit()
            except sqlite3.Error:
                pass
            finally:
                try:
                    connection.close()
                except sqlite3.Error:
                    pass

    def __enter__(self):
        return self

    def __exit__(self, *_exception):
        self.close()
