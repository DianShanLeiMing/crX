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
        for stage in ("待确认", "待上传", "已被覆盖", "已忽略"):
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
        tab.filter_var.set("已忽略")
        tab._render_list()
        ui.update()
        assert not tab.thumbnail_tree.get_children() and not tab.tree.get_children()
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
        assert len(compact_rows) >= 1, (len(compact_rows), tab.tree.winfo_height())
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


def test_five_tabs_single_approval_routes_and_keeps_owned_status(review_ui):
    ui, tab, add, destination = review_ui
    first, duplicate, conflict = add("first"), add("duplicate", shift=5), add("conflict", channel=15)
    assert len(tab.stage_tabs.tabs()) == 5 and not hasattr(tab, "filter_combo")
    assert not {"session", "formal", "runtime"}.intersection(tab.tree.cget("columns"))
    select(tab, first)
    assert tab.card_var.get() == ""  # Candidate hints never become human labels.
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
    select(tab, conflict)
    tab.card_var.set("knight")
    tab.stage_buttons["confirm"].invoke()
    finish(ui, tab)
    assert tab.tree.set(conflict.key, "status") == "需复核"
    assert "巨人" in tab.description_var.get()
    assert len(formal._read_entries(destination)) == 1
    for label, sample in (("待上传", first), ("已被覆盖", duplicate), ("待确认", conflict)):
        switch(ui, tab, label)
        assert tab.tree.get_children() == (sample.key,)
        assert tab.stage_tabs.tab(tab.stage_pages[label], "text") == f"{label}（1）"
    switch(ui, tab, "待上传")
    tab.refresh()
    _wait_ui(ui, lambda: not tab._scan_busy)
    assert tab.samples[first.key].status == "included"


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


def test_ignore_restore_and_running_guards_recheck_after_dialog(review_ui, monkeypatch):
    ui, tab, add, destination = review_ui
    source = add("source")
    select(tab, source)
    tab.card_var.set("knight")
    tab.set_running(True)
    for action in ("confirm", "ignore"):
        assert str(tab.stage_buttons[action].cget("state")) == "disabled"
    tab._save()
    assert not tab._action_busy and not formal._read_entries(destination)
    tab.set_running(False)
    tab.note_var.set("切牌动画")
    tab.stage_buttons["ignore"].invoke()
    finish(ui, tab)
    switch(ui, tab, "已忽略")
    assert tab.tree.get_children() == (source.key,) and "切牌动画" in tab.description_var.get()
    tab.stage_buttons["restore"].invoke()
    finish(ui, tab)
    switch(ui, tab, "待确认")
    assert tab.note_var.get() == "切牌动画"
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


def test_catalog_search_comparison_and_upload_archive_preserve_images(review_ui, monkeypatch):
    from crX.bot.card_data import CARD_NAMES_ZH
    from crX.utils.card_upload import _archive_published

    ui, tab, add, destination = review_ui
    first, second = add("first"), add("second", channel=4)
    for sample in (first, second):
        select(tab, sample)
        tab.card_var.set("knight")
        tab.stage_buttons["confirm"].invoke()
        finish(ui, tab)
    switch(ui, tab, "样本库")
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
    view.references.selection_set(keys)
    ui.update()
    assert str(view.compare_button.cget("state")) == "normal"
    view.compare_button.invoke()
    _wait_ui(ui, lambda: len(view._comparison_photos) == 2)
    assert all(view._comparison_photos)
    assert all(key in caption.get() for key, caption in zip(keys, view.caption_vars, strict=True))
    view.query.set("弓箭手")
    ui.update()
    assert "archers" in view.cards.get_children() and view.cards.set("archers", "count") == "0"
    view.cards.selection_set("archers")
    ui.update()
    assert not view.references.get_children()
    assert "暂无正式参考" in view.image_labels[0].cget("text")
    view.query.set("")
    view.only_samples.set(True)
    view.render_cards()
    assert all(int(view.cards.set(card, "count")) > 0 for card in view.cards.get_children())
    view._sort_counts()
    assert view.cards.get_children()[0] == "knight"
    view._sort_counts()
    assert view.cards.get_children()[-1] == "knight"
    before = destination.read_bytes()
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
    assert all(sample.file("card").is_file() for sample in (first, second))
    switch(ui, tab, "样本库")
    view.query.set("knight")
    ui.update()
    assert view.cards.set("knight", "count") == "2"
    assert all(ref.uploaded for ref in view.data["knight"])
    view.references.selection_set(view.references.get_children())
    ui.update()
    view.compare_button.invoke()
    _wait_ui(ui, lambda: len(view._comparison_photos) == 2)
    assert all(view._comparison_photos)
    tab.set_running(True)
    view.query.set("giant")
    ui.update()
    assert view.cards.get_children() == ("giant",)
    _wait_ui(ui, lambda: len(view._comparison_photos) == 1)
    assert view._comparison_photos == [None]
    assert view.image_labels[0].cget("text") == "暂无原图"
    tab.set_running(False)


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
