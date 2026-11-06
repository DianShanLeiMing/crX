# crX

Python 3.12 only. Use uv and the project's `.venv`; never install packages globally.
This is a screen-recognition state machine, not a game API client.

- Entry point: `uv run python -m crX`.
- GUI in `crX/interface`; independent gameplay process in `crX/bot/worker.py`.
- Queue carries session stats; Event and OS termination stop the worker.
- `bot/states.py` owns job order, RuntimeContext and bounded recovery progress.
- Supported adapters: MuMu on Windows, generic ADB on available desktop platforms.
- New I/O uses list arguments, shell=False, validated targets, bounded timeouts.
- Screenshots, rule matching, input coordinates, datasets and recordings use native 720x1280 (9:16) / 320 DPI, in BGR. Never downscale live frames or multiply input coordinates. Native hand artwork is 108x132; count all pixels and normalize histogram totals to preserve the 4x16 fingerprint schema. REFERENCE_SCREEN_SIZE 360x640 and 419x633 are read-only historical sample geometries, not live rule standards. See docs/screen-720x1280.md for migrated template provenance and verification limits.
- Named screen coordinates belong in `bot/coords.py` (card placement profiles in `bot/card_data.py`).
- Pure find helpers belong in `bot/find.py`; pixel predicates in `bot/state_detect.py`.
- Verify screen state before clicks; reuse `bot/nav.py` for navigation.
- Settings use atomic JSON in `%APPDATA%/crX`; do not add pickle migration.
- Do not reintroduce removed progression, clan, multi-account or Discord features.
- Before delivery run ruff check, ruff format --check, ty check and pytest through uv.
- Emulator tests require an installed/logged-in game; never claim they passed without running them.
- Preserve LICENSE and NOTICE.md. Do not commit without lint checks.
