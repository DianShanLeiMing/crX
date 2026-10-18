"""Desktop-only GUI smoke coverage; no device calls."""

import os
import sys
import time

import pytest

from crX.emulators import EmulatorType
from crX.interface.enums import UIField
from tests.test_card_review import make_sample
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


def test_review_tab_confirm_export_ignore_and_reopen(desktop_ui):
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
        assert str(tab.action_buttons[0].cget("state")) == "normal"
        tab.action_buttons[0].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and tab.selected is None)
        assert ui.notebook.tab(tab, "text") == "卡牌样本"
        tab.filter_var.set("已确认")
        tab._render_list()
        assert tab.selected is not None
        assert tab.selected.card_id == "evo_cannon"
        assert tab.note_var.get() == "人工核对"
        tab.action_buttons[3].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert (source.directory / "sample-000-candidate.json").is_file()
        assert tab.selected is not None
        assert tab.selected.status == "confirmed"
        tab.action_buttons[1].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and tab.selected is None)
        tab.filter_var.set("已忽略")
        tab._render_list()
        assert tab.selected is not None
        assert tab.selected.status == "ignored"
        tab.action_buttons[2].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert ui.notebook.tab(tab, "text") == "卡牌样本 (1)"
        assert not errors
    finally:
        ui.set_button_state("idle")


def test_failure_review_notes_status_and_running_state(desktop_ui):
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
    tab.note_var.set("已确认需要补充识别")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.note_var.get() == "已确认需要补充识别"
    ui.set_button_state("running")
    try:
        assert str(tab.buttons[1].cget("state")) == "normal"
        tab.buttons[1].invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy and tab.selected is None)
        assert ui.notebook.tab(tab, "text") == "异常样本"
        tab.filter_var.set("已处理")
        tab._render()
        assert tab.note_var.get() == "已确认需要补充识别"
        tab.buttons[3].invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy)
        assert ui.notebook.tab(tab, "text") == "异常样本 (1)"
    finally:
        ui.set_button_state("idle")
