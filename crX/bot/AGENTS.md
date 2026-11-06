# Gameplay

`states.py` sequences shop → mode → deck → start → fight → end.
RuntimeContext owns mode/deck progress. Shop-only operation waits between hourly checks.
`worker.py` owns recovery counts; skipped states must not reset failed-recovery counters.
Use named coordinates and validate the current screen before acting.
Keep detection helpers in find.py/state_detect.py and navigation in nav.py.
Card fingerprints live in card_builtin_references.json and card_reference_library.json; names/play profiles stay in card_data.py and algorithms in card_detection.py.
Preserve BGR channel order. Native device/capture/recording geometry is 720x1280 at 320 DPI; rules and card fingerprints operate on the adapter-normalized 360x640 reference image with 54x66 hand artwork; old 419x633 samples use their recorded legacy crop, never for live input.
No new progression/clan/account features. Do not broaden gameplay heuristics without a request.
