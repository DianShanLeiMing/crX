"""Card confirmation searches remain explicit about ordinary/evolution/hero variants."""

from types import SimpleNamespace
from typing import cast
from unittest.mock import Mock

from crX.bot.card_data import CARD_NAMES_ZH
from crX.interface.card_review import CardReviewTab, search_card_choices

CHOICES = {f"{name} ({card_id})": card_id for card_id, name in CARD_NAMES_ZH.items()}


def ids(query):
    return [CHOICES[label] for label in search_card_choices(CHOICES, query)]


def test_chinese_partial_and_non_adjacent_name_search():
    assert {"knight", "evo_knight", "hero_knight"} <= set(ids("骑士"))
    assert "evo_knight" in ids("觉醒骑士")
    assert "knight" not in ids("觉醒骑士")
    assert "evo_knight" in ids("骑士 觉醒")


def test_english_ids_ignore_case_separators_and_rank_exact_first():
    assert ids("KNIGHT")[0] == "knight"
    assert ids("EVO_KNIGHT")[0] == "evo_knight"
    assert "electro_wizard" in ids("ELECTRO WIZARD")
    assert "electro_wizard" in ids("electrowizard")


def test_clear_selected_label_and_no_matches():
    assert search_card_choices(CHOICES, "  ") == sorted(CHOICES)
    assert search_card_choices(CHOICES, "骑士 (knight)") == sorted(CHOICES)
    assert ids("不存在的卡牌") == []


def test_variable_write_filters_without_rewriting_input():
    variable = Mock()
    variable.get.return_value = "觉醒骑士"
    combo = Mock()
    tab = cast(
        CardReviewTab,
        SimpleNamespace(
            _closed=False,
            card_choices=CHOICES,
            card_var=variable,
            card_combo=combo,
            _hide_card_suggestions=Mock(),
            _card_search_focus=Mock(return_value=None),
        ),
    )
    CardReviewTab._search_cards(tab, "variable", "", "write")
    combo.configure.assert_called_once_with(values=search_card_choices(CHOICES, "觉醒骑士"))
    variable.set.assert_not_called()


def test_enter_selects_only_an_exact_id_or_unambiguous_match_without_saving():
    variable = Mock()
    combo = Mock()
    suggestions = Mock()
    suggestions.winfo_ismapped.return_value = False
    tab = cast(
        CardReviewTab,
        SimpleNamespace(
            card_choices=CHOICES,
            card_var=variable,
            card_combo=combo,
            card_suggestions=suggestions,
            _hide_card_suggestions=Mock(),
        ),
    )
    variable.get.return_value = "骑士"
    assert CardReviewTab._choose_single_card(tab) == "break"
    variable.set.assert_not_called()
    variable.get.return_value = "evo_knight"
    assert CardReviewTab._choose_single_card(tab) == "break"
    variable.set.assert_called_once_with("觉醒版骑士 (evo_knight)")


def test_typing_queues_suggestions_only_for_the_focused_search_field():
    variable = Mock()
    variable.get.return_value = "骑士"
    combo, show, later = Mock(), Mock(), Mock(return_value="after-search")
    tab = cast(
        CardReviewTab,
        SimpleNamespace(
            _closed=False,
            card_choices=CHOICES,
            card_var=variable,
            card_combo=combo,
            _hide_card_suggestions=Mock(),
            _show_card_suggestions=show,
            _later=later,
            _card_search_focus=Mock(return_value=combo),
        ),
    )
    CardReviewTab._search_cards(tab, "variable", "", "write")
    later.assert_called_once_with(80, show)
    later.reset_mock()
    variable.get.return_value = ""
    CardReviewTab._search_cards(tab, "variable", "", "write")
    later.assert_not_called()


def test_native_dropdown_focus_does_not_raise_when_tcl_widget_has_no_python_object():
    tab = cast(CardReviewTab, SimpleNamespace(focus_get=Mock(side_effect=KeyError("popdown"))))
    assert CardReviewTab._card_search_focus(tab) is None
