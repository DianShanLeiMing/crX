"""Independent, per-battle diagnostics for visible enemy unit markers."""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from crX.bot.find import find_enemy_unit_markers
from crX.bot.state_detect import image_is_in_battle
from crX.utils.logger import ENEMY_UNIT_LOG_SOURCE

if TYPE_CHECKING:
    import numpy as np

    from crX.bot.find import EnemyUnitMarker
    from crX.utils.logger import Logger

SAMPLE_INTERVAL_S = 0.2
CONFIRM_WINDOW_S = 1.0
LOG_INTERVAL_S = 1.0


class EnemyUnitMonitor:
    def __init__(self, logger: Logger):
        self.logger = logger
        self.markers: tuple[EnemyUnitMarker, ...] = ()
        self._previous: tuple[EnemyUnitMarker, ...] = ()
        self._last_sample = float("-inf")
        self._last_log = float("-inf")
        self._reported_count = 0

    def observe(self, image: np.ndarray | None) -> None:
        now = time.monotonic()
        elapsed = now - self._last_sample
        if elapsed < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now

        if not image_is_in_battle(image):
            self._previous = ()
            self.markers = ()
            return

        current = find_enemy_unit_markers(image)
        previous = self._previous if elapsed <= CONFIRM_WINDOW_S else ()
        # Confirm nearby marks one-to-one; a single prior mark can't confirm a crowd.
        max_distance = 8 + 120 * min(elapsed, CONFIRM_WINDOW_S)
        pairs = []
        for new_index, marker in enumerate(current):
            x, y = marker.center
            for old_index, old in enumerate(previous):
                old_x, old_y = old.center
                distance_sq = (x - old_x) ** 2 + (y - old_y) ** 2
                if distance_sq <= max_distance**2:
                    pairs.append((distance_sq, new_index, old_index))

        matched_current = set()
        matched_previous = set()
        for _, new_index, old_index in sorted(pairs):
            if new_index not in matched_current and old_index not in matched_previous:
                matched_current.add(new_index)
                matched_previous.add(old_index)

        self._previous = current
        self.markers = tuple(
            marker for index, marker in enumerate(current) if marker.has_health_bar or index in matched_current
        )
        count = len(self.markers)
        changed = count != self._reported_count
        if changed or (count and now - self._last_log >= LOG_INTERVAL_S):
            positions = "、".join(f"({x}, {y})" for x, y in (marker.center for marker in self.markers))
            message = f"{count} 个 | {positions}" if count else "当前无已确认标记"
            self.logger.log_recognition(ENEMY_UNIT_LOG_SOURCE, message, persist=changed)
            self._reported_count = count
            self._last_log = now
