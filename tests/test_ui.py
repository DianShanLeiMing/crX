"""Desktop-only GUI smoke coverage; no device calls."""

import os
import sys

import pytest

from crX.emulators import EmulatorType
from crX.interface.enums import UIField

pytestmark = pytest.mark.skipif(sys.platform != "win32" and not os.environ.get("DISPLAY"), reason="No desktop display")


def test_tabs_settings_themes_and_running_state():
    from crX.interface.ui import CrXUI

    ui = CrXUI()
    ui.withdraw()
    try:
        ui.update()
        assert [ui.notebook.tab(tab, "text") for tab in ui.notebook.tabs()] == ["任务", "设备", "统计", "设置"]
        assert ui.get_all_values()[UIField.MAX_FIGHTS] == "0"
        ui.set_all_values({UIField.MAX_FIGHTS: 5})
        assert ui.max_fights_spinbox.get() == "5"
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
        ui.destroy()
