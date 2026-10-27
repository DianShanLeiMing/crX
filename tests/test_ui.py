"""Desktop-only GUI smoke coverage; no device calls."""

import gc
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


@pytest.fixture(autouse=True)
def collect_tk_garbage(desktop_ui):
    yield
    # Reclaim destroyed test widgets on Tk's owning thread, before the next scan.
    gc.collect()


def test_tabs_settings_themes_and_running_state(desktop_ui):
    ui, _root = desktop_ui
    try:
        ui.update()
        assert [ui.notebook.tab(tab, "text") for tab in ui.notebook.tabs()] == [
            "任务",
            "设备",
            "统计",
            "卡牌样本",
            "异常流程",
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


def test_deck_checkbox_order_priority_and_json_roundtrip(desktop_ui, tmp_path, monkeypatch):
    from crX.__main__ import BotApplication, make_job_dictionary
    from crX.utils import caching

    ui, _root = desktop_ui
    monkeypatch.setattr(caching, "top_level", str(tmp_path))
    application = BotApplication.__new__(BotApplication)
    application.ui = ui
    ui.config_callback = application._on_config_change
    try:
        ui.set_all_values({UIField.SELECTED_DECKS: []})
        assert list(ui.deck_checkboxes) == list(range(1, 11))
        for number in [4, 2, 7]:
            ui.deck_checkboxes[number].invoke()
        assert ui.get_all_values()[UIField.SELECTED_DECKS] == [4, 2, 7]
        assert "4 → 2 → 7" in ui.deck_order_var.get()
        ui.deck_checkboxes[2].invoke()
        ui.deck_checkboxes[2].invoke()
        assert ui.get_all_values()[UIField.SELECTED_DECKS] == [4, 7, 2]
        ui.variables[UIField.RANDOM_DECKS_USER_TOGGLE].set(True)
        ui.variables[UIField.CYCLE_DECKS_USER_TOGGLE].set(True)
        persisted = caching.USER_SETTINGS_CACHE.load_data()
        assert persisted[UIField.RANDOM_DECKS_USER_TOGGLE] is True
        assert persisted[UIField.CYCLE_DECKS_USER_TOGGLE] is True
        assert make_job_dictionary(persisted)[UIField.SELECTED_DECKS] == [4, 7, 2]
        ui.set_all_values({UIField.SELECTED_DECKS: []})
        ui.set_all_values(persisted)
        assert ui.get_all_values()[UIField.SELECTED_DECKS] == [4, 7, 2]
        assert [number for number, var in ui.deck_variables.items() if var.get()] == [2, 4, 7]
        assert "4 → 7 → 2" in ui.deck_order_var.get()
        ui.set_button_state("running")
        ui.deck_checkboxes[10].invoke()
        assert ui.get_all_values()[UIField.SELECTED_DECKS] == [4, 7, 2]
        assert all(str(widget.cget("state")) == "disabled" for widget in ui.deck_checkboxes.values())
    finally:
        ui.config_callback = None
        ui.set_button_state("idle")
        ui.set_all_values(
            {
                UIField.SELECTED_DECKS: [1, 2],
                UIField.RANDOM_DECKS_USER_TOGGLE: False,
                UIField.CYCLE_DECKS_USER_TOGGLE: False,
            }
        )


def _wait_ui(ui, condition):
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        ui.update()
        if condition():
            return
        time.sleep(0.01)
    raise AssertionError("GUI background operation did not complete")


def test_review_tab_confirmation_automatically_adds_reference_without_library_actions(desktop_ui):
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
        assert not tab.tree.get_children()
        assert tab.samples[source.key].review["formal_covered"]
        assert not tab.stage_buttons["confirm"].winfo_manager()
        assert tab.tree.heading("enabled", "text") == "样本参考数"
        tab.filter_var.set("全部")
        tab._render_list()
        assert tab.selected is not None
        assert tab.tree.set(source.key, "status") == "已忽略，正式库已有相同或近似参考"
        from tkinter.font import Font

        assert tab.tree.column("status", "width") >= Font(root=tab, font="TkDefaultFont").measure(
            tab.tree.set(source.key, "status")
        )
        assert tab.selected.card_id == "evo_cannon"
        assert tab.note_var.get() == "人工核对"
        assert tab.selected.status == "included"
        assert not (source.directory / "sample-000-candidate.json").exists()
        assert set(tab.stage_buttons) == {"confirm", "formal"}
        assert not errors
    finally:
        ui.set_button_state("idle")


def test_failure_review_status_and_running_state(desktop_ui):
    ui, root = desktop_ui
    make_failure(root / "failures")
    tab = ui.failure_samples_tab
    tab.refresh()
    _wait_ui(ui, lambda: tab.selected is not None and tab._preview is not None)
    assert ui.notebook.tab(tab, "text") == "异常流程 (1)"
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
        assert ui.notebook.tab(tab, "text") == "异常流程"
        tab.filter_var.set("已处理")
        tab._render()
        tab.buttons[2].invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy)
        assert ui.notebook.tab(tab, "text") == "异常流程 (1)"
    finally:
        ui.set_button_state("idle")


def test_failure_selection_deletion_filter_scope_and_running_guard(desktop_ui, tmp_path, monkeypatch):
    import threading

    from crX.interface.failure_review import FailureReviewTab
    from crX.utils.failure_samples import delete_failure_samples, save_failure_review, scan_failures

    ui, _root = desktop_ui
    pending = make_failure(tmp_path)
    handled = make_failure(tmp_path)
    ignored = make_failure(tmp_path)
    save_failure_review(handled, "handled", "修复完成")
    save_failure_review(ignored, "ignored", "无需处理")
    counts = []
    prior_failure_tab = ui.failure_samples_tab
    tab = FailureReviewTab(
        ui.notebook, root=tmp_path, count_callback=counts.append, clear_busy_callback=ui._update_clear_busy
    )
    monkeypatch.setattr(ui, "failure_samples_tab", tab)
    release = threading.Event()
    prompts = []

    def confirm(title, message, **kwargs):
        prompts.append(message)
        return False

    def delayed_delete(root, keys):
        assert release.wait(4)
        return delete_failure_samples(root, keys)

    monkeypatch.setattr("crX.interface.failure_review.messagebox.askyesno", confirm)
    monkeypatch.setattr("crX.interface.failure_review.delete_failure_samples", delayed_delete)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and tab.selected is not None)
        assert tab.tree.get_children() == (pending["key"],)
        tab.select_all_button.invoke()
        assert tab.tree.selection() == (pending["key"],)
        tab.delete_button.invoke()
        assert "待处理" in prompts[-1] and "1 条" in prompts[-1]
        assert len(scan_failures(tmp_path)[0]) == 3  # Cancelling preserves all evidence.
        tab.filter_var.set("全部")
        tab._render()
        tab.tree.event_generate("<Control-a>")
        tab._select_all()
        assert len(tab.tree.selection()) == 3
        tab.clear_selection_button.invoke()
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert not tab.tree.selection() and tab.selected is None
        assert str(tab.delete_button.cget("state")) == "disabled"
        keys = {pending["key"], handled["key"]}
        tab.tree.selection_set(tuple(keys))
        tab._select()
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert set(tab.tree.selection()) == keys
        ui.set_button_state("running")
        assert str(tab.delete_button.cget("state")) == "disabled"
        tab._delete_selected()
        assert len(prompts) == 1
        ui.set_button_state("idle")
        monkeypatch.setattr(
            "crX.interface.failure_review.messagebox.askyesno",
            lambda title, message, **kwargs: prompts.append(message) or True,
        )
        tab.delete_button.invoke()
        assert "2 条" in prompts[-1] and "无法撤销" in prompts[-1]
        assert tab.clearing and str(ui.main_btn.cget("state")) == "disabled"
        tab._delete_selected()
        assert len(prompts) == 2  # Repeated clicks cannot launch another deletion.
        ui.card_samples_tab.clearing = True
        release.set()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy and len(tab.records) == 1)
        assert set(tab.records) == {ignored["key"]}
        assert tab.tree.get_children() == (ignored["key"],)
        assert "已删除 2 条" in tab.message_var.get() and counts[-1] == 0
        assert str(ui.main_btn.cget("state")) == "disabled"  # Other cleanup still blocks starting.
        ui.card_samples_tab.clearing = False
        ui._update_clear_busy()
        assert str(ui.main_btn.cget("state")) == "normal"
        tab._select_all()
        tab.delete_button.invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy and not tab.records)
        assert not tab.tree.get_children() and tab.selected is None and tab._preview is None
    finally:
        release.set()
        ui.card_samples_tab.clearing = False
        ui.set_button_state("idle")
        ui.failure_samples_tab = prior_failure_tab
        tab.destroy()


def test_failure_deletion_rechecks_running_state_and_recovers_after_io_error(desktop_ui, tmp_path, monkeypatch):
    from crX.interface.failure_review import FailureReviewTab

    ui, _root = desktop_ui
    record = make_failure(tmp_path)
    busy = []
    tab = FailureReviewTab(ui.notebook, root=tmp_path, clear_busy_callback=busy.append)

    def start_during_confirmation(*args, **kwargs):
        tab.set_running(True)
        return True

    def fail_delete(*args):
        raise OSError("模拟文件被占用")

    try:
        _wait_ui(ui, lambda: not tab._scan_busy and tab.selected is not None)
        monkeypatch.setattr("crX.interface.failure_review.messagebox.askyesno", start_during_confirmation)
        tab.delete_button.invoke()
        assert not tab._busy and not busy and record["directory"].exists()
        tab.set_running(False)
        monkeypatch.setattr("crX.interface.failure_review.messagebox.askyesno", lambda *args, **kwargs: True)
        monkeypatch.setattr("crX.interface.failure_review.delete_failure_samples", fail_delete)
        tab.delete_button.invoke()
        _wait_ui(ui, lambda: not tab._busy and not tab._scan_busy)
        assert not tab.clearing and busy == [True, False]
        assert "操作失败" in tab.message_var.get()
        assert record["directory"].exists() and record["key"] in tab.records
        assert str(tab.delete_button.cget("state")) == "normal"
    finally:
        tab.destroy()


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
    assert all(reviewed[key].status == "included" for key in (first.key, second.key))
    tab.filter_var.set("补库")
    tab._render_list()
    trial_keys = [key for key in tab.tree.get_children() if tab.samples[key].status == "included"]
    assert len(trial_keys) == 1
    assert all(tab.tree.set(key, "validation_reason") in {"本地试用中", "内置参考使用中"} for key in trial_keys)
    assert {first.key, second.key}.issubset(tab.samples)  # Hidden candidates remain available.
    labels_before = {key: tab.tree.set(key, "validation_reason") for key in trial_keys}
    tab.filter_var.set("全部")
    tab._render_list()
    assert all(tab.tree.set(key, "status") == "已启用" for key in trial_keys)
    tab.filter_var.set("补库")
    tab._render_list()
    assert {key: tab.tree.set(key, "validation_reason") for key in trial_keys} == labels_before
    assert str(tab.select_group_button.cget("state")) == "disabled"
    assert len(tab.tree.selection()) == 1
    assert set(tab.stage_buttons) == {"confirm", "formal"}
    assert not tab.stage_buttons["confirm"].winfo_manager()


def test_card_samples_clear_confirmation_running_guard_and_refresh(desktop_ui, monkeypatch):
    ui, root = desktop_ui
    sample = add_group_sample(root, "clear-all-visible", "sample-000")
    tab = ui.card_samples_tab
    tab.filter_var.set("待确认")
    tab.group_var.set("全部分组")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and sample.key in tab.tree.get_children())
    assert any(item.status == "included" for item in tab.samples.values())
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
    monkeypatch.setattr("crX.utils.formal_library._bundled_references", lambda: [("giant", [[891] + [0] * 15] * 4)])
    ui, root = desktop_ui
    source = add_group_sample(root, "simple-a", "sample-000")
    other = add_group_sample(root, "simple-b", "sample-000")
    tab = ui.card_samples_tab
    assert tuple(tab.filter_combo.cget("values")) == ("待确认", "补库", "全部")
    assert "trial" not in tab.stage_buttons
    tab.filter_var.set("待确认")
    tab.group_var.set("全部分组")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy and source.key in tab.tree.get_children())
    assert tuple(tab.tree.cget("columns")) == ("group", "session", "reason")
    assert tuple(tab.tree.cget("displaycolumns")) == ("#all",)
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
    assert reviewed[source.key].status == "included"
    assert reviewed[other.key].status == "pending"
    tab.filter_var.set("补库")
    tab._render_list()
    assert visible_buttons() == ["确认正式补库"]
    assert set(tab.stage_buttons) == {"confirm", "formal"}
    assert tab.tree.get_children() == (source.key,)
    assert tab.tree.set(source.key, "enabled") == "1"
    assert tab.tree.set(source.key, "runtime") == "0"
    assert "pending" not in tab.tree.cget("columns")
    assert tab.tree.set(source.key, "validation_reason") == "本地试用中"
    assert "本地试用中" in tab.description_var.get()
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
    assert visible_buttons() == ["更正卡名"]


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
    assert tab.tree.get_children() == tuple(sample.key for sample in samples)
    assert all(tab.tree.set(key, "status") == "待启用" for key in tab.tree.get_children())
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
    assert set(tab.tree.get_children()) == {samples[index].key for index in (1, 3, 4)}
    assert tuple(tab.tree.cget("columns")) == ("card", "enabled", "runtime", "formal", "validation_reason")
    assert str(tab.group_combo.cget("state")) == "disabled" and tab.group_var.get() == "全部分组"
    assert tab.tree.set(samples[1].key, "validation_reason") == "本地试用中"
    assert "score" not in tab.tree.cget("columns")
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
    assert tab.tree.set(samples[6].key, "validation_reason") == "本地试用中"
    assert len(tab.samples) == 7 and all(sample.file("frame").exists() for sample in samples)
    tab.filter_var.set("全部")
    tab._render_list()
    assert len(tab.tree.get_children()) == 7 and str(tab.group_combo.cget("state")) == "readonly"


def test_library_summary_fits_small_window_and_wraps_full_description(desktop_ui, tmp_path):
    import tkinter as tk
    from tkinter.font import Font

    from crX.interface.card_review import CardReviewTab
    from crX.utils.card_library import update_library

    ui, _root = desktop_ui
    sample = add_group_sample(tmp_path, "layout", "sample-000")
    save_review(sample, "confirmed", "knight", "布局测试")
    update_library(tmp_path, [sample], "enable")
    window = tk.Toplevel(ui)
    tab = CardReviewTab(window, root=tmp_path)
    tab.pack(fill="both", expand=True)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy)
        tab.filter_var.set("补库")
        tab._render_list()
        column_sizes = []
        for width in (760, 850, 1100):
            window.geometry(f"{width}x700")
            ui.update()
            heading_font = Font(
                root=ui, font=ui.style.lookup("CardSamples.Treeview.Heading", "font") or "TkHeadingFont"
            )
            for column in tab.tree.cget("columns"):
                title = tab.tree.heading(column, "text")
                assert heading_font.measure(title) + 20 <= tab.tree.column(column, "width")
            assert heading_font.measure("缩略图") + 24 <= tab.thumbnail_tree.column("#0", "width")
            bbox = tab.tree.bbox(sample.key, "validation_reason")
            assert bbox and bbox[0] + bbox[2] <= tab.tree.winfo_width()
            font = Font(root=ui, font=ui.style.lookup("Treeview", "font"))
            assert font.measure(tab.tree.set(sample.key, "validation_reason")) + 12 <= bbox[2]
            assert "本地试用中" in tab.description_var.get()
            assert int(tab.description_label.cget("wraplength")) <= tab.description_label.winfo_width()
            card_bbox = tab.tree.bbox(sample.key, "card")
            assert card_bbox
            column_sizes.append((bbox[2], card_bbox[2]))
        assert len(set(column_sizes)) == 1  # Keep columns compact when the window grows.
        widths = {column: tab.tree.column(column, "width") for column in tab.tree.cget("columns")}
        separators = [x for x in range(tab.tree.winfo_width()) if tab.tree.identify_region(x, 10) == "separator"]
        assert separators
        for index, x in enumerate(separators):
            timestamp = (index + 1) * 1000
            tab.tree.event_generate("<Motion>", x=x, y=10)
            assert tab.tree.cget("cursor") == ""
            tab.tree.event_generate("<Button-1>", x=x, y=10, time=timestamp)
            tab.tree.event_generate("<B1-Motion>", x=x + 80, y=10, time=timestamp + 1)
            tab.tree.event_generate("<ButtonRelease-1>", x=x + 80, y=10, time=timestamp + 2)
        ui.update()
        assert {column: tab.tree.column(column, "width") for column in widths} == widths
        tab.description_var.set("很长的完整说明" * 50)
        window.geometry("760x700")
        ui.update()
        assert tab.description_label.winfo_height() > font.metrics("linespace") * 2
        assert tab.description_label.winfo_x() + tab.description_label.winfo_width() <= tab.winfo_width()
        tab._clear_selection()
        assert tab.description_var.get() == "选择样本可查看完整说明。"
    finally:
        window.destroy()


def test_review_details_expand_scroll_and_restore_without_resizing_window(desktop_ui, tmp_path):
    import tkinter as tk

    from crX.interface.card_review import CardReviewTab

    ui, _root = desktop_ui
    sample = add_group_sample(tmp_path, "details-layout", "sample-000")
    window = tk.Toplevel(ui)
    window.geometry("760x540")
    tab = CardReviewTab(window, root=tmp_path)
    tab.pack(fill="both", expand=True)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and tab.selected is not None and bool(tab._images))
        ui.update()
        window_size = (window.winfo_width(), window.winfo_height())
        selection = tab.tree.selection()
        rows = tab.tree.get_children()
        tab.note_var.set("保留正在编辑的备注")
        # The wider native sash can be dragged while both panes remain visible.
        assert int(tab.review_panes.cget("sashwidth")) >= 8
        assert tab.review_panes.cget("sashrelief") == "flat"
        assert not tab.review_panes.tk.getboolean(tab.review_panes.cget("showhandle"))
        tab.review_panes.sash_place(0, 0, tab.review_panes.sash_coord(0)[1] + 12)
        ui.update()
        table_height = tab.sample_table.winfo_height()
        details_height = tab.details.winfo_height()
        assert details_height > 0
        tab.expand_details_button.invoke()
        ui.update()
        assert len(tab.review_panes.panes()) == 1
        assert tab.details.winfo_height() > details_height + 60
        assert (window.winfo_width(), window.winfo_height()) == window_size
        assert tab.expand_details_button.cget("text") == "恢复列表"
        tab.detail_var.set("识别详情与候选信息\n" * 40)
        ui.update()
        tab.info_panel.yview_moveto(1)
        ui.update()
        scroll_position = tab.info_panel.vscroll.get()
        assert scroll_position[0] > 0 and scroll_position[-1] == 1
        tab.expand_details_button.invoke()
        ui.update()
        assert len(tab.review_panes.panes()) == 2
        assert abs(tab.sample_table.winfo_height() - table_height) <= 4
        assert tab.tree.get_children() == rows and tab.tree.selection() == selection == (sample.key,)
        assert tab.note_var.get() == "保留正在编辑的备注"
        assert tab.expand_details_button.cget("text") == "展开详情"
        tab.expand_details_button.invoke()
        window.geometry("760x680")
        ui.update()
        assert tab.details.winfo_height() > details_height + 180
        tab.expand_details_button.invoke()
        ui.update()
        table_height = tab.sample_table.winfo_height()
        window.geometry("760x820")
        ui.update()
        assert tab.sample_table.winfo_height() > table_height + 100
        for stage in ("待确认", "补库"):
            tab.filter_var.set(stage)
            tab._render_list()
            ui.update()
            tab.review_panes.sash_place(0, 0, tab.review_panes.winfo_height())
            ui.update()
            assert tab.review_panes.sash_coord(0)[1] >= tab.review_panes.winfo_height() - 10
            assert tab.sample_table.winfo_height() >= tab.review_panes.winfo_height() - 10
        confirm = tab.stage_buttons["confirm"]
        assert confirm.winfo_rooty() + confirm.winfo_height() <= tab.winfo_rooty() + tab.winfo_height()
    finally:
        window.destroy()


def test_row_thumbnails_align_scroll_and_support_bulk_selection(desktop_ui, tmp_path):
    from crX.interface.card_review import CardReviewTab

    ui, _root = desktop_ui
    samples = [add_group_sample(tmp_path, "thumbnail-rows", f"sample-{index:03d}") for index in range(10)]
    samples[-1].file("card").unlink()
    prior_geometry, prior_tab = ui.geometry(), ui.notebook.select()
    ui.attributes("-alpha", 0)
    ui.geometry("850x760")
    tab = CardReviewTab(ui.notebook, root=tmp_path)
    ui.notebook.add(tab, text="缩略图布局检查")
    ui.notebook.select(tab)
    ui.deiconify()
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and bool(tab._thumbnail_images))
        rows = tab.tree.get_children()
        assert tab.thumbnail_tree.get_children() == rows
        first = rows[0]
        image = tab._thumbnail_images[first]
        assert image is not None and image.width() <= 16 and image.height() <= 20
        assert tab.thumbnail_tree.item(first, "image")
        text_bbox = tab.tree.bbox(first)
        thumbnail_bbox = tab.thumbnail_tree.bbox(first)
        assert text_bbox and thumbnail_bbox
        assert text_bbox[1] == thumbnail_bbox[1] and text_bbox[3] == thumbnail_bbox[3]
        assert 20 <= text_bbox[3] <= 28
        viewport_bottom = tab.tree.winfo_height()
        visible_rows = [key for key in rows if (box := tab.tree.bbox(key)) and box[1] + box[3] <= viewport_bottom]
        assert len(visible_rows) >= 4, (len(visible_rows), tab.tree.winfo_height(), tab.sample_table.winfo_height())

        def assert_thumbnail_next_to_text(key):
            bbox = tab.tree.bbox(key, "reason")
            assert bbox
            gap = tab.thumbnail_tree.winfo_rootx() - (tab.tree.winfo_rootx() + bbox[0] + bbox[2])
            assert 0 <= gap <= 8

        assert_thumbnail_next_to_text(first)
        content_width = tab.text_frame.winfo_width()
        thumbnail_width = tab.thumbnail_tree.winfo_width()
        from tkinter.font import Font

        heading_font = Font(root=ui, font=ui.style.lookup("CardSamples.Treeview.Heading", "font") or "TkHeadingFont")
        assert thumbnail_width == tab.thumbnail_tree.column("#0", "width")
        assert thumbnail_width >= heading_font.measure("缩略图") + 24
        separator_x = next(
            x for x in range(thumbnail_width) if tab.thumbnail_tree.identify_region(x, 10) == "separator"
        )
        tab.thumbnail_tree.event_generate("<Motion>", x=separator_x, y=10)
        tab.thumbnail_tree.event_generate("<Button-1>", x=separator_x, y=10)
        tab.thumbnail_tree.event_generate("<B1-Motion>", x=separator_x + 80, y=10)
        tab.thumbnail_tree.event_generate("<ButtonRelease-1>", x=separator_x + 80, y=10)
        ui.update()
        assert tab.thumbnail_tree.column("#0", "width") == thumbnail_width
        outer_width = tab.rows_frame.winfo_width()
        assert tab.rows_frame.winfo_width() == tab.sample_table.winfo_width()
        ui.geometry("1100x760")
        ui.update()
        assert_thumbnail_next_to_text(first)
        assert tab.text_frame.winfo_width() == content_width
        assert tab.thumbnail_tree.winfo_width() == thumbnail_width
        assert tab.rows_frame.winfo_width() > outer_width
        assert tab.rows_frame.winfo_width() == tab.sample_table.winfo_width()
        assert tab.row_scroll.winfo_rootx() + tab.row_scroll.winfo_width() == (
            tab.rows_frame.winfo_rootx() + tab.rows_frame.winfo_width() - 1
        )

        click_time = [1000]

        def click_thumbnail(key, state=0):
            tab.tree.see(key)
            ui.update()
            bbox = tab.thumbnail_tree.bbox(key)
            assert bbox
            x, y = bbox[0] + bbox[2] // 2, bbox[1] + bbox[3] // 2
            assert tab.thumbnail_tree.identify_row(y) == key
            click_time[0] += 1000  # Keep separate synthetic clicks out of Tk's double-click interval.
            tab.thumbnail_tree.event_generate("<Button-1>", x=x, y=y, state=state, time=click_time[0])
            tab.thumbnail_tree.event_generate("<ButtonRelease-1>", x=x, y=y, state=state, time=click_time[0] + 1)
            ui.update()

        click_thumbnail(rows[0])
        click_thumbnail(rows[1], state=0x0004)  # Ctrl adds another image to the selection.
        assert set(tab.tree.selection()) == set(tab.thumbnail_tree.selection()) == set(rows[:2])
        click_thumbnail(rows[3], state=0x0001)  # Shift selects a contiguous range.
        assert set(rows[1:4]).issubset(tab.tree.selection())
        assert tab.tree.selection() == tab.thumbnail_tree.selection()
        tab.select_all_button.invoke()
        ui.update()
        assert tab.tree.selection() == tab.thumbnail_tree.selection() == rows
        tab._scroll_rows("moveto", 1)
        ui.update()
        _wait_ui(ui, lambda: rows[-1] in tab._thumbnail_images)
        assert tab._thumbnail_images[rows[-1]] is None
        assert tab.thumbnail_tree.item(rows[-1], "text") == "无图"
        assert tab.thumbnail_tree.yview() == tab.tree.yview()
        tab.thumbnail_tree.event_generate("<MouseWheel>", delta=120)
        ui.update()
        assert tab.thumbnail_tree.yview() == tab.tree.yview()
        tab.clear_selection_button.invoke()
        ui.update()
        assert not tab.tree.selection() and not tab.thumbnail_tree.selection()
        tab.filter_var.set("全部")
        tab._render_list()
        ui.update()
        assert tab.thumbnail_tree.get_children() == tab.tree.get_children() == rows
        assert (
            tab.thumbnail_tree.winfo_rootx() + tab.thumbnail_tree.winfo_width()
            <= tab.sample_table.winfo_rootx() + tab.sample_table.winfo_width()
        )
        tab.filter_var.set("待确认")
        tab._render_list()
        ui.geometry("760x700")
        tab.tree.yview_moveto(0)
        ui.update()
        compact_rows = [
            key for key in rows if (box := tab.tree.bbox(key)) and box[1] + box[3] <= tab.tree.winfo_height()
        ]
        assert len(compact_rows) >= 2, (len(compact_rows), tab.tree.winfo_height())
        assert_thumbnail_next_to_text(rows[0])
    finally:
        ui.withdraw()
        ui.notebook.forget(tab)
        tab.destroy()
        ui.notebook.select(prior_tab)
        ui.geometry(prior_geometry)
        ui.attributes("-alpha", 1)


def test_thumbnail_results_follow_current_filter_without_resetting_edits(desktop_ui, tmp_path, monkeypatch):
    import threading
    import tkinter as tk

    import cv2

    from crX.interface.card_review import CardReviewTab
    from crX.utils.card_review import load_card_thumbnail

    ui, _root = desktop_ui
    first = add_group_sample(tmp_path, "first-thumbnail", "sample-000")
    art = cv2.imread(str(first.file("card")))
    second = add_group_sample(tmp_path, "second-thumbnail", "sample-000", pixels=art[:, ::-1])
    entered, release = threading.Event(), threading.Event()

    def delayed_thumbnail(sample):
        entered.set()
        assert release.wait(4)
        return load_card_thumbnail(sample)

    monkeypatch.setattr("crX.interface.card_review.load_card_thumbnail", delayed_thumbnail)
    window = tk.Toplevel(ui)
    window.geometry("850x740")
    tab = CardReviewTab(window, root=tmp_path)
    tab.pack(fill="both", expand=True)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy and entered.is_set())
        group = tab.sample_groups[second.key]
        tab.group_var.set(next(label for label, key in tab.group_choices.items() if key == group))
        tab._render_list()
        tab.note_var.set("后台读图时仍可编辑")
        assert tab.tree.get_children() == (second.key,)
        release.set()
        _wait_ui(ui, lambda: second.key in tab._thumbnail_images and not tab._thumbnail_busy)
        assert tab.thumbnail_tree.get_children() == (second.key,)
        assert first.key not in tab._thumbnail_images
        assert tab.tree.selection() == tab.thumbnail_tree.selection() == (second.key,)
        assert tab.note_var.get() == "后台读图时仍可编辑"
    finally:
        release.set()
        window.destroy()


def test_legacy_confirmed_records_retry_by_reconfirming_from_all(desktop_ui, tmp_path, monkeypatch):
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
        assert "全部列表重新确认卡名" in tab.tree.set(source.key, "validation_reason")
        assert set(tab.stage_buttons) == {"confirm", "formal"}
        assert not tab.stage_buttons["confirm"].winfo_manager()
        tab.filter_var.set("全部")
        tab._render_list()
        tab.stage_buttons["confirm"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert tab.samples[source.key].status == "included"
        assert tab.samples[source.key].review["note"] == "历史确认记录"
        tab.filter_var.set("补库")
        tab._render_list()
        assert not tab.tree.get_children()
        assert tab.samples[source.key].review["formal_covered"]
        assert tab.tree.heading("enabled", "text") == "样本参考数"
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert tab.samples[source.key].status == "included"
    finally:
        tab.destroy()


def test_reconfirmation_keeps_conflicting_labels_blocked_without_library_buttons(desktop_ui, tmp_path):
    from crX.interface.card_review import CardReviewTab

    ui, _root = desktop_ui
    source = add_group_sample(tmp_path, "enable-conflict", "sample-000")
    conflicting = add_group_sample(tmp_path, "enable-conflict", "sample-001")
    save_review(source, "confirmed", "evo_cannon")
    save_review(conflicting, "confirmed", "knight")
    tab = CardReviewTab(ui.notebook, root=tmp_path)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy)
        tab.filter_var.set("全部")
        tab._render_list()
        tab.tree.selection_set(source.key)
        tab.tree.focus(source.key)
        tab._select()
        tab.stage_buttons["confirm"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert tab.samples[source.key].status == "conflict"
        tab.filter_var.set("补库")
        tab._render_list()
        assert tab.tree.set(source.key, "enabled") == "0"
        assert "图像近似" in tab.tree.set(source.key, "validation_reason")
        assert set(tab.stage_buttons) == {"confirm", "formal"}
        assert not tab.stage_buttons["confirm"].winfo_manager()
    finally:
        tab.destroy()


def test_formal_promotion_preview_cancel_confirm_and_running_guard(desktop_ui, tmp_path, monkeypatch):
    import tkinter as tk

    from crX.interface.card_review import CardReviewTab
    from crX.utils import card_library as local
    from crX.utils import formal_library as formal
    from crX.utils.card_review import atomic_json
    from tests.test_card_library import fingerprint

    destination = tmp_path / "builtins.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(formal, "card_color_data", {})
    monkeypatch.setattr(formal, "CARD_FINGERPRINT_VARIANTS", {"giant": [fingerprint(15).tolist()]})
    monkeypatch.setattr(formal, "_runtime_cache", (None, 0.0, []))
    monkeypatch.setattr(
        local, "sample_fingerprint", lambda sample: fingerprint(4 if sample.name == "sample-001" else 0)
    )
    sample = add_group_sample(tmp_path, "promote-ui", "sample-000")
    save_review(sample, "confirmed", "knight")
    local.update_library(tmp_path, [sample], "enable")
    entry = local.read_library(tmp_path)[0]
    local.merge_match_stats(
        tmp_path,
        {
            entry["id"]: {
                "matches": 100,
                "distance_total": 4000,
                "margin_total": 20000,
                "assists": 100,
                "assist_battles": 3,
                "assist_distance_total": 4000,
                "assist_margin_total": 20000,
            }
        },
    )
    ui, _root = desktop_ui
    window = tk.Toplevel(ui)
    window.geometry("760x700")
    tab = CardReviewTab(window, root=tmp_path, clear_busy_callback=ui._update_clear_busy)
    tab.pack(fill="both", expand=True)
    monkeypatch.setattr(ui, "card_samples_tab", tab)
    try:
        _wait_ui(ui, lambda: not tab._scan_busy)
        tab.filter_var.set("补库")
        tab._render_list()
        assert tab.tree.set(sample.key, "formal") == "建议纳入"
        assert tab.tree.set(sample.key, "validation_reason") == "本地试用中"
        assert "补足识别 100 次" in tab.reference_panel.rows[entry["id"]]["label"].cget("text")
        ui.set_button_state("running")
        assert str(tab.stage_buttons["formal"].cget("state")) == "disabled"
        tab._prepare_formal()
        assert not tab.formalizing and not formal._read_entries(destination)
        ui.set_button_state("idle")
        tab.stage_buttons["formal"].invoke()
        _wait_ui(ui, lambda: tab._formal_dialog is not None)
        assert tab.formalizing and str(ui.main_btn.cget("state")) == "disabled"
        assert len(tab._formal_images) == 1 and tab._formal_images[0].height() > 20
        tab._cancel_formal()
        assert not tab.formalizing and not formal._read_entries(destination)
        assert str(ui.main_btn.cget("state")) == "normal"
        with monkeypatch.context() as patch:

            def fail_promotion(*_args):
                raise OSError("test disk full")

            patch.setattr("crX.interface.card_review.confirm_promotion", fail_promotion)
            tab.stage_buttons["formal"].invoke()
            _wait_ui(ui, lambda: tab._formal_dialog is not None)
            error_buttons = tab._formal_dialog.winfo_children()[-1].winfo_children()
            next(button for button in error_buttons if button.cget("text") == "确认加入内置库").invoke()
            _wait_ui(ui, lambda: not tab.formalizing and not tab._scan_busy)
            assert "test disk full" in tab.message_var.get()
            assert not formal._read_entries(destination)
            assert str(ui.main_btn.cget("state")) == "normal"
        tab.stage_buttons["formal"].invoke()
        _wait_ui(ui, lambda: tab._formal_dialog is not None)
        dialog = tab._formal_dialog
        buttons = dialog.winfo_children()[-1].winfo_children()
        next(button for button in buttons if button.cget("text") == "确认加入内置库").invoke()
        _wait_ui(ui, lambda: not tab.formalizing and not tab._scan_busy)
        assert len(formal._read_entries(destination)) == 1
        assert not tab.tree.get_children()
        assert tab.samples[sample.key].review["formal_covered"]
        assert "补库 0 种卡牌" in tab.summary_var.get()
        assert str(tab.stage_buttons["formal"].cget("state")) == "disabled"
        assert str(ui.main_btn.cget("state")) == "normal"
        assert str(tab.clear_button.cget("state")) == "disabled"
        before = destination.read_bytes()
        prompts = []
        monkeypatch.setattr(
            "crX.interface.card_review.messagebox.askyesno",
            lambda _title, message, **_kwargs: prompts.append(message) or True,
        )
        newer = add_group_sample(tmp_path, "promote-ui", "sample-001")
        save_review(newer, "confirmed", "knight")
        local.update_library(tmp_path, [newer], "enable")
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy and newer.key in tab.tree.get_children())
        assert tab.tree.get_children() == (newer.key,)
        assert tab.tree.set(newer.key, "formal") == "暂不建议"
        assert tab.tree.set(newer.key, "enabled") == "1" and tab.tree.set(newer.key, "runtime") == "0"
        assert tab.tree.set(newer.key, "validation_reason") == "本地试用中"
        tab.select_all_button.invoke()
        assert tab._selected_clear_keys() == {newer.key}
        assert {row.key for row in tab._action_samples()} == {newer.key}
        tab.clear_button.invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert sample.key in tab.samples and newer.key not in tab.samples
        assert sample.file("frame").exists() and not newer.file("frame").exists(), tab.message_var.get()
        assert not tab.tree.get_children() and destination.read_bytes() == before
        tab.filter_var.set("全部")
        tab._render_list()
        tab.select_all_button.invoke()
        tab.clear_button.invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)
        assert not tab.samples and "正式内置参考保留" in prompts[0]
        assert destination.read_bytes() == before
    finally:
        if tab.formalizing:
            tab._cancel_formal()
        window.destroy()
        ui._state = "idle"


def test_reference_rows_rename_delete_and_stage_layout(desktop_ui, tmp_path, monkeypatch):
    import tkinter as tk
    from tkinter.ttk import Button

    from crX.interface.card_review import CardReviewTab
    from crX.utils import card_library as local
    from crX.utils import formal_library as formal
    from crX.utils.card_review import atomic_json
    from tests.test_card_library import fingerprint

    destination = tmp_path / "builtins.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "_bundled_references", lambda: [("giant", fingerprint(15).tolist())])
    monkeypatch.setattr(local, "sample_fingerprint", lambda sample: fingerprint(int(sample.name[-1]) % 3))
    sources = [add_group_sample(tmp_path, "reference-rows", f"sample-{index:03d}") for index in range(4)]
    for index, sample in enumerate(sources):
        save_review(sample, "confirmed", "zap", f"备注{index}")
    local.update_library(tmp_path, sources, "enable")
    entries = local.read_library(tmp_path)
    assert len(entries) == 3
    for entry, count in zip(entries, (12, 0, 50), strict=True):
        local.merge_match_stats(
            tmp_path,
            {
                entry["id"]: {
                    "matches": count,
                    "distance_total": count * 10,
                    "margin_total": count * 200,
                    "assists": count,
                    "assist_battles": min(count, 3),
                    "assist_distance_total": count * 10,
                    "assist_margin_total": count * 200,
                }
            },
        )
    ui, _root = desktop_ui
    window = tk.Toplevel(ui)
    window.geometry("850x760")
    tab = CardReviewTab(window, root=tmp_path)
    tab.pack(fill="both", expand=True)
    errors = []
    monkeypatch.setattr(ui, "report_callback_exception", lambda *args: errors.append(args))
    prompts = []
    monkeypatch.setattr(
        "crX.interface.card_review.messagebox.askyesno", lambda _title, text, **_kw: prompts.append(text) or True
    )
    try:
        _wait_ui(ui, lambda: not tab._scan_busy)
        tab.filter_var.set("补库")
        tab._render_list()
        _wait_ui(ui, lambda: len(tab.reference_panel._photos) == 3)
        assert [tab.details.tab(panel, "text") for panel in tab.details.tabs()] == [
            "参考样本",
            "手牌与识别信息",
            "完整战斗截图",
        ]
        assert tab.details.select() == str(tab.reference_panel)
        assert not tab.edit_panel.winfo_manager() and not tab.notes_panel.winfo_manager()
        assert len(tab.reference_panel.rows) == 3
        assert tab.details.winfo_height() >= 190
        for entry, count in zip(entries, (12, 0, 50), strict=True):
            row = tab.reference_panel.rows[entry["id"]]
            assert f"补足识别 {count} 次" in row["label"].cget("text")
            assert tab.reference_panel._photos[entry["id"]] is not None
            assert "details" not in row
            assert "rename" not in row
            assert tab.rename_reference_button.cget("style") == row["delete"].cget("style")
            for state in ((), ("disabled",), ("active",), ("pressed",)):
                assert ui.style.lookup(row["delete"].cget("style"), "foreground", state=state) == "#ffffff"
        row = tab.reference_panel.rows[entries[2]["id"]]
        row["label"].event_generate("<Button-1>")
        _wait_ui(ui, lambda: tab._reference_preview_key == entries[2]["id"] and tab._frame_preview is not None)
        assert entries[2]["id"] in tab.detail_var.get() and "补足识别 50 次" in tab.detail_var.get()
        assert tab.reference_panel.selected == entries[2]["id"]
        selected_row = tab.reference_panel.rows[entries[2]["id"]]
        assert selected_row["frame"].cget("style") == "SelectedReference.TFrame"
        assert selected_row["label"].cget("text") == selected_row["text"]
        assert ui.style.lookup("SelectedReference.TFrame", "background") == ui.style.lookup(
            "CardSamples.Treeview", "background", state=("selected",)
        )
        assert all(
            row["frame"].cget("style") == "TFrame"
            for key, row in tab.reference_panel.rows.items()
            if key != entries[2]["id"]
        )
        tab.details.select(tab.info_panel.container)
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy)
        assert tab.details.select() == str(tab.info_panel.container)
        assert tab.reference_panel.selected == entries[2]["id"]
        tab.details.select(tab.frame_panel)
        assert tab._frame_preview is not None
        tab.details.select(tab.reference_panel)
        tab.set_running(True)
        assert all(str(row["delete"].cget("state")) == "disabled" for row in tab.reference_panel.rows.values())
        tab._delete_reference(entries[0]["id"])
        assert not prompts
        tab.set_running(False)
        tab._select_reference(entries[1]["id"])
        assert tab.rename_reference_button.winfo_manager() == "pack"
        action_widgets = tab.stage_buttons["formal"].master.pack_slaves()
        assert (
            action_widgets.index(tab.rename_reference_button) == action_widgets.index(tab.stage_buttons["formal"]) + 1
        )
        tab.rename_reference_button.invoke()
        tab._reference_editor_name.set("not-a-card")
        assert tab._reference_editor is not None
        buttons = tab._reference_editor.winfo_children()[-1].winfo_children()
        confirm_button = next(button for button in buttons if button.cget("text") == "确认")
        assert isinstance(confirm_button, Button)
        confirm_button.invoke()
        assert tab._reference_editor_error.get() and not tab._action_busy
        tab._reference_editor_name.set("knight")
        confirm_button.invoke()
        _wait_ui(
            ui, lambda: not tab._action_busy and not tab._scan_busy and tab.samples[sources[1].key].card_id == "knight"
        )
        assert tab.samples[sources[1].key].review["note"] == "备注1"
        assert tab.samples[sources[2].key].card_id == "zap"
        tab.filter_var.set("全部")
        tab._render_list()
        tab.tree.selection_set(sources[1].key)
        tab.tree.focus(sources[1].key)
        tab._select()
        assert "knight" in tab.card_var.get() and tab.note_var.get() == "备注1"
        tab.filter_var.set("补库")
        tab._render_list()
        zap_key = next(key for key in tab.tree.get_children() if tab.samples[key].card_id == "zap")
        tab.tree.selection_set(zap_key)
        tab.tree.focus(zap_key)
        tab._select()
        assert len(tab.reference_panel.rows) == 2
        tab.reference_panel.rows[entries[0]["id"]]["delete"].invoke()
        _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and sources[0].key not in tab.samples)
        assert "1 份参考" in prompts[0] and "2 张来源" in prompts[0]
        assert sources[1].file("frame").exists() and sources[2].file("frame").exists()
        assert not sources[0].file("frame").exists() and not sources[3].file("frame").exists()
        assert destination.read_text(encoding="utf-8").count('"entries": []') == 1
        tab.filter_var.set("待确认")
        tab._render_list()
        assert tab.edit_panel.winfo_manager() == tab.notes_panel.winfo_manager() == "grid"
        assert not tab.rename_reference_button.winfo_manager()
        assert [tab.details.tab(panel, "text") for panel in tab.details.tabs()] == ["手牌与识别信息", "完整战斗截图"]
        assert not errors
    finally:
        window.destroy()


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
    monkeypatch.setattr(library, "sync_library", lambda _root: None)
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
        assert "1 种卡牌，共 2 张待补库样本" in prompts[0]
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
