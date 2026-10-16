"""Small atomic JSON settings store, isolated from the upstream application."""

import json
import os
import tempfile
import threading
from pathlib import Path

from crX.utils.platform import get_app_data_dir

MODULE_NAME = "crX"
top_level = get_app_data_dir(MODULE_NAME)


class FileCache:
    def __init__(self, file_name: str) -> None:
        self.file_name = file_name
        self.mutex = threading.RLock()

    @property
    def path(self) -> Path:
        return Path(top_level) / self.file_name

    def cache_data(self, data: dict) -> dict:
        with self.mutex:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            merged = self.load_data() | data
            fd, temporary = tempfile.mkstemp(prefix="settings-", suffix=".tmp", dir=self.path.parent)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as handle:
                    json.dump(merged, handle, indent=2)
                os.replace(temporary, self.path)
            finally:
                Path(temporary).unlink(missing_ok=True)
            return merged

    def load_data(self) -> dict:
        with self.mutex:
            try:
                if self.path.stat().st_size > 1024 * 1024:
                    return {}
                value = json.loads(self.path.read_text(encoding="utf-8"))
                return value if isinstance(value, dict) else {}
            except (OSError, ValueError):
                return {}

    def exists(self) -> bool:
        return self.path.is_file()

    def get(self, key: str, default=None):
        return self.load_data().get(key, default)


USER_SETTINGS_CACHE = FileCache("user_settings.json")
