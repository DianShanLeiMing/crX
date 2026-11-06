"""Open local folders or class-list text files with the operating system."""

from __future__ import annotations

import logging
import os
import subprocess
from pathlib import Path

from crX.utils.platform import is_macos, is_windows


def open_folder(folder_path: str) -> None:
    """Open a folder in the platform's file explorer."""
    os.makedirs(folder_path, exist_ok=True)

    try:
        if is_windows():
            os.startfile(folder_path)
            return

        if is_macos():
            subprocess.Popen(["open", folder_path])
        else:
            subprocess.Popen(["xdg-open", folder_path])
    except FileNotFoundError as exc:
        logging.error("Folder opener command not found when opening %s: %s", folder_path, exc)
    except Exception as exc:
        # Log but don't crash the UI
        logging.error("Failed to open folder %s: %s", folder_path, exc)


def open_text_file(file_path: str) -> None:
    """Create a missing class list, then ask the OS to open it without replacing its contents."""
    if not file_path.strip():
        raise ValueError("请填写类别表文件路径")
    path = Path(file_path).expanduser().absolute()
    if path.suffix.lower() not in {".txt", ".names"}:
        raise ValueError("类别表文件必须使用 .txt 或 .names 扩展名")
    if path.is_dir():
        raise ValueError("类别表路径指向目录，请填写文件路径")
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8"):
            pass
    except FileExistsError:
        if not path.is_file():
            raise ValueError("类别表路径指向目录，请填写文件路径") from None
    if is_windows():
        os.startfile(str(path))
    else:
        # This launches the user's editor; it intentionally outlives the calling worker.
        subprocess.Popen(["open" if is_macos() else "xdg-open", str(path)], shell=False)
