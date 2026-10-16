import sys
from pathlib import Path

from cx_Freeze import Executable, setup

ROOT_DIR = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT_DIR))

PROJECT_NAME = "crX"
AUTHOR = "Matthew Miglio, Martin Miglio"
DESCRIPTION = "Automated Clash Royale"
KEYWORDS = "clash royale automation"
COPYRIGHT = "2023 Matthew Miglio"
ENTRY_POINT = ROOT_DIR / "crX" / "__main__.py"
ICON_PATH = ROOT_DIR / "assets" / "crX.ico"
GUI = True
UPGRADE_CODE = "{731F2318-5C22-4D86-A227-51CC952EBC71}"


try:
    _version_idx = sys.argv.index("--target-version")
    VERSION = sys.argv[_version_idx + 1]
    del sys.argv[_version_idx : _version_idx + 2]
except (ValueError, IndexError):
    VERSION = "v0.0.0"

version_file = ROOT_DIR / "crX" / "__version__"
if not version_file.exists():
    version_file.touch()
with version_file.open("w", encoding="utf-8") as f:
    f.write(VERSION)


build_exe_options = {
    "excludes": ["test", "setuptools"],
    "packages": ["crX", "ttkbootstrap", "tkinter"],
    "include_files": [
        ROOT_DIR / "LICENSE",
        ROOT_DIR / "NOTICE.md",
        ROOT_DIR / "README.md",
        ROOT_DIR / "assets" / "crX.ico",
        ROOT_DIR / "crX" / "detection" / "reference_images",
        ROOT_DIR / "crX" / "__version__",
    ],
    "include_msvcr": True,
}

bdist_msi_options = {
    "upgrade_code": UPGRADE_CODE,
    "add_to_path": False,
    # cx-freeze 8.4+ removed bdist_msi's target_version, so without these the
    # filename/ProductVersion fall back to pyproject's v0.0.0 placeholder.
    "product_version": VERSION,
    "output_name": f"crX-{VERSION}-win64.msi",
    "initial_target_dir": f"[ProgramFilesFolder]\\{PROJECT_NAME}",
    "summary_data": {
        "author": AUTHOR,
        "comments": DESCRIPTION,
        "keywords": KEYWORDS,
    },
}

exe = Executable(
    script=ENTRY_POINT,
    base="Win32GUI" if GUI else None,
    uac_admin=False,
    shortcut_name=f"{PROJECT_NAME} {VERSION}",
    shortcut_dir="DesktopFolder",
    target_name=f"{PROJECT_NAME}.exe",
    copyright=COPYRIGHT,
    icon=ICON_PATH,
)

setup(
    name=PROJECT_NAME,
    description=DESCRIPTION,
    executables=[exe],
    options={
        "bdist_msi": bdist_msi_options,
        "build_exe": build_exe_options,
    },
)
