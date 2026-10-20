# crX

Python 3.12 only. Use uv and the project's `.venv`; never install packages globally.
This is a screen-recognition state machine, not a game API client.

- Entry point: `uv run python -m crX`.
- GUI in `crX/interface`; independent gameplay process in `crX/bot/worker.py`.
- Queue carries session stats; Event and OS termination stop the worker.
- `bot/states.py` owns job order, RuntimeContext and bounded recovery progress.
- Supported adapters: MuMu on Windows, generic ADB on available desktop platforms.
- New I/O uses list arguments, shell=False, validated targets, bounded timeouts.
- Runtime coordinates and screenshots use 360x640 (9:16) / 160 DPI, in BGR. Historical 419x633 card samples are read-only compatible; card fingerprints keep the 54x66 artwork schema.
- Named screen coordinates belong in `bot/coords.py` (card placement profiles in `bot/card_data.py`).
- Pure find helpers belong in `bot/find.py`; pixel predicates in `bot/state_detect.py`.
- Verify screen state before clicks; reuse `bot/nav.py` for navigation.
- Settings use atomic JSON in `%APPDATA%/crX`; do not add pickle migration.
- Do not reintroduce removed progression, clan, multi-account or Discord features.
- Before delivery run ruff check, ruff format --check, ty check and pytest through uv.
- Emulator tests require an installed/logged-in game; never claim they passed without running them.
- Preserve LICENSE and NOTICE.md. Do not commit without lint checks.
