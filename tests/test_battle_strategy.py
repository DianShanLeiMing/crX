"""Decision ordering and affordability for the shared battle planner."""

from unittest.mock import Mock

from crX.bot import fight
from crX.bot.battle_strategy import BattlePlayState, HandCard, rank_hand_plays
from crX.bot.coords import DEFENSE_LANE_POINTS, DEFENSE_SUPPORT_LANE_POINTS
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
    assert opposite[0].point == later[0].point == first.point
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
