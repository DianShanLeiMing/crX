# Live-device suite

Run `uv run pytest -x -s --integration --emulator mumu`.
The selected existing instance must have Clash Royale installed, logged in, tutorial complete and language English.
The fixture calls restart() and requires a detected main menu. Tests may switch existing decks and start battles.
The ordered SUITE covers installation, screenshot contract, main/card/shop navigation, mode selection,
random selection/cycling among existing decks and 1v1/2v2 fights. It does not run during offline validation.
