# Gameplay

`states.py` sequences shop → mode → deck → start → fight → end.
RuntimeContext owns mode/deck progress. Shop-only operation waits between hourly checks.
`worker.py` owns recovery counts; skipped states must not reset failed-recovery counters.
Use named coordinates and validate the current screen before acting.
Keep detection helpers in find.py/state_detect.py and navigation in nav.py.
Card reference data is in card_data.py; algorithms remain in card_detection.py.
Preserve BGR channel order and fixed 419x633 geometry.
No new progression/clan/account features. Do not broaden gameplay heuristics without a request.
