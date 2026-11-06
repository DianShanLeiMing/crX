# Gameplay

`states.py` sequences shop → mode → deck → start → fight → end.
RuntimeContext owns mode/deck progress. Shop-only operation waits between hourly checks.
`worker.py` owns recovery counts; skipped states must not reset failed-recovery counters.
Use named coordinates and validate the current screen before acting.
Keep detection helpers in find.py/state_detect.py and navigation in nav.py.
Card fingerprints live in card_builtin_references.json and card_reference_library.json; names/play profiles stay in card_data.py and algorithms in card_detection.py.
Preserve BGR channel order. Device/capture/recording/rules/input all use native 720x1280 at 320 DPI. No full-frame or card-art downscaling for recognition. Hand artwork is 108x132; normalize color histogram totals (not images) to the existing fingerprint schema. Old 360x640 and 419x633 samples use historical crops only for review and library compatibility.
No new progression/clan/account features. Do not broaden gameplay heuristics without a request.
