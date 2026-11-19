"""Pure target selection for spells using the latest confirmed enemy snapshot."""

from __future__ import annotations

from collections import Counter
from itertools import combinations
from typing import TYPE_CHECKING

from crX.bot.coords import (
    ENEMY_PRINCESS_AIM_POINTS,
    ENEMY_PRINCESS_BAR_WIDTH,
    ENEMY_PRINCESS_TAP_REGIONS_LTRB,
    PLAYABLE_PLAY_REGION_LTRB,
)

if TYPE_CHECKING:
    from crX.bot.card_data import CircularSpellProfile
    from crX.bot.enemy_detect import EnemyUnitSnapshot

MIN_STABILITY_INTERVAL_S = 0.2
MAX_STABILITY_INTERVAL_S = 1.0
STABLE_SPEED_PX_PER_S = 14
MARKER_MATCH_RADIUS_PX = 24
TOWER_FINISH_PERCENT = 10


def low_princess_tower_target(
    bars: tuple[tuple[int | None, bool], ...],
) -> tuple[tuple[int, int], int] | None:
    """Choose the weakest tower with a readable filled bar below the threshold."""
    candidates = [
        (fill, point)
        for (fill, track_seen), point in zip(bars, ENEMY_PRINCESS_AIM_POINTS, strict=True)
        if track_seen and fill is not None and 0 < 100 * fill < ENEMY_PRINCESS_BAR_WIDTH * TOWER_FINISH_PERCENT
    ]
    if not candidates:
        return None
    fill, point = min(candidates, key=lambda entry: entry[0])
    return point, fill


def stable_marker_positions(
    positions: tuple[tuple[int, int], ...],
    reference_positions: tuple[tuple[int, int], ...],
    elapsed_s: float,
) -> frozenset[tuple[int, int]]:
    """Find markers with little motion and an unambiguous match in both frames."""
    if not MIN_STABILITY_INTERVAL_S <= elapsed_s <= MAX_STABILITY_INTERVAL_S:
        return frozenset()

    radius_sq = MARKER_MATCH_RADIUS_PX**2
    speed_limit_sq = (STABLE_SPEED_PX_PER_S * elapsed_s) ** 2
    matches = [
        (current, previous, distance_sq)
        for current in positions
        for previous in reference_positions
        if (distance_sq := (current[0] - previous[0]) ** 2 + (current[1] - previous[1]) ** 2) <= radius_sq
    ]
    by_current = Counter(current for current, _, _ in matches)
    by_previous = Counter(previous for _, previous, _ in matches)
    return frozenset(
        current
        for current, previous, distance_sq in matches
        if by_current[current] == by_previous[previous] == 1 and distance_sq <= speed_limit_sq
    )


def choose_circular_spell_target(
    snapshot: EnemyUnitSnapshot,
    profile: CircularSpellProfile,
    *,
    stable_positions: frozenset[tuple[int, int]] = frozenset(),
) -> tuple[tuple[int, int], int] | None:
    """Find a worthwhile unit target outside the tower platforms."""
    left, top, right, bottom = PLAYABLE_PLAY_REGION_LTRB
    targets = [
        (x, y + profile.marker_offset_y_px, unit.zone != "enemy_half")
        for unit in snapshot.units
        for x, y in (unit.position,)
        if left <= x <= right
        and top <= y + profile.marker_offset_y_px <= bottom
        and (not profile.requires_stable_markers or unit.position in stable_positions)
    ]
    centers = [(x, y) for x, y, _ in targets]
    radius_sq = profile.radius_px**2
    midpoints = [
        ((ax + bx) // 2, (ay + by) // 2)
        for (ax, ay), (bx, by) in combinations(centers, 2)
        if (ax - bx) ** 2 + (ay - by) ** 2 <= 4 * radius_sq
    ]

    def coverage(point: tuple[int, int]) -> tuple[int, int, int]:
        x, y = point
        distances = [((tx - x) ** 2 + (ty - y) ** 2, near_our_side) for tx, ty, near_our_side in targets]
        covered = [(distance, near_our_side) for distance, near_our_side in distances if distance <= radius_sq]
        return (
            len(covered),
            sum(near_our_side for _, near_our_side in covered),
            -sum(distance for distance, _ in covered),
        )

    points = (
        (x, y)
        for x, y in (*centers, *midpoints)
        if not any(
            left <= x < right and top <= y < bottom for left, top, right, bottom in ENEMY_PRINCESS_TAP_REGIONS_LTRB
        )
    )
    defensive_minimum = profile.min_defensive_targets
    scored_points = ((coverage(point), point) for point in points)
    eligible_points = (
        (score, point)
        for score, point in scored_points
        if score[0] >= profile.min_targets or (defensive_minimum is not None and score[1] >= defensive_minimum)
    )
    best = max(eligible_points, key=lambda entry: entry[0], default=None)
    if best is None:
        return None
    score, point = best
    return point, score[0]
