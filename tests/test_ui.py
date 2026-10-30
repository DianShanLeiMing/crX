"""Desktop-only GUI smoke coverage; no device calls."""

import gc
import os
import sys
import time
import tkinter as tk

import pytest

from crX.bot import card_detection as detection
from crX.emulators import EmulatorType
from crX.interface.enums import UIField
from crX.utils import card_library as local
from crX.utils import formal_library as formal
from crX.utils.card_review import atomic_json, save_review
from tests.test_card_library import fingerprint
from tests.test_card_review import add_group_sample
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
        assert int(tab.review_panes.panecget(tab.sample_table, "minsize")) == 80
        original_sash = tab.review_panes.sash_coord(0)[1]
        tab.review_panes.sash_place(0, 0, 0)
        ui.update()
        assert tab.sample_table.winfo_height() <= 100
        tab.review_panes.sash_place(0, 0, original_sash)
        ui.update()
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
        assert int(tab.review_panes.panecget(tab.sample_table, "minsize")) == 80
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
        for stage in ("待确认", "待上传"):
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
        assert image is not None and image.width() <= 24 and image.height() <= 30
        assert tab.thumbnail_tree.item(first, "image")
        text_bbox = tab.tree.bbox(first)
        thumbnail_bbox = tab.thumbnail_tree.bbox(first)
        assert text_bbox and thumbnail_bbox
        assert text_bbox[1] == thumbnail_bbox[1] and text_bbox[3] == thumbnail_bbox[3]
        assert 30 <= text_bbox[3] <= 36
        viewport_bottom = tab.tree.winfo_height()
        visible_rows = [key for key in rows if (box := tab.tree.bbox(key)) and box[1] + box[3] <= viewport_bottom]
        assert len(visible_rows) >= 1, (len(visible_rows), tab.tree.winfo_height(), tab.sample_table.winfo_height())
        # Larger thumbnails must not impose a new minimum on the draggable panes.
        tab.review_panes.sash_place(0, 0, tab.review_panes.sash_coord(0)[1] + 100)
        ui.update()
        assert int(tab.review_panes.panecget(tab.sample_table, "minsize")) == 80

        def assert_thumbnail_next_to_text(key):
            bbox = tab.tree.bbox(key, "group")
            assert bbox
            gap = (
                tab.tree.winfo_rootx() + bbox[0] - (tab.thumbnail_tree.winfo_rootx() + tab.thumbnail_tree.winfo_width())
            )
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
        ui.geometry("760x700")
        tab.tree.yview_moveto(0)
        ui.update()
        compact_rows = [
            key for key in rows if (box := tab.tree.bbox(key)) and box[1] + box[3] <= tab.tree.winfo_height()
        ]
        assert len(compact_rows) >= 1, (len(compact_rows), tab.tree.winfo_height())
        assert_thumbnail_next_to_text(rows[0])
    finally:
        ui.withdraw()
        ui.notebook.forget(tab)
        tab.destroy()
        ui.notebook.select(prior_tab)
        ui.geometry(prior_geometry)
        ui.attributes("-alpha", 1)


def test_thumbnail_results_follow_current_tab_without_resetting_edits(desktop_ui, tmp_path, monkeypatch):
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
        switch(ui, tab, "待上传")
        assert not tab.tree.get_children()
        release.set()
        _wait_ui(ui, lambda: not tab._thumbnail_busy)
        assert not tab._thumbnail_images and not tab.thumbnail_tree.get_children()
        switch(ui, tab, "待确认")
        select(tab, second)
        tab.note_var.set("后台读图时仍可编辑")
        _wait_ui(ui, lambda: second.key in tab._thumbnail_images and not tab._thumbnail_busy)
        assert set(tab.thumbnail_tree.get_children()) == {first.key, second.key}
        assert tab.tree.selection() == tab.thumbnail_tree.selection() == (second.key,)
        assert tab.note_var.get() == "后台读图时仍可编辑"
    finally:
        release.set()
        window.destroy()


@pytest.fixture
def review_ui(desktop_ui, tmp_path, monkeypatch):
    from crX.interface.card_review import CardReviewTab

    ui, _ = desktop_ui
    destination = tmp_path / "formal.json"
    atomic_json(destination, {"version": 1, "entries": []})
    monkeypatch.setattr(formal, "formal_path", lambda: destination)
    monkeypatch.setattr(formal, "source_checkout", lambda: True)
    monkeypatch.setattr(detection, "_REFERENCE_NAMES", ("giant",))
    monkeypatch.setattr(detection, "_REFERENCE_MATRIX", fingerprint(15)[None, ...])
    values = {}
    monkeypatch.setattr(local, "sample_fingerprint", lambda sample: values[sample.key])
    window = tk.Toplevel(ui)
    window.geometry("850x740")
    tab = CardReviewTab(window, root=tmp_path)
    tab.pack(fill="both", expand=True)
    errors = []
    original_handler = ui.report_callback_exception
    ui.report_callback_exception = lambda *args: errors.append(args)
    _wait_ui(ui, lambda: not tab._scan_busy)

    def add(session, channel=0, shift=0):
        sample = add_group_sample(tmp_path, session, "sample-000")
        values[sample.key] = fingerprint(channel, shift)
        tab.refresh()
        _wait_ui(ui, lambda: not tab._scan_busy and sample.key in tab.samples)
        return sample

    yield ui, tab, add, destination
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy and not tab._thumbnail_busy)
    window.destroy()
    ui.report_callback_exception = original_handler
    gc.collect()
    assert not errors


def select(tab, sample):
    tab.tree.selection_set(sample.key)
    tab.tree.focus(sample.key)
    tab._select()


def switch(ui, tab, label):
    tab.stage_tabs.select(tab.stage_pages[label])
    ui.update()
    assert tab.filter_var.get() == label


def finish(ui, tab):
    _wait_ui(ui, lambda: not tab._action_busy and not tab._scan_busy)


def test_two_tabs_single_approval_routes_and_keeps_owned_status(review_ui):
    ui, tab, add, destination = review_ui
    first, duplicate, conflict = add("first"), add("duplicate", shift=5), add("conflict", channel=15)
    assert tuple(tab.stage_pages) == ("待确认", "待上传") and not hasattr(tab, "filter_combo")
    assert not {"ignore", "restore"}.intersection(tab.stage_buttons)
    legacy = add("legacy", channel=4)
    save_review(legacy, "ignored", note="历史忽略记录")
    tab.refresh()
    finish(ui, tab)
    assert legacy.key not in tab.tree.get_children() and legacy.file("card").is_file()
    assert not {"session", "formal", "runtime"}.intersection(tab.tree.cget("columns"))
    select(tab, first)
    assert tab._chosen_card() == first.record["candidates"][0]["card_id"]
    assert not tab.samples[first.key].card_id and not formal._read_entries(destination)
    tab.card_var.set("knight")
    tab.note_var.set("人工核对")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.card_var.get() == "knight" and tab.note_var.get() == "人工核对"
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.samples[first.key].status == "included"
    assert first.key not in tab.tree.get_children()
    select(tab, duplicate)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.samples[duplicate.key].status == "covered"
    assert duplicate.key not in tab.tree.get_children()
    assert "覆盖" in tab.message_var.get()
    assert "覆盖" not in tab.summary_var.get()
    select(tab, conflict)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.tree.set(conflict.key, "status") == "需复核"
    assert "巨人" in tab.description_var.get()
    assert len(formal._read_entries(destination)) == 1
    for label, sample in (("待上传", first), ("待确认", conflict)):
        switch(ui, tab, label)
        assert tab.tree.get_children() == (sample.key,)
        assert tab.stage_tabs.tab(tab.stage_pages[label], "text") == f"{label}（1）"
    switch(ui, tab, "待上传")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.samples[first.key].status == "included"


def test_pending_list_shows_all_groups_and_updates_after_processing(review_ui, monkeypatch):
    import cv2

    ui, tab, add, _destination = review_ui
    first, duplicate, other = add("z-first"), add("y-duplicate", shift=5), add("a-other", channel=4)
    image = cv2.imread(str(other.file("card")))
    cv2.imwrite(str(other.file("card")), image[:, ::-1])
    tab.refresh()
    finish(ui, tab)
    assert not hasattr(tab, "group_combo") and not hasattr(tab, "group_var")
    assert set(tab.tree.get_children()) == {first.key, duplicate.key, other.key}
    select(tab, other)
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *args, **kwargs: True)
    tab.clear_button.invoke()
    finish(ui, tab)
    assert other.key not in tab.samples and not other.file("card").exists()
    assert set(tab.tree.get_children()) == {first.key, duplicate.key}
    select(tab, first)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.tree.get_children() == (duplicate.key,)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.samples[duplicate.key].status == "covered"
    assert not tab.tree.get_children()
    # Processed samples remain available in their corresponding tabs.
    switch(ui, tab, "待上传")
    assert tab.tree.get_children() == (first.key,)
    switch(ui, tab, "待确认")
    assert not tab.tree.get_children()


def test_batch_approval_uses_visible_selection_and_preserves_edits(review_ui):
    ui, tab, add, destination = review_ui
    first, second = add("first"), add("second", shift=5)
    select(tab, first)
    tab.select_group_button.invoke()
    assert set(tab.tree.selection()) == {first.key, second.key}
    tab.card_var.set("knight")
    tab.note_var.set("批量备注")
    other = add("other", channel=4)
    assert set(tab.tree.selection()) == {first.key, second.key}
    assert tab.card_var.get() == "knight" and tab.note_var.get() == "批量备注"
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert {tab.samples[key].status for key in (first.key, second.key)} == {"included", "covered"}
    assert all(tab.samples[key].review["note"] == "批量备注" for key in (first.key, second.key))
    assert tab.samples[other.key].status == "pending"
    assert len(formal._read_entries(destination)) == 1


@pytest.mark.parametrize("stage", ["待确认", "待上传"])
def test_manual_pane_sizes_survive_content_changes_and_refresh(review_ui, stage):
    ui, tab, add, _destination = review_ui
    if stage == "待上传":
        sample = add("resize")
        select(tab, sample)
        tab.card_var.set("knight")
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
        switch(ui, tab, stage)
    ui.update()

    def drag(panes, distance):
        y = panes.sash_coord(0)[1] + 2
        assert panes.identify(20, y)
        panes.event_generate("<ButtonPress-1>", x=20, y=y)
        ui.update()
        for step in (distance // 2, distance):
            panes.event_generate("<B1-Motion>", x=20, y=y + step, state=0x0100)
            ui.update()
        panes.event_generate("<ButtonRelease-1>", x=20, y=y + distance)
        ui.update()

    upper = tab.review_panes.sash_coord(0)[1]
    drag(tab.review_panes, -30)
    assert tab.review_panes.sash_coord(0)[1] <= upper - 20
    bottom = tab.body_panes.sash_coord(0)[1]
    drag(tab.body_panes, 50)
    assert tab.body_panes.sash_coord(0)[1] >= bottom + 40
    sizes = (tab.body_panes.sash_coord(0)[1], tab.sample_table.winfo_height(), tab.details.winfo_height())
    tab.message_var.set("处理结果和说明更新后应保留手动调整的展示区域。\n" * 8)
    ui.update()
    tab._fit_controls()
    ui.update()
    assert abs(tab.body_panes.sash_coord(0)[1] - sizes[0]) <= 2
    tab.refresh()
    finish(ui, tab)
    assert abs(tab.body_panes.sash_coord(0)[1] - sizes[0]) <= 2
    assert abs(tab.sample_table.winfo_height() - sizes[1]) <= 2
    assert abs(tab.details.winfo_height() - sizes[2]) <= 2
    drag(tab.body_panes, -50)
    assert tab.body_panes.sash_coord(0)[1] <= sizes[0] - 40


@pytest.mark.parametrize("pane_name", ["review_panes", "body_panes"])
def test_pane_drag_defers_queued_refresh_and_preview_work(review_ui, pane_name, monkeypatch):
    ui, tab, add, _destination = review_ui
    sample = add("continuous-resize")
    select(tab, sample)
    tab.note_var.set("拖动期间保留输入")
    tab.details.select(tab.frame_panel)
    _wait_ui(ui, lambda: "frame" in tab._images)
    photo = tab._images["frame"]
    panes = getattr(tab, pane_name)
    sash = panes.sash_coord(0)[1]
    y = sash + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=y)
    ui.update()
    assert tab._pane_drag is not None and panes.grab_current() is panes
    # Other Tk widgets can replace the global native sash state. Our active
    # separator keeps its own anchor and continues moving independently.
    panes.tk.call("unset", "-nocomplain", "::tk::Priv(sash)", "::tk::Priv(dx)", "::tk::Priv(dy)")
    scans = []
    background = tab._background
    monkeypatch.setattr(tab, "_background", lambda *args, **kwargs: scans.append(args))
    tab.refresh()
    assert not scans
    monkeypatch.setattr(tab, "_background", background)
    marker = "拖动结束后处理后台结果"
    tab._results.put(("progress", None, marker, None))
    heartbeat = []
    tab._later(180, lambda: heartbeat.append(True))
    for offset in (-20, -40, -60):
        panes.event_generate("<B1-Motion>", x=20, y=y + offset, state=0x0100)
        ui.update()
    _wait_ui(ui, lambda: heartbeat)
    assert panes.sash_coord(0)[1] <= sash - 50
    assert tab._images["frame"] is photo
    assert tab.message_var.get() != marker and not tab._results.empty()
    panes.event_generate("<ButtonRelease-1>", x=20, y=y - 60)
    _wait_ui(ui, lambda: tab.message_var.get() == marker)
    assert tab._pane_drag is None and panes.grab_current() is not panes
    assert tab.note_var.get() == "拖动期间保留输入"
    # Keyboard focus can change without releasing the physical mouse button.
    y = panes.sash_coord(0)[1] + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=y)
    ui.update()
    assert tab._pane_drag is not None
    panes.event_generate("<FocusOut>")
    ui.update()
    assert tab._pane_drag is not None
    panes.event_generate("<ButtonRelease-1>", x=20, y=y)
    ui.update()
    assert tab._pane_drag is None and panes.grab_current() is not panes


def test_frame_resize_skips_hidden_and_unchanged_previews(review_ui, monkeypatch):
    ui, tab, add, _destination = review_ui
    sample = add("preview-resize")
    select(tab, sample)
    _wait_ui(ui, lambda: tab._frame_preview is not None)
    photos = []
    from crX.interface import card_review

    photo_image = card_review.ImageTk.PhotoImage

    def render(*args, **kwargs):
        photos.append(True)
        return photo_image(*args, **kwargs)

    monkeypatch.setattr(card_review.ImageTk, "PhotoImage", render)
    tab._resize_frame()
    assert not photos  # The battle-image tab is still hidden.
    tab.details.select(tab.frame_panel)
    _wait_ui(ui, lambda: "frame" in tab._images)
    ui.update()
    before = len(photos)
    tab._resize_frame()
    tab._resize_frame()
    assert len(photos) == before


@pytest.mark.parametrize("pane_name", ["review_panes", "body_panes"])
@pytest.mark.parametrize("target_name", ["tree", "details"])
def test_sash_drag_receives_motion_and_release_from_child_widgets(review_ui, pane_name, target_name):
    ui, tab, add, _destination = review_ui
    sample = add("child-resize")
    select(tab, sample)
    panes = getattr(tab, pane_name)
    target = getattr(tab, target_name)
    ui.update()
    before = panes.sash_coord(0)[1]
    root_y = panes.winfo_rooty() + before + 2
    original_tags = target.bindtags()
    selection = tab.tree.selection()
    panes.event_generate("<ButtonPress-1>", x=20, y=before + 2, rooty=root_y)
    ui.update()
    # A local Tk grab can deliver events to children of the grabbed pane.
    # Motion/release must reach the active sash before child class bindings.
    for distance in (15, 30, 45):
        target.event_generate("<B1-Motion>", x=20, y=5, rooty=root_y - distance, state=0x0100)
        ui.update()
    _wait_ui(ui, lambda: panes.sash_coord(0)[1] <= before - 35)
    position = panes.sash_coord(0)[1]
    target.event_generate("<ButtonRelease-1>", x=20, y=5, rooty=root_y - 45)
    ui.update()
    assert position <= before - 35
    assert tab._pane_drag is None and panes.grab_current() is not panes
    assert target.bindtags() == original_tags
    assert tab.tree.selection() == selection


@pytest.mark.parametrize("target_name", ["description_label", "description_bar"])
@pytest.mark.parametrize("send_motion", [False, True])
def test_drag_starts_on_description_text_or_its_top_padding(review_ui, target_name, send_motion):
    ui, tab, add, _destination = review_ui
    sample = add("description-edge")
    select(tab, sample)
    ui.update()
    target, panes = getattr(tab, target_name), tab.review_panes
    before = panes.sash_coord(0)[1]
    window = tab.winfo_toplevel()
    window_size = (window.winfo_width(), window.winfo_height())
    selection = tab.tree.selection()
    root_y = target.winfo_rooty() + 1
    target.event_generate("<ButtonPress-1>", x=20, y=1, rooty=root_y)
    assert tab._pane_drag is not None
    if send_motion:
        target.event_generate("<B1-Motion>", x=20, y=-39, rooty=root_y - 40, state=0x0100)
    target.event_generate("<ButtonRelease-1>", x=20, y=-39, rooty=root_y - 40)
    ui.update()
    assert panes.sash_coord(0)[1] <= before - 30
    assert (window.winfo_width(), window.winfo_height()) == window_size
    assert tab.tree.selection() == selection and tab._pane_drag is None
    assert str(target.cget("cursor")) == "sb_v_double_arrow"


@pytest.mark.parametrize("pane_name,expanded", [("review_panes", False), ("body_panes", False), ("body_panes", True)])
def test_detail_dividers_resize_window_only_at_bottom_edge(review_ui, pane_name, expanded):
    ui, tab, add, _destination = review_ui
    add("window-resize")
    if expanded:
        tab.expand_details_button.invoke()
    window = tab.winfo_toplevel()
    ui.update()
    original = (window.winfo_width(), window.winfo_height())
    table_height = tab.sample_table.winfo_height()
    detail_height = tab.details.winfo_height()
    controls_height = tab.controls.container.winfo_height()
    panes = getattr(tab, pane_name)
    root_y = panes.winfo_rooty() + panes.sash_coord(0)[1] + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=panes.sash_coord(0)[1] + 2, rooty=root_y)
    ui.update()
    for distance in (20, 40, 60):
        panes.event_generate("<B1-Motion>", x=20, y=2, rooty=root_y + distance, state=0x0100)
        ui.update()
    if pane_name == "body_panes":
        _wait_ui(ui, lambda: window.winfo_height() == original[1] + 60)
        assert window.winfo_height() == original[1] + 60
        if not expanded:
            assert abs(tab.sample_table.winfo_height() - table_height) <= 2
        assert abs(tab.details.winfo_height() - detail_height - 60) <= 2
        assert abs(tab.controls.container.winfo_height() - controls_height) <= 2
    else:
        assert window.winfo_height() == original[1]
    assert window.winfo_width() == original[0]
    for distance in (40, 20, 0):
        panes.event_generate("<B1-Motion>", x=20, y=2, rooty=root_y + distance, state=0x0100)
        ui.update()
    panes.event_generate("<ButtonRelease-1>", x=20, y=2, rooty=root_y)
    ui.update()
    assert (window.winfo_width(), window.winfo_height()) == original
    assert abs(tab.details.winfo_height() - detail_height) <= 2


def test_window_resize_coalesces_motion_and_flushes_latest_size_on_release(review_ui, monkeypatch):
    ui, tab, add, _destination = review_ui
    add("resize-frames")
    window, panes = tab.winfo_toplevel(), tab.body_panes
    ui.update()
    original_height = window.winfo_height()
    calls = []
    geometry = window.geometry

    def resize(value):
        calls.append(value)
        return geometry(value)

    monkeypatch.setattr(window, "geometry", resize)
    y = panes.sash_coord(0)[1] + 2
    root_y = panes.winfo_rooty() + y
    panes.event_generate("<ButtonPress-1>", x=20, y=y, rooty=root_y)
    ui.update()
    for distance in range(1, 61):
        panes.event_generate("<B1-Motion>", x=20, y=2, rooty=root_y + distance, state=0x0100)
    assert calls == []  # Bursts of input must leave time for painting.
    _wait_ui(ui, lambda: bool(calls) and window.winfo_height() == original_height + 60)
    assert len(calls) == 1
    for distance in range(61, 81):
        panes.event_generate("<B1-Motion>", x=20, y=2, rooty=root_y + distance, state=0x0100)
    panes.event_generate("<ButtonRelease-1>", x=20, y=2, rooty=root_y + 80)
    ui.update()
    assert window.winfo_height() == original_height + 80
    assert len(calls) == 2
    assert tab._window_resize_after is None and tab._window_resize is None


@pytest.mark.parametrize("pane_name", ["review_panes", "body_panes"])
@pytest.mark.parametrize("send_motion", [False, True])
def test_quick_sash_drag_keeps_last_position_without_intermediate_redraw(review_ui, pane_name, send_motion):
    ui, tab, add, _destination = review_ui
    add("quick-resize")
    panes = getattr(tab, pane_name)
    ui.update()
    window = tab.winfo_toplevel()
    window_height, details_height = window.winfo_height(), tab.details.winfo_height()
    before = panes.sash_coord(0)[1]
    y = before + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=y)
    ui.update()
    direction = -1 if pane_name == "review_panes" else 1
    if send_motion:
        for distance in range(2, 62, 2):
            panes.event_generate("<B1-Motion>", x=20, y=y + direction * distance, state=0x0100)
    panes.event_generate("<ButtonRelease-1>", x=20, y=y + direction * 60)
    ui.update()
    assert direction * (panes.sash_coord(0)[1] - before) >= 50
    if pane_name == "body_panes":
        assert window.winfo_height() == window_height + 60
        assert abs(tab.details.winfo_height() - details_height - 60) <= 2


def test_sash_drag_continues_when_keyboard_focus_changes(review_ui):
    ui, tab, add, _destination = review_ui
    add("focus-resize")
    panes = tab.review_panes
    ui.update()
    before = panes.sash_coord(0)[1]
    y = before + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=y)
    ui.update()
    panes.event_generate("<FocusOut>")
    panes.event_generate("<B1-Motion>", x=20, y=y - 50, state=0x0100)
    panes.event_generate("<ButtonRelease-1>", x=20, y=y - 50)
    ui.update()
    assert panes.sash_coord(0)[1] <= before - 40


@pytest.mark.parametrize("pane_name", ["review_panes", "body_panes"])
@pytest.mark.parametrize("direction", [-1, 1])
def test_sash_drag_reverses_immediately_after_hitting_boundary(review_ui, pane_name, direction):
    ui, tab, add, _destination = review_ui
    add("boundary-resize")
    panes = getattr(tab, pane_name)
    ui.update()
    y = panes.sash_coord(0)[1] + 2
    panes.event_generate("<ButtonPress-1>", x=20, y=y)
    ui.update()
    panes.event_generate("<B1-Motion>", x=20, y=y + direction * 2000, state=0x0100)
    ui.update()
    if pane_name == "body_panes":
        _wait_ui(ui, lambda: tab._window_resize_after is None)
    boundary = panes.sash_coord(0)[1]
    panes.event_generate("<B1-Motion>", x=20, y=y + direction * 1980, state=0x0100)
    ui.update()
    if pane_name == "body_panes":
        _wait_ui(ui, lambda: tab._window_resize_after is None)
    position = panes.sash_coord(0)[1]
    panes.event_generate("<ButtonRelease-1>", x=20, y=y + direction * 1980)
    ui.update()
    assert direction * (boundary - position) >= 15


def test_included_cards_merge_and_reference_selection_scopes_edits(review_ui, monkeypatch):
    ui, tab, add, destination = review_ui
    first, supplement, other = add("first"), add("supplement", channel=4), add("other", channel=8)
    for sample, card_id in ((first, "knight"), (supplement, "knight"), (other, "archers")):
        select(tab, sample)
        tab.card_var.set(card_id)
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    switch(ui, tab, "待上传")
    assert len(tab.tree.get_children()) == 2
    assert tab.stage_tabs.tab(tab.stage_pages["待上传"], "text") == "待上传（2）"
    knight_row = next(key for key in tab.tree.get_children() if tab.tree.set(key, "card") == "骑士")
    tab.tree.selection_set(knight_row)
    tab.tree.focus(knight_row)
    tab._select()
    assert tab.tree.set(knight_row, "references") == "2 份"
    assert set(tab.reference_tree.get_children()) == {first.key, supplement.key}
    assert tab.details.select() == str(tab.reference_panel)
    assert len(tab.review_panes.panes()) == 2
    assert "validation_reason" not in tab.tree.cget("columns")
    assert not tab.description_label.winfo_ismapped()
    assert not hasattr(tab, "resize_handle")
    # Keep the footer deliberately shorter than its content: growing the
    # whole window no longer consumes footer space to expand the details.
    tab.body_panes.paneconfigure(tab.controls.container, height=80)
    ui.update()
    before_height = tab.details.winfo_height()
    before_table = tab.sample_table.winfo_height()
    before_sash = tab.body_panes.sash_coord(0)[1]

    def drag_bottom(distance):
        sash = tab.body_panes.sash_coord(0)[1] + 2
        assert tab.body_panes.identify(20, sash)
        tab.body_panes.event_generate("<ButtonPress-1>", x=20, y=sash)
        ui.update()
        tab.body_panes.event_generate("<B1-Motion>", x=20, y=sash + distance, state=0x0100)
        ui.update()
        tab.body_panes.event_generate("<ButtonRelease-1>", x=20, y=sash + distance)
        ui.update()

    # The footer's content height must not block downward expansion.
    drag_bottom(80)
    assert tab.details.winfo_height() >= before_height + 70
    assert abs(tab.sample_table.winfo_height() - before_table) <= 2
    assert tab.controls.container.winfo_height() < tab.controls.winfo_reqheight()
    tab.controls.yview_moveto(1)
    ui.update()
    assert tab.controls.vscroll.get()[0] > 0 and tab.controls.vscroll.get()[1] == 1
    expanded = tab.details.winfo_height()
    tab._fit_controls()
    ui.update()
    assert abs(tab.details.winfo_height() - expanded) <= 2
    drag_bottom(-80)
    tab.controls.yview_moveto(0)
    ui.update()
    drag_bottom(-50)
    assert tab.body_panes.sash_coord(0)[1] <= before_sash - 40
    assert tab.details.winfo_height() <= before_height - 40
    assert abs(tab.sample_table.winfo_height() - before_table) <= 2
    smaller = tab.details.winfo_height()
    drag_bottom(30)
    assert tab.details.winfo_height() >= smaller + 20
    assert abs(tab.sample_table.winfo_height() - before_table) <= 2
    # Recover from a viewport so short that only the heading remains visible.
    drag_bottom(45 - tab.details.winfo_height())
    assert tab.details.winfo_height() <= 55
    drag_bottom(130)
    assert all(tab.reference_tree.bbox(key) for key in (first.key, supplement.key))
    tab.body_panes.sash_place(0, 0, before_sash)
    ui.update()
    assert [tab.details.tab(page, "text") for page in tab.details.tabs()] == [
        "参考列表",
        "手牌与识别信息",
        "完整战斗图",
    ]
    _wait_ui(ui, lambda: supplement.key in tab._thumbnail_images)
    assert all(tab.reference_tree.bbox(key) for key in (first.key, supplement.key))
    assert tab.reference_tree.item(supplement.key, "image")
    assert tab.reference_tree.set(supplement.key, "reason") == "已加入正式库"
    previews = []
    load_details = tab._load_details

    def record_details(sample):
        previews.append(sample.key)
        return load_details(sample)

    monkeypatch.setattr(tab, "_load_details", record_details)
    tab.reference_tree.selection_set(supplement.key)
    tab.reference_tree.focus(supplement.key)
    tab._select_reference()
    assert tab.selected.key == supplement.key and tab._action_samples()[0].key == supplement.key
    ui.update()
    assert tab.details.select() == str(tab.reference_panel) and not previews
    tab.details.select(tab.info_panel.container)
    _wait_ui(ui, lambda: bool(previews) and bool(tab._images))
    assert previews == [supplement.key] and supplement.key in tab.detail_var.get()
    tab.details.select(tab.frame_panel)
    ui.update()
    assert tab._frame_preview is not None and tab.frame_panel.winfo_ismapped()
    tab.card_var.set("cannon")
    tab.refresh()
    finish(ui, tab)
    assert tab.reference_tree.selection() == (supplement.key,)
    assert tab.details.select() == str(tab.frame_panel)
    assert tab.card_var.get() == "cannon"
    tab.set_running(True)
    assert str(tab.stage_buttons["rename"].cget("state")) == "disabled"
    tab.set_running(False)
    tab.expand_details_button.invoke()
    ui.update()
    assert len(tab.review_panes.panes()) == 1
    tab.expand_details_button.invoke()
    ui.update()
    assert len(tab.review_panes.panes()) == 2
    assert int(tab.review_panes.panecget(tab.details, "minsize")) == 0
    tab.stage_buttons["rename"].invoke()
    finish(ui, tab)
    assert tab.samples[first.key].card_id == "knight"
    assert tab.samples[supplement.key].card_id == "cannon"
    assert len(tab.tree.get_children()) == 3
    select(tab, other)
    assert tab.details.select() == str(tab.reference_panel)
    assert tab.reference_tree.get_children() == (other.key,)
    assert tab._action_samples()[0].key == other.key
    tab.select_all_button.invoke()
    assert not tab.reference_tree.get_children() and not tab._action_samples()
    assert str(tab.stage_buttons["remove"].cget("state")) == "disabled"
    select(tab, first)
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *args, **kwargs: True)
    tab.stage_buttons["remove"].invoke()
    finish(ui, tab)
    assert {entry["card_id"] for entry in formal._read_entries(destination)} == {"cannon", "archers"}


def test_delete_and_running_guards_recheck_after_dialog(review_ui, monkeypatch):
    ui, tab, add, destination = review_ui
    source = add("source")
    select(tab, source)
    tab.card_var.set("knight")
    tab.set_running(True)
    assert str(tab.stage_buttons["confirm"].cget("state")) == "disabled"
    assert str(tab.clear_button.cget("state")) == "disabled"
    tab._save()
    tab._clear_samples()
    assert not tab._action_busy and not formal._read_entries(destination)
    tab.set_running(False)
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *args, **kwargs: False)
    tab.clear_button.invoke()
    assert not tab._action_busy and source.file("frame").exists()
    prompts = []

    def start_during_confirmation(_title, message, **_kwargs):
        prompts.append(message)
        tab.set_running(True)
        return True

    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", start_during_confirmation)
    tab.clear_button.invoke()
    assert not tab._action_busy and source.file("frame").exists()
    assert "正式参考保留" in prompts[0]
    tab.set_running(False)


def test_upload_publishes_all_references_of_selected_cards_and_blocks_while_running(review_ui, monkeypatch):
    ui, tab, add, _destination = review_ui
    first, supplement, other = add("first"), add("supplement", channel=4), add("other", channel=8)
    assert str(tab.upload_button.cget("state")) == "disabled"
    for sample, card_id in ((first, "knight"), (supplement, "knight"), (other, "archers")):
        select(tab, sample)
        tab.card_var.set(card_id)
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    switch(ui, tab, "待上传")
    row = next(key for key, sources in tab.row_samples.items() if any(item.key == first.key for item in sources))
    tab.tree.selection_set(row)
    tab.tree.focus(row)
    tab._select()
    calls = []

    def publish(root, samples, *, progress):
        calls.append((root, {sample.key for sample in samples}))
        progress("正在提交选中的正式参考……")
        return "已上传正式参考"

    monkeypatch.setattr("crX.interface.card_review.upload_references", publish)
    assert str(tab.upload_button.cget("state")) == "normal"
    tab.upload_button.invoke()
    assert tab.formalizing and str(tab.upload_button.cget("state")) == "disabled"
    finish(ui, tab)
    assert calls == [(tab.root, {first.key, supplement.key})]
    assert tab.message_var.get() == "已上传正式参考"
    tab.select_all_button.invoke()
    assert str(tab.upload_button.cget("state")) == "normal"
    tab.upload_button.invoke()
    finish(ui, tab)
    assert calls[-1][1] == {first.key, supplement.key, other.key}
    tab.set_running(True)
    assert str(tab.upload_button.cget("state")) == "disabled"
    tab._upload()
    assert len(calls) == 2
    tab.set_running(False)


def test_rename_and_remove_update_shared_sources_without_second_approval(review_ui, monkeypatch):
    ui, tab, add, destination = review_ui
    first, duplicate = add("first"), add("duplicate", shift=5)
    for sample in (first, duplicate):
        select(tab, sample)
        tab.card_var.set("knight")
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    switch(ui, tab, "待上传")
    tab.card_var.set("cannon")
    tab.stage_buttons["rename"].invoke()
    finish(ui, tab)
    assert {tab.samples[key].card_id for key in (first.key, duplicate.key)} == {"cannon"}
    assert formal._read_entries(destination)[0]["card_id"] == "cannon"
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *args, **kwargs: True)
    tab.stage_buttons["remove"].invoke()
    finish(ui, tab)
    assert not formal._read_entries(destination)
    switch(ui, tab, "待确认")
    assert set(tab.tree.get_children()) == {first.key, duplicate.key}
    assert first.file("frame").exists() and duplicate.file("frame").exists()


def test_catalog_search_and_upload_archive_clean_images(review_ui, monkeypatch):
    from crX.bot.card_data import CARD_NAMES_ZH
    from crX.utils.card_upload import _archive_published

    ui, tab, add, destination = review_ui
    first, second = add("first"), add("second", channel=4)
    for sample in (first, second):
        select(tab, sample)
        tab.card_var.set("knight")
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    tab.catalog_button.invoke()
    ui.update()
    assert tab.filter_var.get() == "样本库"
    assert not tab.review_tools.winfo_ismapped()
    assert tab.catalog_button.cget("text") == "返回样本管理"
    view = tab.library_view
    assert len(view.cards.get_children()) >= len(CARD_NAMES_ZH)
    assert not tab.body_panes.winfo_ismapped() and not tab.selection_tools.winfo_ismapped()
    view.query.set("knight")
    ui.update()
    assert "knight" in view.cards.get_children()
    view.cards.selection_set("knight")
    view.cards.focus("knight")
    ui.update()
    assert view.cards.set("knight", "count") == "2"
    keys = view.references.get_children()
    assert len(keys) == 2
    assert not hasattr(view, "compare_button")
    assert str(view.references.cget("selectmode")) == "browse"
    for index, key in enumerate(keys, 1):
        view.references.selection_set(key)
        assert view.references.set(key, "name") == f"参考{index}"
        assert view.references.set(key, "identifier") == key
        assert view.references.set(key, "hits") == "0"
    assert tuple(view.references.cget("columns")) == ("name", "identifier", "hits")
    assert not hasattr(view, "preview_image")
    view.query.set("弓箭手")
    ui.update()
    assert "archers" in view.cards.get_children() and view.cards.set("archers", "count") == "0"
    view.cards.selection_set("archers")
    ui.update()
    assert not view.references.get_children()
    assert "0 份正式参考" in view.title.get()
    view.query.set("")
    view.only_samples.set(True)
    view.render_cards()
    assert all(int(view.cards.set(card, "count")) > 0 for card in view.cards.get_children())
    view._sort_counts()
    assert view.cards.get_children()[0] == "knight"
    view._sort_counts()
    assert view.cards.get_children()[-1] == "knight"
    before = destination.read_bytes()
    tab.catalog_button.invoke()
    ui.update()
    assert tab.filter_var.get() == "待确认"
    assert tab.review_tools.winfo_ismapped()
    switch(ui, tab, "待上传")
    assert tab.tree.get_children() and tab.body_panes.winfo_ismapped()

    def publish(root, samples, *, progress):
        progress("正在上传……")
        _archive_published(root, {sample.review["formal_id"] for sample in samples}, "a" * 40)
        return "已上传 2 份正式参考"

    monkeypatch.setattr("crX.interface.card_review.upload_references", publish)
    tab.select_all_button.invoke()
    tab.upload_button.invoke()
    finish(ui, tab)
    assert not tab.tree.get_children()
    assert tab.stage_tabs.tab(tab.stage_pages["待上传"], "text") == "待上传（0）"
    assert all(tab.samples[sample.key].uploaded for sample in (first, second))
    assert destination.read_bytes() == before
    assert all(not sample.file(kind).exists() for sample in (first, second) for kind in ("card", "hand", "frame"))
    tab.catalog_button.invoke()
    ui.update()
    assert tab.filter_var.get() == "样本库"
    view.query.set("knight")
    ui.update()
    assert view.cards.set("knight", "count") == "2"
    assert all(ref.uploaded for ref in view.data["knight"])
    key = view.references.get_children()[1]
    view.references.selection_set(key)
    tab.set_running(True)
    view.query.set("giant")
    ui.update()
    assert view.cards.get_children() == ("giant",)
    assert view.references.set(view.references.get_children()[0], "name") == "首选（内置参考）"
    assert str(view.preferred_button.cget("state")) == "disabled"
    tab.set_running(False)
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.filter_var.get() == "样本库" and not tab.review_tools.winfo_ismapped()
    tab.catalog_button.invoke()
    ui.update()
    assert tab.filter_var.get() == "待上传" and tab.body_panes.winfo_ismapped()
    assert tab.catalog_button.cget("text") == "样本库"


def test_legacy_trial_is_visible_pending_and_can_be_approved(review_ui):
    ui, tab, add, destination = review_ui
    source = add("legacy")
    # Simulate annotations from the previous release without a workflow result.
    save_review(source, "confirmed", "knight", "历史备注")
    local.update_library(tab.root, [source], "enable")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.tree.get_children() == (source.key,)
    assert tab.samples[source.key].status == "pending"
    select(tab, source)
    assert "knight" in tab.card_var.get() and tab.note_var.get() == "历史备注"
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.samples[source.key].status == "included" and len(formal._read_entries(destination)) == 1


def test_catalog_first_choice_and_clear_require_explicit_stats_upload(review_ui):
    from crX.utils import reference_hits as hits

    ui, tab, add, _destination = review_ui
    source = add("first")
    select(tab, source)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    tab.catalog_button.invoke()
    _wait_ui(ui, lambda: not tab._scan_busy)
    view = tab.library_view
    view.query.set("knight")
    view.cards.selection_set("knight")
    view.cards.focus("knight")
    ui.update()
    key = view.references.get_children()[0]
    view.references.selection_set(key)
    view.preferred_button.invoke()
    finish(ui, tab)
    assert view.references.set(key, "name") == "首选"
    hits.record_hits(tab.root, {key: 8})
    view.refresh_order()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert view.references.set(key, "hits") == "8"
    view.update_hits.set(True)
    tab.update_hits.set(True)
    view.clear_button.invoke()
    finish(ui, tab)
    assert not view.update_hits.get() and not tab.update_hits.get()
    assert view.references.set(key, "hits") == "0"
    assert "仅在本地清空" in view.message.get()
    view.update_hits.set(True)
    view._update_message()
    assert "本次上传将同步清空结果" in view.message.get()


def test_catalog_drag_persists_order_keeps_labels_and_pins_first_choice(review_ui):
    from crX.utils import reference_hits as hits
    from crX.utils import reference_preferences as preferences

    ui, tab, add, _destination = review_ui
    samples = [add("first"), add("second", channel=4), add("third", channel=8)]
    for sample in samples:
        select(tab, sample)
        tab.card_var.set("knight")
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    tab.catalog_button.invoke()
    _wait_ui(ui, lambda: not tab._scan_busy)
    view = tab.library_view
    view.query.set("knight")
    view.cards.selection_set("knight")
    ui.update()
    preferred, one, two = view.references.get_children()
    view.references.selection_set(preferred)
    view.preferred_button.invoke()
    finish(ui, tab)
    labels = {key: view.references.set(key, "name") for key in (preferred, one, two)}

    def drag(source, target, *, cancel=False):
        x, y, _width, height = view.references.bbox(source)
        _x, target_y, _width, target_height = view.references.bbox(target)
        view.references.event_generate("<ButtonPress-1>", x=x + 10, y=y + height // 2)
        view.references.event_generate("<B1-Motion>", x=x + 10, y=target_y + target_height // 2)
        if cancel:
            view._cancel_drag()
        view.references.event_generate("<ButtonRelease-1>", x=x + 10, y=target_y + target_height // 2)
        finish(ui, tab)

    drag(one, two)
    assert view.references.get_children() == (preferred, two, one)
    assert {key: view.references.set(key, "name") for key in (preferred, one, two)} == labels
    value = preferences.read_document(preferences.preferences_path())["cards"]["knight"]
    assert value["order"] == [preferred, two, one]
    hits.record_hits(tab.root, {one: 100})
    view.refresh_order()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert view.references.get_children() == (preferred, two, one)
    drag(one, two, cancel=True)
    assert view.references.get_children() == (preferred, two, one)
    drag(preferred, one)
    assert view.references.get_children() == (preferred, two, one)
    tab.set_running(True)
    drag(one, two)
    assert view.references.get_children() == (preferred, two, one)
    tab.set_running(False)


def test_sample_deletion_preserves_formal_and_only_deletes_selected_rows(review_ui, monkeypatch):
    ui, tab, add, destination = review_ui
    source, other = add("source"), add("other", channel=4)
    select(tab, source)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    before = destination.read_bytes()
    switch(ui, tab, "待上传")
    monkeypatch.setattr("crX.interface.card_review.messagebox.askyesno", lambda *args, **kwargs: True)
    tab.clear_button.invoke()
    finish(ui, tab)
    assert source.key not in tab.samples and other.key in tab.samples
    assert destination.read_bytes() == before


def test_history_reassessment_blocks_mutations_and_defers_while_running(review_ui, monkeypatch):
    import threading

    from crX.interface import card_review

    ui, tab, add, _destination = review_ui
    add("source")
    entered, release = threading.Event(), threading.Event()
    busy = []
    original_scan = card_review.scan_review

    def delayed_history(*args, **kwargs):
        if kwargs["reassess_history"]:
            entered.set()
            assert release.wait(4)
        return original_scan(*args, **kwargs)

    monkeypatch.setattr(card_review, "scan_review", delayed_history)
    tab.clear_busy_callback = busy.append
    tab._history_pending = True
    tab.set_running(True)
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert not entered.is_set() and tab._history_pending
    tab.set_running(False)
    tab.refresh()
    try:
        _wait_ui(ui, entered.is_set)
        assert tab._action_busy and tab.formalizing and busy == [True]
        assert str(tab.stage_buttons["confirm"].cget("state")) == "disabled"
    finally:
        release.set()
    finish(ui, tab)
    assert not tab._history_pending and not tab.formalizing
    assert busy == [True, False]
