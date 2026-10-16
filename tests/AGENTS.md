# Tests

Run offline tests with `uv run pytest -q`. Tests marked emulator are excluded by default.
Use `--integration --emulator mumu` or `--emulator adb --adb-serial ...` for real-device tests.
Integration tests launch the game and execute navigation/deck/battle operations.
The registry and adapters must remain testable with fakes; do not require a local MuMu installation for offline tests.
Regression coverage includes BGR card fingerprints, worker termination, recording/archival,
job reduction, settings isolation, dynamic MuMu ports and shell-free argument execution.
