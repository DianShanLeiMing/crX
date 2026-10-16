"""Platform detection and OS-specific directory helpers."""

import os
import re
import shutil
import sys
from enum import StrEnum, auto
from pathlib import Path

# A recorded fight pack is a directory named "%Y%m%d-%H%M%S-<6 hex>" (see
# recorder._new_slug). Clearing only matches this pattern so that pointing the
# recordings folder at a user-chosen directory never deletes unrelated content.
_PACK_SLUG_RE = re.compile(r"^\d{8}-\d{6}-[0-9a-f]{6}$")


class Platform(StrEnum):
    """Supported platforms."""

    WINDOWS = auto()
    MACOS = auto()
    LINUX = auto()


def get_platform() -> Platform:
    """Detect the current platform."""
    if sys.platform == "darwin":
        return Platform.MACOS
    elif sys.platform.startswith("linux"):
        return Platform.LINUX
    return Platform.WINDOWS


def get_app_data_dir(app_name: str) -> str:
    """Return OS-appropriate directory for application data/cache."""
    platform = get_platform()
    if platform == Platform.MACOS:
        return os.path.join(os.path.expanduser("~/Library/Application Support"), app_name)
    elif platform == Platform.LINUX:
        return os.path.join(os.environ.get("XDG_DATA_HOME", os.path.expanduser("~/.local/share")), app_name)
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), app_name)


def get_log_dir(app_name: str) -> str:
    """Return OS-appropriate directory for log files."""
    platform = get_platform()
    if platform == Platform.MACOS:
        return os.path.join(os.path.expanduser("~/Library/Logs"), app_name)
    elif platform == Platform.LINUX:
        return os.path.join(os.environ.get("XDG_STATE_HOME", os.path.expanduser("~/.local/state")), app_name, "logs")
    return os.path.join(os.environ.get("APPDATA", os.path.expanduser("~")), app_name, "logs")


def get_recordings_dir(app_name: str = "crX", custom_path: str | None = None) -> str:
    """Return the single source-of-truth directory for recorded fight packs.

    Args:
        app_name: Application name for default path construction.
        custom_path: Optional user-specified recording folder path.

    Returns:
        The custom path if provided and non-empty, otherwise the default
        OS-appropriate recordings directory.
    """
    if custom_path and custom_path.strip():
        return custom_path.strip()
    return os.path.join(get_app_data_dir(app_name), "recordings")


def get_recording_zips_dir(app_name: str = "crX", custom_path: str | None = None) -> str:
    """Return the directory that holds archived (zipped) recording packs.

    A ``zips`` subfolder *inside* the recordings folder, so archives stay under
    the same (already-validated, user-chosen or default) directory as the loose
    packs -- the default layout is ``<app-data>/recordings/zips``. The ``zips``
    name never matches the pack slug pattern, so pack scans and the "Clear
    recordings" pack loop ignore it.
    """
    return os.path.join(get_recordings_dir(app_name, custom_path), "zips")


def validate_recordings_path(custom_path: str | None) -> tuple[bool, str]:
    """Validate a user-supplied recordings folder. Returns (is_ok, message).

    Empty/whitespace is valid -- it falls back to the default app-data location.
    A non-empty path is valid when recordings can be written there: either it is
    an existing writable directory, or it does not exist yet but its nearest
    existing ancestor is a writable directory (so it can be created on demand).
    The message is suitable for display next to the folder field.
    """
    if not custom_path or not custom_path.strip():
        return (True, "使用默认录制目录。")

    path = custom_path.strip()
    if os.path.exists(path):
        if not os.path.isdir(path):
            return (False, "该路径指向文件，请选择目录。")
        if not os.access(path, os.W_OK):
            return (False, "目录已存在，但没有写入权限。")
        return (True, "目录有效，可以写入。")

    # Path does not exist yet: walk up to the nearest existing ancestor and
    # check whether we could create the folder there.
    ancestor = os.path.dirname(os.path.abspath(path))
    while ancestor and not os.path.exists(ancestor):
        parent = os.path.dirname(ancestor)
        if parent == ancestor:  # reached a filesystem root that doesn't exist
            break
        ancestor = parent
    if not ancestor or not os.path.isdir(ancestor) or not os.access(ancestor, os.W_OK):
        return (False, "目录不存在，且无法在此位置创建。")
    return (True, "开始录制时将自动创建该目录。")


# Absolute free-space floor (in bytes) below which the UI warns the user that the
# recordings drive is running low. Distinct from recorder.MIN_FREE_DISK_FRACTION,
# which is a percentage gate that stops new recordings.
LOW_DISK_SPACE_BYTES = 10 * 1024**3  # 10 GB


def recordings_drive_free_bytes(custom_path: str | None = None) -> int | None:
    """Free bytes on the drive that holds the recordings folder.

    Resolves the recordings directory (default or custom) and walks up to the
    nearest existing ancestor to query the drive. Returns None if the drive
    can't be determined (e.g. an invalid path on a missing root).
    """
    path = get_recordings_dir(custom_path=custom_path)
    while path and not os.path.exists(path):
        parent = os.path.dirname(path)
        if parent == path:
            return None
        path = parent
    try:
        return shutil.disk_usage(path).free
    except OSError:
        return None


def _dir_size(path: str) -> int:
    """Best-effort total size in bytes of every file under path."""
    total = 0
    for root, _dirs, files in os.walk(path):
        for name in files:
            try:
                total += os.path.getsize(os.path.join(root, name))
            except OSError:
                continue
    return total


def recordings_total_bytes(app_name: str = "crX", custom_path: str | None = None) -> int:
    """Total bytes used by recorded fight packs in the recordings folder.

    Counts only slug-named pack directories (the same set "Clear recordings"
    would remove), so unrelated content in a custom folder is ignored. Returns
    0 if the folder doesn't exist yet.
    """
    rec_dir = get_recordings_dir(app_name, custom_path)
    if not os.path.isdir(rec_dir):
        return 0
    total = 0
    for name in os.listdir(rec_dir):
        pack = os.path.join(rec_dir, name)
        if os.path.isdir(pack) and _PACK_SLUG_RE.match(name):
            total += _dir_size(pack)
    return total


def recording_zips_total_bytes(app_name: str = "crX", custom_path: str | None = None) -> int:
    """Total bytes used by archived (``.zip``) recording packs in the zips folder.

    Counts only ``.zip`` files (the archives produced by recording_archiver), so
    unrelated content in the folder is ignored. Returns 0 if it doesn't exist yet.
    """
    zips_dir = get_recording_zips_dir(app_name, custom_path)
    if not os.path.isdir(zips_dir):
        return 0
    total = 0
    for name in os.listdir(zips_dir):
        path = os.path.join(zips_dir, name)
        if os.path.isfile(path) and name.lower().endswith(".zip"):
            try:
                total += os.path.getsize(path)
            except OSError:
                continue
    return total


def recordings_total_bytes_all_locations(app_name: str = "crX", custom_path: str | None = None) -> int:
    """Total recording bytes (loose packs + zip archives) across all locations.

    Always counts the default app-data recordings folder and its zips folder;
    when a custom folder is set and resolves to a different directory, its packs
    and zips are added too. Locations are de-duplicated by normalized path so a
    custom path equal to the default isn't counted twice.
    """
    default_dir = os.path.normcase(os.path.abspath(get_recordings_dir(app_name)))
    total = recordings_total_bytes(app_name) + recording_zips_total_bytes(app_name)
    if custom_path and custom_path.strip():
        custom_dir = os.path.normcase(os.path.abspath(get_recordings_dir(app_name, custom_path)))
        if custom_dir != default_dir:
            total += recordings_total_bytes(app_name, custom_path) + recording_zips_total_bytes(app_name, custom_path)
    return total


def _is_link(path: Path) -> bool:
    return path.is_symlink() or path.is_junction()


def clear_recordings(app_name: str = "crX", custom_path: str | None = None) -> tuple[int, int]:
    """Remove generated packs/archives only; preserve unrelated files and links."""
    root = Path(get_recordings_dir(app_name, custom_path)).resolve()
    if not root.is_dir():
        return 0, 0
    removed, freed = 0, 0
    for pack in root.iterdir():
        if _is_link(pack) or not pack.is_dir() or not _PACK_SLUG_RE.fullmatch(pack.name):
            continue
        if pack.resolve().parent != root:
            continue
        # Linked descendants cannot be treated as recording data.
        if any(_is_link(item) for item in pack.rglob("*")):
            continue
        size = _dir_size(str(pack))
        shutil.rmtree(pack)
        removed += 1
        freed += size
    zips = root / "zips"
    if zips.is_dir() and not _is_link(zips) and zips.resolve().parent == root:
        for archive in zips.iterdir():
            if (
                archive.is_file()
                and not _is_link(archive)
                and re.fullmatch(r"packs-[\w-]+__[\w-]+\.zip(?:\.tmp)?", archive.name)
            ):
                freed += archive.stat().st_size
                archive.unlink()
        if not any(zips.iterdir()):
            zips.rmdir()
    return removed, freed


def is_windows() -> bool:
    """Check if running on Windows."""
    return get_platform() == Platform.WINDOWS


def is_macos() -> bool:
    """Check if running on macOS."""
    return get_platform() == Platform.MACOS


def is_linux() -> bool:
    """Check if running on Linux."""
    return get_platform() == Platform.LINUX


CURRENT_PLATFORM = get_platform()
