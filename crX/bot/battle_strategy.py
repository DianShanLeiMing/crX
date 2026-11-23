"""Rank the playable hand against one native battle observation."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import dist
from typing import TYPE_CHECKING, Literal

from crX.bot.card_data import (
    CIRCULAR_SPELL_PROFILES,
    PLAY_COORDS,
    RANGED_SUPPORT_CARDS,
    TOWER_FINISH_SPELLS,
    UNRESTRICTED_SPELLS,
)
from crX.bot.coords import (
    DEFENSE_LANE_POINTS,
    DEFENSE_SUPPORT_LANE_POINTS,
    ENEMY_PRINCESS_AIM_POINTS,
    ENEMY_UNIT_RIVER_Y_RANGE,
)
from crX.bot.spell_targeting import find_circular_spell_target, low_princess_tower_target

if TYPE_CHECKING:
    from collections.abc import Sequence

    from crX.bot.enemy_detect import EnemyUnitLocation, EnemyUnitSnapshot

type Point = tuple[int, int]
type TowerBars = tuple[tuple[int | None, bool], ...]
type PlayPurpose = Literal["defend", "finish", "clear", "advance"]

ADVANCE_ELIXIR = 7
NEAR_FULL_ELIXIR = 9
# In the paired live logs, the HUD sometimes took nearly 0.8 s to show the debit.
ELIXIR_SETTLE_S = 0.85
DEFENSE_FOLLOW_UP_S = 1.5
DEFENSE_FOLLOW_UP_RADIUS_PX = 96
DEFENSE_SUPPORT_SPACING_PX = 64
DEFENSE_OBSERVE_S = 0.6
# A held card must outrank a rotating trio in the other three hand slots.
MAX_WAITING_BONUS = 3

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

    @property
    def is_support(self) -> bool:
        return self.card_id.removeprefix("evo_").removeprefix("hero_") in RANGED_SUPPORT_CARDS


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
    role_fit: int = 0
    waiting_bonus: int = 0
    threat_lane: int | None = None
    lane_pressure: int = 0

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
            self.role_fit + self.waiting_bonus,
            int(self.advances_weak_lane),
            int(self.pressure_card),
            known_cost,
        )


@dataclass(slots=True)
class BattlePlayState:
    """Keep bounded hand preferences, a push target and recent deployment evidence."""

    elixir_remaining: int = 0
    elixir_settles_at: float = 0.0
    pending_card: HandCard | None = None
    settlement_reading: tuple[int, str] | None = None
    defender: PlayOption | None = None
    defender_until: float = 0.0
    hand_waits: dict[int, tuple[str, int]] = field(default_factory=dict)
    advance_target: HandCard | None = None

    def elixir_limit(self, now: float) -> int | None:
        return self.elixir_remaining if now < self.elixir_settles_at else None

    def recent_defender(self, now: float) -> Point | None:
        return self.defender.point if self.defender is not None and now < self.defender_until else None

    def observe_hand(self, hand: Sequence[HandCard]) -> None:
        # Unaffordable slots are absent from this observation. Retain their age
        # until a replacement is recognized or a play is sent from that slot.
        for card in hand:
            previous = self.hand_waits.get(card.slot)
            if previous is None or previous[0] != card.card_id:
                self.hand_waits[card.slot] = (card.card_id, 0)

    def waiting_bonus(self, card: HandCard) -> int:
        previous = self.hand_waits.get(card.slot)
        return previous[1] if previous is not None and previous[0] == card.card_id and not card.is_spell else 0

    def advance_elixir(self) -> int:
        return _advance_elixir(self.advance_target.cost) if self.advance_target is not None else ADVANCE_ELIXIR

    def observing_defense(self, snapshot: EnemyUnitSnapshot) -> bool:
        last = self.defender
        if last is None or snapshot.sampled_at >= self.defender_until - DEFENSE_FOLLOW_UP_S + DEFENSE_OBSERVE_S:
            return False
        threats = [unit for unit in snapshot.units if unit_pressure(unit)]
        return (
            bool(threats)
            and all(
                _unit_lane(unit) == last.threat_lane and unit.position[1] < DEFENSE_LANE_POINTS[_unit_lane(unit)][1][1]
                for unit in threats
            )
            and sum(unit_pressure(unit) for unit in threats) <= last.lane_pressure
        )

    def record_play(self, play: PlayOption, available_elixir: int, now: float) -> None:
        cost = play.card.cost
        self.elixir_remaining = max(0, available_elixir - (cost if cost is not None else available_elixir))
        self.elixir_settles_at = now + ELIXIR_SETTLE_S
        self.pending_card = play.card
        self.settlement_reading = None
        self.hand_waits.pop(play.card.slot, None)
        for slot, (card_id, skipped) in self.hand_waits.items():
            self.hand_waits[slot] = (card_id, min(skipped + 1, MAX_WAITING_BONUS))
        self.advance_target = None
        if play.purpose == "defend" and not play.card.is_spell:
            self.defender = play
            self.defender_until = now + DEFENSE_FOLLOW_UP_S

    def observe_settlement(self, elixir: int, replacement: str | None) -> bool:
        """Release the ceiling after two agreeing frames with a cleared or replaced slot."""
        if (
            self.pending_card is None
            or replacement is None
            or replacement == self.pending_card.card_id
            or elixir != self.elixir_remaining
        ):
            self.settlement_reading = None
            return False
        reading = (elixir, replacement)
        if reading != self.settlement_reading:
            self.settlement_reading = reading
            return False
        self.elixir_settles_at = 0.0
        self.pending_card = None
        self.settlement_reading = None
        return True


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


def _advance_elixir(cost: int | None) -> int:
    """Minimum reachable budget with the existing two/three-elixir reserve."""
    return next(
        (
            amount
            for amount in range(ADVANCE_ELIXIR, 11)
            if cost is not None and cost <= amount - (2 if amount >= NEAR_FULL_ELIXIR else 3)
        ),
        11,
    )


def _defense_point(card: HandCard, lane: int, threat_y: int, recent_defender: Point | None) -> Point | None:
    if card.group == "defense_building":
        return _placement_for_lane(card, lane)
    if not card.is_support:
        return min(DEFENSE_LANE_POINTS[lane], key=lambda point: abs(point[1] - threat_y))
    return min(
        DEFENSE_SUPPORT_LANE_POINTS[lane],
        key=lambda point: (
            recent_defender is not None and dist(point, recent_defender) < DEFENSE_SUPPORT_SPACING_PX,
            point[1] < threat_y + DEFENSE_SUPPORT_SPACING_PX,
            abs(point[1] - threat_y),
        ),
    )


def rank_hand_plays(
    hand: Sequence[HandCard],
    snapshot: EnemyUnitSnapshot,
    tower_bars: TowerBars,
    elixir: int,
    *,
    stable_positions: frozenset[Point] | None = None,
    recent_defender: Point | None = None,
    play_state: BattlePlayState | None = None,
) -> list[PlayOption]:
    """Return feasible options; absent stability evidence allows a preview for later revalidation."""
    threat_weights = {unit.position: unit_pressure(unit) for unit in snapshot.units}
    threats = [unit for unit in snapshot.units if threat_weights[unit.position] > 0]
    urgent = max(threats, key=lambda unit: threat_weights[unit.position], default=None)
    observe_defense = False
    if play_state is not None:
        play_state.observe_hand(hand)
        if urgent is not None:
            play_state.advance_target = None
        observe_defense = play_state.observing_defense(snapshot)
        recent_defender = play_state.recent_defender(snapshot.sampled_at)
    known_towers = [
        (fill, side) for side, (fill, seen) in enumerate(tower_bars) if seen and fill is not None and fill > 0
    ]
    weak_lane = min(known_towers)[1] if known_towers else None
    finisher = low_princess_tower_target(tower_bars)
    reserve = 2 if elixir >= NEAR_FULL_ELIXIR else 3
    offensive_budget = elixir - reserve
    stability = frozenset(threat_weights) if stable_positions is None else stable_positions
    options = []
    advances = []

    for card in hand:
        if elixir <= 0 or card.cost is None or card.cost > elixir:
            continue
        can_spend_on_offense = card.cost <= offensive_budget
        base_id = card.card_id.removeprefix("evo_")
        pressure_card = base_id in PRESSURE_CARDS or card.group in PRESSURE_GROUPS
        waiting_bonus = play_state.waiting_bonus(card) if play_state is not None else 0
        if card.is_spell:
            profile = CIRCULAR_SPELL_PROFILES.get(card.card_id)
            if profile is not None:
                target = find_circular_spell_target(
                    snapshot,
                    profile,
                    stable_positions=stability,
                    threat_weights=threat_weights,
                )
                if (
                    target is not None
                    and not observe_defense
                    and (target.pressure or (urgent is None and can_spend_on_offense))
                ):
                    options.append(
                        PlayOption(
                            card,
                            target.point,
                            "defend" if target.pressure else "clear",
                            crossed_pressure=target.crossed_pressure,
                            pressure=target.pressure,
                            covered=target.count,
                            role_fit=int(bool(target.pressure)),
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
            and not observe_defense
        ):
            lane = _unit_lane(urgent)
            point = _defense_point(card, lane, urgent.position[1], recent_defender)
            front_present = (
                recent_defender is not None
                and recent_defender in DEFENSE_LANE_POINTS[lane]
                and abs(recent_defender[1] - urgent.position[1]) <= DEFENSE_FOLLOW_UP_RADIUS_PX
            )
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
                        role_fit=int(card.is_support == front_present),
                        threat_lane=lane,
                        lane_pressure=sum(
                            threat_weights[unit.position] for unit in threats if _unit_lane(unit) == lane
                        ),
                    )
                )
            continue

        if urgent is None and elixir >= ADVANCE_ELIXIR and _advance_elixir(card.cost) <= 10:
            point = _placement_for_lane(card, weak_lane)
            if point is not None:
                advances.append(
                    PlayOption(
                        card,
                        point,
                        "advance",
                        advances_weak_lane=weak_lane is not None,
                        pressure_card=pressure_card,
                        waiting_bonus=waiting_bonus,
                    )
                )
    if play_state is not None:
        saved = play_state.advance_target
        target = next(
            (
                play
                for play in advances
                if saved is not None and (play.card.slot, play.card.card_id) == (saved.slot, saved.card_id)
            ),
            None,
        )
        if target is None:
            target = max(advances, key=lambda play: play.priority, default=None)
        play_state.advance_target = None
        if target is not None:
            if target.card.cost is not None and target.card.cost <= offensive_budget:
                options.extend(
                    [target]
                    if saved is not None
                    else (
                        play for play in advances if play.card.cost is not None and play.card.cost <= offensive_budget
                    )
                )
            else:
                play_state.advance_target = target.card
    else:
        options.extend(play for play in advances if play.card.cost is not None and play.card.cost <= offensive_budget)
    return options
