"""Decision ordering and affordability for the shared battle planner."""

from unittest.mock import Mock

import pytest

from crX.bot import fight
from crX.bot.battle_strategy import BattlePlayState, HandCard, PlayOption, rank_hand_plays
from crX.bot.card_data import get_card_elixir_cost
from crX.bot.card_detection import get_card_group
from crX.bot.coords import (
    DEFENSE_LANE_POINTS,
    DEFENSE_SUPPORT_LANE_POINTS,
    HAND_CARD_TOP_LEFTS,
    HAND_EMPTY_SLOT_SAMPLES,
)
from crX.bot.enemy_detect import EnemyUnitLocation, EnemyUnitMonitor, EnemyUnitSnapshot
from crX.bot.state_detect import ELIXIR_COLOR, ELIXIR_COORDS, read_battle_elixir
from tests.test_enemy_detect import RED, battle_frame, draw_badge, mock_decision_hand

FULL_TOWERS = ((79, True), (79, True))
ARROWS = HandCard(0, "arrows", "large_spell", 3, (204, 292))
ZAP = HandCard(1, "zap", "reactive_spell", 2, (202, 374))
HOG = HandCard(2, "hog", "bridge_rush", 4, (132, 568))


def test_cheaper_spell_wins_when_both_cover_the_same_crossed_threat():
    crossed = EnemyUnitLocation((183, 640), "left", "friendly_half")
    snapshot = EnemyUnitSnapshot(10.0, (crossed,))
    options = rank_hand_plays((ARROWS, HOG, ZAP), snapshot, FULL_TOWERS, 8)
    play = max(options, key=lambda option: option.priority)
    assert play.card == ZAP and play.purpose == "defend"
    assert play.crossed_pressure > 0
    assert all(option.card != HOG for option in options)


def test_spell_aim_prioritizes_crossed_threat_over_a_cluster_at_the_river():
    crossed = EnemyUnitLocation((190, 735), "left", "friendly_half")
    river = tuple(EnemyUnitLocation((x, 510), "right", "river") for x in (410, 430, 450, 470, 490, 510))
    options = rank_hand_plays((ARROWS,), EnemyUnitSnapshot(10.0, (*river, crossed)), FULL_TOWERS, 7)
    assert options[0].point == (190, 767)
    assert options[0].covered == 1


def test_safe_push_uses_the_weaker_enemy_tower_and_leaves_elixir():
    snapshot = EnemyUnitSnapshot(10.0, ())
    towers = ((73, True), (27, True))
    assert rank_hand_plays((HOG,), snapshot, towers, 6) == []
    plan = rank_hand_plays((HOG,), snapshot, towers, 7)
    assert plan[0].point[0] > 400 and plan[0].purpose == "advance"
    assert plan[0].card.cost == 4
    unknown = HandCard(3, "mirror", "center_spell", None, (200, 324))
    assert rank_hand_plays((unknown,), snapshot, towers, 10) == []


def test_defensive_troop_uses_the_deeper_threat_lane_without_advance_reserve():
    knight = HandCard(3, "knight", "bridge_line", 3, (198, 672))
    snapshot = EnemyUnitSnapshot(
        10.0,
        (
            EnemyUnitLocation((190, 610), "left", "friendly_half"),
            EnemyUnitLocation((540, 870), "right", "friendly_half"),
        ),
    )
    assert rank_hand_plays((knight,), snapshot, FULL_TOWERS, 2) == []
    assert rank_hand_plays((knight,), snapshot, FULL_TOWERS, 3)[0].point == DEFENSE_LANE_POINTS[1][-1]


def test_follow_up_support_uses_a_separate_point_on_the_threatened_lane():
    unit = EnemyUnitLocation((146, 638), "left", "friendly_half")
    snapshot = EnemyUnitSnapshot(10.0, (unit,))
    guard = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    wizard = HandCard(1, "electro_wizard", "back_support", 4, (118, 894))
    furnace = HandCard(2, "furnace", "back_support", 4, (118, 894))
    first = rank_hand_plays((guard,), snapshot, FULL_TOWERS, 7)[0]
    state = BattlePlayState()
    state.record_play(first, 7, 10.0)
    support = rank_hand_plays((wizard, furnace), snapshot, FULL_TOWERS, 4, recent_defender=state.recent_defender(10.4))
    assert len(support) == 2
    assert all(option.point in DEFENSE_SUPPORT_LANE_POINTS[0] for option in support)
    opposite = rank_hand_plays((wizard,), snapshot, FULL_TOWERS, 4, recent_defender=DEFENSE_LANE_POINTS[1][0])
    later = rank_hand_plays((wizard,), snapshot, FULL_TOWERS, 4, recent_defender=state.recent_defender(12.0))
    assert opposite[0].point == later[0].point == DEFENSE_SUPPORT_LANE_POINTS[0][0]
    follow_up = rank_hand_plays((furnace,), snapshot, FULL_TOWERS, 4, recent_defender=support[0].point)
    assert follow_up[0].point != support[0].point


def test_consecutive_defense_does_not_spend_elixir_still_visible_from_previous_play(monkeypatch):
    image = battle_frame()
    draw_badge(image, 180, 620, bar_color=RED)
    mock_decision_hand(monkeypatch, image, slots=(0,), elixir=5)
    cards = iter(("electro_wizard", "furnace", "furnace"))
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda *_: (next(cards), (118, 894)))
    emulator = Mock()
    logger = Mock()
    state = BattlePlayState()
    now = 10.0
    monkeypatch.setattr(fight.time, "monotonic", lambda: now)
    strategy = fight.BattleStrategy()
    assert fight.play_a_card(emulator, logger, False, strategy, decision_frame=image, play_state=state)
    assert state.elixir_limit(now) == 1

    now = 10.75
    assert not fight.play_a_card(emulator, logger, False, strategy, decision_frame=image.copy(), play_state=state)
    assert emulator.click.call_count == 2

    now = 11.0
    assert fight.play_a_card(emulator, logger, False, strategy, decision_frame=image.copy(), play_state=state)
    assert emulator.click.call_args.args in DEFENSE_SUPPORT_LANE_POINTS[0]


def test_defense_checks_the_verified_cost_and_holds_a_card_with_unknown_cost():
    unit = EnemyUnitLocation((180, 620), "left", "friendly_half")
    snapshot = EnemyUnitSnapshot(10.0, (unit,))
    golden_knight = HandCard(0, "golden_knight", "bridge_line", get_card_elixir_cost("golden_knight"), (198, 672))
    unknown = HandCard(1, "spirit_empress", "bridge_line", None, (198, 672))
    assert rank_hand_plays((golden_knight, unknown), snapshot, FULL_TOWERS, 3) == []
    assert [play.card for play in rank_hand_plays((golden_knight, unknown), snapshot, FULL_TOWERS, 4)] == [
        golden_knight
    ]


@pytest.mark.parametrize(
    ("card_id", "cost"),
    [
        ("elixir_golem", 3),
        ("skeleton_army", 3),
        ("skeleton_barrel", 3),
        ("archer_queen", 5),
        ("goblin_giant", 6),
        ("electro_giant", 7),
    ],
)
def test_observed_fixed_cost_cards_have_a_cost_and_still_require_enough_elixir(card_id, cost):
    assert get_card_elixir_cost(card_id) == cost
    card = HandCard(0, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), (198, 672))
    threat = EnemyUnitLocation((180, 640), "left", "friendly_half")
    assert rank_hand_plays((card,), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, cost - 1) == []


@pytest.mark.parametrize(
    "ids",
    [
        ("elixir_golem", "skeleton_army", "goblin_giant", "archer_queen"),
        ("skeleton_army", "skeleton_barrel", "electro_giant", "archer_queen"),
    ],
)
def test_four_previously_skipped_cards_can_now_advance_and_defend(monkeypatch, ids):
    hand = [
        HandCard(i, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), (198, 672))
        for i, card_id in enumerate(ids)
    ]
    calm = rank_hand_plays(hand, EnemyUnitSnapshot(10, ()), FULL_TOWERS, 10)
    assert calm and max(calm, key=lambda play: play.priority).purpose == "advance"
    threat = EnemyUnitLocation((180, 640), "left", "friendly_half")
    defense = rank_hand_plays(hand, EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 3)
    assert defense and all(play.card.card_id == "skeleton_army" for play in defense)

    frame = battle_frame()
    mock_decision_hand(monkeypatch, frame, slots=(0, 1, 2, 3), elixir=10)
    monkeypatch.setattr(
        fight, "get_play_coords_for_card", lambda _e, _l, slot, _t: (hand[slot].card_id, hand[slot].preset)
    )
    emulator = Mock()
    state = BattlePlayState()
    assert fight.play_a_card(emulator, Mock(), False, fight.BattleStrategy(), decision_frame=frame, play_state=state)
    assert emulator.click.call_count == 2
    assert state.elixir_remaining == 7


@pytest.mark.parametrize("elixir", [7, 10])
@pytest.mark.parametrize(
    "ids",
    [
        ("goblin_giant", "golem", "skeleton_barrel", "lava_hound"),
        ("elixir_golem", "goblin_giant", "lava_hound", "golem"),
    ],
)
def test_pressure_only_hand_cycles_the_cheapest_troop_to_reach_a_defender(ids, elixir):
    hand = [
        HandCard(i, card, get_card_group(card), get_card_elixir_cost(card), (198, 672)) for i, card in enumerate(ids)
    ]
    threat = EnemyUnitLocation((540, 870), "right", "friendly_half")
    state = BattlePlayState()
    state.observe_hand(hand)
    state.hand_waits[ids.index("lava_hound")] = ("lava_hound", 3)
    options = rank_hand_plays(hand, EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, elixir, play_state=state)
    chosen = max(options, key=lambda play: play.priority)
    assert chosen.purpose == "advance" and chosen.card.cost == 3
    assert all(play.card.cost is not None and elixir - play.card.cost >= (2 if elixir == 10 else 3) for play in options)
    assert state.advance_target is None


def test_available_defense_prevents_pressure_only_cycle_and_unknown_spells_stay_held():
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    threat = EnemyUnitLocation((180, 640), "left", "friendly_half")
    snapshot = EnemyUnitSnapshot(10, (threat,))
    options = rank_hand_plays((HOG, knight), snapshot, FULL_TOWERS, 10)
    assert options and all(play.purpose == "defend" for play in options)
    log = HandCard(0, "log", "lane_spell", 2, (202, 374))
    unknown = HandCard(1, "goblin_giant", "bridge_line", None, (198, 672))
    assert rank_hand_plays((log, unknown), snapshot, FULL_TOWERS, 10) == []
    assert rank_hand_plays((HOG,), snapshot, FULL_TOWERS, 6) == []


def test_pressure_cycle_respects_recent_defense_observation():
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    threat = EnemyUnitLocation((180, 620), "left", "friendly_half")
    first = rank_hand_plays((knight,), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 7)[0]
    state = BattlePlayState()
    state.record_play(first, 7, 10)
    assert rank_hand_plays((HOG,), EnemyUnitSnapshot(10.3, (threat,)), FULL_TOWERS, 10, play_state=state) == []


def test_observed_offensive_hand_sends_a_cycle_instead_of_waiting_forever(monkeypatch):
    ids = ("goblin_giant", "golem", "skeleton_barrel", "lava_hound")
    frame = battle_frame()
    draw_badge(frame, 540, 700, bar_color=RED)
    mock_decision_hand(monkeypatch, frame, slots=(0, 1, 2, 3), elixir=10)
    monkeypatch.setattr(fight, "get_play_coords_for_card", lambda _e, _l, slot, _t: (ids[slot], (198, 672)))
    emulator = Mock()
    state = BattlePlayState()
    assert fight.play_a_card(emulator, Mock(), False, fight.BattleStrategy(), decision_frame=frame, play_state=state)
    assert emulator.click.call_args_list[0].args == fight.HAND_CARDS_COORDS[2]
    assert state.elixir_remaining == 7


def test_stale_elixir_or_unconfirmed_replacement_interrupts_settlement():
    state = BattlePlayState()
    state.record_play(PlayOption(HOG, (132, 568), "advance"), 7, 10.0)
    assert not state.observe_settlement(3, "knight")
    assert not state.observe_settlement(4, "knight")
    assert not state.observe_settlement(0, "knight")
    assert not state.observe_settlement(3, "knight")
    assert not state.observe_settlement(3, HOG.card_id)
    assert not state.observe_settlement(3, "knight")
    assert state.elixir_limit(10.8) == 3
    assert state.elixir_limit(10.9) is None


def test_elixir_wait_releases_settled_budget_before_timeout_without_extra_capture(monkeypatch):
    first, second, ready = (battle_frame() for _ in range(3))
    left, top = HAND_CARD_TOP_LEFTS[HOG.slot]
    for frame in (first, second):
        for dx, dy, color in HAND_EMPTY_SLOT_SAMPLES:
            frame[top + dy, left + dx] = color
    y, x = ELIXIR_COORDS[0]
    ready[y, x] = ELIXIR_COLOR
    emulator = Mock()
    emulator.screenshot.side_effect = [first, second, ready]
    state = BattlePlayState()
    state.record_play(PlayOption(HOG, (132, 568), "defend"), 4, 10.0)
    intervals = iter([10.0, 10.2, 10.4, 10.6])
    monkeypatch.setattr(fight.time, "monotonic", lambda: next(intervals))
    monkeypatch.setattr(fight, "check_which_cards_are_available", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(fight, "switch_side", lambda: (0, "left"))
    decisions = []

    assert fight.wait_for_elixir(emulator, Mock(), 1, play_state=state, on_decision_frame=decisions.append)
    assert decisions[0] is ready and emulator.screenshot.call_count == 3
    assert state.elixir_limit(10.6) is None


def test_over_river_threat_bypasses_routine_elixir_wait_and_reuses_the_frame():
    frame = battle_frame()
    draw_badge(frame, 180, 620, bar_color=RED)
    for y, x in ELIXIR_COORDS[:3]:
        frame[y, x] = ELIXIR_COLOR
    emulator = Mock()
    emulator.screenshot.return_value = frame
    logger = Mock()
    snapshots = []
    monitor = EnemyUnitMonitor(logger)
    assert read_battle_elixir(frame) == 3
    assert fight.wait_for_elixir(
        emulator,
        logger,
        7,
        enemy_monitor=monitor,
        on_decision_frame=snapshots.append,
    )
    emulator.screenshot.assert_called_once_with()
    assert snapshots[0] is frame


def test_low_elixir_poll_reuses_the_frame_for_activity_and_card_play(monkeypatch):
    frame = battle_frame()
    emulator = Mock()
    emulator.screenshot.return_value = frame
    available = Mock(return_value=[0])
    ability = Mock(return_value=False)
    monkeypatch.setattr(fight, "check_which_cards_are_available", available)
    monkeypatch.setattr(fight, "image_has_hero_champion_ability", ability)
    monkeypatch.setattr(fight, "switch_side", lambda: (11000, "left"))
    decision_frames = []

    assert fight.wait_for_elixir(emulator, Mock(), 7, on_decision_frame=decision_frames.append)
    emulator.screenshot.assert_called_once_with()
    assert available.call_args.kwargs["frame"] is frame
    assert ability.call_args.args[0] is frame
    assert decision_frames[0] is frame


def test_live_spell_recheck_can_change_cards_and_recent_slots_cannot_override_defense(monkeypatch):
    before, after = battle_frame(), battle_frame()
    for pos in ((180, 620), (295, 621)):
        draw_badge(before, *pos, bar_color=RED)
    draw_badge(after, 180, 620, bar_color=RED)
    mock_decision_hand(monkeypatch, before, slots=(0, 1, 2), elixir=5)
    # Slot 0 is the last played, but covering two crossed units beats the
    # defender. Then the refresh makes the cheaper slot 1 the best response.
    monkeypatch.setattr(fight, "last_three_cards", [0])
    hand = [ARROWS, ZAP, HandCard(2, "knight", "bridge_line", 3, (198, 672))]
    monkeypatch.setattr(
        fight, "get_play_coords_for_card", lambda _device, _log, slot, _elapsed: (hand[slot].card_id, hand[slot].preset)
    )
    emulator = Mock()
    emulator.screenshot.return_value = after
    logger = Mock()
    assert fight.play_a_card(emulator, logger, False, fight.BattleStrategy())
    emulator.screenshot.assert_called_once_with()
    assert emulator.click.call_args_list[0].args == fight.HAND_CARDS_COORDS[1]


@pytest.mark.parametrize(
    ("card_id", "starting_elixir", "required_elixir"),
    [("ronin", 7, 8), ("witch", 7, 8), ("giant_skeleton", 7, 9), ("pekka", 7, 9), ("golem", 8, 10)],
)
def test_held_troops_can_save_for_a_push_without_spending_the_reserve(card_id, starting_elixir, required_elixir):
    held = HandCard(0, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), (198, 672))
    state = BattlePlayState()
    state.observe_hand((held, HOG))
    state.record_play(PlayOption(HOG, (132, 568), "advance"), 7, 1.0)
    snapshot = EnemyUnitSnapshot(10.0, ())

    assert rank_hand_plays((held, HOG), snapshot, FULL_TOWERS, starting_elixir, play_state=state) == []
    assert state.advance_elixir() == required_elixir
    # Polling the same hand neither ages cards nor lets the cheaper play steal the budget.
    assert rank_hand_plays((held, HOG), snapshot, FULL_TOWERS, starting_elixir, play_state=state) == []
    options = rank_hand_plays((held, HOG), snapshot, FULL_TOWERS, required_elixir, play_state=state)
    assert [option.card for option in options] == [held]
    assert required_elixir - held.cost >= 2


def test_hand_preferences_survive_unaffordable_frames_and_reset_on_replacement():
    held = HandCard(0, "witch", "king_lane", 5, (120, 936))
    replacement = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    state = BattlePlayState()
    state.observe_hand((held, HOG))
    for _ in range(5):
        state.record_play(PlayOption(HOG, (132, 568), "advance"), 7, 1.0)
        state.observe_hand((HOG,))
    assert 0 < state.waiting_bonus(held) <= 3
    snapshot = EnemyUnitSnapshot(10.0, ())
    assert rank_hand_plays((held, HOG), snapshot, FULL_TOWERS, 7, play_state=state) == []
    options = rank_hand_plays((replacement, HOG), snapshot, FULL_TOWERS, 7, play_state=state)
    assert options and state.advance_target is None
    assert state.waiting_bonus(replacement) == 0
    assert len(state.hand_waits) == 2


def test_new_threat_cancels_saving_and_allows_low_elixir_defense():
    held = HandCard(0, "witch", "king_lane", 5, (120, 936))
    knight = HandCard(1, "knight", "bridge_line", 3, (198, 672))
    state = BattlePlayState()
    state.observe_hand((held, knight))
    state.record_play(PlayOption(knight, (198, 672), "advance"), 7, 1.0)
    assert rank_hand_plays((held, knight), EnemyUnitSnapshot(10.0, ()), FULL_TOWERS, 7, play_state=state) == []
    crossed = EnemyUnitLocation((180, 700), "left", "friendly_half")
    options = rank_hand_plays((held, knight), EnemyUnitSnapshot(10.2, (crossed,)), FULL_TOWERS, 3, play_state=state)
    assert options[0].card == knight and options[0].purpose == "defend"
    assert state.advance_target is None


@pytest.mark.parametrize("card_id", ["golem", "pekka", "giant_skeleton"])
def test_previously_seen_unaffordable_troop_can_plan_saving_without_a_current_match(card_id):
    held = HandCard(0, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), (198, 672))
    cheap = HandCard(1, "knight", "bridge_line", 3, (198, 672))
    state = BattlePlayState()
    state.observe_hand((held, cheap))
    assert held.cost is not None
    state.record_play(PlayOption(cheap, (198, 672), "advance"), 10, 1)
    assert rank_hand_plays((cheap,), EnemyUnitSnapshot(10, ()), FULL_TOWERS, held.cost - 1, play_state=state) == []
    # At low elixir no routine push is planned; the remembered slot still ages.
    assert state.known_hand[0] == held
    state.hand_waits[0] = (card_id, 3)
    if held.cost > 7:
        assert rank_hand_plays((cheap,), EnemyUnitSnapshot(11, ()), FULL_TOWERS, 7, play_state=state) == []
        assert state.advance_target == held and state.advance_elixir() == 10
    # A previously seen card alone is never permission to click a current slot.
    options = rank_hand_plays((cheap,), EnemyUnitSnapshot(12, ()), FULL_TOWERS, 10, play_state=state)
    assert all(option.card == cheap for option in options)
    options = rank_hand_plays((held, cheap), EnemyUnitSnapshot(13, ()), FULL_TOWERS, 10, play_state=state)
    chosen = max(options, key=lambda option: option.priority)
    assert chosen.card == held
    state.record_play(chosen, 10, 13)
    assert 0 not in state.known_hand and state.waiting_bonus(held) == 0


def test_cached_heavy_troop_cannot_block_emergency_defense_or_survive_a_replacement():
    heavy = HandCard(0, "golem", "king_lane", 8, (198, 672))
    knight = HandCard(1, "knight", "bridge_line", 3, (198, 672))
    state = BattlePlayState()
    state.observe_hand((heavy, knight))
    state.hand_waits[0] = ("golem", 3)
    assert rank_hand_plays((knight,), EnemyUnitSnapshot(10, ()), FULL_TOWERS, 7, play_state=state) == []
    assert state.advance_target == heavy
    threat = EnemyUnitLocation((180, 700), "left", "friendly_half")
    options = rank_hand_plays((knight,), EnemyUnitSnapshot(11, (threat,)), FULL_TOWERS, 3, play_state=state)
    assert options and options[0].card == knight and options[0].purpose == "defend"
    assert state.advance_target is None
    replacement = HandCard(0, "skeletons", "bridge_line", 1, (198, 672))
    rank_hand_plays((replacement, knight), EnemyUnitSnapshot(12, ()), FULL_TOWERS, 7, play_state=state)
    assert state.known_hand[0] == replacement and state.waiting_bonus(replacement) == 0


def test_unaffordable_finisher_spells_are_not_promoted_from_hand_memory():
    lightning = HandCard(0, "lightning", "large_spell", 6, (204, 292))
    state = BattlePlayState()
    state.observe_hand((lightning,))
    assert rank_hand_plays((), EnemyUnitSnapshot(10, ()), ((1, True), (79, True)), 3, play_state=state) == []
    assert state.advance_target is None


@pytest.mark.parametrize("random_mode", [False, True])
def test_fight_saves_for_remembered_heavy_card_but_requires_current_recognition_to_tap(monkeypatch, random_mode):
    heavy = HandCard(0, "golem", "king_lane", 8, (198, 672))
    state = BattlePlayState()
    state.observe_hand((heavy,))
    state.hand_waits[0] = ("golem", 3)
    frame, slots, elixir, now = battle_frame(), [1], [7], [10.0]
    mock_decision_hand(monkeypatch, frame, slots=slots)
    monkeypatch.setattr(fight.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: elixir[0])
    monkeypatch.setattr(
        fight, "get_play_coords_for_card", lambda _e, _l, slot, _t: (("golem", "knight")[slot], (198, 672))
    )
    emulator, logger = Mock(), Mock()

    def play():
        if random_mode:
            return fight.play_random_available_card(emulator, logger, False, 0, decision_frame=frame, play_state=state)
        return fight.play_a_card(
            emulator, logger, False, fight.BattleStrategy(), decision_frame=frame, play_state=state
        )

    assert not play() and state.advance_elixir() == 10
    emulator.click.assert_not_called()
    elixir[0], now[0] = 10, 11.0
    assert play()
    assert emulator.click.call_args_list[0].args == fight.HAND_CARDS_COORDS[1]
    emulator.reset_mock()
    slots[:] = [0, 1]
    now[0] = 12.0
    assert play()
    assert emulator.click.call_args_list[0].args == fight.HAND_CARDS_COORDS[0]
    assert state.elixir_remaining == 2


@pytest.mark.parametrize("card_id", ["ronin", "witch", "giant_skeleton", "pekka"])
def test_four_card_hand_does_not_keep_starving_the_costliest_troop(card_id):
    ids = (card_id, "knight", "bandit", "hog")
    hand = [
        HandCard(i, card, get_card_group(card), get_card_elixir_cost(card), (198, 672)) for i, card in enumerate(ids)
    ]
    state = BattlePlayState()
    for turn in range(4):
        snapshot = EnemyUnitSnapshot(10 + turn, ())
        elixir = 7
        options = rank_hand_plays(hand, snapshot, FULL_TOWERS, elixir, play_state=state)
        if not options:
            elixir = state.advance_elixir()
            options = rank_hand_plays(hand, snapshot, FULL_TOWERS, elixir, play_state=state)
        chosen = max(options, key=lambda play: play.priority)
        if chosen.card.card_id == card_id:
            return
        state.record_play(chosen, elixir, 10 + turn)
    pytest.fail("The three cheaper slots kept the held troop out of every safe push")


@pytest.mark.parametrize("card_id", ["musketeer", "archers", "witch", "evo_archers", "hero_ice_wizard", "furnace"])
def test_support_placement_uses_the_shared_role_without_a_recent_defender(card_id):
    support = HandCard(0, card_id, get_card_group(card_id), get_card_elixir_cost(card_id), (118, 894))
    threat = EnemyUnitLocation((540, 620), "right", "friendly_half")
    play = rank_hand_plays((support,), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 10)[0]
    assert play.point in DEFENSE_SUPPORT_LANE_POINTS[1]
    assert play.point[1] > threat.position[1]


def test_defense_prefers_interception_then_complementary_support():
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    support = HandCard(1, "archers", "back_support", 3, (118, 894))
    threat = EnemyUnitLocation((180, 620), "left", "friendly_half")
    options = rank_hand_plays((support, knight), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 7)
    first = max(options, key=lambda option: option.priority)
    assert first.card == knight
    state = BattlePlayState()
    state.record_play(first, 7, 10)
    follow_up = rank_hand_plays((knight, support), EnemyUnitSnapshot(10.7, (threat,)), FULL_TOWERS, 4, play_state=state)
    assert max(follow_up, key=lambda option: option.priority).card == support


@pytest.mark.parametrize(
    "units",
    [
        (EnemyUnitLocation((180, 680), "left", "friendly_half"),),
        (EnemyUnitLocation((180, 850), "left", "friendly_half"),),
        (EnemyUnitLocation((540, 620), "right", "friendly_half"),),
        (
            EnemyUnitLocation((180, 620), "left", "friendly_half"),
            EnemyUnitLocation((210, 620), "left", "friendly_half"),
        ),
    ],
)
def test_defense_observation_yields_to_deeper_more_or_other_lane_threats(units):
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    original = EnemyUnitLocation((180, 620), "left", "friendly_half")
    first = rank_hand_plays((knight,), EnemyUnitSnapshot(10, (original,)), FULL_TOWERS, 7)[0]
    state = BattlePlayState()
    state.record_play(first, 7, 10)
    assert rank_hand_plays((knight,), EnemyUnitSnapshot(10.3, (original,)), FULL_TOWERS, 4, play_state=state) == []
    assert rank_hand_plays((knight,), EnemyUnitSnapshot(10.3, units), FULL_TOWERS, 4, play_state=state)
    assert rank_hand_plays((knight,), EnemyUnitSnapshot(10.7, (original,)), FULL_TOWERS, 4, play_state=state)


def test_observing_recent_defense_keeps_the_tower_finisher_available():
    knight = HandCard(0, "knight", "bridge_line", 3, (198, 672))
    threat = EnemyUnitLocation((180, 620), "left", "friendly_half")
    first = rank_hand_plays((knight,), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 7)[0]
    state = BattlePlayState()
    state.record_play(first, 7, 10)
    options = rank_hand_plays(
        (ARROWS,), EnemyUnitSnapshot(10.3, (threat,)), ((2, True), (79, True)), 3, play_state=state
    )
    assert [option.purpose for option in options] == ["finish"]


def test_waiting_push_preference_cannot_override_affordable_emergency_defense():
    heavy = HandCard(0, "pekka", "bridge_line", 7, (198, 672))
    knight = HandCard(1, "knight", "bridge_line", 3, (198, 672))
    state = BattlePlayState()
    state.observe_hand((heavy, knight))
    for _ in range(3):
        state.record_play(PlayOption(knight, (198, 672), "advance"), 7, 1.0)
    threat = EnemyUnitLocation((180, 900), "left", "friendly_half")
    options = rank_hand_plays((heavy, knight, ZAP), EnemyUnitSnapshot(10, (threat,)), FULL_TOWERS, 10, play_state=state)
    assert max(options, key=lambda option: option.priority).card == ZAP


@pytest.mark.parametrize("random_mode", [False, True])
def test_both_fight_modes_wait_for_the_planned_budget_before_tapping(monkeypatch, random_mode):
    frame = battle_frame()
    mock_decision_hand(monkeypatch, frame, slots=(0, 1), elixir=7)
    hand = (HandCard(0, "witch", "king_lane", 5, (120, 936)), HandCard(1, "hog", "bridge_rush", 4, (132, 568)))
    monkeypatch.setattr(
        fight, "get_play_coords_for_card", lambda _e, _l, slot, _t: (hand[slot].card_id, hand[slot].preset)
    )
    monkeypatch.setattr(fight, "create_default_bridge_iar", lambda _: None)
    monkeypatch.setattr(fight, "stop_fight_capture", lambda: None)
    now = 0.0
    available_elixir = 7
    amounts = []

    def wait(_emulator, _logger, amount, *_args, **kwargs):
        nonlocal now, available_elixir
        amounts.append(amount)
        if len(amounts) == 4:
            return "no battle"
        now += 1
        available_elixir = amount
        kwargs["on_decision_frame"](frame)
        return True

    monkeypatch.setattr(fight, "wait_for_elixir", wait)
    monkeypatch.setattr(fight.time, "monotonic", lambda: now)
    monkeypatch.setattr(fight, "read_battle_elixir", lambda _: available_elixir)
    emulator, logger = Mock(), Mock()
    logger.get_cards_played.return_value = 0
    assert fight._fight_loop(emulator, logger, False, random_mode=random_mode)
    assert amounts == [7, 7, 8, 7]
    selected_slots = [call.args for call in emulator.click.call_args_list[::2]]
    assert selected_slots == [fight.HAND_CARDS_COORDS[1], fight.HAND_CARDS_COORDS[0]]
