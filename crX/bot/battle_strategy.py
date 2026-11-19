"""Rank the playable hand against one native battle observation."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from crX.bot.card_data import CIRCULAR_SPELL_PROFILES, PLAY_COORDS, TOWER_FINISH_SPELLS, UNRESTRICTED_SPELLS
from crX.bot.coords import DEFENSE_LANE_POINTS, ENEMY_PRINCESS_AIM_POINTS, ENEMY_UNIT_RIVER_Y_RANGE
from crX.bot.spell_targeting import find_circular_spell_target, low_princess_tower_target

if TYPE_CHECKING:
    from collections.abc import Sequence

    from crX.bot.enemy_detect import EnemyUnitLocation, EnemyUnitSnapshot

type Point = tuple[int, int]
type TowerBars = tuple[tuple[int | None, bool], ...]
type PlayPurpose = Literal["defend", "finish", "clear", "advance"]

ADVANCE_ELIXIR = 7
NEAR_FULL_ELIXIR = 9

SPELL_PLACEMENT_GROUPS = frozenset(
    {
        "center_spell",
        "lane_spell",
        "large_spell",
        "poison",
        "reactive_spell",
        "rocket",
        "tornado",
        "graveyard",
        "goblin_barrel",
    }
)
DEFENSIVE_GROUPS = frozenset({"back_support", "bridge_line", "defense_building", "king_lane", "princess", "spirit"})
PRESSURE_GROUPS = frozenset({"bridge_rush", "goblin_drill", "miner", "siege_building"})
PRESSURE_CARDS = frozenset(
    {
        "balloon",
        "elixir_golem",
        "giant",
        "goblin_giant",
        "golem",
        "lava_hound",
        "royal_giant",
        "royal_hogs",
    }
)

PURPOSE_LABELS: dict[PlayPurpose, str] = {
    "defend": "防守",
    "finish": "残血公主塔补刀",
    "clear": "清理聚集目标",
    "advance": "推进",
}
PURPOSE_PRIORITY: dict[PlayPurpose, int] = {"defend": 4, "finish": 3, "clear": 2, "advance": 1}


@dataclass(frozen=True, slots=True)
class HandCard:
    slot: int
    card_id: str
    group: str
    cost: int | None
    preset: Point | None

    @property
    def is_spell(self) -> bool:
        return (
            self.group in SPELL_PLACEMENT_GROUPS or self.card_id in CIRCULAR_SPELL_PROFILES or self.card_id == "clone"
        )


@dataclass(frozen=True, slots=True)
class PlayOption:
    card: HandCard
    point: Point
    purpose: PlayPurpose
    crossed_pressure: int = 0
    pressure: int = 0
    covered: int = 0
    tower_fill: int | None = None
    advances_weak_lane: bool = False
    pressure_card: bool = False

    @property
    def priority(self) -> tuple[int, ...]:
        # Defense values nearby pressure and coverage before cost. Routine pushes
        # prefer a lane with a known live tower and a card built to pressure it.
        known_cost = -self.card.cost if self.card.cost is not None else -11
        return (
            PURPOSE_PRIORITY[self.purpose],
            self.crossed_pressure,
            self.pressure,
            self.covered,
            int(self.advances_weak_lane),
            int(self.pressure_card),
            known_cost,
        )


def unit_pressure(unit: EnemyUnitLocation) -> int:
    if unit.zone == "enemy_half":
        return 0
    if unit.zone == "river":
        return 2
    return 8 + (unit.position[1] - ENEMY_UNIT_RIVER_Y_RANGE[1]) // 64


def _unit_lane(unit: EnemyUnitLocation) -> int:
    if unit.lane == "center":
        return min(range(2), key=lambda side: abs(unit.position[0] - ENEMY_PRINCESS_AIM_POINTS[side][0]))
    return int(unit.lane == "right")


def _placement_for_lane(card: HandCard, lane: int | None) -> Point | None:
    if lane is not None:
        points = PLAY_COORDS.get(card.group, {}).get(("left", "right")[lane])
        if points:
            return points[0]
    return card.preset


def rank_hand_plays(
    hand: Sequence[HandCard],
    snapshot: EnemyUnitSnapshot,
    tower_bars: TowerBars,
    elixir: int,
    *,
    stable_positions: frozenset[Point] | None = None,
) -> list[PlayOption]:
    """Return feasible options; absent stability evidence allows a preview for later revalidation."""
    threat_weights = {unit.position: unit_pressure(unit) for unit in snapshot.units}
    threats = [unit for unit in snapshot.units if threat_weights[unit.position] > 0]
    urgent = max(threats, key=lambda unit: threat_weights[unit.position], default=None)
    known_towers = [
        (fill, side) for side, (fill, seen) in enumerate(tower_bars) if seen and fill is not None and fill > 0
    ]
    weak_lane = min(known_towers)[1] if known_towers else None
    finisher = low_princess_tower_target(tower_bars)
    reserve = 2 if elixir >= NEAR_FULL_ELIXIR else 3
    offensive_budget = elixir - reserve
    stability = frozenset(threat_weights) if stable_positions is None else stable_positions
    options = []

    for card in hand:
        if elixir <= 0 or (card.cost is not None and card.cost > elixir):
            continue
        can_spend_on_offense = card.cost is not None and card.cost <= offensive_budget
        base_id = card.card_id.removeprefix("evo_")
        pressure_card = base_id in PRESSURE_CARDS or card.group in PRESSURE_GROUPS
        if card.is_spell:
            profile = CIRCULAR_SPELL_PROFILES.get(card.card_id)
            if profile is not None:
                target = find_circular_spell_target(
                    snapshot,
                    profile,
                    stable_positions=stability,
                    threat_weights=threat_weights,
                )
                if target is not None and (target.pressure or (urgent is None and can_spend_on_offense)):
                    options.append(
                        PlayOption(
                            card,
                            target.point,
                            "defend" if target.pressure else "clear",
                            crossed_pressure=target.crossed_pressure,
                            pressure=target.pressure,
                            covered=target.count,
                        )
                    )
            if card.card_id in TOWER_FINISH_SPELLS and finisher is not None:
                point, fill = finisher
                options.append(PlayOption(card, point, "finish", tower_fill=fill))
            if card.card_id not in UNRESTRICTED_SPELLS:
                continue
        elif (
            urgent is not None
            and card.group in DEFENSIVE_GROUPS
            and not pressure_card
            and base_id != "elixir_collector"
        ):
            lane = _unit_lane(urgent)
            if card.group == "defense_building":
                point = _placement_for_lane(card, lane)
            else:
                point = min(DEFENSE_LANE_POINTS[lane], key=lambda option: abs(option[1] - urgent.position[1]))
            if point is not None:
                pressure = threat_weights[urgent.position]
                options.append(
                    PlayOption(
                        card,
                        point,
                        "defend",
                        crossed_pressure=pressure if urgent.zone == "friendly_half" else 0,
                        pressure=pressure,
                        covered=1,
                    )
                )
            continue

        if urgent is None and elixir >= ADVANCE_ELIXIR and can_spend_on_offense:
            point = _placement_for_lane(card, weak_lane)
            if point is not None:
                options.append(
                    PlayOption(
                        card,
                        point,
                        "advance",
                        advances_weak_lane=weak_lane is not None,
                        pressure_card=pressure_card,
                    )
                )
    return options
