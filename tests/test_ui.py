"""Desktop-only GUI smoke coverage; no device calls."""

import os
import sys
import time

import pytest

from crX.emulators import EmulatorType
from crX.interface.enums import UIField
from crX.utils.card_review import save_review, scan_samples
from tests.test_card_review import add_group_sample, make_sample
from tests.test_failure_samples import make_failure

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="No desktop display")


@pytest.fixture(scope="module")
def desktop_ui(tmp_path_factory):
    """Use one Tcl interpreter, matching the application's single-window lifetime."""
    from crX.interface.ui import CrXUI

    root = tmp_path_factory.mktemp("ui-card-samples")
    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("crX.interface.card_review.get_card_samples_dir", lambda: root)
        patch.setattr("crX.interface.failure_review.get_failure_samples_dir", lambda: root / "failures")
        ui = CrXUI()
        ui.withdraw()
        yield ui, root
        ui.destroy()


def test_tabs_settings_themes_and_running_state(desktop_ui):
    ui, _root = desktop_ui
    try:
        ui.update()
        assert [ui.notebook.tab(tab, "text") for tab in ui.notebook.tabs()] == [
            "任务",
            "设备",
            "统计",
            "卡牌样本",
            "异常样本",
            "设置",
        ]
        assert ui.get_all_values()[UIField.MAX_FIGHTS] == "0"
        ui.set_all_values({UIField.MAX_FIGHTS: 5})
        assert ui.max_fights_spinbox.get() == "5"
        assert ui.get_all_values()[UIField.STOP_AFTER_REWARDS] is False
        assert ui.get_all_values()[UIField.USE_EMOTES] is False
        ui.set_all_values({UIField.USE_EMOTES: True})
        assert ui.get_all_values()[UIField.USE_EMOTES] is True
        ui.set_all_values({UIField.USE_EMOTES: False})
        ui.set_all_values({UIField.STOP_AFTER_REWARDS: True})
        assert ui.get_all_values()[UIField.STOP_AFTER_REWARDS] is True
        assert ui.stop_after_rewards_checkbox.cget("text") == "奖励领完后停止"
        assert ui.mumu_frame.winfo_manager() == "pack"
        assert ui.adb_frame.winfo_manager() == ""
        assert ui.device_advanced_frame.winfo_manager() == ""
        ui.set_all_values({UIField.ADB_SERIAL: "127.0.0.1:12345", UIField.EMULATOR: EmulatorType.ADB})
        assert ui.device_backend_var.get() == "通用 ADB"
        assert ui.mumu_frame.winfo_manager() == ""
        assert ui.adb_frame.winfo_manager() == "pack"
        ui.show_device_advanced.set(True)
        ui._update_device_fields()
        assert ui.device_advanced_frame.winfo_manager() == "pack"
        assert ui.mumu_path_row.winfo_manager() == ""
        ui.device_backend_combo.set("MuMu")
        ui.device_backend_combo.event_generate("<<ComboboxSelected>>")
        ui.update()
        assert ui.get_all_values()[UIField.EMULATOR] == EmulatorType.MUMU
        assert ui.mumu_path_row.winfo_manager() == "pack"
        assert ui.get_all_values()[UIField.ADB_SERIAL] == "127.0.0.1:12345"
        ui.set_all_values({UIField.THEME_NAME: "flatly", UIField.SHOP_DAILY_OFFER_USER_TOGGLE: True})
        assert ui.get_all_values()[UIField.SHOP_DAILY_OFFER_USER_TOGGLE] is True
        assert ui.style.theme_use() == "flatly"
        assert ui.theme_label_var.get() == "扁平浅色"
        ui.theme_combo.set("经典深色")
        ui.theme_combo.event_generate("<<ComboboxSelected>>")
        ui.update()
        assert ui.get_all_values()[UIField.THEME_NAME] == "darkly"
        assert ui.style.theme_use() == "darkly"
        ui.set_all_values({UIField.DISABLE_WIN_TRACK_TOGGLE: False})
        assert ui.get_all_values()[UIField.DISABLE_WIN_TRACK_TOGGLE] is False
        ui.update_stats({"wins": 3, "winrate": "60%", "completed_fights": 5})
        assert ui.stat_variables["winrate"].get() == "60%"
        assert ui.stat_variables["completed_fights"].get() == "5"
        ui.set_button_state("running")
        assert all(str(widget.cget("state")) == "disabled" for widget, _ in ui.config_widgets)
        ui.set_button_state("idle")
        assert str(ui.main_btn.cget("text")) == "启动"
    finally:
        ui.set_button_state("idle")


def _wait_ui(ui, condition):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        ui.update()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("GUI background operation did not complete")


def test_review_tab_confirm_trial_and_revoke(desktop_ui):
    ui, root = desktop_ui
    source = make_sample(root)
    errors = []
    ui.report_callback_exception = lambda *args: errors.append(args)
    try:
        tab = ui.card_samples_tab
        tab.refresh()
        _wait_ui(ui, lambda: tab.selected is not None and "card" in tab._images)
        assert ui.notebook.tab(tab, "text") == "卡牌样本 (1)"
        assert tab.card_var.get() == ""  # Do not prefill the incorrect top candidate.
        tab.card_var.set("觉醒")
        tab._search_cards()
        assert tab.card_combo.cget("values")
        assert all("觉醒" in value for value in tab.card_combo.cget("values"))
        tab.card_var.set("evo_cannon")
        tab.note_var.set("人工核对")
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert tab.card_var.get() == "evo_cannon" and tab.note_var.get() == "人工核对"
        ui.set_button_state("running")
        assert str(tab.stage_buttons["confirm"].cget("state")) == "normal"
        tab.stage_buttons["confirm"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and tab.selected is None)
        assert ui.notebook.tab(tab, "text") == "卡牌样本"
        tab.filter_var.set("补库")
        tab._render_list()
        assert tab.selected is not None
        assert tab.selected.card_id == "evo_cannon"
        assert tab.note_var.get() == "人工核对"
        assert tab.selected.status in ("trial", "covered")
        assert not (source.directory / "sample-000-candidate.json").exists()
        tab.stage_buttons["revoke"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        tab.filter_var.set("补库")
        tab._render_list()
        assert tab.selected.status == "confirmed" and tab.selected.card_id == "evo_cannon"
        assert tab.note_var.get() == "人工核对"
        assert not errors
    finally:
        ui.set_button_state("idle")


def test_failure_review_status_and_running_state(desktop_ui):
    ui, root = desktop_ui
    make_failure(root / "failures")
    tab = ui.failure_samples_tab
    tab.refresh()
    _wait_ui(ui, lambda: tab.selected is not None and tab._preview is not None)
    assert ui.notebook.tab(tab, "text") == "异常样本 (1)"
    assert "unknown" in tab.info_var.get()
    assert "validation-session.txt" in tab.info_var.get()
    assert str(tab.zoom_button.cget("state")) == "normal"
    before = set(tab.winfo_children())
    tab.zoom_button.invoke()
    ui.update()
    popups = set(tab.winfo_children()) - before
    assert len(popups) == 1
    for popup in popups:
        popup.destroy()
    assert not hasattr(tab, "note_var")
    assert [button.cget("text") for button in tab.buttons] == ["标记已处理", "忽略", "恢复待处理"]
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    ui.set_button_state("running")
    try:
        assert str(tab.buttons[0].cget("state")) == "normal"
        tab.buttons[0].invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy and tab.selected is None)
        assert ui.notebook.tab(tab, "text") == "异常样本"
        tab.filter_var.set("已处理")
        tab._render()
        tab.buttons[2].invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy)
        assert ui.notebook.tab(tab, "text") == "异常样本 (1)"
    finally:
        ui.set_button_state("idle")


def test_card_review_group_multi_selection_refresh_and_bulk_actions(desktop_ui, monkeypatch):
    monkeypatch.setattr("crX.utils.card_library.base_match", lambda _: ("UNKNOWN", 10000))
    ui, root = desktop_ui
    first = add_group_sample(root, "bulk-a", "sample-000")
    second = add_group_sample(root, "bulk-b", "sample-000", slot=2)
    tab = ui.card_samples_tab
    tab.filter_var.set("待确认")
    tab.group_var.set("全部分组")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy and second.key in tab.samples)
    tab.tree.selection_set(first.key)
    tab.tree.focus(first.key)
    tab._select()
    tab.select_group_button.invoke()
    assert set(tab.tree.selection()) == {first.key, second.key}
    assert "已选 2 张" in tab.selection_var.get()
    tab.card_var.set("evo_cannon")
    tab.note_var.set("批量确认")
    third = add_group_sample(root, "bulk-c", "sample-000", slot=1)
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy and third.key in tab.samples)
    assert set(tab.tree.selection()) == {first.key, second.key}
    assert tab.card_var.get() == "evo_cannon" and tab.note_var.get() == "批量确认"
    tab.stage_buttons["confirm"].invoke()
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
    reviewed = {sample.key: sample for sample in scan_samples(root)[0]}
    assert all(
        reviewed[key].card_id == "evo_cannon" and reviewed[key].review["note"] == "批量确认"
        for key in (first.key, second.key)
    )
    assert reviewed[third.key].status == "pending"
    # A group selection only includes rows visible under the status filter.
    group = tab.sample_groups[third.key]
    tab.group_var.set(next(label for label, value in tab.group_choices.items() if value == group))
    tab._render_list()
    tab.select_group_button.invoke()
    assert tab.tree.selection() == (third.key,)
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *_args, **_kwargs: True)
    tab.clear_button.invoke()
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and tab.selected is None)
    assert third.key not in {sample.key for sample in scan_samples(root)[0]}
    reviewed = {sample.key: sample for sample in scan_samples(root)[0]}
    assert all(reviewed[key].status in ("trial", "covered") for key in (first.key, second.key))
    tab.filter_var.set("补库")
    tab._render_list()
    trial_keys = [key for key in tab.tree.get_children() if tab.samples[key].status == "trial"]
    assert len(trial_keys) == 1
    assert all(tab.tree.set(key, "status") == "暂无可用补库" for key in trial_keys)
    assert {first.key, second.key}.issubset(tab.samples)  # Hidden candidates remain available.
    labels_before = {key: tab.tree.set(key, "status") for key in trial_keys}
    tab.filter_var.set("全部")
    tab._render_list()
    assert all(tab.tree.set(key, "status") == "证据不足" for key in trial_keys)
    tab.filter_var.set("补库")
    tab._render_list()
    assert {key: tab.tree.set(key, "status") for key in trial_keys} == labels_before
    assert str(tab.select_group_button.cget("state")) == "disabled"
    assert len(tab.tree.selection()) == 1
    tab.stage_buttons["revoke"].invoke()
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
    reviewed = {sample.key: sample for sample in scan_samples(root)[0]}
    assert all(reviewed[key].status == "confirmed" for key in (first.key, second.key))


def test_card_samples_clear_confirmation_running_guard_and_refresh(desktop_ui, monkeypatch):
    ui, root = desktop_ui
    sample = add_group_sample(root, "clear-all-visible", "sample-000")
    tab = ui.card_samples_tab
    tab.filter_var.set("待确认")
    tab.group_var.set("全部分组")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and sample.key in tab.tree.get_children())
    assert any(item.status == "confirmed" for item in tab.samples.values())
    group = tab.sample_groups[sample.key]
    tab.group_var.set(next(label for label, value in tab.group_choices.items() if value == group))
    tab._render_list()
    assert len(tab.tree.get_children()) < len(tab.samples)
    assert tab.clear_button.cget("text") == "清空选中"
    prompts = []
    monkeypatch.setattr(
        "crX.interface.card_review.messagebox.askyesno",
        lambda title, message, **kwargs: prompts.append(message) or False,
    )
    tab.clear_selection_button.invoke()
    assert not tab.tree.selection() and str(tab.clear_button.cget("state")) == "disabled"
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert not tab.tree.selection() and str(tab.clear_button.cget("state")) == "disabled"
    tab._clear_samples()
    assert not prompts
    tab.select_all_button.invoke()
    selected_keys = set(tab.tree.get_children())
    before = set(tab.samples)
    assert set(tab.tree.selection()) == selected_keys and selected_keys < before
    tab.clear_button.invoke()
    assert "当前列表中选中的" in prompts[0]
    assert sample.file("frame").exists()
    ui.set_button_state("running")
    try:
        assert str(tab.clear_button.cget("state")) == "disabled"
        tab._clear_samples()
        assert sample.file("frame").exists()
    finally:
        ui.set_button_state("idle")
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *_args, **_kwargs: True)
    tab.clear_button.invoke()
    assert tab.clearing and str(ui.main_btn.cget("state")) == "disabled"
    _wait_ui(ui, lambda: not tab.clearing and not tab._scan_busy and sample.key not in tab.samples)
    assert set(tab.samples) == before - selected_keys
    assert not sample.directory.exists()
    assert not tab.tree.get_children() and tab.selected is None
    assert ui.notebook.tab(tab, "text") == "卡牌样本"
    assert str(ui.main_btn.cget("state")) == "normal"
    tab.filter_var.set("全部")
    tab.group_var.set("全部分组")
    tab._render_list()
    tab.select_all_button.invoke()
    assert set(tab.tree.selection()) == set(tab.samples)
    tab.clear_button.invoke()
    _wait_ui(ui, lambda: not tab.clearing and not tab._scan_busy and not tab.samples)


def test_stage_buttons_and_clearing_preserve_other_stages(desktop_ui, monkeypatch):
    monkeypatch.setattr("crX.utils.card_library.base_match", lambda _: ("UNKNOWN", 10000))
    ui, root = desktop_ui
    source = add_group_sample(root, "simple-a", "sample-000")
    other = add_group_sample(root, "simple-b", "sample-000")
    tab = ui.card_samples_tab
    assert tuple(tab.filter_combo.cget("values")) == ("待确认", "补库", "全部")
    assert "trial" not in tab.stage_buttons
    assert {"score", "runtime", "chosen"}.issubset(tab.tree.cget("columns"))
    tab.filter_var.set("待确认")
    tab.group_var.set("全部分组")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy and source.key in tab.tree.get_children())
    tab.tree.selection_set(source.key)
    tab.tree.focus(source.key)
    tab._select()
    tab.card_var.set("evo_cannon")

    def visible_buttons():
        return [button.cget("text") for button in tab.stage_buttons.values() if button.winfo_manager()]

    assert visible_buttons() == ["确认"]
    tab.stage_buttons["confirm"].invoke()
    _wait_ui(
        ui,
        lambda: not tab._action_busy
        and not tab._scan_busy
        and tab.selected is not None
        and tab.selected.key == other.key,
    )
    reviewed = {sample.key: sample for sample in scan_samples(root)[0]}
    assert reviewed[source.key].status == "trial"
    assert reviewed[other.key].status == "pending"
    tab.filter_var.set("补库")
    tab._render_list()
    assert visible_buttons() == ["重新验证", "撤销"]
    assert tab.tree.get_children() == (source.key,)
    assert tab.tree.set(source.key, "score") == "—"
    assert tab.tree.set(source.key, "status") == "暂无可用补库"
    assert tab.tree.set(source.key, "runtime") == "无匹配记录"
    assert tab.tree.set(source.key, "chosen") == "未使用"
    assert "缺少其他对局的同卡样本" in tab.tree.set(source.key, "validation_reason")
    if tab.selected.status == "trial":
        tab.stage_buttons["promote"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert "未合格项保持未启用" in tab.message_var.get()
    prompts = []
    monkeypatch.setattr(
        "crX.interface.card_review.messagebox.askyesno",
        lambda title, message, **kwargs: prompts.append(message) or True,
    )
    tab.clear_button.invoke()
    _wait_ui(ui, lambda: not tab.clearing and not tab._scan_busy and source.key not in tab.samples)
    assert "补库" in prompts[0] and "1 张" in prompts[0]
    assert not source.file("frame").exists() and other.file("frame").exists()
    tab.filter_var.set("待确认")
    tab._render_list()
    assert tab.tree.get_children() == (other.key,)
    tab.filter_var.set("全部")
    tab._render_list()
    assert visible_buttons() == []


def test_card_group_score_order_and_selection(desktop_ui, monkeypatch):
    ui, root = desktop_ui
    tab = ui.card_samples_tab
    _wait_ui(ui, lambda: not tab._scan_busy and not tab._action_busy)
    monkeypatch.setattr(tab, "refresh", lambda: None)
    samples = [add_group_sample(root, "score-order", f"sample-{index:03d}") for index in range(6)]
    for sample, score in zip(samples, (10, None, 75, 75, 50, 90), strict=True):
        sample.review = {"library_status": "trial", "library_score": score}
    tab.samples = {sample.key: sample for sample in samples}
    groups = {
        samples[0].key: [sample.key for sample in samples[:4]],
        samples[4].key: [sample.key for sample in samples[4:]],
    }
    tab.filter_var.set("全部")
    tab.group_var.set("全部分组")
    tab._update_groups(groups)
    tab._render_list()
    assert tab.tree.get_children() == tuple(samples[index].key for index in (2, 3, 0, 1, 5, 4))
    assert all(tab.tree.set(key, "status") == "证据不足" for key in tab.tree.get_children())
    tab.tree.selection_set(samples[0].key)
    tab.tree.focus(samples[0].key)
    tab._select()
    tab.note_var.set("正在编辑")
    samples[0].review["library_score"] = 95
    tab._update_groups(groups)
    tab._render_list()
    assert tab.tree.get_children()[0] == samples[0].key
    assert tab.tree.selection() == (samples[0].key,)
    assert tab.note_var.get() == "正在编辑"


def test_library_summary_shows_one_result_per_card_and_tracks_selection(desktop_ui, monkeypatch):
    ui, root = desktop_ui
    tab = ui.card_samples_tab
    _wait_ui(ui, lambda: not tab._scan_busy and not tab._action_busy)
    monkeypatch.setattr(tab, "refresh", lambda: None)
    rows = [
        ("knight", "trial", 100),
        ("knight", "included", 60),
        ("knight", "trial", 99),
        ("evo_knight", "trial", 30),
        ("cannon", "trial", 90),
        ("cannon", "covered", 10),
        ("evo_knight", "trial", 20),
    ]
    passed = {"correct": 1, "wrong": 0, "extra": 1, "other_cards": 1}
    samples = [add_group_sample(root, "summary-view", f"sample-{index:03d}") for index in range(len(rows))]
    for sample, (card, status, score) in zip(samples, rows, strict=True):
        sample.review = {
            "status": "confirmed",
            "confirmed_card_id": card,
            "library_status": status,
            "library_score": score,
            "library_selected": status == "included",
            "validation": passed if status == "included" else {"correct": 0, "wrong": 0, "same_card_samples": 0},
        }
    tab.samples = {sample.key: sample for sample in samples}
    groups = {
        samples[0].key: [sample.key for sample in samples[:4]],
        samples[4].key: [sample.key for sample in samples[4:]],
    }
    tab._update_groups(groups)
    tab.group_var.set(next(iter(tab.group_choices)))
    tab.filter_var.set("补库")
    tab._render_list()
    assert set(tab.tree.get_children()) == {samples[index].key for index in (1, 3, 5)}
    assert tuple(tab.tree.cget("displaycolumns")) == ("card", "status", "score", "validation_reason", "runtime")
    assert str(tab.group_combo.cget("state")) == "disabled" and tab.group_var.get() == "全部分组"
    assert tab.tree.set(samples[1].key, "status") == "当前使用"
    assert tab.tree.set(samples[1].key, "score") == "60.0"
    assert tab.tree.set(samples[3].key, "score") == "—"
    assert tab.tree.set(samples[3].key, "validation_reason") == "缺少其他对局的同卡样本"
    assert tab.tree.set(samples[5].key, "status") == "原库可识别"
    tab.tree.selection_set(samples[1].key)
    tab.tree.focus(samples[1].key)
    tab._select()
    assert {sample.key for sample in tab._action_samples()} == {sample.key for sample in samples[:3]}
    assert tab._selected_clear_keys() == {sample.key for sample in samples[:3]}
    tab.tree.selection_set(samples[3].key)
    tab.tree.focus(samples[3].key)
    tab._select()
    samples[6].review.update(library_status="included", library_selected=True, validation=passed)
    tab._update_groups(groups)
    tab._render_list()
    assert tab.tree.selection() == (samples[6].key,)  # Keep the selected card when its representative changes.
    assert tab.tree.set(samples[6].key, "status") == "当前使用"
    assert len(tab.samples) == 7 and all(sample.file("frame").exists() for sample in samples)
    tab.filter_var.set("全部")
    tab._render_list()
    assert len(tab.tree.get_children()) == 7 and str(tab.group_combo.cget("state")) == "readonly"


def test_legacy_confirmed_records_retry_from_library_without_confirmed_filter(desktop_ui, tmp_path, monkeypatch):
    from crX.interface.card_review import CardReviewTab

    monkeypatch.setattr("crX.utils.card_library.base_match", lambda _: ("UNKNOWN", 10000))
    ui, _root = desktop_ui
    source = add_group_sample(tmp_path, "legacy-confirmed", "sample-000")
    save_review(source, "confirmed", "evo_cannon", "历史确认记录")
    tab = CardReviewTab(ui.notebook, root=tmp_path)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and source.key in tab.samples)
        assert tuple(tab.filter_combo.cget("values")) == ("待确认", "补库", "全部")
        tab.filter_var.set("补库")
        tab._render_list()
        assert tab.tree.get_children() == (source.key,)
        assert "重新验证" in tab.tree.set(source.key, "validation_reason")
        assert str(tab.stage_buttons["promote"].cget("state")) == "normal"
        tab.stage_buttons["promote"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert tab.samples[source.key].status == "trial"
        assert tab.samples[source.key].review["note"] == "历史确认记录"
        tab.stage_buttons["revoke"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert tab.samples[source.key].status == "confirmed"
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert tab.samples[source.key].status == "confirmed"  # Refresh must not undo an explicit revocation.
        tab.stage_buttons["promote"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert tab.samples[source.key].status == "trial"
    finally:
        tab.destroy()


def test_summary_clear_selected_card_warns_and_keeps_unselected_card(desktop_ui, tmp_path, monkeypatch):
    from crX.interface.card_review import CardReviewTab
    from crX.utils import card_library as library

    ui, _root = desktop_ui
    rows = [add_group_sample(tmp_path, "chosen-clear", f"sample-{index:03d}") for index in range(3)]
    for sample, card in zip(rows, ("knight", "knight", "evo_knight"), strict=True):
        save_review(sample, "confirmed", card)
    passed = {"correct": 1, "wrong": 0, "extra": 1, "other_cards": 1}
    entries = [
        {
            "id": f"{index + 1:016x}",
            "card_id": card,
            "sources": [sample.key],
            "fingerprint": library.sample_fingerprint(sample).tolist(),
            "status": "trial" if index == 1 else "active",
            "auto_selected": index != 1,
            "validation": passed,
            "score": 50 if index == 1 else 70,
        }
        for index, (sample, card) in enumerate(zip(rows, ("knight", "knight", "evo_knight"), strict=True))
    ]
    library._write_library(tmp_path, entries)
    monkeypatch.setattr(library, "refresh_ranked_library", lambda _root: None)
    prompts = []
    approve = False

    def confirm(_title, message, **_kwargs):
        prompts.append(message)
        return approve

    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", confirm)
    tab = CardReviewTab(ui.notebook, root=tmp_path)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and rows[2].key in tab.samples)
        tab.filter_var.set("补库")
        tab._render_list()
        assert len(tab.tree.get_children()) == 2
        tab.tree.selection_set(rows[0].key)
        tab.tree.focus(rows[0].key)
        tab._select()
        assert str(tab.clear_button.cget("state")) == "normal"  # Current use is not silently exempted.
        before = library.library_path(tmp_path).read_bytes()
        tab.clear_button.invoke()
        assert "1 种卡牌，共 2 张样本" in prompts[0]
        assert "后台候选" in prompts[0] and "正在使用" in prompts[0]
        assert library.library_path(tmp_path).read_bytes() == before
        approve = True
        tab.clear_button.invoke()
        _wait_ui(ui, lambda: not tab.clearing and not tab._scan_busy and rows[0].key not in tab.samples)
        assert set(tab.samples) == {rows[2].key}
        assert not rows[0].file("frame").exists() and not rows[1].file("frame").exists()
        assert rows[2].file("frame").exists()
        assert library.read_library(tmp_path) == [entries[2]]
    finally:
        tab.destroy()
