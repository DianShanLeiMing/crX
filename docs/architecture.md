# Architecture

```text
crX/__main__.py             GUI lifecycle, settings validation, worker/device task dispatch
  interface/                Jobs / Device / Stats / Settings tabs
  bot/worker.py             Separate process, bounded recovery, stop helper
    bot/states.py           Serial job state machine + RuntimeContext
      shop_daily_state.py   Hourly daily free-offer check
      deck.py               Randomize / cycle existing decks
      fight.py              Existing heuristic or random strategy, result handling
      nav.py                Main / cards / shop navigation and blocking-popup dismissal
      card_detection.py     Card recognition and placement algorithms
      card_data.py          Upstream fingerprints, groups and placement data
      state_detect.py       Screen/pixel predicates
      find.py               Coordinate finding
      recorder.py           Frames, actions, result; target 3 fps
      recording_archiver.py Crash-safe ZIP bundling of oldest packs
  detection/                OpenCV template matching + reference PNGs
  emulators/                Base → shared ADB → generic ADB / MuMu
  utils/                    Atomic JSON, logging, platform directories, folder opening
```

The UI and worker communicate via Queue (stats) and Event (shutdown).
Device calls run on a background thread in the UI; gameplay runs in a child process.
Stopping uses OS-level process termination; incomplete recordings can remain after forced stops.

The MuMu layer is intentionally small. It discovers MuMuManager, queries and launches exactly
one existing instance, then delegates all screen I/O to ADB. No game policy lives in the adapter.
The generic ADB layer configures fixed capture geometry, launches the selected installed game
and verifies screen readiness. Business policy does not know about MuMu command-line details.

Only main/card/shop navigation remains. Dismissing a blocking clan-voyage popup is retained
as navigation recovery; it does not perform clan tasks or clan-war gameplay.
