"""Per-battle observations and diagnostics for visible enemy unit markers."""

from __future__ import annotations

import time
from collections import Counter
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from crX.bot.coords import ENEMY_UNIT_CENTER_LANE_X_RANGE, ENEMY_UNIT_RIVER_Y_RANGE
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

type EnemyLane = Literal["left", "center", "right"]
type EnemyZone = Literal["enemy_half", "river", "friendly_half"]

LANE_LABELS: dict[EnemyLane, str] = {"left": "左路", "center": "中路", "right": "右路"}
ZONE_LABELS: dict[EnemyZone, str] = {"enemy_half": "敌方半场", "river": "河道", "friendly_half": "我方半场"}


@dataclass(frozen=True, slots=True)
class EnemyUnitLocation:
    """Location of the level marker in the original frame."""

    position: tuple[int, int]
    lane: EnemyLane
    zone: EnemyZone


@dataclass(frozen=True, slots=True)
class EnemyUnitSnapshot:
    """Confirmed locations from a single battle frame, timestamped with time.monotonic()."""

    sampled_at: float
    units: tuple[EnemyUnitLocation, ...]


def _locate_unit(marker: EnemyUnitMarker) -> EnemyUnitLocation:
    x, y = marker.center
    center_left, center_right = ENEMY_UNIT_CENTER_LANE_X_RANGE
    if x < center_left:
        lane: EnemyLane = "left"
    elif x < center_right:
        lane = "center"
    else:
        lane = "right"

    river_top, river_bottom = ENEMY_UNIT_RIVER_Y_RANGE
    if y < river_top:
        zone: EnemyZone = "enemy_half"
    elif y < river_bottom:
        zone = "river"
    else:
        zone = "friendly_half"
    return EnemyUnitLocation((x, y), lane, zone)


def _format_locations(units: tuple[EnemyUnitLocation, ...], groups: Counter[tuple[EnemyLane, EnemyZone]]) -> str:
    if not units:
        return "当前无已确认标记"

    summary = []
    for lane, label in LANE_LABELS.items():
        zones = [(zone_label, groups[lane, zone]) for zone, zone_label in ZONE_LABELS.items()]
        total = sum(count for _, count in zones)
        breakdown = "、".join(f"{zone_label} {count}" for zone_label, count in zones if count)
        summary.append(f"{label} {total}（{breakdown}）" if breakdown else f"{label} {total}")
    positions = "、".join(f"({x}, {y})" for x, y in (unit.position for unit in units))
    return " | ".join([f"{len(units)} 个", *summary, positions])


class EnemyUnitMonitor:
    def __init__(self, logger: Logger):
        self.logger = logger
        self.markers: tuple[EnemyUnitMarker, ...] = ()
        self.snapshot: EnemyUnitSnapshot | None = None
        self._previous: tuple[EnemyUnitMarker, ...] = ()
        self._last_sample = float("-inf")
        self._last_log = float("-inf")
        self._reported_groups: Counter[tuple[EnemyLane, EnemyZone]] = Counter()

    def observe(self, image: np.ndarray | None, *, force: bool = False) -> None:
        now = time.monotonic()
        elapsed = now - self._last_sample
        if not force and elapsed < SAMPLE_INTERVAL_S:
            return
        self._last_sample = now

        if not image_is_in_battle(image):
            self._previous = ()
            self.markers = ()
            self.snapshot = None
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
        units = tuple(_locate_unit(marker) for marker in self.markers)
        self.snapshot = EnemyUnitSnapshot(now, units)
        groups = Counter((unit.lane, unit.zone) for unit in units)
        changed = groups != self._reported_groups
        if changed or (units and now - self._last_log >= LOG_INTERVAL_S):
            self.logger.log_recognition(ENEMY_UNIT_LOG_SOURCE, _format_locations(units, groups), persist=changed)
            self._reported_groups = groups
            self._last_log = now
